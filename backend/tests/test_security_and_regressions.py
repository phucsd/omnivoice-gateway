import io
import os
import secrets
import sqlite3
import wave
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

os.environ["APP_ENV"] = "testing"
os.environ["WORKER_MODE"] = "mock"
os.environ["DATABASE_URL"] = "sqlite:///./test_sec_regressions.sqlite"
os.environ["WORKER_TOKEN"] = "test_secret_token"

from app.config import settings
from app.database import Base, SessionLocal, auto_align_legacy_user_ids, engine
from app.main import app
from app.models import LLMProfile, SystemSetting, User, VoiceDesignPreview, VoiceSample
from app.routers.compat import resolve_ref_audio
from app.routers.internal_worker import _safe_extract_zip
from app.services.job_service import JobService
from app.utils.auth import create_access_token, get_password_hash

client = TestClient(app)


def _make_tiny_wav_bytes(duration_sec: float = 0.25, sample_rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    num_frames = int(duration_sec * sample_rate)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * num_frames)
    return buf.getvalue()


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        def _ensure_user(user_id: str, username: str, email: str, is_admin: bool) -> User:
            u = db.query(User).filter(User.username == username).first()
            if u:
                return u
            u = User(
                id=user_id,
                username=username,
                email=email,
                hashed_password=get_password_hash("TestPass123!"),
                is_verified=True,
                is_approved=True,
                is_admin=is_admin,
                api_key=f"ovg_live_{secrets.token_hex(24)}",
            )
            db.add(u)
            db.commit()
            db.refresh(u)
            return u

        _ensure_user("usr_sec_admin", "sec_admin", "sec_admin@example.com", True)
        _ensure_user("usr_sec_a", "sec_user_a", "sec_user_a@example.com", False)
        _ensure_user("usr_sec_b", "sec_user_b", "sec_user_b@example.com", False)
    finally:
        db.close()

    yield

    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    for path in (
        "test_sec_regressions.sqlite",
        "test_sec_regressions.sqlite-shm",
        "test_sec_regressions.sqlite-wal",
    ):
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def _auth_headers(username: str) -> dict[str, str]:
    token = create_access_token(data={"sub": username})
    return {"Authorization": f"Bearer {token}"}


def test_security_headers_and_health_db_rbac():
    res = client.get("/health")
    assert res.status_code == 200
    csp = res.headers.get("content-security-policy", "")
    assert "frame-ancestors 'self' https://huggingface.co https://*.hf.space" in csp
    assert res.headers.get("x-content-type-options") == "nosniff"
    assert "referrer-policy" in res.headers
    assert "permissions-policy" in res.headers
    assert "x-frame-options" not in res.headers

    for path in ("/health/db", "/v1/health/db"):
        unauth = client.get(path)
        assert unauth.status_code == 401

        non_admin = client.get(path, headers=_auth_headers("sec_user_a"))
        assert non_admin.status_code == 403

        admin_res = client.get(path, headers=_auth_headers("sec_admin"))
        assert admin_res.status_code == 200
        body = admin_res.json()
        assert "api_key" not in body
        assert "db_path" not in body


def test_worker_token_rejects_user_api_key():
    db = SessionLocal()
    try:
        user_a = db.query(User).filter(User.username == "sec_user_a").first()
        user_api_key = user_a.api_key
    finally:
        db.close()

    payload = {"worker_id": "wrk_sec_test", "status": "ready", "message": "test"}
    bad = client.post(
        "/v1/internal/workers/register",
        json=payload,
        headers={"Authorization": f"Bearer {user_api_key}"},
    )
    assert bad.status_code == 401

    good = client.post(
        "/v1/internal/workers/register",
        json=payload,
        headers={"Authorization": f"Bearer {settings.WORKER_TOKEN}"},
    )
    assert good.status_code == 200


def test_safe_extract_zip_blocks_zip_slip(tmp_path):
    evil_zip = tmp_path / "evil.zip"
    dest_dir = tmp_path / "extract"
    with zipfile.ZipFile(evil_zip, "w") as zf:
        zf.writestr("../../escaped.txt", "pwned")

    with pytest.raises(ValueError):
        _safe_extract_zip(str(evil_zip), str(dest_dir))

    assert not (tmp_path / "escaped.txt").exists()


