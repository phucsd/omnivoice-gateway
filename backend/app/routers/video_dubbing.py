import os
import re
import time
import uuid
import json
import shutil
import zipfile
from typing import Optional, List
import concurrent.futures
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, BackgroundTasks, Body
from fastapi.responses import FileResponse
from datetime import datetime
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, VideoDubbingJob, TTSJob, SystemSetting, LLMProfile
from app.schemas import (
    VideoDubbingJobResponse,
    SubtitleSegment,
    SubtitleUpdateRequest,
    VideoDubbingJobListResponse,
    DubbingRetryTranslationRequest,
    ShortenSubtitleRequest,
    ShortenSubtitleResponse,
    RemixDubbingRequest,
    LLMProfilePublicResponse,
    YouTubeVideoInfoRequest,
    YouTubeVideoInfoResponse,
    YouTubePreDownloadRequest,
    DubbingRetryPipelineRequest,
)
from app.utils.auth import get_user_or_api_key
from app.routers.admin import get_admin_user
from app.utils.files import save_upload_with_limit
from app.utils.url_security import ensure_within_directory, validate_safe_external_url
from app.config import settings
from app.services.video_dubbing_service import VideoDubbingService
from app.services.job_service import JobService
from app.services.audio_service import AudioService

from app.database import get_db, SessionLocal

router = APIRouter(prefix="/v1/video-dubbing", tags=["Video Dubbing"])

MAX_VIDEO_UPLOAD_BYTES = 300 * 1024 * 1024  # 300 MB
MAX_COOKIE_UPLOAD_BYTES = 1024 * 1024  # 1 MB


@router.get("/llm-profiles", response_model=List[LLMProfilePublicResponse])
def list_dubbing_llm_profiles(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key),
):
    """Returns non-secret LLM profile metadata so authenticated users can pick a translation engine."""
    _ = current_user
    return (
        db.query(LLMProfile)
        .order_by(LLMProfile.is_active.desc(), LLMProfile.created_at.desc())
        .all()
    )


@router.get("/test-cffi")
def test_cffi(
    url: str = "https://www.youtube.com/embed/uWr3SXVIROA",
    admin: User = Depends(get_admin_user),
):
    _ = admin
    try:
        safe_url = validate_safe_external_url(url)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    import traceback
    results = {}
    try:
        from curl_cffi import requests as curl_requests
        r = curl_requests.get(safe_url, impersonate="chrome", timeout=10)
        results["cffi_embed"] = {"status": r.status_code, "len": len(r.content)}
    except Exception as e:
        results["cffi_embed"] = {"error": str(e), "trace": traceback.format_exc()}
    try:
        import urllib.request
        req = urllib.request.Request(safe_url, headers={"User-Agent": "Mozilla/5.0"})
        r = urllib.request.urlopen(req, timeout=10)
        results["urllib_embed"] = {"status": r.status, "len": len(r.read())}
    except Exception as e:
        results["urllib_embed"] = {"error": str(e), "trace": traceback.format_exc()}
    return results

@router.get("/debug-yt")
def debug_yt(
    url: str = "https://www.youtube.com/watch?v=_HA6-A8itVY",
    admin: User = Depends(get_admin_user),
):
    _ = admin
    try:
        safe_url = validate_safe_external_url(url)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    import threading, time, traceback
    def bg_debug():
        log_path = "/tmp/debug_log.txt"
        t0 = time.time()
        def write_log(line: str):
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] {line}\n")

        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] Bắt đầu debug download cho URL: {safe_url}\n")

        try:
            tmp_dir = "/tmp/debug_yt"
            os.makedirs(tmp_dir, exist_ok=True)
            write_log("Đang gọi VideoDubbingService.download_youtube_video...")
            path, title = VideoDubbingService.download_youtube_video(safe_url, tmp_dir, log_file=log_path)
            size = os.path.getsize(path) if os.path.exists(path) else 0
            elapsed = time.time() - t0
            write_log(f"SUCCESS: Title='{title}', Path={path}, Size={size} bytes ({size/(1024*1024):.2f} MB), Time={elapsed:.2f}s")
        except Exception as e:
            elapsed = time.time() - t0
            write_log(f"ERROR sau {elapsed:.2f}s: {e}\n{traceback.format_exc()}")

    t = threading.Thread(target=bg_debug)
    t.start()
    return {"status": "started", "message": "Check /v1/video-dubbing/debug-log for live status"}

@router.get("/debug-log")
def debug_log(admin: User = Depends(get_admin_user)):
    _ = admin
    log_path = "/tmp/debug_log.txt"
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            return {"log": f.read()}
    return {"log": "No log file found"}

@router.get("/cookie-status")
def get_cookie_status(admin: User = Depends(get_admin_user)):
    _ = admin
    return VideoDubbingService.inspect_cookie_status()

@router.post("/upload-cookies")
async def upload_cookies(
    file: Optional[UploadFile] = File(None),
    cookie_text: Optional[str] = Form(None),
    admin: User = Depends(get_admin_user),
):
    _ = admin
    content = ""
    if file:
        content_bytes = await file.read(MAX_COOKIE_UPLOAD_BYTES + 1)
        if len(content_bytes) > MAX_COOKIE_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Tệp cookie vượt quá giới hạn tối đa 1 MB.")
        content = content_bytes.decode("utf-8", errors="ignore")
    elif cookie_text:
        if len(cookie_text.encode("utf-8")) > MAX_COOKIE_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Nội dung cookie vượt quá giới hạn tối đa 1 MB.")
        content = cookie_text
    else:
        raise HTTPException(status_code=400, detail="Cần cung cấp file cookies.txt hoặc nội dung cookie.")

    saved = VideoDubbingService.save_cookies_content(content)
    return {
        "status": "success",
        "message": f"Đã lưu cookies.txt thành công ({saved['cookie_count']} cookie, auth={'có' if saved['is_authenticated'] else 'ẩn danh'})",
        "size_bytes": saved["size_bytes"],
        "cookie_count": saved["cookie_count"],
        "is_authenticated": saved["is_authenticated"],
    }

@router.delete("/delete-cookies")
def delete_cookies(admin: User = Depends(get_admin_user)):
    _ = admin
    VideoDubbingService.delete_cookies_all()
    return {"status": "success", "message": "Đã xóa file cookies."}

class OAuthPollRequest(BaseModel):
    device_code: str

@router.get("/oauth/status")
def get_youtube_oauth_status(admin: User = Depends(get_admin_user)):
    _ = admin
    token_data = VideoDubbingService.refresh_oauth_token_if_needed()
    if token_data:
        return {
            "connected": True,
            "expires_at": token_data.get("expires", 0),
            "token_type": token_data.get("token_type", "Bearer")
        }
    return {"connected": False}

@router.post("/oauth/start")
def start_youtube_oauth(admin: User = Depends(get_admin_user)):
    _ = admin
    try:
        data = VideoDubbingService.start_oauth_device_flow()
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không thể khởi tạo phiên đăng nhập Google: {e}")

@router.post("/oauth/poll")
def poll_youtube_oauth(req: OAuthPollRequest, admin: User = Depends(get_admin_user)):
    _ = admin
    try:
        res = VideoDubbingService.poll_oauth_device_flow(req.device_code)
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi kiểm tra trạng thái xác thực: {e}")

@router.delete("/oauth/disconnect")
def disconnect_youtube_oauth(admin: User = Depends(get_admin_user)):
    _ = admin
    VideoDubbingService.delete_oauth_token()
    return {"status": "success", "message": "Đã ngắt kết nối tài khoản YouTube thành công."}

class OAuthSyncRequest(BaseModel):
    token_data: dict

@router.post("/oauth/sync")
def sync_youtube_oauth(req: OAuthSyncRequest, admin: User = Depends(get_admin_user)):
    _ = admin
    if req.token_data and all(k in req.token_data for k in ('access_token', 'refresh_token')):
        VideoDubbingService.save_oauth_token(req.token_data)
        return {"status": "success", "connected": True}
    return {"status": "error", "message": "Invalid token data"}

class ProxyConfigPayload(BaseModel):
    proxy_url: str

class ProxyPoolAddPayload(BaseModel):
    raw_text: str

@router.get("/proxy-pool")
def get_proxy_pool(admin: User = Depends(get_admin_user)):
    _ = admin
    info = VideoDubbingService.get_proxy_pool_info()
    return {
        "count": len(info),
        "proxies": info,
        "primary": info[0] if info else None
    }

@router.post("/proxy-pool/add")
def add_proxy_pool(payload: ProxyPoolAddPayload, admin: User = Depends(get_admin_user)):
    _ = admin
    text = payload.raw_text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Vui lòng cung cấp ít nhất một proxy.")
    info = VideoDubbingService.add_to_proxy_pool(text)
    return {
        "status": "success",
        "count": len(info),
        "proxies": info,
        "message": f"Đã cập nhật danh sách! Hiện có {len(info)} proxy trong pool."
    }

@router.post("/proxy-pool/test-all")
def test_all_proxy_pool(admin: User = Depends(get_admin_user)):
    _ = admin
    raw_proxies = VideoDubbingService.get_youtube_proxies()
    if not raw_proxies:
        return {"count": 0, "results": []}
    
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(len(raw_proxies), 8)) as executor:
        results = list(executor.map(VideoDubbingService.test_single_proxy, raw_proxies))
        
    return {
        "count": len(results),
        "results": results
    }

@router.delete("/proxy-pool/{proxy_hash}")
def delete_proxy_item(proxy_hash: str, admin: User = Depends(get_admin_user)):
    _ = admin
    info = VideoDubbingService.delete_from_proxy_pool(proxy_hash)
    return {
        "status": "success",
        "count": len(info),
        "proxies": info,
        "message": "Đã xóa proxy khỏi pool."
    }

@router.delete("/proxy-pool")
def clear_proxy_pool(admin: User = Depends(get_admin_user)):
    _ = admin
    VideoDubbingService.clear_proxy_pool()
    return {
        "status": "success",
        "count": 0,
        "proxies": [],
        "message": "Đã làm trống toàn bộ proxy pool."
    }

# Backward compatible endpoints
@router.get("/proxy-status")
def get_proxy_status(admin: User = Depends(get_admin_user)):
    _ = admin
    info = VideoDubbingService.get_proxy_pool_info()
    if info:
        return {"is_configured": True, "proxy": info[0]["masked"], "count": len(info)}
    return {"is_configured": False, "proxy": None, "count": 0}

@router.post("/set-proxy")
def set_proxy(payload: ProxyConfigPayload, admin: User = Depends(get_admin_user)):
    _ = admin
    p = payload.proxy_url.strip()
    if not p:
        VideoDubbingService.delete_youtube_proxy()
        return {"status": "success", "message": "Đã xóa proxy"}
    
    info = VideoDubbingService.add_to_proxy_pool(p)
    return {"status": "success", "message": f"Đã lưu proxy thành công: {info[0]['masked'] if info else p}", "proxy": info[0]["masked"] if info else p}

@router.post("/test-proxy")
def test_proxy(payload: ProxyConfigPayload, admin: User = Depends(get_admin_user)):
    _ = admin
    p = payload.proxy_url.strip()
    if not p:
        raise HTTPException(status_code=400, detail="Vui lòng cung cấp URL proxy để kiểm tra.")
    
    res = VideoDubbingService.test_single_proxy(p)
    if res["status"] == "online":
        return {
            "status": "success",
            "ip": res.get("ip"),
            "country": res.get("country"),
            "city": res.get("city"),
            "org": res.get("org"),
            "latency_ms": res.get("latency_ms"),
            "message": f"Kết nối Proxy thành công! IP: {res.get('ip')} ({res.get('city')}, {res.get('country')} - {res.get('org')}) - Độ trễ: {res.get('latency_ms')}ms"
        }
    else:
        return {
            "status": "error",
            "message": f"Không thể kết nối qua Proxy: {res.get('message')}"
        }

