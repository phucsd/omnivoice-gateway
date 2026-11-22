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
from app.models import ApiKey, LLMProfile, SystemSetting, TTSJob, User, VideoDubbingJob, VoiceDesignPreview, VoiceSample
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

    root_dist_index = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist", "index.html")
    )
    if os.path.exists(root_dist_index):
        root_res = client.get("/")
        assert root_res.status_code == 200
        assert root_res.headers.get("cache-control") == "no-cache"

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


def test_google_oauth_full_callback_flow(monkeypatch):
    from app.routers import auth as auth_router

    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "test-google-client-id")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "test-google-client-secret")
    monkeypatch.setattr(settings, "FRONTEND_ORIGINS", f"{settings.FRONTEND_ORIGINS},https://voice.oloka.net")

    login_res = client.get("/v1/auth/oauth/login/google", params={"redirect_uri": "https://voice.oloka.net/"})
    assert login_res.status_code == 200
    state = login_res.json()["state"]

    class _DummyResp:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    def _fake_post(url, data=None, timeout=15):
        assert url == "https://oauth2.googleapis.com/token"
        assert data["code"] == "valid_google_code_999"
        return _DummyResp(200, {"access_token": "google_access_tok_abc"})

    def _fake_get(url, headers=None, timeout=15):
        assert url == "https://www.googleapis.com/oauth2/v3/userinfo"
        return _DummyResp(
            200,
            {
                "email": "oauth_e2e_user@example.com",
                "sub": "sub_oauth_e2e_999",
                "email_verified": True,
                "name": "OAuth E2E User",
            },
        )

    monkeypatch.setattr(auth_router.requests, "post", _fake_post)
    monkeypatch.setattr(auth_router.requests, "get", _fake_get)

    cb_res = client.get(
        "/v1/auth/oauth/callback/google",
        params={"code": "valid_google_code_999", "redirect_uri": "https://voice.oloka.net/", "state": state},
    )
    assert cb_res.status_code == 200, cb_res.text
    access_token = cb_res.json()["access_token"]
    assert access_token

    me_res = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me_res.status_code == 200, me_res.text
    assert me_res.json()["is_verified"] is True

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == "oauth_e2e_user@example.com").first()
        if u:
            db.delete(u)
            db.commit()
    finally:
        db.close()


def test_auto_align_deletes_dangling_api_keys_instead_of_admin_reassignment(tmp_path):
    temp_db_file = tmp_path / "legacy_apikeys.sqlite"
    db_url = f"sqlite:///{temp_db_file}"
    temp_engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=temp_engine)
    temp_engine.dispose()

    conn = sqlite3.connect(str(temp_db_file))
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (id, username, email, hashed_password, is_verified, is_approved, is_admin, created_at) "
            "VALUES ('usr_admin_only', 'admin', 'admin@example.com', 'h', 1, 1, 1, CURRENT_TIMESTAMP)"
        )
        cur.execute(
            "INSERT INTO api_keys (id, user_id, name, key, created_at) "
            "VALUES ('ak_deleted_user', 'usr_deleted_999', 'Old Revoked User Key', 'ovg_live_deleted_999', CURRENT_TIMESTAMP)"
        )
        cur.execute(
            "INSERT INTO api_keys (id, user_id, name, key, created_at) "
            "VALUES ('ak_valid_admin', 'usr_admin_only', 'Admin Key', 'ovg_live_admin_ok', CURRENT_TIMESTAMP)"
        )
        conn.commit()
    finally:
        conn.close()

    auto_align_legacy_user_ids(db_url)

    conn = sqlite3.connect(str(temp_db_file))
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, user_id FROM api_keys")
        rows = cur.fetchall()
        assert rows == [("ak_valid_admin", "usr_admin_only")]
    finally:
        conn.close()


