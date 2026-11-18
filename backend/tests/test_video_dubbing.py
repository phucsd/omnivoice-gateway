import os
import pytest
import io
import json

os.environ["APP_ENV"] = "testing"
os.environ["WORKER_MODE"] = "mock"
os.environ["DATABASE_URL"] = "sqlite:///./test_video_db.sqlite"
os.environ["WORKER_TOKEN"] = "test_secret_token"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from app.main import app
from app.database import Base, get_db, engine
from app.models import VideoDubbingJob

client = TestClient(app)

@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    try:
        if os.path.exists("test_video_db.sqlite"):
            os.remove("test_video_db.sqlite")
    except Exception:
        pass

def test_video_dubbing_flow():
    # 1. Register a test user and login
    reg_res = client.post("/v1/auth/register", json={
        "username": "dub_user",
        "password": "password_123",
        "email": "dub_user@example.com"
    })
    assert reg_res.status_code == 201
    otp_code = reg_res.json()["debug_code"]
    
    verify_res = client.post("/v1/auth/verify-email", json={
        "username": "dub_user",
        "code": otp_code
    })
    assert verify_res.status_code == 200

    login_res = client.post("/v1/auth/login", json={
        "username": "dub_user",
        "password": "password_123"
    })
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]
    user_headers = {"Authorization": f"Bearer {token}"}

    # 2. Submit a video dubbing job (mock file upload)
    import tempfile, uuid
    from app.services.audio_service import AudioService
    dummy_audio_path = os.path.join(tempfile.gettempdir(), f"test_dub_{uuid.uuid4().hex}.wav")
    AudioService.generate_mock_wav(dummy_audio_path, duration=2.0, freq=440.0)
    with open(dummy_audio_path, "rb") as f:
        dummy_video = io.BytesIO(f.read())
    if os.path.exists(dummy_audio_path):
        os.remove(dummy_audio_path)
    files = {"file": ("test.mp4", dummy_video, "video/mp4")}
    data_payload = {"target_language": "English"}

    response = client.post("/v1/video-dubbing", files=files, data=data_payload, headers=user_headers)
    assert response.status_code == 200
    data = response.json()
    job_id = data["id"]
    assert job_id.startswith("vd_")
    assert data["status"] in ["queued", "downloading", "separating_audio", "transcribing", "translating", "awaiting_review"]

    # 3. Wait for background task to finish processing up to "awaiting_review"
    import time
    max_retries = 10
    finished_preparation = False
    for _ in range(max_retries):
        status_res = client.get(f"/v1/video-dubbing/jobs/{job_id}", headers=user_headers)
        assert status_res.status_code == 200
        status_data = status_res.json()
        if status_data["status"] == "awaiting_review":
            finished_preparation = True
            assert len(status_data["original_subtitles"]) > 0
            assert len(status_data["translated_subtitles"]) > 0
            break
        time.sleep(1.0)
    
    assert finished_preparation, f"Failed to reach awaiting_review state. Status: {status_data['status']}"

    # 4. Try updating the translated subtitles
    edited_subs = status_data["translated_subtitles"]
    edited_subs[0]["text"] = "Hello, this is a test segment text."
    
    update_res = client.put(
        f"/v1/video-dubbing/jobs/{job_id}/subtitles",
        headers=user_headers,
        json={
            "translated_subtitles": edited_subs
        }
    )
    assert update_res.status_code == 200
    assert update_res.json()["status"] == "success"

    # 5. Finalize the job
    finalize_res = client.post(f"/v1/video-dubbing/jobs/{job_id}/finalize", headers=user_headers)
    assert finalize_res.status_code == 200
    
    # 6. Wait for finalization completion
    finished_finalization = False
    for _ in range(max_retries):
        status_res = client.get(f"/v1/video-dubbing/jobs/{job_id}", headers=user_headers)
        assert status_res.status_code == 200
        status_data = status_res.json()
        if status_data["status"] == "completed":
            finished_finalization = True
            assert status_data["output_video_url"] is not None
            break
        time.sleep(1.0)

    assert finished_finalization, f"Failed to reach completed state. Status: {status_data['status']}"

    # 7. Try downloading output media files
    video_res = client.get(f"/v1/video-dubbing/jobs/{job_id}/video", headers=user_headers)
    assert video_res.status_code == 200
    
    vocals_res = client.get(f"/v1/video-dubbing/jobs/{job_id}/vocals", headers=user_headers)
    assert vocals_res.status_code == 200
    
    bgm_res = client.get(f"/v1/video-dubbing/jobs/{job_id}/bgm", headers=user_headers)
    assert bgm_res.status_code == 200

    # Ensure vocals and bgm are distinct and not duplicate copies
    import hashlib
    assert hashlib.md5(vocals_res.content).hexdigest() != hashlib.md5(bgm_res.content).hexdigest()
    
    # 8. Test re-separate endpoint
    resep_res = client.post(f"/v1/video-dubbing/jobs/{job_id}/re-separate", headers=user_headers)
    assert resep_res.status_code == 200
    resep_data = resep_res.json()
    assert resep_data["vocals_audio_path"] is not None
    assert resep_data["bgm_audio_path"] is not None
    
    output_res = client.get(f"/v1/video-dubbing/jobs/{job_id}/output", headers=user_headers)
    assert output_res.status_code == 200

    # 9. Test shorten-subtitle endpoint
    shorten_res = client.post(
        f"/v1/video-dubbing/jobs/{job_id}/shorten-subtitle",
        headers=user_headers,
        json={
            "segment_id": 1,
            "text": "Đây là một câu rất dài dòng vượt quá thời lượng cần nói của video này",
            "target_duration": 1.5,
            "target_language": "Vietnamese"
        }
    )
    assert shorten_res.status_code == 200
    shorten_data = shorten_res.json()
    assert shorten_data["segment_id"] == 1
    assert "shortened_text" in shorten_data
    assert len(shorten_data["shortened_text"]) > 0

    # 10. Test fast remix endpoint
    remix_res = client.post(
        f"/v1/video-dubbing/jobs/{job_id}/remix",
        headers=user_headers,
        json={
            "vocals_volume": 1.3,
            "bgm_volume": 0.25
        }
    )
    assert remix_res.status_code == 200
    remix_data = remix_res.json()
    assert remix_data["status"] == "success"
    assert "output_video_url" in remix_data

    # 11. Test re-edit endpoint (unlock completed job back to awaiting_review)
    reedit_res = client.post(f"/v1/video-dubbing/jobs/{job_id}/re-edit", headers=user_headers)
    assert reedit_res.status_code == 200
    reedit_data = reedit_res.json()
    assert reedit_data["status"] == "awaiting_review"
    assert len(reedit_data["translated_subtitles"]) > 0

    client.delete(f"/v1/video-dubbing/jobs/{job_id}", headers=user_headers)


