import io
import json
import os
import secrets
import wave
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ["APP_ENV"] = "testing"
os.environ["WORKER_MODE"] = "mock"
os.environ["DATABASE_URL"] = "sqlite:///./test_vd_llm.sqlite"
os.environ["WORKER_TOKEN"] = "test_secret_token"

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import LLMProfile, TTSJob, User, VoiceDesignPreview, VoiceSample
from app.services.voice_design_service import VoiceDesignService
from app.utils.auth import create_access_token, get_password_hash

client = TestClient(app)


def _make_wav_bytes(duration_sec: float = 0.5, sample_rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    frames = int(duration_sec * sample_rate)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.username == "vd_tester").first()
        if not u:
            u = User(
                id="usr_vd_tester",
                username="vd_tester",
                email="vd_tester@example.com",
                hashed_password=get_password_hash("Pass123!"),
                is_verified=True,
                is_approved=True,
                is_admin=False,
                api_key=f"ovg_live_{secrets.token_hex(16)}",
            )
            db.add(u)
            db.commit()
    finally:
        db.close()

    yield

    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    for p in ("test_vd_llm.sqlite", "test_vd_llm.sqlite-shm", "test_vd_llm.sqlite-wal"):
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass


def _headers() -> dict[str, str]:
    token = create_access_token({"sub": "vd_tester"})
    return {"Authorization": f"Bearer {token}"}


def test_instruct_sanitizer_enforces_omnivoice_categories():
    # Synonyms like 'warm' -> 'low pitch', 'uk accent' -> 'british accent', max 1 per category
    inst, attrs = VoiceDesignService.validate_and_normalize_instruct(
        "male, mature, warm, deep, uk accent, invalid_noise_tag"
    )
    assert attrs["gender"] == "male"
    assert attrs["age"] == "middle-aged"
    assert attrs["pitch"] == "low pitch"
    assert attrs["accent"] == "british accent"
    assert attrs["style"] is None
    assert inst == "male, middle-aged, low pitch, british accent"


def test_llm_status_does_not_leak_secrets():
    db = SessionLocal()
    prof_id = f"llm_vd_{secrets.token_hex(4)}"
    try:
        db.query(LLMProfile).update({LLMProfile.is_active: False})
        prof = LLMProfile(
            id=prof_id,
            name="Test Gemini Studio",
            provider="gemini",
            api_key="AIzaSySuperSecretKeyValue123456",
            model="gemini-2.5-flash",
            thinking_effort="low",
            is_active=True,
        )
        db.add(prof)
        db.commit()

        res = client.get("/v1/voice-design/llm-status", headers=_headers())
        assert res.status_code == 200
        body = res.json()
        assert body["is_configured"] is True
        assert body["active_model"] == "gemini-2.5-flash"
        serialized = json.dumps(body)
        assert "AIzaSySuperSecretKeyValue123456" not in serialized
    finally:
        row = db.query(LLMProfile).filter(LLMProfile.id == prof_id).first()
        if row:
            db.delete(row)
            db.commit()
        db.close()


def test_assist_heuristic_fallback_when_no_llm_key():
    db = SessionLocal()
    try:
        db.query(LLMProfile).delete()
        db.commit()
    finally:
        db.close()

    res = client.post(
        "/v1/voice-design/assist",
        json={
            "voice_request": "Giọng nam trung niên miền Bắc đọc bản tin thời sự đĩnh đạc, trầm ấm",
            "target_language": "vi",
        },
        headers=_headers(),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["source"] == "heuristic_fallback"
    assert data["attributes"]["gender"] == "male"
    assert data["attributes"]["age"] == "middle-aged"
    assert data["attributes"]["pitch"] == "low pitch"
    assert "male, middle-aged, low pitch" == data["instruct"]
    assert len(data["variants"]) == 3
    assert "Tin tức" in data["suggested_tags"]
    assert "Miền Bắc" in data["suggested_tags"]


def test_assist_with_configured_gemini_llm_mock():
    db = SessionLocal()
    prof_id = f"llm_mock_{secrets.token_hex(4)}"
    try:
        prof = LLMProfile(
            id=prof_id,
            name="Mock Gemini Profile",
            provider="gemini",
            api_key="AIzaMockTestKey999",
            model="gemini-2.5-flash",
            thinking_effort="none",
            is_active=True,
        )
        db.add(prof)
        db.commit()

        fake_llm_json = {
            "attributes": {
                "gender": "female",
                "age": "young adult",
                "pitch": "low pitch",
                "style": None,
                "accent": "british accent",  # Should be stripped automatically when language == 'vi'
            },
            "recommended_params": {
                "speed": 0.91,
                "guidance_scale": 2.3,
                "position_temperature": 4.3,
                "class_temperature": 0.1,
                "t_shift": 0.1,
                "num_step": 36,
                "denoise": True,
                "language": "vi",
            },
            "preview_text": "Đêm thu dịu nhẹ trải dài trên phố nhỏ, mang theo hương hoa sữa thoang thoảng gợi nhớ bao kỷ niệm êm đềm.",
            "suggested_name": "Nữ Trẻ Kể Chuyện Đêm Khuya",
            "suggested_tags": ["Miền Bắc", "Trẻ", "Kể chuyện"],
            "explanation": "Chọn giọng nữ thanh niên trầm ấm, nhịp độ 0.91x giúp truyền tải chiều sâu cảm xúc.",
            "variants": [
                {
                    "label": "Trầm ấm sâu lắng",
                    "summary": "Nhịp chậm kể chuyện đêm khuya",
                    "attributes": {"gender": "female", "age": "young adult", "pitch": "low pitch"},
                    "speed": 0.91,
                    "guidance_scale": 2.3,
                    "position_temperature": 4.3,
                    "class_temperature": 0.1,
                    "preview_text": "Đêm thu dịu nhẹ trải dài trên phố nhỏ.",
                }
            ],
        }

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [
                {"content": {"parts": [{"text": json.dumps(fake_llm_json, ensure_ascii=False)}]}}
            ]
        }

        with patch("app.services.voice_design_service.requests.post", return_value=mock_resp):
            res = client.post(
                "/v1/voice-design/assist",
                json={
                    "voice_request": "Giọng nữ trẻ kể chuyện đêm khuya",
                    "target_language": "vi",
                    "llm_profile_id": prof_id,
                },
                headers=_headers(),
            )
            assert res.status_code == 200
            body = res.json()
            assert body["source"] == "llm"
            assert body["llm_model"] == "gemini-2.5-flash"
            # Verify English accent stripped for Vietnamese synthesis
            assert body["attributes"]["accent"] is None
            assert body["instruct"] == "female, young adult, low pitch"
            assert body["recommended_params"]["speed"] == 0.91
            assert body["suggested_slug"] == "nu_tre_ke_chuyen_dem_khuya"
            assert len(body["variants"]) == 1
    finally:
        row = db.query(LLMProfile).filter(LLMProfile.id == prof_id).first()
        if row:
            db.delete(row)
            db.commit()
        db.close()