def test_compat_resolve_ref_audio_blocks_local_path_and_ssrf(tmp_path):
    local_file = tmp_path / "secret.wav"
    local_file.write_bytes(_make_tiny_wav_bytes())

    db = SessionLocal()
    try:
        with pytest.raises(ValueError):
            resolve_ref_audio(db, str(local_file), user_id="usr_sec_a")

        with pytest.raises(ValueError):
            resolve_ref_audio(db, "http://127.0.0.1:7860/health", user_id="usr_sec_a")

        with pytest.raises(ValueError):
            resolve_ref_audio(db, "http://169.254.169.254/latest/meta-data/", user_id="usr_sec_a")
    finally:
        db.close()


def test_dubbing_non_admin_rbac_and_public_llm_profiles():
    user_headers = _auth_headers("sec_user_a")

    prof_res = client.get("/v1/video-dubbing/llm-profiles", headers=user_headers)
    assert prof_res.status_code == 200
    assert isinstance(prof_res.json(), list)
    for item in prof_res.json():
        assert "api_key" not in item

    for admin_only in (
        "/v1/video-dubbing/cookie-status",
        "/v1/video-dubbing/proxy-pool",
        "/v1/video-dubbing/oauth/status",
    ):
        res = client.get(admin_only, headers=user_headers)
        assert res.status_code == 403


def test_private_voice_sample_isolation_and_openai_voices_endpoint():
    wav_bytes = _make_tiny_wav_bytes()
    headers_a = _auth_headers("sec_user_a")
    headers_b = _auth_headers("sec_user_b")

    up = client.post(
        "/v1/voice-samples",
        files={"file": ("sec_private.wav", wav_bytes, "audio/wav")},
        data={"name": "Private Sec Sample", "ref_text": "Xin chao"},
        headers=headers_a,
    )
    assert up.status_code == 200, up.text
    sample_id = up.json()["voice_sample_id"]

    try:
        assert client.get(f"/v1/voice-samples/{sample_id}/audio").status_code == 401
        assert client.get(f"/v1/voice-samples/{sample_id}/audio", headers=headers_b).status_code == 403
        assert client.get(f"/v1/voice-samples/{sample_id}/audio", headers=headers_a).status_code == 200

        put_b = client.put(
            f"/v1/voice-samples/{sample_id}",
            json={"name": "Hacked"},
            headers=headers_b,
        )
        assert put_b.status_code == 404

        del_b = client.delete(f"/v1/voice-samples/{sample_id}", headers=headers_b)
        assert del_b.status_code == 404

        tts_b = client.post(
            "/v1/tts/jobs",
            json={"mode": "clone_voice", "text": "Test clone", "voice_sample_id": sample_id},
            headers=headers_b,
        )
        assert tts_b.status_code == 400

        tts_a = client.post(
            "/v1/tts/jobs",
            json={"mode": "clone_voice", "text": "Test clone", "voice_sample_id": sample_id},
            headers=headers_a,
        )
        assert tts_a.status_code == 200

        voices_res = client.get("/v1/audio/voices", headers=headers_a)
        assert voices_res.status_code == 200
        voice_ids = [v["voice_id"] for v in voices_res.json().get("voices", [])]
        assert sample_id in voice_ids
    finally:
        client.delete(f"/v1/voice-samples/{sample_id}", headers=headers_a)


def test_accept_preview_is_idempotent(tmp_path):
    preview_wav = tmp_path / "preview_demo.wav"
    preview_wav.write_bytes(_make_tiny_wav_bytes())

    db = SessionLocal()
    preview_id = f"vd_idemp_{secrets.token_hex(6)}"
    created_sample_id = f"vs_from_{preview_id}"
    try:
        pv = VoiceDesignPreview(
            id=preview_id,
            user_id="usr_sec_a",
            voice_request="nu tre",
            instruct="female, young adult",
            preview_text="Xin chao",
            preview_audio_path=str(preview_wav),
            status="completed",
        )
        db.add(pv)
        db.commit()

        s1 = JobService.accept_preview(db, preview_id, user_id="usr_sec_a")
        s2 = JobService.accept_preview(db, preview_id, user_id="usr_sec_a")
        assert s1.id == s2.id == created_sample_id
        assert db.query(VoiceSample).filter(VoiceSample.id == created_sample_id).count() == 1
    finally:
        sample = db.query(VoiceSample).filter(VoiceSample.id == created_sample_id).first()
        if sample:
            if sample.file_path and os.path.exists(sample.file_path):
                os.remove(sample.file_path)
            db.delete(sample)
        pv_row = db.query(VoiceDesignPreview).filter(VoiceDesignPreview.id == preview_id).first()
        if pv_row:
            db.delete(pv_row)
        db.commit()
        db.close()