def test_youtube_url_normalization_and_security(monkeypatch):
    import socket
    from app.services.video_dubbing_service import VideoDubbingService

    def _fake_getaddrinfo(host, port, *args, **kwargs):
        if "youtube" in host or "youtu.be" in host or "example.com" in host:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.190.78", port))]
        raise socket.gaierror("unknown host")

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)

    valid_urls = [
        "https://www.youtube.com/watch?v=_HA6-A8itVY&list=PL12345&index=3",
        "https://youtu.be/_HA6-A8itVY?si=abcd",
        "https://www.youtube.com/shorts/_HA6-A8itVY?feature=share",
        "https://www.youtube.com/live/_HA6-A8itVY",
        "https://m.youtube.com/watch?v=_HA6-A8itVY",
        "https://music.youtube.com/watch?v=_HA6-A8itVY",
    ]
    for u in valid_urls:
        vid, canonical = VideoDubbingService.normalize_youtube_url(u)
        assert vid == "_HA6-A8itVY"
        assert canonical == "https://www.youtube.com/watch?v=_HA6-A8itVY"

    for invalid_url in [
        "https://example.com/watch?v=_HA6-A8itVY",
        "http://127.0.0.1:7860/watch?v=_HA6-A8itVY",
        "https://www.youtube.com/watch?v=invalid",
    ]:
        with pytest.raises(ValueError):
            VideoDubbingService.normalize_youtube_url(invalid_url)


def test_proxy_webshare_format_and_storage_isolation(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.video_dubbing_service import VideoDubbingService

    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path))

    assert (
        VideoDubbingService._normalize_single_proxy("198.23.239.134:6540:myuser:mypass")
        == "http://myuser:mypass@198.23.239.134:6540"
    )

    saved = VideoDubbingService.save_cookies_content("SID=abc123; SAPISID=xyz789")
    assert saved["path"] == os.path.join(str(tmp_path), "cookies.txt")
    assert os.path.exists(saved["path"])

    status_info = VideoDubbingService.inspect_cookie_status()
    assert status_info["has_cookies"] is True
    assert status_info["is_authenticated"] is True
    assert status_info["cookie_count"] == 2

    VideoDubbingService.delete_cookies_all()
    assert VideoDubbingService.inspect_cookie_status()["has_cookies"] is False