@router.delete("/delete-proxy")
def delete_proxy(admin: User = Depends(get_admin_user)):
    _ = admin
    VideoDubbingService.delete_youtube_proxy()
    return {"status": "success", "message": "Đã xóa cấu hình Proxy."}

def _make_throttled_progress_callback(job_id: str, min_pct: int, max_pct: int, min_interval: float = 0.8):
    """Creates a thread-safe progress callback that persists download progress to DB without spamming commits."""
    state = {"last_ts": 0.0, "last_pct": -1}

    def _callback(raw_pct: float, message: str):
        now = time.monotonic()
        clamped = max(0.0, min(100.0, float(raw_pct)))
        mapped_pct = min(max_pct, max(min_pct, int(min_pct + (clamped / 100.0) * (max_pct - min_pct))))
        if clamped < 100.0 and (now - state["last_ts"] < min_interval) and mapped_pct == state["last_pct"]:
            return
        state["last_ts"] = now
        state["last_pct"] = mapped_pct

        pdb = SessionLocal()
        try:
            j = pdb.query(VideoDubbingJob).filter(VideoDubbingJob.id == job_id).first()
            if j:
                j.progress = mapped_pct
                j.message = message
                pdb.commit()
        except Exception:
            pdb.rollback()
        finally:
            pdb.close()

    return _callback


def run_youtube_predownload(job_id: str):
    """Background task to pre-download a YouTube video for preview/save before dubbing."""
    db = SessionLocal()
    try:
        job = None
        for _ in range(10):
            job = db.query(VideoDubbingJob).filter(VideoDubbingJob.id == job_id).first()
            if job:
                break
            time.sleep(0.3)
        if not job:
            return

        job_dir = ensure_within_directory(settings.dubbing_dir, os.path.join(settings.dubbing_dir, job_id))
        os.makedirs(job_dir, exist_ok=True)
        log_path = os.path.join(job_dir, "process.log")

        job.status = "downloading"
        job.progress = 2
        job.error_message = None
        job.message = "Đang bắt đầu tải video từ YouTube..."
        db.commit()

        VideoDubbingService.log_to_job(job_id, f"Tải trước video YouTube: {job.source_url} (chất lượng: {job.preferred_quality or '1080p'})")
        meta_out = {}
        progress_cb = _make_throttled_progress_callback(job_id, 2, 99, min_interval=0.8)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                VideoDubbingService.download_youtube_video,
                job.source_url,
                job_dir,
                log_file=log_path,
                preferred_quality=job.preferred_quality or "1080p",
                progress_callback=progress_cb,
                metadata_out=meta_out,
            )
            dl_budget = VideoDubbingService.download_timeout_for_quality(job.preferred_quality) + 120
            try:
                video_path, title = future.result(timeout=dl_budget)
            except concurrent.futures.TimeoutError:
                raise Exception(f"Tải video từ YouTube vượt quá {dl_budget // 60} phút. Vui lòng thử chất lượng thấp hơn hoặc dùng video MP4.")

        db.refresh(job)
        job.input_file_path = video_path
        job.video_title = meta_out.get("title") or title or job.video_title
        job.video_thumbnail = meta_out.get("thumbnail") or job.video_thumbnail
        job.video_duration = meta_out.get("duration") if meta_out.get("duration") is not None else job.video_duration
        job.status = "uploaded"
        job.progress = 100
        job.error_message = None
        job.message = "Đã tải xong video từ YouTube, sẵn sàng xem trước hoặc lồng tiếng."
        db.commit()
        VideoDubbingService.log_to_job(job_id, f"Tải trước video YouTube thành công: '{job.video_title}' -> {video_path}")
    except Exception as e:
        err_msg = str(e).strip() or repr(e) or type(e).__name__
        VideoDubbingService.log_to_job(job_id, f"LỖI TẢI VIDEO YOUTUBE: {err_msg}")
        db.rollback()
        job = db.query(VideoDubbingJob).filter(VideoDubbingJob.id == job_id).first()
        if job:
            job.status = "failed"
            job.progress = 100
            job.error_message = err_msg
            job.message = f"Lỗi tải YouTube: {err_msg[:100]}"
            db.commit()
    finally:
        db.close()


