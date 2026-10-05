import os
import json
import uuid
import pytest

TEST_DB_PATH = "test_align_db.sqlite"
os.environ["APP_ENV"] = "testing"
os.environ["WORKER_MODE"] = "mock"
os.environ["DATABASE_URL"] = f"sqlite:///./{TEST_DB_PATH}"

from fastapi.testclient import TestClient
from app.config import settings
from app.database import Base, engine
from app.main import app

client = TestClient(app)

@pytest.fixture(scope="module", autouse=True)
def setup_and_teardown_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    try:
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
    except Exception:
        pass

def test_full_lifecycle_with_alignment():
    uid = uuid.uuid4().hex[:8]
    uname = f"align_{uid}"
    email = f"align_{uid}@example.com"
    pwd = "password_align"

    # 1. Register and login test user
    reg_res = client.post("/v1/auth/register", json={
        "username": uname,
        "email": email,
        "password": pwd
    })
    assert reg_res.status_code == 201
    otp_code = reg_res.json()["debug_code"]

    verify_res = client.post("/v1/auth/verify-email", json={
        "username": uname,
        "code": otp_code
    })
    assert verify_res.status_code == 200

    login_res = client.post("/v1/auth/login", json={
        "username": uname,
        "password": pwd
    })
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]
    user_headers = {"Authorization": f"Bearer {token}"}

    # 2. Submit TTS Job with with_alignment=True
    create_res = client.post("/v1/tts/jobs", json={
        "mode": "auto_voice",
        "text": "Chào mừng bạn đến với OmniVoice",
        "with_alignment": True
    }, headers=user_headers)
    assert create_res.status_code == 200
    job_id = create_res.json()["job_id"]
    assert job_id

    # 3. Worker pulls the job via /v1/internal/jobs/next using settings.WORKER_TOKEN
    worker_headers = {"Authorization": f"Bearer {settings.WORKER_TOKEN}"}
    next_res = client.get("/v1/internal/jobs/next?worker_id=worker_align", headers=worker_headers)
    assert next_res.status_code == 200
    worker_job = next_res.json()["job"]
    assert worker_job is not None
    assert worker_job["job_id"] == job_id
    assert worker_job["with_alignment"] is True

    # 4. Worker finishes and uploads output with alignment JSON
    mock_alignment = [
        {"word": "Chào", "start": 0.0, "end": 0.3},
        {"word": "mừng", "start": 0.3, "end": 0.6},
        {"word": "bạn", "start": 0.6, "end": 0.8},
        {"word": "đến", "start": 0.8, "end": 1.0},
        {"word": "với", "start": 1.0, "end": 1.2},
        {"word": "OmniVoice", "start": 1.2, "end": 1.8}
    ]
    alignment_str = json.dumps(mock_alignment)

    dummy_wav_bytes = b"RIFF" + b"\x00" * 36 + b"data" + b"\x00" * 100
    upload_res = client.post(
        f"/v1/internal/jobs/{job_id}/output",
        files={"file": (f"{job_id}.wav", dummy_wav_bytes, "audio/wav")},
        data={"alignment": alignment_str},
        headers=worker_headers
    )
    assert upload_res.status_code == 200

    # 5. Check status via /v1/jobs/{job_id}
    status_res = client.get(f"/v1/jobs/{job_id}", headers=user_headers)
    assert status_res.status_code == 200
    job_data = status_res.json()
    assert job_data["status"] == "completed"
    assert job_data["alignment"] is not None
    assert isinstance(job_data["alignment"], list)
    assert len(job_data["alignment"]) == 6
    assert job_data["alignment"][0]["word"] == "Chào"
    assert job_data["alignment"][0]["start"] == 0.0
    assert job_data["alignment"][0]["end"] == 0.3

    # 6. Check status via /v1/tts/jobs/{job_id}
    tts_status_res = client.get(f"/v1/tts/jobs/{job_id}", headers=user_headers)
    assert tts_status_res.status_code == 200
    tts_job_data = tts_status_res.json()
    assert tts_job_data["status"] == "completed"
    assert tts_job_data["alignment"] is not None
    assert len(tts_job_data["alignment"]) == 6
    assert tts_job_data["alignment"][0]["word"] == "Chào"

    # 7. Check list jobs /v1/jobs
    list_res = client.get("/v1/jobs", headers=user_headers)
    assert list_res.status_code == 200
    jobs_list = list_res.json()
    matched = next((j for j in jobs_list if j["job_id"] == job_id), None)
    assert matched is not None
    assert matched["alignment"] is not None
    assert len(matched["alignment"]) == 6

    # 8. Check batch jobs /v1/jobs/batch
    batch_res = client.post("/v1/jobs/batch", json={"job_ids": [job_id]}, headers=user_headers)
    assert batch_res.status_code == 200
    batch_data = batch_res.json()
    assert job_id in batch_data
    assert batch_data[job_id]["alignment"] is not None
    assert len(batch_data[job_id]["alignment"]) == 6

def test_compat_endpoints_with_alignment():
    uid = uuid.uuid4().hex[:8]
    uname = f"align_compat_{uid}"
    email = f"align_compat_{uid}@example.com"
    pwd = "password_align"

    reg_res = client.post("/v1/auth/register", json={
        "username": uname,
        "email": email,
        "password": pwd
    })
    assert reg_res.status_code == 201
    otp_code = reg_res.json()["debug_code"]

    client.post("/v1/auth/verify-email", json={
        "username": uname,
        "code": otp_code
    })

    login_res = client.post("/v1/auth/login", json={
        "username": uname,
        "password": pwd
    })
    token = login_res.json()["access_token"]
    user_headers = {"Authorization": f"Bearer {token}"}

    # Test /v1/audio/speech
    speech_res = client.post("/v1/audio/speech", json={
        "model": "omnivoice",
        "input": "Hôm nay thời tiết đẹp quá",
        "voice": "female, young adult",
        "with_alignment": True
    }, headers=user_headers)
    assert speech_res.status_code == 200
    assert "application/json" in speech_res.headers["content-type"]
    speech_data = speech_res.json()
    assert speech_data["status"] == "completed"
    assert "audioUrl" in speech_data
    assert "alignment" in speech_data
    assert isinstance(speech_data["alignment"], list)
    assert len(speech_data["alignment"]) > 0

    # Test /v1/tts/inference
    infer_res = client.post("/v1/tts/inference", json={
        "text": "Kiểm tra suy luận tương thích",
        "with_alignment": True
    }, headers=user_headers)
    assert infer_res.status_code == 200
    assert "application/json" in infer_res.headers["content-type"]
    infer_data = infer_res.json()
    assert infer_data["status"] == "completed"
    assert "audioUrl" in infer_data
    assert "alignment" in infer_data
    assert isinstance(infer_data["alignment"], list)
    assert len(infer_data["alignment"]) > 0