def test_youtube_info_predownload_skip_redownload_and_retry_flow(tmp_path, monkeypatch):
    import secrets
    import socket
    import time
    from app.config import settings
    from app.database import SessionLocal
    from app.models import User
    from app.services.audio_service import AudioService
    from app.services.video_dubbing_service import VideoDubbingService
    from app.utils.auth import create_access_token, get_password_hash

    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path))

    def _fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.190.78", port))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "yt_test_user").first()
        if not user:
            user = User(
                id="usr_yt_test",
                username="yt_test_user",
                email="yt_test_user@example.com",
                hashed_password=get_password_hash("TestPass123!"),
                is_verified=True,
                is_approved=True,
                is_admin=False,
                api_key=f"ovg_live_{secrets.token_hex(16)}",
            )
            db.add(user)

        admin = db.query(User).filter(User.username == "yt_test_admin").first()
        if not admin:
            admin = User(
                id="usr_yt_admin",
                username="yt_test_admin",
                email="yt_test_admin@example.com",
                hashed_password=get_password_hash("TestPass123!"),
                is_verified=True,
                is_approved=True,
                is_admin=True,
                api_key=f"ovg_live_{secrets.token_hex(16)}",
            )
            db.add(admin)
        db.commit()
    finally:
        db.close()

    user_headers = {"Authorization": f"Bearer {create_access_token(data={'sub': 'yt_test_user'})}"}
    admin_headers = {"Authorization": f"Bearer {create_access_token(data={'sub': 'yt_test_admin'})}"}

    def _fake_get_info(url: str):
        vid, canonical = VideoDubbingService.normalize_youtube_url(url)
        return {
            "video_id": vid,
            "canonical_url": canonical,
            "title": "Mock YT Title",
            "channel": "Mock Channel",
            "duration": 2.0,
            "thumbnail_url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            "available_qualities": ["1080p", "720p", "480p", "360p"],
            "is_live": False,
        }

    monkeypatch.setattr(VideoDubbingService, "get_youtube_video_info", staticmethod(_fake_get_info))

    # RBAC check on /test-youtube
    assert client.post("/v1/video-dubbing/test-youtube", headers=user_headers).status_code == 403
    admin_test_res = client.post("/v1/video-dubbing/test-youtube", headers=admin_headers)
    assert admin_test_res.status_code == 200
    assert admin_test_res.json()["status"] == "success"
    assert admin_test_res.json()["title"] == "Mock YT Title"

    # Info check
    info_ok = client.post(
        "/v1/video-dubbing/youtube/info",
        json={"url": "https://youtu.be/_HA6-A8itVY?si=test"},
        headers=user_headers,
    )
    assert info_ok.status_code == 200
    assert info_ok.json()["video_id"] == "_HA6-A8itVY"
    assert info_ok.json()["title"] == "Mock YT Title"

    info_bad = client.post(
        "/v1/video-dubbing/youtube/info",
        json={"url": "https://example.com/watch?v=_HA6-A8itVY"},
        headers=user_headers,
    )
    assert info_bad.status_code == 400

    # Mock download_youtube_video with progress callback & controllable failure
    state = {"call_count": 0, "fail_next": False}

    def _fake_download(
        url,
        output_dir,
        log_file=None,
        preferred_quality="1080p",
        progress_callback=None,
        metadata_out=None,
    ):
        state["call_count"] += 1
        if state["fail_next"]:
            state["fail_next"] = False
            raise RuntimeError("Simulated YouTube bot block")

        os.makedirs(output_dir, exist_ok=True)
        if progress_callback:
            progress_callback(50.0, "Downloading 50%")
        target_path = os.path.join(output_dir, "input_video.mp4")
        AudioService.generate_mock_wav(target_path, duration=2.0, freq=440.0)
        if metadata_out is not None:
            metadata_out["title"] = "Mock YT Title"
            metadata_out["duration"] = 2.0
            metadata_out["thumbnail"] = "https://i.ytimg.com/vi/_HA6-A8itVY/hqdefault.jpg"
        return target_path, "Mock YT Title"

    monkeypatch.setattr(VideoDubbingService, "download_youtube_video", staticmethod(_fake_download))

    # 1. Pre-download YouTube video
    dl_res = client.post(
        "/v1/video-dubbing/youtube/download",
        json={
            "url": "https://www.youtube.com/watch?v=_HA6-A8itVY&list=PL999",
            "preferred_quality": "720p",
            "target_language": "Vietnamese",
        },
        headers=user_headers,
    )
    assert dl_res.status_code == 200, dl_res.text
    predl_id = dl_res.json()["id"]

    predl_data = None
    for _ in range(20):
        st = client.get(f"/v1/video-dubbing/jobs/{predl_id}", headers=user_headers)
        assert st.status_code == 200
        predl_data = st.json()
        if predl_data["status"] == "uploaded":
            break
        time.sleep(0.25)

    assert predl_data is not None and predl_data["status"] == "uploaded"
    assert state["call_count"] == 1
    assert predl_data["has_source_video"] is True
    assert predl_data["video_title"] == "Mock YT Title"
    assert predl_data["preferred_quality"] == "720p"

    # 2. Download source video with attachment disposition
    vid_dl_res = client.get(f"/v1/video-dubbing/jobs/{predl_id}/video?download=1", headers=user_headers)
    assert vid_dl_res.status_code == 200
    assert "attachment" in vid_dl_res.headers.get("content-disposition", "").lower()

    # 3. Start dubbing from pre-downloaded uploaded_job_id (must NOT re-download)
    start_res = client.post(
        "/v1/video-dubbing",
        data={"uploaded_job_id": predl_id, "target_language": "Vietnamese"},
        headers=user_headers,
    )
    assert start_res.status_code == 200, start_res.text

    review_data = None
    for _ in range(30):
        st = client.get(f"/v1/video-dubbing/jobs/{predl_id}", headers=user_headers)
        review_data = st.json()
        if review_data["status"] == "awaiting_review":
            break
        time.sleep(0.25)

    assert review_data is not None and review_data["status"] == "awaiting_review"
    assert state["call_count"] == 1

    # 4. Direct YouTube job fails -> retry succeeds with updated preferred_quality
    state["fail_next"] = True
    fail_res = client.post(
        "/v1/video-dubbing",
        data={
            "youtube_url": "https://www.youtube.com/shorts/_HA6-A8itVY",
            "target_language": "Vietnamese",
            "preferred_quality": "1080p",
        },
        headers=user_headers,
    )
    assert fail_res.status_code == 200, fail_res.text
    job2_id = fail_res.json()["id"]

    job2_data = None
    for _ in range(20):
        st = client.get(f"/v1/video-dubbing/jobs/{job2_id}", headers=user_headers)
        job2_data = st.json()
        if job2_data["status"] == "failed":
            break
        time.sleep(0.25)

    assert job2_data is not None and job2_data["status"] == "failed"
    assert "Simulated YouTube bot block" in (job2_data.get("error_message") or "")

    retry_res = client.post(
        f"/v1/video-dubbing/jobs/{job2_id}/retry",
        json={"preferred_quality": "480p", "download_only": False},
        headers=user_headers,
    )
    assert retry_res.status_code == 200, retry_res.text

    for _ in range(30):
        st = client.get(f"/v1/video-dubbing/jobs/{job2_id}", headers=user_headers)
        job2_data = st.json()
        if job2_data["status"] == "awaiting_review":
            break
        time.sleep(0.25)

    assert job2_data is not None and job2_data["status"] == "awaiting_review"
    assert job2_data["preferred_quality"] == "480p"
    assert job2_data["video_title"] == "Mock YT Title"

    client.delete(f"/v1/video-dubbing/jobs/{predl_id}", headers=user_headers)
    client.delete(f"/v1/video-dubbing/jobs/{job2_id}", headers=user_headers)