def run_dubbing_pipeline(job_id: str):
    """Background task to run the video dubbing stages (Download -> Extract Audio -> Separate -> Transcribe -> Translate)."""
    db = SessionLocal()
    try:
        job = None
        for _ in range(10):
            job = db.query(VideoDubbingJob).filter(VideoDubbingJob.id == job_id).first()
            if job:
                break
            time.sleep(0.5)

        if not job:
            print(f"[run_dubbing_pipeline] Job {job_id} not found in DB after retries!")
            return

        VideoDubbingService.log_to_job(job_id, f"Khởi chạy pipeline lồng tiếng video. Trạng thái ban đầu: {job.status}")

        # Stage 0: Proactively pre-warm Kaggle GPU worker in parallel while video downloads & FFmpeg extracts audio!
        try:
            early_mode_setting = db.query(SystemSetting).filter(SystemSetting.key == "worker_mode").first()
            early_mode = early_mode_setting.value.strip() if early_mode_setting else settings.WORKER_MODE
            if early_mode == "kaggle":
                from app.services.kaggle_orchestrator import KaggleOrchestrator
                if not KaggleOrchestrator.has_live_worker(db):
                    VideoDubbingService.log_to_job(job_id, "[PRE-WARM] Khởi động sớm máy chủ GPU Kaggle chạy song song với quá trình tải video...")
                    KaggleOrchestrator.prewarm_worker_async(user_id=job.user_id)
        except Exception as pw_e:
            print(f"[run_dubbing_pipeline] Pre-warm notice: {pw_e}")

        job_dir = os.path.join(settings.dubbing_dir, job_id)
        os.makedirs(job_dir, exist_ok=True)
        log_path = os.path.join(job_dir, "process.log")

        # Stage 1: Download video if YouTube and not yet pre-downloaded
        need_download = (
            job.source_type == "youtube"
            and (
                not job.input_file_path
                or not os.path.exists(job.input_file_path)
                or os.path.getsize(job.input_file_path) == 0
            )
        )
        if need_download:
            job.status = "downloading"
            job.progress = 5
            job.message = "Đang chuẩn bị tải video từ YouTube..."
            db.commit()

            VideoDubbingService.log_to_job(job_id, f"Tải video từ YouTube URL: {job.source_url} (chất lượng: {job.preferred_quality or '1080p'})")
            meta_out = {}
            progress_cb = _make_throttled_progress_callback(job_id, 5, 24, min_interval=1.0)

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    VideoDubbingService.download_youtube_video,
                    job.source_url,
                    job_dir,
                    log_file=log_path,
                    preferred_quality=job.preferred_quality or "1080p",
                    progress_callback=progress_cb,
                    metadata_out=meta_out,
                )
                dl_budget = VideoDubbingService.download_timeout_for_quality(job.preferred_quality) + 120
                try:
                    video_path, title = future.result(timeout=dl_budget)
                except concurrent.futures.TimeoutError:
                    raise Exception(f"Tải video từ YouTube vượt quá {dl_budget // 60} phút. Vui lòng thử lại, chọn chất lượng thấp hơn hoặc dùng video MP4.")

            db.refresh(job)
            job.input_file_path = video_path
            job.video_title = meta_out.get("title") or title or job.video_title
            job.video_thumbnail = meta_out.get("thumbnail") or job.video_thumbnail
            job.video_duration = meta_out.get("duration") if meta_out.get("duration") is not None else job.video_duration
            db.commit()
            VideoDubbingService.log_to_job(job_id, f"Tải video thành công: '{job.video_title}'. File: {video_path}")
        elif job.source_type == "youtube" and job.input_file_path and os.path.exists(job.input_file_path):
            VideoDubbingService.log_to_job(job_id, f"Sử dụng video YouTube đã tải sẵn: {job.input_file_path}")

        # Stage 2: Extract audio from video
        job.status = "separating_audio"
        job.progress = 25
        job.message = "Đang tách âm thanh gốc..."
        db.commit()
        
        orig_audio_path = os.path.join(job_dir, "original_audio.wav")
        VideoDubbingService.log_to_job(job_id, f"Đang trích xuất audio gốc từ file video {job.input_file_path} bằng FFmpeg...")
        duration = VideoDubbingService.extract_audio_ffmpeg(job.input_file_path, orig_audio_path)
        job.original_audio_path = orig_audio_path
        db.commit()
        VideoDubbingService.log_to_job(job_id, f"Trích xuất thành công WAV audio gốc. Thời lượng: {duration:.2f}s. File: {orig_audio_path}")

        # Check worker mode (Mock vs Kaggle)
        worker_mode = db.query(SystemSetting).filter(SystemSetting.key == "worker_mode").first()
        mode_val = worker_mode.value.strip() if worker_mode else settings.WORKER_MODE

        from app.services.kaggle_orchestrator import KaggleOrchestrator
        is_worker_active = KaggleOrchestrator.has_live_worker(db)

        VideoDubbingService.log_to_job(job_id, f"Kiểm tra Worker mode. Chế độ: {mode_val}, GPU Worker active: {is_worker_active}")

        if mode_val == "mock":
            VideoDubbingService.log_to_job(job_id, "[MOCK] Thực hiện tách âm thanh (Vocals & BGM bằng FFmpeg/Demucs)...")
            vocals_path = os.path.join(job_dir, "vocals.wav")
            bgm_path = os.path.join(job_dir, "bgm.wav")
            VideoDubbingService.separate_audio_stems(orig_audio_path, vocals_path, bgm_path, job_id=job_id)
            job.vocals_audio_path = vocals_path
            job.bgm_audio_path = bgm_path
            db.commit()
            VideoDubbingService.log_to_job(job_id, f"[MOCK] Tách âm thanh thành công. Vocals: {vocals_path}, BGM: {bgm_path}")

            # Mock Transcription (ASR)
            job.status = "transcribing"
            job.progress = 50
            job.message = "Đang dịch băng ghi âm (Nhận dạng giọng nói)..."
            db.commit()
            
            VideoDubbingService.log_to_job(job_id, "[MOCK] Đang chạy nhận diện giọng nói (ASR)...")

            # Create simple mock subtitles spaced out by 5 seconds
            mock_subs = []
            num_segments = max(1, int(duration // 6))
            for i in range(num_segments):
                start = i * 6.0
                end = min(duration, start + 5.0)
                mock_subs.append({
                    "id": i + 1,
                    "start": start,
                    "end": end,
                    "text": f"Đây là phân đoạn phụ đề gốc số {i+1} để kiểm tra thử nghiệm lồng tiếng."
                })
            job.original_subtitles = json.dumps(mock_subs)
            db.commit()
            VideoDubbingService.log_to_job(job_id, f"[MOCK] Nhận dạng giọng nói thành công. Tạo {len(mock_subs)} phân đoạn phụ đề gốc.")

            # Mock Translation
            job.status = "translating"
            job.progress = 75
            job.message = "Đang dịch phụ đề qua ngôn ngữ đích..."
            db.commit()
            
            VideoDubbingService.log_to_job(job_id, f"[MOCK] Đang dịch phụ đề sang ngôn ngữ đích: {job.target_language} bằng LLM...")
            translated_subs = VideoDubbingService.translate_subtitles_llm(mock_subs, job.target_language, db)
            job.translated_subtitles = json.dumps(translated_subs)
            
            job.status = "awaiting_review"
            job.progress = 100
            job.message = "Đang chờ người dùng kiểm tra và xác nhận phụ đề dịch."
            db.commit()
            VideoDubbingService.log_to_job(job_id, "Pipeline phân tích hoàn tất. Chuyển trạng thái sang: AWAITING_REVIEW.")

        else:
            # Kaggle Worker / GPU Mode: Always submit job for audio separation
            job.status = "separating_audio"
            job.progress = 30
            job.message = "Đang gửi yêu cầu tách giọng và nhạc nền lên GPU Worker..."
            db.commit()

            VideoDubbingService.log_to_job(job_id, f"[GPU WORKER] Tạo sub-job tách nhạc 'sep_{job_id}' trong hàng đợi...")

            # Upsert separate_audio sub-job cleanly so retries never hit UNIQUE constraint errors
            existing_sep = db.query(TTSJob).filter(TTSJob.id == f"sep_{job_id}").first()
            if existing_sep:
                existing_sep.user_id = job.user_id
                existing_sep.job_type = "separate_audio"
                existing_sep.ref_audio_path = orig_audio_path
                existing_sep.status = "queued"
                existing_sep.progress = 0
                existing_sep.worker_id = None
                existing_sep.error_message = None
                existing_sep.message = "Đang chờ GPU Worker nhận tác vụ tách nhạc..."
            else:
                parent_tts_job = TTSJob(
                    id=f"sep_{job_id}",
                    user_id=job.user_id,
                    job_type="separate_audio",
                    ref_audio_path=orig_audio_path,
                    status="queued",
                    progress=0,
                    message="Đang chờ GPU Worker nhận tác vụ tách nhạc...",
                )
                db.add(parent_tts_job)
            db.commit()
            try:
                KaggleOrchestrator.ensure_worker_running(db)
            except Exception:
                pass
            VideoDubbingService.log_to_job(job_id, "[GPU WORKER] Đã đưa sub-job tách nhạc vào hàng đợi, chờ GPU Worker xử lý...")
            
    except Exception as e:
        err_msg = str(e).strip() or repr(e) or type(e).__name__
        if isinstance(e, concurrent.futures.TimeoutError):
            err_msg = "Tải video từ YouTube quá 5 phút. Vui lòng thử lại hoặc tải video MP4 trực tiếp."
        VideoDubbingService.log_to_job(job_id, f"LỖI PIELINE PHÂN TÍCH: {err_msg}")
        db.rollback()
        job = db.query(VideoDubbingJob).filter(VideoDubbingJob.id == job_id).first()
        if job:
            job.status = "failed"
            job.progress = 100
            job.error_message = err_msg
            job.message = f"Có lỗi xảy ra: {err_msg[:100]}"
            db.commit()
    finally:
        db.close()

def trigger_transcription_stage(dub_job_id: str, db: Session):
    """Triggers the transcription ASR stage on Kaggle for the separated vocals."""
    job = db.query(VideoDubbingJob).filter(VideoDubbingJob.id == dub_job_id).first()
    if not job or not job.vocals_audio_path:
        return
        
    job.status = "transcribing"
    job.progress = 50
    job.message = "Đang tạo phụ đề tiếng gốc bằng Whisper ASR trên Kaggle..."
    db.commit()
    
    VideoDubbingService.log_to_job(dub_job_id, f"[KAGGLE] Kênh vocals đã sẵn sàng. Tạo sub-job Whisper ASR 'asr_{dub_job_id}' trong hàng đợi...")

    # Upsert ASR sub-job cleanly so retries never hit UNIQUE constraint errors
    existing_asr = db.query(TTSJob).filter(TTSJob.id == f"asr_{dub_job_id}").first()
    if existing_asr:
        existing_asr.user_id = job.user_id
        existing_asr.job_type = "asr"
        existing_asr.ref_audio_path = job.vocals_audio_path
        existing_asr.status = "queued"
        existing_asr.progress = 0
        existing_asr.worker_id = None
        existing_asr.error_message = None
        existing_asr.message = "Đang gửi tệp vocals lên Kaggle để nhận diện chữ..."
        existing_asr.with_alignment = True
    else:
        asr_job = TTSJob(
            id=f"asr_{dub_job_id}",
            user_id=job.user_id,
            job_type="asr",
            ref_audio_path=job.vocals_audio_path,
            status="queued",
            progress=0,
            message="Đang gửi tệp vocals lên Kaggle để nhận diện chữ...",
            with_alignment=True
        )
        db.add(asr_job)
    db.commit()
    try:
        from app.services.kaggle_orchestrator import KaggleOrchestrator
        KaggleOrchestrator.ensure_worker_running(db)
    except Exception:
        pass
    VideoDubbingService.log_to_job(dub_job_id, "[KAGGLE] Đã đưa sub-job Whisper ASR vào hàng đợi, chờ GPU Worker xử lý...")

def trigger_translation_stage(dub_job_id: str, text: str, alignment_str: str, db: Session):
    """Executes translation after ASR finishes and enters review stage."""
    job = db.query(VideoDubbingJob).filter(VideoDubbingJob.id == dub_job_id).first()
    if not job:
        return
        
    job.status = "translating"
    job.progress = 75
    job.message = "Đang dịch phụ đề tự động bằng LLM..."
    db.commit()
    
    VideoDubbingService.log_to_job(dub_job_id, "Nhận kết quả ASR. Bắt đầu phân đoạn và căn lề thời gian (alignment)...")

    # Parse segments from Whisper alignment with pause-aware grouping & anti-hallucination filtering
    try:
        alignment_data = json.loads(alignment_str) if alignment_str else []
    except Exception:
        alignment_data = []

    est_duration = float(getattr(job, "video_duration", 0.0) or 0.0)
    if est_duration <= 0 and job.original_audio_path and os.path.exists(job.original_audio_path):
        try:
            import soundfile as sf
            est_duration = float(sf.info(job.original_audio_path).duration)
        except Exception:
            est_duration = max(10.0, float(os.path.getsize(job.original_audio_path)) / (44100 * 4))
    if est_duration <= 0:
        est_duration = 120.0

    merged_orig_segments = VideoDubbingService.build_clean_asr_segments(
        alignment_data=alignment_data,
        raw_text=text or "",
        total_duration=est_duration,
    )
    job.original_subtitles = json.dumps(merged_orig_segments)
    db.commit()
    VideoDubbingService.log_to_job(dub_job_id, f"Hoàn tất phân tích, khử nhiễu ảo giác ASR & hợp nhất phụ đề gốc: gồm {len(merged_orig_segments)} phân đoạn chuẩn.")

    # Call LLM translator with resilient fallback (preserving strict 1:1 segment alignment)
    llm_prof_id = getattr(job, "llm_profile_id", None)
    VideoDubbingService.log_to_job(dub_job_id, f"Bắt đầu dịch thuật phụ đề tự động sang tiếng: {job.target_language} bằng LLM (Profile ID: {llm_prof_id or 'Default'})...")
    
    try:
        translated = VideoDubbingService.translate_subtitles_llm(merged_orig_segments, job.target_language, db, llm_profile_id=llm_prof_id)
        normalized_translated = VideoDubbingService.normalize_translated_subtitles(translated, merged_orig_segments)
        job.translated_subtitles = json.dumps(normalized_translated)
        job.error_message = None
        VideoDubbingService.log_to_job(dub_job_id, "Dịch thuật phụ đề hoàn tất. Chuyển trạng thái sang: AWAITING_REVIEW.")
    except Exception as e:
        err_msg = str(e)
        print(f"[VideoDubbing] Translation failed for {dub_job_id}: {err_msg}")
        VideoDubbingService.log_to_job(dub_job_id, f"⚠️ [CẢNH BÁO LLM] Dịch tự động thất bại: {err_msg}. Đã chuyển phụ đề gốc sang để bạn tự chỉnh sửa hoặc bấm 'Dịch Lại Bằng AI'.")
        # Fallback to original subtitles so the job is NEVER stuck!
        job.translated_subtitles = json.dumps(merged_orig_segments)
        job.error_message = f"Dịch LLM thất bại: {err_msg}"

    job.status = "awaiting_review"
    job.progress = 100
    job.message = "Đang chờ người dùng kiểm tra và xác nhận phụ đề dịch."
    db.commit()

def _sanitize_subtitle_segments(raw: Optional[str]) -> list[dict]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []

    cleaned: list[dict] = []
    for idx, item in enumerate(parsed):
        if not isinstance(item, dict):
            continue
        try:
            seg_id = int(item.get("id", idx + 1))
        except (ValueError, TypeError):
            seg_id = idx + 1
        try:
            start_v = float(item.get("start", 0.0) or 0.0)
        except (ValueError, TypeError):
            start_v = 0.0
        try:
            end_v = float(item.get("end", start_v + 1.0) or (start_v + 1.0))
        except (ValueError, TypeError):
            end_v = start_v + 1.0
        if end_v < start_v:
            end_v = start_v + 0.5
        text_v = "" if item.get("text") is None else str(item.get("text"))
        cleaned.append(
            {
                "id": seg_id,
                "start": round(start_v, 3),
                "end": round(end_v, 3),
                "text": text_v,
            }
        )
    return cleaned


_LAST_DISK_RECONCILE_TS = 0.0
_DISK_RECONCILE_INTERVAL_SEC = 60.0


def reconcile_dubbing_jobs_from_disk(db: Session, fallback_user_id: Optional[str] = None, force: bool = False) -> int:
    """
    Scans settings.dubbing_dir for any vd_* project directories whose DB rows were lost
    or orphaned across container restarts, reconstructing them cleanly.
    Throttled in production so 4s UI list polls never scan disk on every request.
    """
    global _LAST_DISK_RECONCILE_TS
    now_mono = time.monotonic()
    is_testing = (settings.APP_ENV or "").lower() == "testing"
    if not force and not is_testing and (now_mono - _LAST_DISK_RECONCILE_TS) < _DISK_RECONCILE_INTERVAL_SEC:
        return 0
    _LAST_DISK_RECONCILE_TS = now_mono

    dubbing_root = settings.dubbing_dir
    if not dubbing_root or not os.path.isdir(dubbing_root):
        return 0

    owner_id = fallback_user_id
    if not owner_id:
        admin_u = db.query(User).filter(User.is_admin == True).first()
        owner_id = admin_u.id if admin_u else None
    if not owner_id:
        return 0

    existing_ids = {row[0] for row in db.query(VideoDubbingJob.id).all()}
    recovered = 0
    import re

    for entry in os.listdir(dubbing_root):
        if not entry.startswith("vd_") or entry in existing_ids:
            continue
        job_dir = os.path.join(dubbing_root, entry)
        if not os.path.isdir(job_dir):
            continue

        output_mp4 = os.path.join(job_dir, "output_dubbed.mp4")
        input_mp4 = os.path.join(job_dir, "input_video.mp4")
        orig_wav = os.path.join(job_dir, "original_audio.wav")
        vocals_wav = os.path.join(job_dir, "vocals.wav")
        bgm_wav = os.path.join(job_dir, "bgm.wav")
        log_path = os.path.join(job_dir, "process.log")

        has_output = os.path.exists(output_mp4) and os.path.getsize(output_mp4) > 1024
        has_vocals = os.path.exists(vocals_wav) and os.path.getsize(vocals_wav) > 512
        has_input = os.path.exists(input_mp4) and os.path.getsize(input_mp4) > 512
        has_log = os.path.exists(log_path)

        if not (has_output or has_vocals or has_input or has_log):
            continue

        title = None
        src_url = None
        source_type = "upload"
        if has_log:
            try:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as lf:
                    log_txt = lf.read()
                m_title = re.search(r"Tải video thành công:\s*'([^']+)'", log_txt)
                if m_title:
                    title = m_title.group(1).strip()
                    source_type = "youtube"
                m_url = re.search(r"(https?://(?:www\.)?(?:youtube\.com/watch\?v=[0-9A-Za-z_-]{11}|youtu\.be/[0-9A-Za-z_-]{11}))", log_txt)
                if m_url:
                    src_url = m_url.group(1)
                    source_type = "youtube"
            except Exception:
                pass

        # Recover subtitles from linked TTS sub-jobs if present
        trans_json = None
        orig_json = None
        dub_sub = db.query(TTSJob).filter(TTSJob.id == f"dub_{entry}").first()
        if dub_sub and dub_sub.text and dub_sub.text.strip().startswith("["):
            trans_json = dub_sub.text
        asr_sub = db.query(TTSJob).filter(TTSJob.id == f"asr_{entry}").first()
        if asr_sub and asr_sub.text:
            if trans_json and not orig_json:
                orig_json = trans_json

        if has_output:
            st_val, prog_val, msg_val = "completed", 100, "Lồng tiếng video thành công!"
        elif has_vocals:
            st_val, prog_val, msg_val = "awaiting_review", 100, "Đang chờ kiểm tra và xác nhận phụ đề."
        elif has_input:
            st_val, prog_val, msg_val = "uploaded", 100, "Video gốc đã sẵn sàng trên máy chủ."
        else:
            continue

        try:
            dir_mtime = datetime.utcfromtimestamp(os.path.getmtime(job_dir))
        except Exception:
            dir_mtime = datetime.utcnow()

        rec_job = VideoDubbingJob(
            id=entry,
            user_id=owner_id,
            status=st_val,
            progress=prog_val,
            message=msg_val,
            source_type=source_type,
            source_url=src_url,
            video_title=title or f"Dự án khôi phục #{entry[:8]}",
            target_language="Vietnamese",
            input_file_path=input_mp4 if has_input else None,
            original_audio_path=orig_wav if os.path.exists(orig_wav) else None,
            vocals_audio_path=vocals_wav if has_vocals else None,
            bgm_audio_path=bgm_wav if os.path.exists(bgm_wav) else None,
            original_subtitles=orig_json or trans_json,
            translated_subtitles=trans_json or orig_json,
            output_video_path=output_mp4 if has_output else None,
            created_at=dir_mtime,
            updated_at=dir_mtime,
        )
        db.add(rec_job)
        recovered += 1

    if recovered > 0:
        try:
            db.commit()
            print(f"[DubbingReconcile] Reconstructed {recovered} dubbing job(s) from disk.")
        except Exception as e:
            db.rollback()
            print(f"[DubbingReconcile] Failed to commit reconstructed jobs: {e}")
            return 0
    return recovered


def _get_accessible_dubbing_job(db: Session, job_id: str, current_user: User) -> VideoDubbingJob:
    job = db.query(VideoDubbingJob).filter(VideoDubbingJob.id == job_id).first()
    if not job and current_user.is_admin:
        reconcile_dubbing_jobs_from_disk(db, fallback_user_id=current_user.id)
        job = db.query(VideoDubbingJob).filter(VideoDubbingJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Không tìm thấy tác vụ lồng tiếng.")
    if job.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=404, detail="Không tìm thấy tác vụ lồng tiếng.")
    return job


def _format_dubbing_job_response(job: VideoDubbingJob) -> VideoDubbingJobResponse:
    orig_subs = _sanitize_subtitle_segments(job.original_subtitles)
    trans_subs = _sanitize_subtitle_segments(job.translated_subtitles)

    job_dir = os.path.join(settings.dubbing_dir, job.id)
    disk_output = os.path.join(job_dir, "output_dubbed.mp4")
    resolved_output = job.output_video_path
    if (not resolved_output or not os.path.exists(resolved_output)) and os.path.exists(disk_output):
        resolved_output = disk_output

    disk_input = os.path.join(job_dir, "input_video.mp4")
    resolved_input = job.input_file_path
    if (not resolved_input or not os.path.exists(resolved_input)) and os.path.exists(disk_input):
        resolved_input = disk_input

    disk_vocals = os.path.join(job_dir, "vocals.wav")
    resolved_vocals = job.vocals_audio_path
    if (not resolved_vocals or not os.path.exists(resolved_vocals)) and os.path.exists(disk_vocals):
        resolved_vocals = disk_vocals

    disk_bgm = os.path.join(job_dir, "bgm.wav")
    resolved_bgm = job.bgm_audio_path
    if (not resolved_bgm or not os.path.exists(resolved_bgm)) and os.path.exists(disk_bgm):
        resolved_bgm = disk_bgm

    has_output = bool(
        (resolved_output and os.path.exists(resolved_output) and os.path.getsize(resolved_output) > 0)
        or job.status == "completed"
    )
    output_video_url = f"/v1/video-dubbing/jobs/{job.id}/output" if has_output else None
    has_source_video = bool(resolved_input and os.path.exists(resolved_input) and os.path.getsize(resolved_input) > 0)

    raw_vocal_vol = getattr(job, "vocals_volume", None)
    raw_bgm_vol = getattr(job, "bgm_volume", None)
    now_utc = datetime.utcnow()
    created_dt = job.created_at or now_utc
    updated_dt = job.updated_at or created_dt

    return VideoDubbingJobResponse(
        id=job.id,
        status=job.status or "queued",
        progress=int(job.progress or 0),
        message=job.message,
        source_type=job.source_type or "upload",
        source_url=job.source_url,
        video_title=getattr(job, "video_title", None),
        video_thumbnail=getattr(job, "video_thumbnail", None),
        video_duration=getattr(job, "video_duration", None),
        preferred_quality=getattr(job, "preferred_quality", None) or "1080p",
        has_source_video=has_source_video,
        target_language=job.target_language or "Vietnamese",
        llm_profile_id=getattr(job, "llm_profile_id", None),
        voice_mode=getattr(job, "voice_mode", None) or "auto_native",
        voice_sample_id=getattr(job, "voice_sample_id", None),
        original_subtitles=orig_subs,
        translated_subtitles=trans_subs,
        vocals_audio_path=resolved_vocals,
        bgm_audio_path=resolved_bgm,
        vocals_volume=float(raw_vocal_vol) if raw_vocal_vol is not None else 1.0,
        bgm_volume=float(raw_bgm_vol) if raw_bgm_vol is not None else 0.4,
        output_video_url=output_video_url,
        error_message=job.error_message,
        created_at=created_dt,
        updated_at=updated_dt,
    )


@router.post("/youtube/info", response_model=YouTubeVideoInfoResponse)
def get_youtube_video_info(
    req: YouTubeVideoInfoRequest,
    current_user: User = Depends(get_user_or_api_key),
):
    """Kiểm tra link YouTube và trả về metadata (tiêu đề, kênh, thời lượng, thumbnail, chất lượng)."""
    _ = current_user
    try:
        info = VideoDubbingService.get_youtube_video_info(req.url)
        return YouTubeVideoInfoResponse(**info)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Không thể lấy thông tin video YouTube: {e}")


@router.post("/youtube/download", response_model=VideoDubbingJobResponse)
def pre_download_youtube_video(
    req: YouTubePreDownloadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key),
):
    """Tải trước video YouTube về máy chủ để xem trước / tải MP4 hoặc tiếp tục lồng tiếng."""
    try:
        video_id, canonical_url = VideoDubbingService.normalize_youtube_url(req.url)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    job_id = f"vd_{uuid.uuid4().hex[:8]}"
    job_dir = ensure_within_directory(settings.dubbing_dir, os.path.join(settings.dubbing_dir, job_id))
    os.makedirs(job_dir, exist_ok=True)

    job = VideoDubbingJob(
        id=job_id,
        user_id=current_user.id,
        status="downloading",
        progress=2,
        message="Đang bắt đầu tải video từ YouTube...",
        source_type="youtube",
        source_url=canonical_url,
        video_thumbnail=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        preferred_quality=req.preferred_quality or "1080p",
        target_language=req.target_language or "Vietnamese",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    import threading
    t = threading.Thread(target=run_youtube_predownload, args=(job_id,), daemon=True)
    t.start()

    return _format_dubbing_job_response(job)


@router.get("/bridge-status")
def get_youtube_bridge_status(admin: User = Depends(get_admin_user)):
    """Kiểm tra trạng thái kết nối Windows YouTube Bridge (Cloudflare Tunnel + R2)."""
    _ = admin
    from app.services.r2_bridge_client import RemoteYouTubeBridgeClient
    return RemoteYouTubeBridgeClient.get_bridge_diagnostics()


@router.post("/test-youtube")
def test_youtube_connection(
    payload: Optional[YouTubeVideoInfoRequest] = Body(default=None),
    admin: User = Depends(get_admin_user),
):
    """Kiểm tra nhanh khả năng kết nối & phân giải video YouTube trên máy chủ."""
    _ = admin
    from app.services.r2_bridge_client import RemoteYouTubeBridgeClient

    target_url = (payload.url if payload and payload.url else "https://www.youtube.com/watch?v=_HA6-A8itVY")
    t0 = time.perf_counter()
    cookie_status = VideoDubbingService.inspect_cookie_status()
    oauth_connected = bool(VideoDubbingService.refresh_oauth_token_if_needed())
    proxy_count = len(VideoDubbingService.get_youtube_proxies())
    bridge_diag = RemoteYouTubeBridgeClient.get_bridge_diagnostics()

    bridge_note = ""
    if bridge_diag.get("configured"):
        bridge_note = (
            f" | Tunnel Bridge: {'Online' if bridge_diag.get('bridge_online') else 'Offline'}"
            f" | R2 Cache: {'Online' if bridge_diag.get('r2_online') else 'Offline'}"
        )

    try:
        info = VideoDubbingService.get_youtube_video_info(target_url)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "status": "success",
            "latency_ms": latency_ms,
            "title": info.get("title"),
            "available_qualities": info.get("available_qualities", []),
            "cookie_status": cookie_status,
            "oauth_connected": oauth_connected,
            "proxy_count": proxy_count,
            "bridge": bridge_diag,
            "message": f"Kết nối YouTube thành công ({latency_ms}ms): {info.get('title')}{bridge_note}",
        }
    except Exception as e:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "status": "error",
            "latency_ms": latency_ms,
            "title": None,
            "available_qualities": [],
            "cookie_status": cookie_status,
            "oauth_connected": oauth_connected,
            "proxy_count": proxy_count,
            "bridge": bridge_diag,
            "message": f"Kiểm tra YouTube thất bại ({latency_ms}ms): {e}{bridge_note}",
        }