def test_xi_api_key_auth_female_tag_parsing_and_multiword_voice_resolution():
    wav_bytes = _make_tiny_wav_bytes()
    headers_a = _auth_headers("sec_user_a")

    db = SessionLocal()
    try:
        user_a = db.query(User).filter(User.username == "sec_user_a").first()
        xi_key = user_a.api_key
    finally:
        db.close()

    up = client.post(
        "/v1/voice-samples",
        files={"file": ("mien_nam_female.wav", wav_bytes, "audio/wav")},
        data={
            "name": "Giọng Nữ Miền Nam Dịu Dàng",
            "ref_text": "Xin chào quý khán giả",
            "tags": "Nữ, Miền Nam, Truyền cảm",
        },
        headers=headers_a,
    )
    assert up.status_code == 200, up.text
    sample_id = up.json()["voice_sample_id"]

    try:
        # 1. xi-api-key header authentication & "Nữ, Miền Nam" must classify as female (not male via substring "nam")
        voices_res = client.get("/v1/voices", headers={"xi-api-key": xi_key})
        assert voices_res.status_code == 200, voices_res.text
        matched = [v for v in voices_res.json().get("voices", []) if v["voice_id"] == sample_id]
        assert len(matched) == 1
        assert matched[0]["labels"]["gender"] == "female"

        # 2. Multi-word VoiceSample.name resolution in OpenAI /v1/audio/speech without treating spaces as instruct
        speech_res = client.post(
            "/v1/audio/speech",
            json={
                "model": "omnivoice-v1",
                "input": "Xin chào từ kiểm thử tự động",
                "voice": "Giọng Nữ Miền Nam Dịu Dàng",
                "response_format": "wav",
            },
            headers={"xi-api-key": xi_key},
        )
        assert speech_res.status_code == 200, speech_res.text
        assert len(speech_res.content) > 44
    finally:
        client.delete(f"/v1/voice-samples/{sample_id}", headers=headers_a)


def test_asr_history_audio_url_and_settings_system_fallback():
    headers_b = _auth_headers("sec_user_b")
    wav_bytes = _make_tiny_wav_bytes()

    # Global SystemSetting kaggle_timeout_seconds = 4200; user_b has no UserSetting override yet
    db = SessionLocal()
    try:
        sys_row = db.query(SystemSetting).filter(SystemSetting.key == "kaggle_timeout_seconds").first()
        if sys_row:
            sys_row.value = "4200"
        else:
            db.add(SystemSetting(key="kaggle_timeout_seconds", value="4200"))
        db.commit()
    finally:
        db.close()

    settings_res = client.get("/v1/settings", headers=headers_b)
    assert settings_res.status_code == 200
    assert settings_res.json()["kaggle_timeout_seconds"] == 4200

    # Create ASR job and verify History audio_url points to /v1/asr/jobs/{id}/audio
    asr_res = client.post(
        "/v1/asr",
        files={"file": ("asr_hist.wav", wav_bytes, "audio/wav")},
        headers=headers_b,
    )
    assert asr_res.status_code == 200, asr_res.text
    asr_id = asr_res.json()["job_id"]

    try:
        job_detail = client.get(f"/v1/jobs/{asr_id}", headers=headers_b)
        assert job_detail.status_code == 200
        audio_url = job_detail.json().get("audio_url") or ""
        assert audio_url == f"/v1/asr/jobs/{asr_id}/audio"

        audio_stream = client.get(audio_url, headers=headers_b)
        assert audio_stream.status_code == 200
    finally:
        client.delete(f"/v1/jobs/{asr_id}", headers=headers_b)


def test_dubbing_cancel_and_delete_cleanup_child_tts_segments():
    headers_a = _auth_headers("sec_user_a")
    dub_id = f"dub_cleanup_{secrets.token_hex(4)}"
    seg_job_id = f"dub_seg_{dub_id}_1_abcd"

    db = SessionLocal()
    try:
        dub = VideoDubbingJob(
            id=dub_id,
            user_id="usr_sec_a",
            status="generating_tts",
            progress=65,
            message="Generating segment 1",
            source_type="upload",
            target_language="Vietnamese",
        )
        seg = TTSJob(
            id=seg_job_id,
            user_id="usr_sec_a",
            job_type="clone_voice",
            text="Segment 1 text",
            status="processing",
            batch_id=dub_id,
        )
        db.add(dub)
        db.add(seg)
        db.commit()
    finally:
        db.close()

    cancel_res = client.post(f"/v1/video-dubbing/jobs/{dub_id}/cancel", headers=headers_a)
    assert cancel_res.status_code == 200, cancel_res.text
    assert cancel_res.json()["status"] == "cancelled"

    db = SessionLocal()
    try:
        seg_after_cancel = db.query(TTSJob).filter(TTSJob.id == seg_job_id).first()
        assert seg_after_cancel is not None
        assert seg_after_cancel.status == "cancelled"
    finally:
        db.close()

    del_res = client.delete(f"/v1/jobs/{dub_id}", headers=headers_a)
    assert del_res.status_code in (200, 204), del_res.text

    db = SessionLocal()
    try:
        assert db.query(VideoDubbingJob).filter(VideoDubbingJob.id == dub_id).first() is None
        assert db.query(TTSJob).filter(TTSJob.id == seg_job_id).first() is None
    finally:
        db.close()

