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


def test_dubbing_output_download_preview_stems_disk_reconcile_and_history_playback(tmp_path, monkeypatch):
    import secrets
    from app.config import settings
    from app.database import SessionLocal
    from app.models import TTSJob, User, VideoDubbingJob
    from app.services.audio_service import AudioService
    from app.utils.auth import create_access_token, get_password_hash

    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path))

    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "dub_reg_admin").first()
        if not admin:
            admin = User(
                id="usr_dub_reg_admin",
                username="dub_reg_admin",
                email="dub_reg_admin@example.com",
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

    admin_headers = {"Authorization": f"Bearer {create_access_token(data={'sub': 'dub_reg_admin'})}"}

    # 1. Create an orphaned vd_* folder on disk (no DB row yet) + legacy sep_/dub_ rows pointing to deleted .zip
    orphan_id = "vd_a1b2c3d4"
    job_dir = os.path.join(settings.dubbing_dir, orphan_id)
    os.makedirs(job_dir, exist_ok=True)
    vocals_wav = os.path.join(job_dir, "vocals.wav")
    bgm_wav = os.path.join(job_dir, "bgm.wav")
    dubbed_wav = os.path.join(job_dir, "dubbed_vocals.wav")
    output_mp4 = os.path.join(job_dir, "output_dubbed.mp4")

    AudioService.generate_mock_wav(vocals_wav, duration=1.0, freq=440.0)
    AudioService.generate_mock_wav(bgm_wav, duration=1.0, freq=220.0)
    AudioService.generate_mock_wav(dubbed_wav, duration=1.0, freq=520.0)
    with open(output_mp4, "wb") as f:
        f.write(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096)
    with open(os.path.join(job_dir, "process.log"), "w", encoding="utf-8") as lf:
        lf.write("[2026-11-18 06:21:09] Tải video thành công: 'Recovered Dental Video'.\n")

    db = SessionLocal()
    try:
        db.add(
            TTSJob(
                id=f"dub_{orphan_id}",
                user_id="usr_dub_reg_admin",
                job_type="dub_segments",
                text=json.dumps([{"id": 1, "start": 0.0, "end": 1.0, "text": "Xin chào các bạn"}]),
                status="completed",
                progress=100,
                output_audio_path=os.path.join(job_dir, f"dub_{orphan_id}.zip"),  # deleted zip!
                cdn_audio_url="https://broken-cdn.example/dub_zip_disguised.wav",
            )
        )
        db.commit()
    finally:
        db.close()

    # 2. Admin listing automatically reconstructs the missing VideoDubbingJob from disk
    list_res = client.get("/v1/video-dubbing/jobs", headers=admin_headers)
    assert list_res.status_code == 200, list_res.text
    found = [j for j in list_res.json()["jobs"] if j["id"] == orphan_id]
    assert len(found) == 1
    assert found[0]["status"] == "completed"
    assert found[0]["video_title"] == "Recovered Dental Video"

    # 3. Malformed subtitle entries or null timestamps must never crash GET /jobs/{id}
    db = SessionLocal()
    try:
        vd_row = db.query(VideoDubbingJob).filter(VideoDubbingJob.id == orphan_id).first()
        vd_row.translated_subtitles = json.dumps([{"id": "1", "start": None, "end": "2.5", "text": None}, "bad_item"])
        vd_row.bgm_volume = 0.0
        db.commit()
    finally:
        db.close()

    detail_res = client.get(f"/v1/video-dubbing/jobs/{orphan_id}", headers=admin_headers)
    assert detail_res.status_code == 200, detail_res.text
    detail_json = detail_res.json()
    assert detail_json["bgm_volume"] == 0.0
    assert len(detail_json["translated_subtitles"]) == 1
    assert detail_json["translated_subtitles"][0]["text"] == ""

    # 4. Output video supports both inline playback and attachment download
    out_inline = client.get(f"/v1/video-dubbing/jobs/{orphan_id}/output", headers=admin_headers)
    assert out_inline.status_code == 200
    assert "inline" in out_inline.headers.get("content-disposition", "").lower()
    assert out_inline.headers.get("accept-ranges") == "bytes"

    out_dl = client.get(f"/v1/video-dubbing/jobs/{orphan_id}/output?download=1", headers=admin_headers)
    assert out_dl.status_code == 200
    assert "attachment" in out_dl.headers.get("content-disposition", "").lower()

    # 5. Preview stem endpoint returns valid audio (compressed M4A when ffmpeg present, WAV fallback otherwise)
    voc_prev = client.get(f"/v1/video-dubbing/jobs/{orphan_id}/vocals?preview=1", headers=admin_headers)
    assert voc_prev.status_code == 200
    assert len(voc_prev.content) > 100

    # 6. History -> Video Dubbing tab returns clean project item & working audio playback (self-healed!)
    hist_res = client.get("/v1/jobs?page=1&page_size=20&job_type=video_dubbing", headers=admin_headers)
    assert hist_res.status_code == 200, hist_res.text
    hist_items = hist_res.json()["items"]
    vd_hist = [it for it in hist_items if it["job_id"] == orphan_id]
    assert len(vd_hist) == 1
    assert vd_hist[0]["cdn_audio_url"] is None
    assert vd_hist[0]["audio_url"] == f"/v1/video-dubbing/jobs/{orphan_id}/dubbed-audio"
    assert vd_hist[0]["params"]["has_output_video"] is True

    dub_audio_res = client.get(vd_hist[0]["audio_url"], headers=admin_headers)
    assert dub_audio_res.status_code == 200
    assert len(dub_audio_res.content) > 500

    # Legacy /v1/tts/jobs/dub_<id>/audio also self-heals from deleted .zip to dubbed_vocals.wav
    legacy_tts_audio = client.get(f"/v1/tts/jobs/dub_{orphan_id}/audio", headers=admin_headers)
    assert legacy_tts_audio.status_code == 200
    assert len(legacy_tts_audio.content) > 500


def test_asr_hallucination_filtering_and_sentence_boundary_preservation():
    from app.services.video_dubbing_service import VideoDubbingService

    # Exact reproduction of Screenshot 2:
    # Segment #20 has "MIS. Make it simple." merged with a mixed-script Whisper hallucination ("δiem")
    # Segment #21 has a retrograde overlapping timestamp (204.38s -> 205.28s after 202.70s -> 204.82s)
    contaminated_subs = [
        {
            "id": 19,
            "start": 195.0,
            "end": 201.5,
            "text": "Tighten the prosthetic screw to the recommended torque.",
        },
        {
            "id": 20,
            "start": 202.70,
            "end": 204.82,
            "text": "MIS. Make it simple. If you want to do the assembly, use the orthopedic tool to δiem the implant, use the left to give the implant.",
        },
        {
            "id": 21,
            "start": 204.38,
            "end": 205.28,
            "text": "If you want to double -dial it, it's just a little bit too small.",
        },
    ]

    cleaned = VideoDubbingService.merge_and_normalize_subtitles(contaminated_subs)
    assert len(cleaned) == 2
    assert cleaned[0]["text"] == "Tighten the prosthetic screw to the recommended torque."
    assert cleaned[1]["text"] == "MIS. Make it simple."
    assert cleaned[1]["start"] == 202.70
    assert cleaned[1]["end"] == 204.82

    # Also test build_clean_asr_segments from word alignments with low-prob / retrograde tail words
    words = [
        {"word": "MIS.", "start": 202.70, "end": 203.20, "prob": 0.96, "seg_end": True},
        {"word": "Make", "start": 203.35, "end": 203.70, "prob": 0.98},
        {"word": "it", "start": 203.70, "end": 203.95, "prob": 0.99},
        {"word": "simple.", "start": 203.95, "end": 204.50, "prob": 0.97, "seg_end": True},
        # Hallucinated outro music words with low probability and script corruption
        {"word": "If", "start": 204.60, "end": 204.65, "prob": 0.25},
        {"word": "δiem", "start": 204.65, "end": 204.82, "prob": 0.12},
        # Retrograde timestamp jump
        {"word": "double", "start": 204.38, "end": 205.28, "prob": 0.30, "seg_end": True},
    ]
    built = VideoDubbingService.build_clean_asr_segments(words, "MIS. Make it simple.", total_duration=213.0)
    assert len(built) == 1
    assert built[0]["text"] == "MIS. Make it simple."

    # Verify 1-to-1 translated subtitle normalization never merges completed short Vietnamese sentences
    trans_in = [
        {"id": 1, "start": 195.0, "end": 201.5, "text": "Siết vít phục hình theo lực khuyến nghị."},
        {"id": 2, "start": 202.70, "end": 204.82, "text": "MIS. Giữ mọi việc đơn giản."},
    ]
    norm_trans = VideoDubbingService.normalize_translated_subtitles(trans_in, cleaned)
    assert len(norm_trans) == len(cleaned) == 2
    assert norm_trans[1]["text"] == "MIS. Giữ mọi việc đơn giản."


def test_wsola_atempo_stretch_without_librosa_and_stereo_bgm_preservation(tmp_path, monkeypatch):
    import sys
    import subprocess
    import numpy as np
    import soundfile as sf
    from app.services.video_dubbing_service import VideoDubbingService

    # Simulate production container where librosa is NOT installed
    monkeypatch.setitem(sys.modules, "librosa", None)

    sr24 = 24000
    seg1_wav = str(tmp_path / "seg1.wav")
    seg2_wav = str(tmp_path / "seg2.wav")
    dubbed_vocal_wav = str(tmp_path / "dubbed_vocals.wav")

    # Segment 1: 3.0s total -> 2.5s @ 220Hz followed by distinct 0.5s @ 1760Hz tail
    t_body = np.linspace(0, 2.5, int(sr24 * 2.5), endpoint=False, dtype=np.float32)
    t_tail = np.linspace(0, 0.5, int(sr24 * 0.5), endpoint=False, dtype=np.float32)
    seg1_arr = np.concatenate([
        0.35 * np.sin(2 * np.pi * 220 * t_body),
        0.45 * np.sin(2 * np.pi * 1760 * t_tail),
    ])
    sf.write(seg1_wav, seg1_arr, sr24, format="WAV", subtype="PCM_16")

    # Segment 2: 1.0s @ 330Hz
    t_seg2 = np.linspace(0, 1.0, sr24, endpoint=False, dtype=np.float32)
    sf.write(seg2_wav, 0.35 * np.sin(2 * np.pi * 330 * t_seg2), sr24, format="WAV", subtype="PCM_16")

    segments = [
        {"start": 0.0, "end": 2.0, "file_path": seg1_wav},
        {"start": 2.1, "end": 3.1, "file_path": seg2_wav},
    ]
    VideoDubbingService.assemble_dubbed_vocal(segments, dubbed_vocal_wav, total_duration=5.0)

    out_vox, out_sr = sf.read(dubbed_vocal_wav, dtype="float32")
    assert out_sr == 24000

    # Verify the stretched 1760Hz tail arrived before 2.04s instead of being truncated at 2.0s!
    pre_gap_slice = out_vox[int(1.65 * sr24):int(2.00 * sr24)]
    pre_gap_rms = float(np.sqrt(np.mean(np.square(pre_gap_slice))))
    assert pre_gap_rms > 0.05
    # Spectral energy around 1760Hz must be strong in 1.65s..2.00s (stretched from 2.5s..3.0s at ~1.5x)
    freqs = np.fft.rfftfreq(len(pre_gap_slice), 1.0 / sr24)
    mag = np.abs(np.fft.rfft(pre_gap_slice))
    peak_freq = float(freqs[int(np.argmax(mag))])
    assert 1500.0 <= peak_freq <= 2000.0, f"Expected stretched 1760Hz tail, got peak {peak_freq}Hz"

    # Breathing gap 2.05s..2.09s before Segment 2 starts at 2.1s must stay silent
    gap_slice = out_vox[int(2.05 * sr24):int(2.09 * sr24)]
    gap_rms = float(np.sqrt(np.mean(np.square(gap_slice))))
    assert gap_rms < 0.01, f"Expected silent breathing gap, got rms={gap_rms}"

    # Create a 5.0s H.264 MP4 and a 5.0s 44.1kHz Stereo BGM with distinct L (440Hz) and R (660Hz) channels
    src_mp4 = str(tmp_path / "src.mp4")
    bgm_wav = str(tmp_path / "bgm.wav")
    out_mp4 = str(tmp_path / "output_dubbed.mp4")

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=320x240:r=24:d=5",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            src_mp4,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    sr44 = 44100
    t_bgm = np.linspace(0, 5.0, sr44 * 5, endpoint=False, dtype=np.float32)
    bgm_stereo = np.stack([
        0.30 * np.sin(2 * np.pi * 440 * t_bgm),
        0.30 * np.sin(2 * np.pi * 660 * t_bgm),
    ], axis=1)
    sf.write(bgm_wav, bgm_stereo, sr44, format="WAV", subtype="PCM_16")

    VideoDubbingService.mix_and_mux_video(
        video_path=src_mp4,
        bgm_path=bgm_wav,
        vocal_path=dubbed_vocal_wav,
        output_path=out_mp4,
        vocal_vol=1.0,
        bgm_vol=0.4,
    )

    probe_res = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,sample_rate,channels,duration",
            "-of", "json",
            out_mp4,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    streams = json.loads(probe_res.stdout).get("streams", [])
    assert len(streams) == 1
    a_stream = streams[0]
    assert int(a_stream["channels"]) == 2, f"Expected 2-channel stereo, got {a_stream}"
    assert str(a_stream["sample_rate"]) == "44100", f"Expected 44100Hz, got {a_stream}"
    assert float(a_stream["duration"]) >= 4.8, f"Expected full >=4.8s duration, got {a_stream}"

    # Verify stereo L/R difference is preserved in output outro (3.5s..4.8s)
    extracted_wav = str(tmp_path / "extracted_out.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-i", out_mp4, "-vn", "-acodec", "pcm_s16le", extracted_wav],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    mix_arr, mix_sr = sf.read(extracted_wav, dtype="float32")
    assert mix_arr.ndim == 2 and mix_arr.shape[1] == 2
    outro_slice = mix_arr[int(3.5 * mix_sr):int(4.8 * mix_sr)]
    side_rms = float(np.sqrt(np.mean(np.square(outro_slice[:, 0] - outro_slice[:, 1]))))
    assert side_rms > 0.02, f"Expected preserved stereo L-R difference in BGM outro, got side_rms={side_rms}"


def test_cohesive_asr_clause_segmentation_orphan_reattachment_and_nonblocking_worker_upload():
    """
    Verifies Phase-2 live-audit fixes:
    1. Mid-phrase 20-word sentences ('...in the proper position on the organizer tray.') are never chopped after 'proper' or 'the'.
    2. Whisper chunk 'seg_end' without an acoustic pause does not chop infinitive/conjunction tails ('...packaging and place it into the implant.').
    3. Orphan 1-word clause starter ('After') pulled back by Whisper DTW across a 3.7s silence is re-attached to the post-pause speech onset.
    4. Subword spacing ('4 .2 mm', 'MIS -C1XD', 'single -use') and double punctuation ('implant,.') are cleaned in both EN and translated VI.
    5. internal_worker.upload_job_output runs in FastAPI's threadpool (regular def, not async def) so FFmpeg preview/mux never blocks the event loop.
    """
    import inspect
    from app.routers.internal_worker import upload_job_output
    from app.services.video_dubbing_service import VideoDubbingService

    assert not inspect.iscoroutinefunction(upload_job_output), (
        "upload_job_output must be a sync threadpool handler (def), not async def, so FFmpeg I/O never freezes Uvicorn's event loop"
    )

    words_sentence_1 = [
        "This", "video", "will", "demonstrate", "the", "placement", "of", "an", "MIS", "-C1XD", "implant,",
        "using", "an", "implant", "with", "a", "diameter", "of", "4", ".2", "mm", "and", "a", "length", "of", "13", "mm.",
    ]
    words_sentence_2 = [
        "Begin", "by", "opening", "the", "implant's", "package", "and", "placing", "the", "implant's",
        "inner", "tube", "in", "the", "proper", "position", "on", "the", "organizer", "tray.",
    ]
    words_sentence_3 = [
        "The", "recommended", "torque", "is", "35", "to", "60", "Nm.",
    ]
    # Simulate Whisper DTW pulling 'After' back to 171.21-172.09 before 3.73s silence, followed by continuous speech with mid-utterance seg_end
    words_after_pause = [
        ("After", 171.21, 172.09, False),
        ("placing", 175.82, 176.25, False),
        ("the", 176.25, 176.40, False),
        ("implant,", 176.40, 176.95, False),
        ("use", 177.05, 177.30, False),
        ("the", 177.30, 177.45, False),
        ("single", 177.45, 177.80, False),
        ("-use", 177.80, 178.05, False),
        ("tool", 178.05, 178.35, False),
        ("to", 178.35, 178.50, False),
        ("remove", 178.50, 178.90, False),
        ("the", 178.90, 179.05, False),
        ("cover", 179.05, 179.45, False),
        ("screw", 179.45, 179.85, False),
        ("from", 179.85, 180.05, False),
        ("the", 180.05, 180.15, False),
        ("packaging", 180.15, 180.40, True),  # Whisper internal chunk boundary with 0.00s gap!
        ("and", 180.40, 180.55, False),
        ("place", 180.55, 180.85, False),
        ("it", 180.85, 181.00, False),
        ("into", 181.00, 181.25, False),
        ("the", 181.25, 181.40, False),
        ("implant.", 181.40, 181.94, True),
    ]

    alignment = []
    t_cursor = 3.03
    for w in words_sentence_1:
        dur = 0.35
        alignment.append({"word": w, "start": round(t_cursor, 2), "end": round(t_cursor + dur, 2), "prob": 0.96, "seg_end": w.endswith(".")})
        t_cursor += dur
    t_cursor = 14.59
    for w in words_sentence_2:
        dur = 0.34
        alignment.append({"word": w, "start": round(t_cursor, 2), "end": round(t_cursor + dur, 2), "prob": 0.95, "seg_end": w.endswith(".")})
        t_cursor += dur
    t_cursor = 166.49
    for w in words_sentence_3:
        dur = 0.59
        alignment.append({"word": w, "start": round(t_cursor, 2), "end": round(t_cursor + dur, 2), "prob": 0.97, "seg_end": w.endswith(".")})
        t_cursor += dur
    for w, st, et, se in words_after_pause:
        alignment.append({"word": w, "start": st, "end": et, "prob": 0.94, "seg_end": se})

    segs = VideoDubbingService.build_clean_asr_segments(alignment, raw_text="", total_duration=213.0)
    texts = [s["text"] for s in segs]

    # 1. No ',.' double punctuation and subword spacing cleaned
    assert all(",." not in t and "4 .2" not in t and " -use" not in t and " -C1XD" not in t for t in texts), texts
    # 2. First sentence splits cleanly at comma into 2 balanced clauses, with clean 'MIS-C1XD' and '4.2 mm'
    assert texts[0] == "This video will demonstrate the placement of an MIS-C1XD implant."
    assert texts[1] == "Using an implant with a diameter of 4.2 mm and a length of 13 mm."
    # 3. 20-word sentence stays intact (not chopped after 'proper')
    assert texts[2] == "Begin by opening the implant's package and placing the implant's inner tube in the proper position on the organizer tray."
    # 4. Segment 1 start is preserved at 3.03s (no legacy 8.5s jump)
    assert segs[0]["start"] == 3.03
    # 5. Orphan 'After' is re-attached to 'placing the implant...' with onset anchored at 175.47s (not a standalone 'After.' segment)
    assert not any(t.lower() in ("after.", "after") for t in texts), texts
    assert texts[4] == "After placing the implant, use the single-use tool to remove the cover screw from the packaging and place it into the implant."
    assert abs(segs[4]["start"] - 175.47) < 0.05

    # 6. normalize_translated_subtitles also strips trailing ',.' in Vietnamese LLM output
    vi_norm = VideoDubbingService.normalize_translated_subtitles(
        [{"text": "Mỗi mũi khoan được đóng gói vô trùng, mã màu để dễ nhận biết,"}],
        [{"id": 1, "start": 29.0, "end": 33.84, "text": "Each drill is provided in a sterile pouch."}],
    )
    assert vi_norm[0]["text"] == "Mỗi mũi khoan được đóng gói vô trùng, mã màu để dễ nhận biết."