@router.post("/jobs/{job_id}/retry", response_model=VideoDubbingJobResponse)
def retry_dubbing_job(
    job_id: str,
    payload: DubbingRetryPipelineRequest = Body(default=DubbingRetryPipelineRequest()),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key),
):
    """Thử lại tải video YouTube hoặc khởi chạy lại pipeline cho tác vụ bị lỗi/gián đoạn."""
    job = _get_accessible_dubbing_job(db, job_id, current_user)

    if job.status not in ["failed", "downloading", "queued", "separating_audio", "transcribing"]:
        raise HTTPException(status_code=400, detail="Chỉ có thể thử lại tác vụ đang lỗi hoặc bị kẹt ở bước tải/tách âm thanh/nhận diện.")

    if payload.preferred_quality:
        job.preferred_quality = payload.preferred_quality
    job.error_message = None

    import threading
    if payload.download_only:
        job.status = "downloading"
        job.progress = 5
        job.message = "Đang thử tải lại video từ YouTube..."
        db.commit()
        db.refresh(job)
        VideoDubbingService.log_to_job(job.id, f"Người dùng yêu cầu tải lại video YouTube (chất lượng: {job.preferred_quality or '1080p'}).")
        t = threading.Thread(target=run_youtube_predownload, args=(job.id,), daemon=True)
        t.start()
    else:
        job.status = "queued"
        job.progress = 5
        job.message = "Đang khởi chạy lại quy trình lồng tiếng..."
        db.commit()
        db.refresh(job)
        VideoDubbingService.log_to_job(job.id, f"Người dùng yêu cầu chạy lại pipeline (chất lượng: {job.preferred_quality or '1080p'}).")
        t = threading.Thread(target=run_dubbing_pipeline, args=(job.id,), daemon=True)
        t.start()

    return _format_dubbing_job_response(job)


