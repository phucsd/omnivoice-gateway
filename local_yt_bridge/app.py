import hmac
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
from typing import Any, Dict, Optional

# Strictly disable outbound bridge calls inside the local bridge server process
os.environ["YT_BRIDGE_SERVER_MODE"] = "1"

_BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_BRIDGE_DIR)
_BACKEND_DIR = os.path.join(_REPO_ROOT, "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import settings
from app.services.r2_bridge_client import R2GatewayClient
from app.services.video_dubbing_service import VideoDubbingService

_SECRETS_FILE = os.path.join(_BRIDGE_DIR, ".bridge_secrets.json")
_VALID_QUALITIES = {"1080p", "720p", "480p", "360p"}
_TASK_RETENTION_SECONDS = 7200  # 2 hours


def _load_bridge_secrets() -> Dict[str, str]:
    data: Dict[str, str] = {}
    if os.path.exists(_SECRETS_FILE):
        try:
            with open(_SECRETS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                data = {str(k): str(v) for k, v in raw.items() if v is not None}
        except Exception as e:
            print(f"[YTBridge] Warning: could not load .bridge_secrets.json: {e}", flush=True)

    r2_url = (data.get("r2_url") or os.environ.get("YT_R2_GATEWAY_URL") or settings.YT_R2_GATEWAY_URL or "").strip()
    bridge_secret = (data.get("bridge_secret") or os.environ.get("YT_BRIDGE_SECRET") or settings.YT_BRIDGE_SECRET or "").strip()
    bridge_url = (data.get("bridge_url") or os.environ.get("YT_BRIDGE_URL") or settings.YT_BRIDGE_URL or "").strip()

    if r2_url:
        settings.YT_R2_GATEWAY_URL = r2_url
    if bridge_secret:
        settings.YT_BRIDGE_SECRET = bridge_secret
    if bridge_url:
        settings.YT_BRIDGE_URL = bridge_url

    return {
        "r2_url": r2_url,
        "bridge_secret": bridge_secret,
        "bridge_url": bridge_url,
    }


_BRIDGE_CFG = _load_bridge_secrets()

app = FastAPI(title="OmniVoice Windows YouTube Bridge", version="1.0.0")

_TASKS_LOCK = threading.Lock()
_TASKS: Dict[str, Dict[str, Any]] = {}


class BridgeInfoRequest(BaseModel):
    url: str


class BridgeJobRequest(BaseModel):
    url: str
    preferred_quality: str = "1080p"


def _require_bridge_secret(request: Request) -> None:
    expected = _BRIDGE_CFG.get("bridge_secret") or settings.YT_BRIDGE_SECRET or ""
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bridge secret chưa được cấu hình.",
        )

    candidate = (request.headers.get("X-Bridge-Secret") or "").strip()
    if not candidate:
        auth = request.headers.get("Authorization") or ""
        if auth.startswith("Bearer "):
            candidate = auth[7:].strip()

    if not candidate or not hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Bridge-Secret không hợp lệ.",
        )


def _prune_old_tasks_locked(now: float) -> None:
    expired = [
        tid
        for tid, t in _TASKS.items()
        if t.get("status") in ("completed", "failed") and (now - float(t.get("updated_at") or now)) > _TASK_RETENTION_SECONDS
    ]
    for tid in expired:
        _TASKS.pop(tid, None)


def _update_task(task_id: str, **fields: Any) -> None:
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return
        task.update(fields)
        task["updated_at"] = time.time()


def _run_bridge_download_task(task_id: str) -> None:
    with _TASKS_LOCK:
        task = dict(_TASKS.get(task_id) or {})
    if not task:
        return

    canonical_url = task["canonical_url"]
    video_id = task["video_id"]
    quality = task["preferred_quality"]
    video_key = f"yt/{video_id}_{quality}.mp4"
    meta_key = f"yt/{video_id}_{quality}.meta.json"
    info_key = f"yt/{video_id}.info.json"

    r2 = R2GatewayClient(gateway_url=_BRIDGE_CFG.get("r2_url"), bridge_secret=_BRIDGE_CFG.get("bridge_secret"))
    tmp_dir: Optional[str] = None

    try:
        # 1. Check R2 cache first so parallel/duplicate jobs skip downloading
        if r2.is_configured():
            try:
                head = r2.head_object(video_key)
                if head and head.get("size", 0) > 1024:
                    meta = r2.get_json(meta_key) or {}
                    title = meta.get("title") or f"YouTube Video ({video_id})"
                    _update_task(
                        task_id,
                        status="completed",
                        progress=100,
                        message="Video đã sẵn sàng trên bộ nhớ đệm R2.",
                        r2_key=video_key,
                        title=title,
                        metadata=meta,
                    )
                    return
            except Exception:
                pass

        _update_task(task_id, status="downloading", progress=5, message="Máy Windows đang tải video từ YouTube...")
        tmp_dir = tempfile.mkdtemp(prefix=f"ytb_{task_id}_")
        meta_out: Dict[str, Any] = {}

        def _on_dl_progress(pct: float, msg: str) -> None:
            mapped = 5.0 + (max(0.0, min(100.0, pct)) / 100.0) * 65.0
            _update_task(task_id, status="downloading", progress=int(round(mapped)), message=msg)

        final_mp4, title = VideoDubbingService.download_youtube_video(
            canonical_url,
            tmp_dir,
            preferred_quality=quality,
            progress_callback=_on_dl_progress,
            metadata_out=meta_out,
            allow_remote_bridge=False,
        )

        if not r2.is_configured():
            raise RuntimeError("Chưa cấu hình YT_R2_GATEWAY_URL để đẩy video từ Windows lên R2.")

        _update_task(task_id, status="uploading_r2", progress=72, message="Đã tải xong, đang đẩy MP4 lên Cloudflare R2...")

        def _on_upload_progress(pct: float, msg: str) -> None:
            mapped = 70.0 + (max(0.0, min(100.0, pct)) / 100.0) * 28.0
            _update_task(task_id, status="uploading_r2", progress=int(round(mapped)), message=msg)

        r2.upload_file(final_mp4, video_key, content_type="video/mp4", progress_callback=_on_upload_progress)

        meta_payload = {
            "video_id": video_id,
            "canonical_url": canonical_url,
            "title": title,
            "duration": meta_out.get("duration"),
            "thumbnail": meta_out.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            "channel": meta_out.get("channel"),
            "preferred_quality": quality,
            "size": os.path.getsize(final_mp4),
        }
        r2.put_json(meta_key, meta_payload)

        # Populate info cache opportunistically if absent
        try:
            if not r2.head_object(info_key):
                r2.put_json(
                    info_key,
                    {
                        "video_id": video_id,
                        "canonical_url": canonical_url,
                        "title": title,
                        "channel": meta_payload.get("channel"),
                        "duration": meta_payload.get("duration"),
                        "thumbnail_url": meta_payload.get("thumbnail"),
                        "available_qualities": ["1080p", "720p", "480p", "360p"],
                        "is_live": False,
                    },
                )
        except Exception:
            pass

        _update_task(
            task_id,
            status="completed",
            progress=100,
            message="Đã tải video YouTube và đẩy lên Cloudflare R2 thành công.",
            r2_key=video_key,
            title=title,
            metadata=meta_payload,
        )
    except Exception as exc:
        err_str = str(exc).strip() or type(exc).__name__
        _update_task(
            task_id,
            status="failed",
            progress=100,
            message=f"Lỗi: {err_str[:160]}",
            error=err_str,
        )
    finally:
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