def test_unauthenticated_cookies_ignored_and_player_client_selection(tmp_path, monkeypatch):
    import socket
    import yt_dlp
    from app.config import settings
    from app.services.video_dubbing_service import VideoDubbingService

    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(VideoDubbingService, "get_oauth_token", staticmethod(lambda: None))
    monkeypatch.setattr(VideoDubbingService, "get_youtube_proxies", staticmethod(lambda: []))
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.190.78", port))],
    )
    monkeypatch.setattr(
        VideoDubbingService,
        "_probe_media_streams",
        staticmethod(lambda fp: {"probed": True, "has_audio": True, "has_video": True}),
    )

    captured_calls = []

    class _DummyYoutubeDL:
        def __init__(self, opts):
            self.opts = opts
            captured_calls.append(dict(opts))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=True):
            outtmpl = self.opts["outtmpl"]
            target_file = outtmpl.replace("%(ext)s", "mp4")
            os.makedirs(os.path.dirname(target_file), exist_ok=True)
            with open(target_file, "wb") as f:
                f.write(b"\x00" * 4096)
            return {
                "title": "Client Check",
                "duration": 12.5,
                "thumbnail": "https://i.ytimg.com/vi/_HA6-A8itVY/hqdefault.jpg",
                "channel": "CheckChannel",
            }

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _DummyYoutubeDL)

    try:
        # 1. Visitor-only cookies must NOT be passed to yt-dlp and must use safe unauthenticated clients
        VideoDubbingService.save_cookies_content("PREF=hl=en; SOCS=CAI; VISITOR_INFO1_LIVE=abc")
        status_anon = VideoDubbingService.inspect_cookie_status()
        assert status_anon["has_cookies"] is True
        assert status_anon["is_authenticated"] is False

        out_dir_1 = os.path.join(str(tmp_path), "job_anon")
        path1, title1 = VideoDubbingService.download_youtube_video(
            "https://youtu.be/_HA6-A8itVY",
            out_dir_1,
            preferred_quality="720p",
        )
        assert os.path.exists(path1)
        assert title1 == "Client Check"
        opts_anon = captured_calls[-1]
        assert "cookiefile" not in opts_anon
        assert opts_anon["extractor_args"]["youtube"]["player_client"] == [
            "web_embedded",
            "tv_simply",
            "mweb",
            "android",
        ]

        # 2. Authenticated cookies MUST be isolated & passed without forcing unauthenticated client override
        VideoDubbingService.save_cookies_content("SID=sec123; SAPISID=auth456")
        status_auth = VideoDubbingService.inspect_cookie_status()
        assert status_auth["has_cookies"] is True
        assert status_auth["is_authenticated"] is True

        out_dir_2 = os.path.join(str(tmp_path), "job_auth")
        path2, _ = VideoDubbingService.download_youtube_video(
            "https://youtu.be/_HA6-A8itVY",
            out_dir_2,
            preferred_quality="720p",
        )
        assert os.path.exists(path2)
        opts_auth = captured_calls[-1]
        assert "cookiefile" in opts_auth
        assert opts_auth["cookiefile"] == os.path.join(out_dir_2, "_cookies_work.txt")
        assert "player_client" not in opts_auth.get("extractor_args", {}).get("youtube", {})
        assert not os.path.exists(os.path.join(out_dir_2, "_cookies_work.txt"))
    finally:
        VideoDubbingService.delete_cookies_all()