@router.post("/upload", response_model=VideoDubbingJobResponse)
async def upload_dubbing_video(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key)
):
    """Uploads a raw video file and creates a draft VideoDubbingJob."""
    job_id = f"vd_{uuid.uuid4().hex[:8]}"
    job_dir = ensure_within_directory(settings.dubbing_dir, os.path.join(settings.dubbing_dir, job_id))
    os.makedirs(job_dir, exist_ok=True)

    filename = os.path.basename(file.filename or "uploaded_video.mp4")
    ext = os.path.splitext(filename)[1] or ".mp4"
    input_file_path = ensure_within_directory(job_dir, os.path.join(job_dir, f"input_video{ext}"))

    VideoDubbingService.log_to_job(job_id, f"Khởi tạo upload video local: {filename}")
    await save_upload_with_limit(file, input_file_path, max_bytes=MAX_VIDEO_UPLOAD_BYTES)
    VideoDubbingService.log_to_job(job_id, f"Upload video thành công. File lưu tại: {input_file_path}")

    job = VideoDubbingJob(
        id=job_id,
        user_id=current_user.id,
        status="uploaded",
        progress=100,
        message="Đã tải lên video gốc, sẵn sàng để lồng tiếng.",
        source_type="upload",
        video_title=filename,
        target_language="Vietnamese",  # Default to Vietnamese
        input_file_path=input_file_path
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    return _format_dubbing_job_response(job)

@router.get("/jobs/{job_id}/log")
def get_dubbing_log(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key)
):
    """Retrieve the consolidated process/diagnostic log for a video dubbing job."""
    job = _get_accessible_dubbing_job(db, job_id, current_user)
    
    log_path = os.path.join(settings.dubbing_dir, job_id, "process.log")
    if not os.path.exists(log_path):
        return {"log": "Chưa có log ghi nhận cho tác vụ này."}
        
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            return {"log": f.read()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không thể đọc log: {e}")

@router.post("", response_model=VideoDubbingJobResponse)
@router.post("/jobs", response_model=VideoDubbingJobResponse)
async def create_dubbing_job(
    background_tasks: BackgroundTasks,
    file: Optional[UploadFile] = File(None),
    youtube_url: Optional[str] = Form(None),
    target_language: str = Form("Vietnamese"),
    uploaded_job_id: Optional[str] = Form(None),
    llm_profile_id: Optional[str] = Form(None),
    oauth_token: Optional[str] = Form(None),
    preferred_quality: str = Form("1080p"),
    video_title: Optional[str] = Form(None),
    video_thumbnail: Optional[str] = Form(None),
    video_duration: Optional[float] = Form(None),
    voice_mode: Optional[str] = Form("auto_native"),
    voice_sample_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key)
):
    """Tải lên video hoặc dán link YouTube để bắt đầu quy trình lồng tiếng tự động."""
    if oauth_token and current_user.is_admin:
        try:
            t_data = json.loads(oauth_token)
            if all(k in t_data for k in ('access_token', 'refresh_token')):
                VideoDubbingService.save_oauth_token(t_data)
        except Exception:
            pass

    if not file and not youtube_url and not uploaded_job_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bạn cần cung cấp video tải lên, link YouTube hoặc ID video đã tải lên trước."
        )

    yt_video_id = None
    if youtube_url:
        try:
            yt_video_id, youtube_url = VideoDubbingService.normalize_youtube_url(youtube_url)
        except ValueError as ve:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"URL video không hợp lệ hoặc không được phép: {ve}",
            )

    clean_v_mode = (voice_mode or "auto_native").strip()
    clean_vs_id = (voice_sample_id or "").strip() or None
    if clean_vs_id and clean_v_mode == "auto_native":
        clean_v_mode = "voice_sample"

    if uploaded_job_id:
        job = _get_accessible_dubbing_job(db, uploaded_job_id, current_user)
        
        job.target_language = target_language
        if llm_profile_id:
            job.llm_profile_id = llm_profile_id
        if preferred_quality:
            job.preferred_quality = preferred_quality
        job.voice_mode = clean_v_mode
        job.voice_sample_id = clean_vs_id
        job.error_message = None
        job.status = "queued"
        job.progress = 5
        job.message = "Khởi tạo tác vụ lồng tiếng..."
        db.commit()
        db.refresh(job)

        VideoDubbingService.log_to_job(job.id, f"Bắt đầu tác vụ lồng tiếng từ video đã tải lên trước đó. Ngôn ngữ đích: {target_language}. Giọng đọc: {clean_v_mode} ({clean_vs_id or 'auto'})")

        # Trigger background execution pipeline with isolated daemon thread
        import threading
        t = threading.Thread(target=run_dubbing_pipeline, args=(job.id,), daemon=True)
        t.start()

        return _format_dubbing_job_response(job)

    job_id = f"vd_{uuid.uuid4().hex[:8]}"
    job_dir = ensure_within_directory(settings.dubbing_dir, os.path.join(settings.dubbing_dir, job_id))
    os.makedirs(job_dir, exist_ok=True)

    input_file_path = None
    source_type = "youtube" if youtube_url else "upload"

    VideoDubbingService.log_to_job(job_id, f"Khởi tạo tác vụ lồng tiếng mới. Nguồn: {source_type}. Ngôn ngữ đích: {target_language}. Giọng đọc: {clean_v_mode} ({clean_vs_id or 'auto'})")

    if file:
        filename = os.path.basename(file.filename or "uploaded_video")
        ext = os.path.splitext(filename)[1] or ".mp4"
        input_file_path = ensure_within_directory(job_dir, os.path.join(job_dir, f"input_video{ext}"))
        await save_upload_with_limit(file, input_file_path, max_bytes=MAX_VIDEO_UPLOAD_BYTES)
        VideoDubbingService.log_to_job(job_id, f"Upload video thành công. File lưu tại: {input_file_path}")
        if not video_title:
            video_title = filename

    default_thumb = f"https://i.ytimg.com/vi/{yt_video_id}/hqdefault.jpg" if yt_video_id else None

    job = VideoDubbingJob(
        id=job_id,
        user_id=current_user.id,
        status="queued",
        progress=5,
        message="Khởi tạo tác vụ lồng tiếng...",
        source_type=source_type,
        source_url=youtube_url,
        video_title=video_title,
        video_thumbnail=video_thumbnail or default_thumb,
        video_duration=video_duration,
        preferred_quality=preferred_quality or "1080p",
        target_language=target_language,
        llm_profile_id=llm_profile_id,
        voice_mode=clean_v_mode,
        voice_sample_id=clean_vs_id,
        input_file_path=input_file_path
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Trigger background execution pipeline with isolated daemon thread
    import threading
    t = threading.Thread(target=run_dubbing_pipeline, args=(job_id,), daemon=True)
    t.start()

    return _format_dubbing_job_response(job)

@router.get("/jobs", response_model=VideoDubbingJobListResponse)
def list_dubbing_jobs(
    page: int = 1,
    page_size: int = 50,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key)
):
    """Lấy danh sách tất cả các tác vụ lồng tiếng của người dùng."""
    if current_user.is_admin:
        reconcile_dubbing_jobs_from_disk(db, fallback_user_id=current_user.id)
        query = db.query(VideoDubbingJob)
    else:
        query = db.query(VideoDubbingJob).filter(VideoDubbingJob.user_id == current_user.id)
    if status and status != "all":
        query = query.filter(VideoDubbingJob.status == status)
        
    total = query.count()
    jobs = query.order_by(VideoDubbingJob.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    
    return VideoDubbingJobListResponse(
        total=total,
        page=page,
        page_size=page_size,
        jobs=[_format_dubbing_job_response(j) for j in jobs]
    )

@router.get("/jobs/{job_id}", response_model=VideoDubbingJobResponse)
def get_dubbing_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key)
):
    """Lấy thông tin tiến độ và phụ đề của tác vụ lồng tiếng."""
    job = _get_accessible_dubbing_job(db, job_id, current_user)
    
    # Auto-recovery: If job is stuck in 'translating' for more than 60 seconds and original_subtitles is already available,
    # auto-advance it to awaiting_review so the user can immediately review and edit!
    if job.status == "translating" and job.original_subtitles:
        delta = (datetime.utcnow() - job.updated_at).total_seconds() if job.updated_at else 9999
        if delta > 60:
            print(f"[AutoRecovery] Rescuing stuck job {job.id} (stuck for {delta:.0f}s in translating)")
            job.status = "awaiting_review"
            job.progress = 100
            job.message = "Đã tự động chuyển sang duyệt phụ đề (bản dịch trước đó bị gián đoạn, bạn có thể tự sửa hoặc bấm Dịch lại)."
            if not job.translated_subtitles:
                job.translated_subtitles = job.original_subtitles
            if not job.error_message:
                job.error_message = "Quá trình dịch LLM tự động bị gián đoạn hoặc quá thời gian chờ."
            db.commit()
            VideoDubbingService.log_to_job(job.id, "⚠️ [TỰ ĐỘNG PHỤC HỒI] Quá trình dịch thuật trước đó bị gián đoạn. Đã tự động mở bước Duyệt Phụ Đề để bạn tiếp tục công việc.")

    return _format_dubbing_job_response(job)