def test_admin_secret_masking_and_non_destructive_save():
    admin_headers = _auth_headers("sec_admin")
    raw_smtp = "smtp_super_secret_value_2026"
    assert client.post("/v1/admin/settings", json={"smtp_password": raw_smtp}, headers=admin_headers).status_code == 200

    got_settings = client.get("/v1/admin/settings", headers=admin_headers)
    assert got_settings.status_code == 200
    masked_smtp = got_settings.json()["smtp_password"]
    assert "••••" in masked_smtp
    assert raw_smtp not in masked_smtp

    # Saving the masked placeholder back must preserve the real secret in DB
    assert client.post("/v1/admin/settings", json={"smtp_password": masked_smtp}, headers=admin_headers).status_code == 200

    raw_llm_key = "sk-secret-llm-profile-key-987654"
    created_prof = client.post(
        "/v1/admin/llm-profiles",
        json={"name": "Sec Mask Profile", "provider": "openai", "api_key": raw_llm_key, "model": "gpt-4o-mini"},
        headers=admin_headers,
    )
    assert created_prof.status_code == 200
    prof_data = created_prof.json()
    assert "••••" in prof_data["api_key"]
    assert raw_llm_key not in prof_data["api_key"]

    upd_prof = client.put(
        f"/v1/admin/llm-profiles/{prof_data['id']}",
        json={"name": "Sec Mask Profile Renamed", "api_key": prof_data["api_key"]},
        headers=admin_headers,
    )
    assert upd_prof.status_code == 200

    db = SessionLocal()
    try:
        smtp_row = db.query(SystemSetting).filter(SystemSetting.key == "smtp_password").first()
        assert smtp_row is not None and smtp_row.value == raw_smtp
        prof_row = db.query(LLMProfile).filter(LLMProfile.id == prof_data["id"]).first()
        assert prof_row is not None and prof_row.api_key == raw_llm_key
        if prof_row:
            db.delete(prof_row)
            db.commit()
    finally:
        db.close()


def test_oauth_redirect_uri_and_state_validation(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "test-google-client-id")

    bad_redirect = client.get("/v1/auth/oauth/login/google", params={"redirect_uri": "https://evil.example.com/cb"})
    assert bad_redirect.status_code == 400

    ok_login = client.get("/v1/auth/oauth/login/google", params={"redirect_uri": "http://localhost:5173/"})
    assert ok_login.status_code == 200
    data = ok_login.json()
    assert data.get("state")
    assert "state=" in data.get("auth_url", "")

    tampered = client.get(
        "/v1/auth/oauth/callback/google",
        params={"code": "dummy", "redirect_uri": "http://localhost:5173/", "state": "1700000000.nonce.bad_signature"},
    )
    assert tampered.status_code == 400


def test_auto_align_legacy_user_ids_preserves_multi_tenant_owners(tmp_path):
    temp_db_file = tmp_path / "legacy_align.sqlite"
    db_url = f"sqlite:///{temp_db_file}"
    temp_engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=temp_engine)
    temp_engine.dispose()

    conn = sqlite3.connect(str(temp_db_file))
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (id, username, email, hashed_password, is_verified, is_approved, is_admin, created_at) "
            "VALUES ('usr_admin_legacy', 'admin', 'phucsd@gmail.com', 'h', 1, 1, 1, CURRENT_TIMESTAMP)"
        )
        cur.execute(
            "INSERT INTO users (id, username, email, hashed_password, is_verified, is_approved, is_admin, created_at) "
            "VALUES ('usr_tenant_b', 'tenantb', 'tenantb@example.com', 'h', 1, 1, 0, CURRENT_TIMESTAMP)"
        )
        cur.execute(
            "INSERT INTO voice_samples (id, user_id, source_type, file_path, status, is_public, created_at) "
            "VALUES ('vs_owned_b', 'usr_tenant_b', 'uploaded', 'b.wav', 'ready', 0, CURRENT_TIMESTAMP)"
        )
        cur.execute(
            "INSERT INTO voice_samples (id, user_id, source_type, file_path, status, is_public, created_at) "
            "VALUES ('vs_orphan', '', 'uploaded', 'orphan.wav', 'ready', 0, CURRENT_TIMESTAMP)"
        )
        conn.commit()
    finally:
        conn.close()

    auto_align_legacy_user_ids(db_url)

    conn = sqlite3.connect(str(temp_db_file))
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM voice_samples WHERE id = 'vs_owned_b'")
        assert cur.fetchone()[0] == "usr_tenant_b"
        cur.execute("SELECT user_id FROM voice_samples WHERE id = 'vs_orphan'")
        assert cur.fetchone()[0] == "usr_admin_legacy"
    finally:
        conn.close()