def test_generate_script_and_preview_history_lifecycle(tmp_path):
    # 1. Generate fallback script
    script_res = client.post(
        "/v1/voice-design/generate-script",
        json={
            "voice_request": "Giọng nữ trẻ quảng cáo",
            "instruct": "female, young adult, high pitch",
            "style_or_topic": "Quảng cáo thương hiệu",
            "language": "vi",
        },
        headers=_headers(),
    )
    assert script_res.status_code == 200
    script_data = script_res.json()
    assert len(script_data["preview_text"]) > 20
    assert script_data["estimated_duration"] > 1.0

    # 2. Create preview with explicit instruct
    create_res = client.post(
        "/v1/voice-design/previews",
        json={
            "voice_request": "Giọng nữ trẻ quảng cáo cà phê",
            "instruct": "female, young adult, high pitch",
            "preview_text": script_data["preview_text"],
            "speed": 1.08,
            "num_step": 32,
            "guidance_scale": 2.4,
            "language": "vi",
        },
        headers=_headers(),
    )
    assert create_res.status_code == 200
    c_body = create_res.json()
    preview_id = c_body["preview_id"]
    job_id = c_body["job_id"]
    assert c_body["instruct"] == "female, young adult, high pitch"

    # Simulate completion so we can test history listing, accept with metadata, and delete
    dummy_wav = tmp_path / f"{preview_id}.wav"
    dummy_wav.write_bytes(_make_wav_bytes())

    db = SessionLocal()
    try:
        pv = db.query(VoiceDesignPreview).filter(VoiceDesignPreview.id == preview_id).first()
        jb = db.query(TTSJob).filter(TTSJob.id == job_id).first()
        pv.status = "completed"
        pv.preview_audio_path = str(dummy_wav)
        jb.status = "completed"
        jb.output_audio_path = str(dummy_wav)
        db.commit()
    finally:
        db.close()

    # 3. List recent previews
    hist_res = client.get("/v1/voice-design/previews?limit=10", headers=_headers())
    assert hist_res.status_code == 200
    hist_items = hist_res.json()
    matched = next((x for x in hist_items if x["id"] == preview_id), None)
    assert matched is not None
    assert matched["instruct"] == "female, young adult, high pitch"
    assert matched["speed"] == 1.08
    assert matched["audio_url"] == f"/v1/voice-design/previews/{preview_id}/audio"

    # 4. Accept preview with AI-suggested name and tags
    acc_res = client.post(
        f"/v1/voice-design/previews/{preview_id}/accept",
        json={
            "name": "Nữ Quảng Cáo Cà Phê",
            "tags": ["Trẻ", "Quảng cáo"],
            "is_public": False,
        },
        headers=_headers(),
    )
    assert acc_res.status_code == 200
    vs_id = acc_res.json()["voice_sample_id"]

    db = SessionLocal()
    try:
        vs = db.query(VoiceSample).filter(VoiceSample.id == vs_id).first()
        assert vs is not None
        assert vs.name == "Nữ Quảng Cáo Cà Phê"
        assert "Quảng cáo" in (vs.tags or "")
        assert vs.source_job_data is not None
        if vs.file_path and os.path.exists(vs.file_path):
            os.remove(vs.file_path)
        db.delete(vs)
        db.commit()
    finally:
        db.close()

    # 5. Delete preview take
    del_res = client.delete(f"/v1/voice-design/previews/{preview_id}", headers=_headers())
    assert del_res.status_code == 200
    hist_after = client.get("/v1/voice-design/previews?limit=10", headers=_headers()).json()
    assert all(x["id"] != preview_id for x in hist_after)