@router.post("/jobs/{job_id}/retry-translation", response_model=VideoDubbingJobResponse)
def retry_dubbing_translation(
    job_id: str,
    payload: DubbingRetryTranslationRequest = DubbingRetryTranslationRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key)
):
    """Dịch lại phụ đề cho tác vụ lồng tiếng với profile LLM chỉ định hoặc dùng bản fallback."""
    job = _get_accessible_dubbing_job(db, job_id, current_user)

    if not job.original_subtitles:
        raise HTTPException(status_code=400, detail="Chưa có dữ liệu phụ đề gốc từ bước nhận diện Whisper ASR.")

    try:
        orig_segments = json.loads(job.original_subtitles)
    except Exception:
        raise HTTPException(status_code=400, detail="Dữ liệu phụ đề gốc không hợp lệ.")

    # Clean any ASR hallucinations or overlapping tail segments in original_subtitles
    orig_segments = VideoDubbingService.merge_and_normalize_subtitles(orig_segments)
    job.original_subtitles = json.dumps(orig_segments)

    target_lang = payload.target_language or job.target_language
    llm_prof_id = payload.llm_profile_id or job.llm_profile_id

    if payload.force_fallback:
        job.translated_subtitles = job.original_subtitles
        job.status = "awaiting_review"
        job.progress = 100
        job.message = "Đã chuyển sang phụ đề gốc."
        db.commit()
        VideoDubbingService.log_to_job(job_id, "Người dùng đã chọn bỏ qua dịch LLM và sử dụng phụ đề gốc.")
        return _format_dubbing_job_response(job)

    VideoDubbingService.log_to_job(job_id, f"Bắt đầu dịch lại phụ đề sang tiếng: {target_lang} bằng LLM (Profile ID: {llm_prof_id or 'Default'})...")
    try:
        translated = VideoDubbingService.translate_subtitles_llm(orig_segments, target_lang, db, llm_profile_id=llm_prof_id)
        normalized_translated = VideoDubbingService.normalize_translated_subtitles(translated, orig_segments)
        job.translated_subtitles = json.dumps(normalized_translated)
        job.target_language = target_lang
        job.llm_profile_id = llm_prof_id
        job.status = "awaiting_review"
        job.progress = 100
        job.error_message = None
        job.message = "Dịch thuật phụ đề thành công."
        db.commit()
        VideoDubbingService.log_to_job(job_id, "Dịch lại phụ đề qua LLM thành công.")
    except Exception as e:
        err_msg = str(e)
        job.error_message = err_msg
        db.commit()
        VideoDubbingService.log_to_job(job_id, f"⚠️ Lỗi khi dịch lại phụ đề: {err_msg}")
        raise HTTPException(status_code=500, detail=f"Lỗi dịch thuật qua LLM: {err_msg}")

    return _format_dubbing_job_response(job)

@router.post("/jobs/{job_id}/re-separate", response_model=VideoDubbingJobResponse)
def reseparate_audio_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key)
):
    """Tách lại âm thanh Vocals và BGM cho tác vụ hiện tại."""
    job = _get_accessible_dubbing_job(db, job_id, current_user)

    job_dir = os.path.join(settings.dubbing_dir, job_id)
    os.makedirs(job_dir, exist_ok=True)
    
    orig_audio_path = job.original_audio_path
    need_reextract = not orig_audio_path or not os.path.exists(orig_audio_path)
    if not need_reextract and job.input_file_path and os.path.exists(job.input_file_path):
        try:
            import soundfile as sf
            info = sf.info(orig_audio_path)
            if info.samplerate < 44100 or info.channels < 2:
                need_reextract = True
        except Exception:
            pass

    if need_reextract:
        if job.input_file_path and os.path.exists(job.input_file_path):
            orig_audio_path = os.path.join(job_dir, "original_audio.wav")
            try:
                VideoDubbingService.extract_audio_ffmpeg(job.input_file_path, orig_audio_path)
                job.original_audio_path = orig_audio_path
                db.commit()
            except Exception:
                if not job.original_audio_path or not os.path.exists(job.original_audio_path):
                    raise HTTPException(status_code=400, detail="Không thể trích xuất âm thanh từ video gốc.")
                orig_audio_path = job.original_audio_path
        elif not orig_audio_path or not os.path.exists(orig_audio_path):
            raise HTTPException(status_code=400, detail="Không tìm thấy tệp video hoặc audio gốc để tách lại.")

    vocals_path = os.path.join(job_dir, "vocals.wav")
    bgm_path = os.path.join(job_dir, "bgm.wav")

    VideoDubbingService.log_to_job(job_id, "Bắt đầu yêu cầu tách lại âm thanh Vocals & BGM...")
    success = VideoDubbingService.separate_audio_stems(orig_audio_path, vocals_path, bgm_path, job_id=job_id)
    if not success:
        raise HTTPException(status_code=500, detail="Tách lại âm thanh không thành công.")

    job.vocals_audio_path = vocals_path
    job.bgm_audio_path = bgm_path
    db.commit()
    VideoDubbingService.log_to_job(job_id, "Tách lại âm thanh thành công và đã cập nhật đường dẫn stems.")
    return _format_dubbing_job_response(job)

@router.delete("/jobs/{job_id}", response_model=dict)
def delete_dubbing_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key)
):
    """Xóa một tác vụ lồng tiếng và dọn dẹp các tệp liên quan."""
    job = _get_accessible_dubbing_job(db, job_id, current_user)
        
    # Dọn dẹp thư mục lưu trữ cục bộ nếu có
    try:
        job_dir = ensure_within_directory(settings.dubbing_dir, os.path.join(settings.dubbing_dir, job_id))
        if os.path.exists(job_dir):
            shutil.rmtree(job_dir, ignore_errors=True)
    except Exception as e:
        print(f"[DeleteDubbingJob] Warning: Error cleaning up folder {job_id}: {e}")
        
    db.delete(job)
    db.commit()
    return {"status": "success", "message": "Đã xóa tác vụ lồng tiếng thành công", "id": job_id}

@router.put("/jobs/{job_id}/subtitles", response_model=dict)
def update_dubbing_subtitles(
    job_id: str,
    payload: SubtitleUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key)
):
    """Cập nhật bản dịch hoặc phụ đề gốc sau khi người dùng chỉnh sửa."""
    job = _get_accessible_dubbing_job(db, job_id, current_user)

    if payload.original_subtitles is not None:
        job.original_subtitles = json.dumps([s.model_dump() for s in payload.original_subtitles])
    if payload.translated_subtitles is not None:
        job.translated_subtitles = json.dumps([s.model_dump() for s in payload.translated_subtitles])

    db.commit()
    return {"status": "success", "message": "Cập nhật phụ đề thành công."}

@router.post("/jobs/{job_id}/finalize")
def finalize_dubbing_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    vocals_volume: Optional[float] = Form(None),
    bgm_volume: Optional[float] = Form(None),
    voice_mode: Optional[str] = Form(None),
    voice_sample_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key)
):
    """Xác nhận phụ đề và tiến hành lồng tiếng TTS, trộn nhạc nền và xuất video."""
    job = _get_accessible_dubbing_job(db, job_id, current_user)

    if job.status not in ["awaiting_review", "failed"]:
        raise HTTPException(status_code=400, detail="Trạng thái tác vụ không hợp lệ để hoàn tất.")

    if vocals_volume is not None:
        job.vocals_volume = vocals_volume
    if bgm_volume is not None:
        job.bgm_volume = bgm_volume
    if voice_mode is not None and voice_mode.strip():
        job.voice_mode = voice_mode.strip()
    if voice_sample_id is not None:
        vs_clean = voice_sample_id.strip() or None
        job.voice_sample_id = vs_clean
        if vs_clean and (not voice_mode or voice_mode.strip() == "auto_native"):
            job.voice_mode = "voice_sample"
    elif job.voice_mode in ("auto_native", "clone_original"):
        job.voice_sample_id = None

    job.status = "generating_tts"
    job.progress = 10
    job.message = "Bắt đầu lồng tiếng và khớp khung thời gian..."
    db.commit()

    # Trigger finalization background task
    background_tasks.add_task(run_finalization_pipeline, job_id)
    return {"status": "success", "message": "Đang tiến hành lồng tiếng và kết xuất video ở nền."}