def test_remote_bridge_r2_cache_and_anti_recursion(tmp_path, monkeypatch):
    import socket
    import requests
    from app.config import settings
    from app.services import r2_bridge_client as r2_mod
    from app.services.r2_bridge_client import R2GatewayClient, RemoteYouTubeBridgeClient, _validate_r2_key
    from app.services.video_dubbing_service import VideoDubbingService

    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "YT_BRIDGE_URL", "https://yt-bridge.example.test")
    monkeypatch.setattr(settings, "YT_R2_GATEWAY_URL", "https://yt-r2.example.test")
    monkeypatch.setattr(settings, "YT_BRIDGE_SECRET", "unit_test_secret_hex")
    monkeypatch.setattr(settings, "CF_ACCESS_CLIENT_ID", "unit_cf_id")
    monkeypatch.setattr(settings, "CF_ACCESS_CLIENT_SECRET", "unit_cf_sec")
    monkeypatch.delenv("YT_BRIDGE_SERVER_MODE", raising=False)

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.190.78", port))],
    )
    monkeypatch.setattr(
        VideoDubbingService,
        "_probe_media_streams",
        staticmethod(lambda fp: {"probed": True, "has_audio": True, "has_video": True}),
    )

    # 1. R2 key path validation
    assert _validate_r2_key("yt/_HA6-A8itVY_720p.mp4") == "yt/_HA6-A8itVY_720p.mp4"
    with pytest.raises(ValueError):
        _validate_r2_key("yt/../bad.mp4")
    with pytest.raises(ValueError):
        _validate_r2_key("other/file.mp4")

    # 2. Anti-recursion guard when YT_BRIDGE_SERVER_MODE=1
    assert RemoteYouTubeBridgeClient.is_enabled() is True
    monkeypatch.setenv("YT_BRIDGE_SERVER_MODE", "1")
    assert RemoteYouTubeBridgeClient.is_enabled() is False
    monkeypatch.delenv("YT_BRIDGE_SERVER_MODE", raising=False)
    assert RemoteYouTubeBridgeClient.is_enabled() is True

    # 3. Cloudflare Access 302 login redirect must raise PermissionError (allow_redirects=False)
    class _FakeResp:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = ""

        def json(self):
            return self._payload

    def _redirect_req(method, url, **kwargs):
        assert kwargs.get("allow_redirects") is False
        return _FakeResp(302)

    monkeypatch.setattr(requests, "request", _redirect_req)
    with pytest.raises(PermissionError):
        RemoteYouTubeBridgeClient._request("GET", "/v1/health")

    # 4. R2 24h Cache Hit skips POST /v1/jobs completely
    bridge_calls = []

    def _fail_if_called(method, url, **kwargs):
        bridge_calls.append((method, url))
        raise AssertionError(f"Unexpected bridge call on cache hit: {method} {url}")

    monkeypatch.setattr(requests, "request", _fail_if_called)
    monkeypatch.setattr(R2GatewayClient, "head_object", lambda self, key: {"key": key, "size": 4096})
    monkeypatch.setattr(
        R2GatewayClient,
        "get_json",
        lambda self, key: {"title": "R2 Cached Video", "duration": 99.0, "channel": "CachedCh"},
    )

    def _fake_r2_download(self, key, dest_path, progress_callback=None):
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(b"\x00" * 4096)
        return dest_path

    monkeypatch.setattr(R2GatewayClient, "download_object", _fake_r2_download)

    out_cache = os.path.join(str(tmp_path), "cache_hit")
    meta_cache = {}
    p_cache, t_cache = VideoDubbingService.download_youtube_video(
        "https://www.youtube.com/watch?v=_HA6-A8itVY",
        out_cache,
        preferred_quality="720p",
        metadata_out=meta_cache,
    )
    assert os.path.exists(p_cache)
    assert t_cache == "R2 Cached Video"
    assert meta_cache.get("duration") == 99.0
    assert bridge_calls == []

    # 5. Async 202 Tunnel job polling -> downloads from R2 & populates metadata_out
    monkeypatch.setattr(R2GatewayClient, "head_object", lambda self, key: None)
    monkeypatch.setattr(r2_mod.time, "sleep", lambda s: None)

    poll_state = {"count": 0}

    def _mock_bridge_http(method, url, **kwargs):
        assert kwargs.get("allow_redirects") is False
        headers = kwargs.get("headers") or {}
        assert headers.get("CF-Access-Client-Id") == "unit_cf_id"
        assert headers.get("CF-Access-Client-Secret") == "unit_cf_sec"
        assert headers.get("X-Bridge-Secret") == "unit_test_secret_hex"
        if method == "POST" and url.endswith("/v1/jobs"):
            return _FakeResp(202, {"task_id": "tsk_async123", "status": "queued"})
        if method == "GET" and url.endswith("/v1/jobs/tsk_async123"):
            poll_state["count"] += 1
            if poll_state["count"] == 1:
                return _FakeResp(200, {"task_id": "tsk_async123", "status": "downloading", "progress": 42, "message": "Downloading 42%"})
            return _FakeResp(
                200,
                {
                    "task_id": "tsk_async123",
                    "status": "completed",
                    "progress": 100,
                    "r2_key": "yt/_HA6-A8itVY_720p.mp4",
                    "title": "Async Tunnel Video",
                    "metadata": {
                        "title": "Async Tunnel Video",
                        "duration": 163.0,
                        "channel": "BridgeCh",
                        "thumbnail": "https://i.ytimg.com/vi/_HA6-A8itVY/hqdefault.jpg",
                    },
                },
            )
        raise AssertionError(f"Unexpected call: {method} {url}")

    monkeypatch.setattr(requests, "request", _mock_bridge_http)

    out_async = os.path.join(str(tmp_path), "async_bridge")
    meta_async = {}
    p_async, t_async = VideoDubbingService.download_youtube_video(
        "https://www.youtube.com/watch?v=_HA6-A8itVY",
        out_async,
        preferred_quality="720p",
        metadata_out=meta_async,
    )
    assert os.path.exists(p_async)
    assert t_async == "Async Tunnel Video"
    assert meta_async.get("duration") == 163.0
    assert meta_async.get("channel") == "BridgeCh"
    assert poll_state["count"] == 2