@app.get("/local-ping")
def local_ping() -> Dict[str, Any]:
    return {"ok": True, "mode": "yt_bridge_server"}


@app.get("/v1/health", dependencies=[Depends(_require_bridge_secret)])
def bridge_health() -> Dict[str, Any]:
    js_cli, _ = VideoDubbingService._detect_js_runtimes()
    with _TASKS_LOCK:
        active_count = sum(1 for t in _TASKS.values() if t.get("status") in ("queued", "downloading", "uploading_r2"))
        total_count = len(_TASKS)
    return {
        "ok": True,
        "service": "omnivoice-yt-bridge",
        "js_runtimes": js_cli or "none",
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
        "r2_configured": bool(_BRIDGE_CFG.get("r2_url") and _BRIDGE_CFG.get("bridge_secret")),
        "active_tasks": active_count,
        "tracked_tasks": total_count,
    }


@app.post("/v1/info", dependencies=[Depends(_require_bridge_secret)])
def bridge_video_info(req: BridgeInfoRequest) -> Dict[str, Any]:
    try:
        video_id, canonical_url = VideoDubbingService.normalize_youtube_url(req.url)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    info_key = f"yt/{video_id}.info.json"
    r2 = R2GatewayClient(gateway_url=_BRIDGE_CFG.get("r2_url"), bridge_secret=_BRIDGE_CFG.get("bridge_secret"))
    if r2.is_configured():
        try:
            cached = r2.get_json(info_key)
            if cached and cached.get("video_id") == video_id and cached.get("title"):
                return cached
        except Exception:
            pass

    try:
        info = VideoDubbingService.get_youtube_video_info(canonical_url, allow_remote_bridge=False)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Không thể lấy thông tin video YouTube: {exc}")

    if r2.is_configured():
        try:
            r2.put_json(info_key, info)
        except Exception:
            pass
    return info


@app.post("/v1/jobs", dependencies=[Depends(_require_bridge_secret)])
def create_bridge_job(req: BridgeJobRequest):
    try:
        video_id, canonical_url = VideoDubbingService.normalize_youtube_url(req.url)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    quality = (req.preferred_quality or "1080p").strip().lower()
    if quality not in _VALID_QUALITIES:
        quality = "1080p"

    now = time.time()
    with _TASKS_LOCK:
        _prune_old_tasks_locked(now)
        for existing in _TASKS.values():
            if (
                existing.get("video_id") == video_id
                and existing.get("preferred_quality") == quality
                and existing.get("status") in ("queued", "downloading", "uploading_r2")
            ):
                return JSONResponse(
                    status_code=status.HTTP_202_ACCEPTED,
                    content={
                        "task_id": existing["task_id"],
                        "video_id": video_id,
                        "preferred_quality": quality,
                        "status": existing["status"],
                        "deduplicated": True,
                    },
                )

        task_id = uuid.uuid4().hex[:12]
        _TASKS[task_id] = {
            "task_id": task_id,
            "video_id": video_id,
            "canonical_url": canonical_url,
            "preferred_quality": quality,
            "status": "queued",
            "progress": 1,
            "message": "Đã xếp hàng tải video trên máy Windows...",
            "r2_key": f"yt/{video_id}_{quality}.mp4",
            "title": None,
            "metadata": {},
            "error": None,
            "created_at": now,
            "updated_at": now,
        }

    t = threading.Thread(target=_run_bridge_download_task, args=(task_id,), daemon=True)
    t.start()

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "task_id": task_id,
            "video_id": video_id,
            "preferred_quality": quality,
            "status": "queued",
            "deduplicated": False,
        },
    )


@app.get("/v1/jobs/{task_id}", dependencies=[Depends(_require_bridge_secret)])
def get_bridge_job(task_id: str) -> Dict[str, Any]:
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Không tìm thấy bridge task.")
        return dict(task)