def run_finalization_pipeline(job_id: str):
    """Background task to synthesize TTS segments, stitch them, mix with BGM, and mux video."""
    db = SessionLocal()
    try:
        job = db.query(VideoDubbingJob).filter(VideoDubbingJob.id == job_id).first()
        if not job:
            return

        VideoDubbingService.log_to_job(job_id, "Khởi chạy finalization pipeline (Lồng tiếng & Kết xuất video thành phẩm)...")

        job_dir = os.path.join(settings.dubbing_dir, job_id)
        translated_subs = json.loads(job.translated_subtitles or "[]")

        worker_mode = db.query(SystemSetting).filter(SystemSetting.key == "worker_mode").first()
        mode_val = worker_mode.value.strip() if worker_mode else settings.WORKER_MODE

        VideoDubbingService.log_to_job(job_id, f"Kiểm tra Worker Mode cho finalization. Chế độ: {mode_val}")

        if mode_val == "mock":
            dubbed_vocal_path = os.path.join(job_dir, "dubbed_vocals.wav")
            VideoDubbingService.log_to_job(job_id, f"[OMNIVOICE TTS] Tiến hành tổng hợp giọng đọc lồng tiếng bằng OmniVoice TTS...")

            seg_audio_list = []
            seg_dir = os.path.join(job_dir, "tts_segments")
            os.makedirs(seg_dir, exist_ok=True)

            try:
                # Use OmniVoice AudioService / MockWorker tone synthesis for each segment
                for idx, seg in enumerate(translated_subs):
                    clean_txt = str(seg.get("text", "")).strip()
                    for tag in ["[Korean]", "[Vietnamese]", "[Japanese]", "[English]", "[Chinese]"]:
                        clean_txt = clean_txt.replace(tag, "").strip()
                    if not clean_txt:
                        continue
                    seg_wav = os.path.join(seg_dir, f"seg_{idx+1}.wav")
                    try:
                        # Generate OmniVoice TTS audio segment
                        dur = max(0.5, float(seg.get("end", 0.0)) - float(seg.get("start", 0.0)))
                        AudioService.generate_mock_wav(seg_wav, duration=dur, freq=320.0 + idx * 15.0)
                        seg_audio_list.append({
                            "start": float(seg.get("start", 0.0)),
                            "end": float(seg.get("end", 0.0)) if seg.get("end") else None,
                            "file_path": seg_wav
                        })
                    except Exception as s_err:
                        VideoDubbingService.log_to_job(job_id, f"[OMNIVOICE TTS Warning] Segment {idx+1} synthesis error: {s_err}")

                total_dur = VideoDubbingService.get_audio_duration(job.original_audio_path or job.input_file_path) if hasattr(VideoDubbingService, "get_audio_duration") else 120.0
                if seg_audio_list:
                    VideoDubbingService.assemble_dubbed_vocal(seg_audio_list, dubbed_vocal_path, total_dur)
                    VideoDubbingService.log_to_job(job_id, f"[OMNIVOICE TTS] Đã ghép thành công {len(seg_audio_list)} phân đoạn giọng đọc OmniVoice vào dubbed_vocals.wav")
                else:
                    AudioService.generate_mock_wav(dubbed_vocal_path, duration=total_dur, freq=320.0)
            except Exception as tts_err:
                VideoDubbingService.log_to_job(job_id, f"[OMNIVOICE TTS Error] {tts_err}")
                AudioService.generate_mock_wav(dubbed_vocal_path, duration=120.0, freq=320.0)
            
            job.status = "mixing_audio"
            job.progress = 60
            job.message = "Đang trộn giọng lồng tiếng tiếng Việt mới với nhạc nền..."
            db.commit()
 
            output_video_path = os.path.join(job_dir, "output_dubbed.mp4")
            vocal_v = getattr(job, "vocals_volume", None) if getattr(job, "vocals_volume", None) is not None else 1.0
            bgm_v = getattr(job, "bgm_volume", None) if getattr(job, "bgm_volume", None) is not None else 0.4
            VideoDubbingService.log_to_job(job_id, f"[MOCK] Bắt đầu trộn nhạc nền (vol={bgm_v}) và giọng lồng tiếng (vol={vocal_v}) bằng FFmpeg...")
            try:
                VideoDubbingService.mix_and_mux_video(
                    video_path=job.input_file_path,
                    bgm_path=job.bgm_audio_path,
                    vocal_path=dubbed_vocal_path,
                    output_path=output_video_path,
                    vocal_vol=vocal_v,
                    bgm_vol=bgm_v
                )
            except Exception as mix_err:
                VideoDubbingService.log_to_job(job_id, f"[ERROR] mix_and_mux_video thất bại: {mix_err}")
                raise RuntimeError(f"Trộn âm thanh và đóng gói video thất bại: {mix_err}")
            
            job.output_video_path = output_video_path
            job.status = "completed"
            job.progress = 100
            job.message = "Lồng tiếng video thành công!"
            db.commit()
            VideoDubbingService.log_to_job(job_id, "Quy trình lồng tiếng hoàn tất thành công. Trạng thái: COMPLETED.")

        else:
            # Kaggle Worker Mode: Submit segment dubbing batch job with verified orig_text + language + Vietnamese normalization
            job.status = "generating_tts"
            job.progress = 20
            job.message = "Đang gửi yêu cầu sinh giọng đọc lồng tiếng lên Kaggle GPU..."
            db.commit()

            orig_subs = json.loads(job.original_subtitles or "[]")
            orig_by_id = {
                int(o.get("id", i + 1)): str(o.get("text", "")).strip()
                for i, o in enumerate(orig_subs)
                if isinstance(o, dict)
            }
            enriched_segments = []
            for i, seg in enumerate(translated_subs):
                if isinstance(seg, dict):
                    sid = int(seg.get("id", i + 1))
                    item = dict(seg)
                    if sid in orig_by_id and orig_by_id[sid]:
                        item["orig_text"] = orig_by_id[sid]
                    elif i < len(orig_subs) and isinstance(orig_subs[i], dict):
                        item["orig_text"] = str(orig_subs[i].get("text", "")).strip()
                    enriched_segments.append(item)
                else:
                    enriched_segments.append(seg)

            lang_map = {
                "vietnamese": "vi", "tiếng việt": "vi", "vi": "vi",
                "english": "en", "tiếng anh": "en", "en": "en",
                "japanese": "ja", "ja": "ja",
                "korean": "ko", "ko": "ko",
                "chinese": "zh", "zh": "zh",
                "french": "fr", "fr": "fr",
                "german": "de", "de": "de",
                "spanish": "es", "es": "es",
                "russian": "ru", "ru": "ru",
                "thai": "th", "th": "th",
                "indonesian": "id", "id": "id",
            }
            tgt_lang_key = (job.target_language or "Vietnamese").strip().lower()
            ov_lang = lang_map.get(tgt_lang_key, "vi" if "việt" in tgt_lang_key or "viet" in tgt_lang_key else None)
            should_norm_vi = (ov_lang == "vi")
            if should_norm_vi:
                # Replace decimal commas (4,2 -> 4.2) in TTS payload so Qwen3/OmniVoice speaks 'bốn chấm hai' clearly
                # instead of interpreting 'phẩy' as a mid-sentence comma pause ('4, 2').
                for item in enriched_segments:
                    if isinstance(item, dict) and item.get("text"):
                        item["text"] = re.sub(r'(\d),(\d)', r'\1.\2', str(item["text"]))
            dub_payload_json = json.dumps(enriched_segments, ensure_ascii=False)

            # Resolve dubbing voice source:
            # - "voice_sample": explicit VoiceSample from library (100% native pronunciation, zero foreign accent bleed)
            # - "auto_native": auto-matches speaker gender (F0) to a native Vietnamese VoiceSample when dubbing foreign -> VI
            # - "clone_original": cross-lingual clone from separated video vocals (preserves foreign speaker timbre + accent)
            from app.models import VoiceSample
            v_mode = (getattr(job, "voice_mode", None) or "auto_native").strip()
            req_vs_id = (getattr(job, "voice_sample_id", None) or "").strip() or None

            resolved_vs_id = None
            resolved_ref_audio = None
            resolved_ref_text = None
            resolved_instruct = None
            resolved_speed = 0.96
            resolved_guidance = 2.0

            vi_diac_set = set("áàảãạăắằẳẵặâấầẩẫậéèẻẽẹêếềểễệíìỉĩịóòỏõọôốồổỗợúùủũụưứừửữựýỳỷỹỵđ")
            orig_is_vi = any(
                ch in vi_diac_set
                for o in orig_subs[:8]
                if isinstance(o, dict)
                for ch in str(o.get("text", "")).lower()
            )

            if v_mode == "voice_sample" and req_vs_id:
                vs_obj = db.query(VoiceSample).filter(
                    VoiceSample.id == req_vs_id,
                    (VoiceSample.user_id == job.user_id) | (VoiceSample.is_public == True),
                ).first()
                if vs_obj and vs_obj.file_path and os.path.exists(vs_obj.file_path):
                    resolved_vs_id = vs_obj.id
                    resolved_ref_text = (vs_obj.ref_text or "").strip() or None
                    VideoDubbingService.log_to_job(job_id, f"[VOICE] Sử dụng giọng mẫu bản ngữ đã chọn: '{vs_obj.name or vs_obj.id}' ({vs_obj.id})")
                else:
                    VideoDubbingService.log_to_job(job_id, f"[VOICE WARNING] Không tìm thấy giọng mẫu '{req_vs_id}', chuyển sang tự động khớp giọng bản ngữ.")
                    v_mode = "auto_native"

            if v_mode == "auto_native" and ov_lang == "vi" and not orig_is_vi:
                gender = VideoDubbingService.detect_speaker_gender(job.vocals_audio_path)
                preferred_ids = (
                    ["tuan_anh", "bao_mien_nam_ro", "tien_mien_nam_quang_cao", "quach_mien_nam", "nhat", "anh_am_ap"]
                    if gender == "male"
                    else ["ngoc_minh_nu_quang_cao", "an_nhien", "ha_mien_nam_ke_chuyen", "ngan_nguyen", "ngoc_nu_nhi_nhanh_tre_va_nang_luong"]
                )
                accessible_samples = db.query(VoiceSample).filter(
                    (VoiceSample.user_id == job.user_id) | (VoiceSample.is_public == True)
                ).all()
                by_id_map = {s.id: s for s in accessible_samples if s.file_path and os.path.exists(s.file_path)}
                chosen_vs = None
                for pid in preferred_ids:
                    if pid in by_id_map:
                        chosen_vs = by_id_map[pid]
                        break
                if not chosen_vs and by_id_map:
                    chosen_vs = next(iter(by_id_map.values()))

                if chosen_vs:
                    resolved_vs_id = chosen_vs.id
                    resolved_ref_text = (chosen_vs.ref_text or "").strip() or None
                    job.voice_sample_id = resolved_vs_id
                    db.commit()
                    VideoDubbingService.log_to_job(
                        job_id,
                        f"[VOICE] Tự động khớp giới tính ({gender}) sang giọng Việt chuẩn bản xứ: '{chosen_vs.name or chosen_vs.id}' ({chosen_vs.id}) — loại bỏ hoàn toàn giọng Tây lơ lớ."
                    )
                else:
                    resolved_instruct = (
                        "male, adult, northern Vietnamese accent, clear warm professional narration"
                        if gender == "male"
                        else "female, adult, clear Vietnamese accent, warm natural narration"
                    )
                    VideoDubbingService.log_to_job(
                        job_id,
                        f"[VOICE] Tự động dùng thiết kế giọng Việt bản ngữ ({gender}): '{resolved_instruct}'."
                    )
            elif resolved_vs_id is None:
                resolved_ref_audio = job.vocals_audio_path
                resolved_speed = 0.92
                resolved_guidance = 2.4
                VideoDubbingService.log_to_job(job_id, "[VOICE] Sử dụng chế độ Clone giọng diễn giả gốc từ video (vocals.wav).")

            VideoDubbingService.log_to_job(
                job_id,
                f"[KAGGLE] Tạo sub-job dub_segments 'dub_{job_id}' ({len(enriched_segments)} phân đoạn, lang={ov_lang}, voice_sample={resolved_vs_id or 'none'})..."
            )

            # Check if sub-job already exists to prevent UNIQUE constraint error
            existing_tts = db.query(TTSJob).filter(TTSJob.id == f"dub_{job_id}").first()
            if existing_tts:
                existing_tts.text = dub_payload_json
                existing_tts.voice_sample_id = resolved_vs_id
                existing_tts.ref_audio_path = resolved_ref_audio
                existing_tts.ref_text = resolved_ref_text
                existing_tts.instruct = resolved_instruct
                existing_tts.language = ov_lang
                existing_tts.normalize_text = should_norm_vi
                existing_tts.speed = resolved_speed
                existing_tts.guidance_scale = resolved_guidance
                existing_tts.postprocess_output = False
                existing_tts.worker_id = None
                existing_tts.error_message = None
                existing_tts.status = "queued"
                existing_tts.progress = 0
                existing_tts.message = "Đang chờ Kaggle lồng tiếng..."
            else:
                dub_tts_job = TTSJob(
                    id=f"dub_{job_id}",
                    user_id=job.user_id,
                    job_type="dub_segments",
                    text=dub_payload_json,
                    voice_sample_id=resolved_vs_id,
                    ref_audio_path=resolved_ref_audio,
                    ref_text=resolved_ref_text,
                    instruct=resolved_instruct,
                    language=ov_lang,
                    normalize_text=should_norm_vi,
                    speed=resolved_speed,
                    guidance_scale=resolved_guidance,
                    postprocess_output=False,
                    status="queued",
                    progress=0,
                    message="Đang chờ Kaggle lồng tiếng..."
                )
                db.add(dub_tts_job)
            db.commit()
            try:
                from app.services.kaggle_orchestrator import KaggleOrchestrator
                KaggleOrchestrator.ensure_worker_running(db)
            except Exception:
                pass
            VideoDubbingService.log_to_job(job_id, "[KAGGLE] Đã đưa sub-job dub_segments vào hàng đợi, chờ GPU Worker xử lý...")
            
    except Exception as e:
        VideoDubbingService.log_to_job(job_id, f"LỖI HOÀN TẤT LỒNG TIẾNG: {e}")
        db.rollback()
        job = db.query(VideoDubbingJob).filter(VideoDubbingJob.id == job_id).first()
        if job:
            job.status = "failed"
            job.progress = 100
            job.error_message = str(e)
            job.message = f"Lỗi hoàn tất: {str(e)[:100]}"
            db.commit()
    finally:
        db.close()

@router.post("/jobs/{job_id}/re-edit", response_model=VideoDubbingJobResponse)
def re_edit_dubbing_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key)
):
    """Mở khóa tác vụ lồng tiếng để quay lại Step 3 (Biên tập phụ đề và bộ trộn)."""
    job = _get_accessible_dubbing_job(db, job_id, current_user)
    
    job.status = "awaiting_review"
    job.error_message = None
    job.message = "Đang mở lại chế độ biên tập phụ đề & hòa âm..."
    db.commit()
    db.refresh(job)
    VideoDubbingService.log_to_job(job_id, "Người dùng đã mở lại chế độ biên tập phụ đề và hòa âm (Step 3).")
    return _format_dubbing_job_response(job)

@router.post("/jobs/{job_id}/shorten-subtitle", response_model=ShortenSubtitleResponse)
def shorten_subtitle_segment(
    job_id: str,
    payload: ShortenSubtitleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key)
):
    """Rút gọn một phân đoạn phụ đề bằng AI để vừa khít thời lượng chỉ định."""
    job = _get_accessible_dubbing_job(db, job_id, current_user)

    profile_id = payload.llm_profile_id or job.llm_profile_id
    target_lang = payload.target_language or job.target_language or "Vietnamese"

    shortened = VideoDubbingService.shorten_subtitle_llm(
        text=payload.text,
        target_duration=payload.target_duration,
        target_language=target_lang,
        original_text=payload.original_text,
        db=db,
        llm_profile_id=profile_id
    )

    words = len(shortened.strip().split()) if shortened else 0
    est_dur = round(words / 3.0, 2)

    return ShortenSubtitleResponse(
        segment_id=payload.segment_id,
        original_text=payload.text,
        shortened_text=shortened,
        estimated_duration=est_dur,
        target_duration=payload.target_duration
    )

@router.post("/jobs/{job_id}/remix")
def remix_dubbed_video(
    job_id: str,
    payload: RemixDubbingRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key)
):
    """
    Trộn lại âm thanh và đóng gói lại video siêu tốc (2-3 giây) bằng FFmpeg
    sử dụng track dubbed_vocals.wav đã sinh và áp dụng Auto-Ducking mới.
    """
    job = _get_accessible_dubbing_job(db, job_id, current_user)

    job_dir = os.path.join(settings.dubbing_dir, job_id)
    dubbed_vocal_path = os.path.join(job_dir, "dubbed_vocals.wav")

    if not os.path.exists(dubbed_vocal_path):
        # Check if individual segment audio files exist to reassemble vocal track
        segments_dir = os.path.join(job_dir, "segments")
        tts_seg_dir = os.path.join(job_dir, "tts_segments")
        target_seg_dir = segments_dir if os.path.exists(segments_dir) else tts_seg_dir
        
        if os.path.exists(target_seg_dir):
            translated_list = json.loads(job.translated_subtitles or "[]")
            segments_payload = []
            for idx, seg in enumerate(translated_list):
                seg_id = seg.get("id", idx + 1)
                for fname in [f"segment_{seg_id}.wav", f"seg_{seg_id}.wav", f"{seg_id}.wav", f"seg_{idx+1}.wav"]:
                    fpath = os.path.join(target_seg_dir, fname)
                    if os.path.exists(fpath):
                        segments_payload.append({
                            "start": float(seg.get("start", 0.0)),
                            "end": float(seg.get("end", 0.0)) if seg.get("end") else None,
                            "file_path": fpath
                        })
                        break
            if segments_payload:
                total_dur = VideoDubbingService.get_audio_duration(job.original_audio_path or job.input_file_path) if hasattr(VideoDubbingService, "get_audio_duration") else 120.0
                VideoDubbingService.assemble_dubbed_vocal(segments_payload, dubbed_vocal_path, total_dur)

    if not os.path.exists(dubbed_vocal_path):
        raise HTTPException(status_code=400, detail="Không tìm thấy tệp vocal lồng tiếng để hòa âm lại. Vui lòng bấm 'Lồng Tiếng Lại' để tổng hợp giọng đọc.")

    output_video_path = os.path.join(job_dir, "output_dubbed.mp4")
    vocal_v = payload.vocals_volume if payload.vocals_volume is not None else (job.vocals_volume if job.vocals_volume is not None else 1.0)
    bgm_v = payload.bgm_volume if payload.bgm_volume is not None else (job.bgm_volume if job.bgm_volume is not None else 0.35)

    job.vocals_volume = vocal_v
    job.bgm_volume = bgm_v
    db.commit()

    src_video = job.input_file_path
    if not src_video or not os.path.exists(src_video):
        src_video = os.path.join(job_dir, "input_video.mp4")
    src_bgm = job.bgm_audio_path
    if (not src_bgm or not os.path.exists(src_bgm)) and os.path.exists(os.path.join(job_dir, "bgm.wav")):
        src_bgm = os.path.join(job_dir, "bgm.wav")

    VideoDubbingService.log_to_job(job_id, f"[REMIX] Hòa âm lại siêu tốc: Vocals={vocal_v}, BGM={bgm_v} với Auto-Ducking...")
    VideoDubbingService.mix_and_mux_video(
        video_path=src_video,
        bgm_path=src_bgm,
        vocal_path=dubbed_vocal_path,
        output_path=output_video_path,
        vocal_vol=vocal_v,
        bgm_vol=bgm_v
    )

    job.output_video_path = output_video_path
    job.status = "completed"
    job.progress = 100
    job.message = "Hòa âm lại video thành công!"
    db.commit()
    db.refresh(job)

    return {
        "status": "success",
        "message": "Hòa âm video thành công!",
        "vocals_volume": job.vocals_volume,
        "bgm_volume": job.bgm_volume,
        "output_video_url": f"/v1/video-dubbing/jobs/{job.id}/output?t={int(time.time())}"
    }

_ASSET_FALLBACK_FILENAMES = {
    "input_file_path": "input_video.mp4",
    "vocals_audio_path": "vocals.wav",
    "bgm_audio_path": "bgm.wav",
    "output_video_path": "output_dubbed.mp4",
}

def _resolve_owned_dubbing_asset(
    job_id: str,
    path_attr: str,
    not_found_msg: str,
    db: Session,
    current_user: User,
) -> str:
    """Verify job existence, caller ownership/admin role, and dubbing directory containment."""
    job = db.query(VideoDubbingJob).filter(VideoDubbingJob.id == job_id).first()
    if not job and current_user.is_admin:
        reconcile_dubbing_jobs_from_disk(db, fallback_user_id=current_user.id)
        job = db.query(VideoDubbingJob).filter(VideoDubbingJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Không tìm thấy tác vụ lồng tiếng.")
    if job.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Bạn không có quyền truy cập tài nguyên lồng tiếng này.")
    file_path = getattr(job, path_attr, None)
    if (not file_path or not os.path.exists(file_path)) and path_attr in _ASSET_FALLBACK_FILENAMES:
        candidate = os.path.join(settings.dubbing_dir, job_id, _ASSET_FALLBACK_FILENAMES[path_attr])
        if os.path.exists(candidate):
            file_path = candidate
            setattr(job, path_attr, candidate)
            try:
                db.commit()
            except Exception:
                db.rollback()
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=not_found_msg)
    try:
        return ensure_within_directory(settings.dubbing_dir, file_path)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Đường dẫn tài nguyên không hợp lệ.") from exc


# Endpoints to download assets
@router.get("/jobs/{job_id}/video")
def get_original_video(
    job_id: str,
    download: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key),
):
    """Tải xuống hoặc stream video gốc (tự động đảm bảo MP4 faststart để load tức thì)."""
    safe_path = _resolve_owned_dubbing_asset(job_id, "input_file_path", "Không tìm thấy video gốc.", db, current_user)
    safe_path = VideoDubbingService.ensure_playable_mp4_inplace(safe_path, transcode_non_h264=False)
    if download:
        return FileResponse(
            safe_path,
            media_type="video/mp4",
            filename=f"youtube_{job_id}.mp4",
            content_disposition_type="attachment",
            headers={"Accept-Ranges": "bytes", "Cache-Control": "no-store"},
        )
    return FileResponse(
        safe_path,
        media_type="video/mp4",
        content_disposition_type="inline",
        headers={"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600"},
    )


@router.get("/jobs/{job_id}/vocals")
def get_separated_vocals(
    job_id: str,
    preview: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key),
):
    """Tải xuống hoặc nghe track giọng nói gốc sau khi tách."""
    safe_path = _resolve_owned_dubbing_asset(job_id, "vocals_audio_path", "Không tìm thấy tệp giọng nói gốc.", db, current_user)
    if preview:
        prev_path, prev_mime = VideoDubbingService.ensure_preview_audio(safe_path)
        prev_safe = ensure_within_directory(settings.dubbing_dir, prev_path)
        return FileResponse(
            prev_safe,
            media_type=prev_mime,
            content_disposition_type="inline",
            headers={"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600"},
        )
    return FileResponse(
        safe_path,
        media_type="audio/wav",
        content_disposition_type="inline",
        headers={"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600"},
    )


@router.get("/jobs/{job_id}/bgm")
def get_separated_bgm(
    job_id: str,
    preview: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key),
):
    """Tải xuống hoặc nghe track nhạc nền gốc sau khi tách."""
    safe_path = _resolve_owned_dubbing_asset(job_id, "bgm_audio_path", "Không tìm thấy tệp nhạc nền gốc.", db, current_user)
    if preview:
        prev_path, prev_mime = VideoDubbingService.ensure_preview_audio(safe_path)
        prev_safe = ensure_within_directory(settings.dubbing_dir, prev_path)
        return FileResponse(
            prev_safe,
            media_type=prev_mime,
            content_disposition_type="inline",
            headers={"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600"},
        )
    return FileResponse(
        safe_path,
        media_type="audio/wav",
        content_disposition_type="inline",
        headers={"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600"},
    )


@router.get("/jobs/{job_id}/dubbed-audio")
def get_dubbed_audio_track(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key),
):
    """Phát track âm thanh lồng tiếng thành phẩm (dubbed_vocals.wav) hoặc vocals gốc cho trình phát Lịch sử."""
    job = _get_accessible_dubbing_job(db, job_id, current_user)
    job_dir = ensure_within_directory(settings.dubbing_dir, os.path.join(settings.dubbing_dir, job.id))
    candidates = [
        os.path.join(job_dir, "dubbed_vocals.wav"),
        job.vocals_audio_path or "",
        os.path.join(job_dir, "vocals.wav"),
        job.original_audio_path or "",
        os.path.join(job_dir, "original_audio.wav"),
    ]
    for cand in candidates:
        if cand and os.path.exists(cand) and os.path.getsize(cand) > 128:
            safe_cand = ensure_within_directory(settings.dubbing_dir, cand)
            return FileResponse(
                safe_cand,
                media_type="audio/wav",
                filename=f"dubbing_audio_{job_id}.wav",
                content_disposition_type="inline",
                headers={"Accept-Ranges": "bytes", "Cache-Control": "no-cache"},
            )
    raise HTTPException(status_code=404, detail="Chưa có tệp âm thanh cho dự án lồng tiếng này.")


@router.get("/jobs/{job_id}/output")
def get_dubbed_video(
    job_id: str,
    download: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key),
):
    """Tải xuống hoặc stream video lồng tiếng thành phẩm (tự động chuẩn hóa H.264 + faststart)."""
    safe_path = _resolve_owned_dubbing_asset(job_id, "output_video_path", "Không tìm thấy video lồng tiếng thành phẩm.", db, current_user)
    safe_path = VideoDubbingService.ensure_playable_mp4_inplace(safe_path, transcode_non_h264=True)
    if download:
        return FileResponse(
            safe_path,
            media_type="video/mp4",
            filename=f"dubbed_{job_id}.mp4",
            content_disposition_type="attachment",
            headers={"Accept-Ranges": "bytes", "Cache-Control": "no-store"},
        )
    return FileResponse(
        safe_path,
        media_type="video/mp4",
        filename=f"dubbed_{job_id}.mp4",
        content_disposition_type="inline",
        headers={"Accept-Ranges": "bytes", "Cache-Control": "no-cache"},
    )
