import json
import os
import re
import time
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from app.config import settings

_R2_KEY_RE = re.compile(r"^yt/[A-Za-z0-9._-]{1,180}$")
_SINGLE_UPLOAD_LIMIT_BYTES = 40 * 1024 * 1024  # 40 MB
_MULTIPART_CHUNK_BYTES = 20 * 1024 * 1024     # 20 MB


def _validate_r2_key(key: str) -> str:
    clean = (key or "").strip().lstrip("/")
    if not _R2_KEY_RE.match(clean) or ".." in clean:
        raise ValueError(f"Khoá R2 không hợp lệ: {key!r}")
    return clean


class R2GatewayClient:
    """HTTP client talking to the omnivoice-yt-r2 Worker (yt-r2.oloka.net)."""

    def __init__(
        self,
        gateway_url: Optional[str] = None,
        bridge_secret: Optional[str] = None,
        timeout: int = 20,
    ) -> None:
        self._gateway_url = gateway_url
        self._bridge_secret = bridge_secret
        self.timeout = timeout

    @property
    def base_url(self) -> str:
        raw = self._gateway_url if self._gateway_url is not None else settings.YT_R2_GATEWAY_URL
        return (raw or "").strip().rstrip("/")

    @property
    def secret(self) -> str:
        raw = self._bridge_secret if self._bridge_secret is not None else settings.YT_BRIDGE_SECRET
        return (raw or "").strip()

    def is_configured(self) -> bool:
        return bool(self.base_url and self.secret)

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        hdrs = {"Authorization": f"Bearer {self.secret}"}
        if extra:
            hdrs.update(extra)
        return hdrs

    def check_health(self) -> Dict[str, Any]:
        if not self.is_configured():
            return {"configured": False, "online": False, "latency_ms": 0, "detail": "Chưa cấu hình YT_R2_GATEWAY_URL"}
        t0 = time.perf_counter()
        try:
            res = requests.get(f"{self.base_url}/health", timeout=4, allow_redirects=False)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            if res.status_code == 200:
                return {"configured": True, "online": True, "latency_ms": latency_ms, "detail": f"R2 Gateway OK ({latency_ms}ms)"}
            return {
                "configured": True,
                "online": False,
                "latency_ms": latency_ms,
                "detail": f"R2 Gateway HTTP {res.status_code}",
            }
        except Exception as e:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            return {"configured": True, "online": False, "latency_ms": latency_ms, "detail": f"R2 không phản hồi: {str(e)[:120]}"}

    def head_object(self, key: str) -> Optional[Dict[str, Any]]:
        if not self.is_configured():
            return None
        clean_key = _validate_r2_key(key)
        res = requests.head(
            f"{self.base_url}/objects/{clean_key}",
            headers=self._headers(),
            timeout=6,
            allow_redirects=False,
        )
        if res.status_code == 404:
            return None
        if res.status_code != 200:
            raise RuntimeError(f"R2 HEAD thất bại (HTTP {res.status_code})")
        return {
            "key": clean_key,
            "size": int(res.headers.get("Content-Length") or res.headers.get("X-Object-Size") or 0),
            "etag": res.headers.get("ETag"),
            "content_type": res.headers.get("Content-Type"),
        }

    def get_json(self, key: str) -> Optional[Dict[str, Any]]:
        if not self.is_configured():
            return None
        clean_key = _validate_r2_key(key)
        res = requests.get(
            f"{self.base_url}/objects/{clean_key}",
            headers=self._headers(),
            timeout=8,
            allow_redirects=False,
        )
        if res.status_code == 404:
            return None
        if res.status_code != 200:
            raise RuntimeError(f"R2 GET JSON thất bại (HTTP {res.status_code})")
        data = res.json()
        return data if isinstance(data, dict) else None

    def put_json(self, key: str, data: Dict[str, Any]) -> Dict[str, Any]:
        if not self.is_configured():
            raise RuntimeError("Chưa cấu hình R2 Gateway.")
        clean_key = _validate_r2_key(key)
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        res = requests.put(
            f"{self.base_url}/objects/{clean_key}",
            data=raw,
            headers=self._headers({
                "Content-Type": "application/json",
                "Content-Length": str(len(raw)),
            }),
            timeout=self.timeout,
            allow_redirects=False,
        )
        if res.status_code >= 400:
            raise RuntimeError(f"R2 PUT JSON thất bại (HTTP {res.status_code}): {res.text[:160]}")
        return res.json()

    def delete_object(self, key: str) -> None:
        if not self.is_configured():
            return
        clean_key = _validate_r2_key(key)
        res = requests.delete(
            f"{self.base_url}/objects/{clean_key}",
            headers=self._headers(),
            timeout=10,
            allow_redirects=False,
        )
        if res.status_code >= 400 and res.status_code != 404:
            raise RuntimeError(f"R2 DELETE thất bại (HTTP {res.status_code})")

    def upload_file(
        self,
        file_path: str,
        key: str,
        content_type: str = "video/mp4",
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> Dict[str, Any]:
        if not self.is_configured():
            raise RuntimeError("Chưa cấu hình R2 Gateway.")
        clean_key = _validate_r2_key(key)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Không tìm thấy tệp upload lên R2: {file_path}")

        file_size = os.path.getsize(file_path)
        if file_size <= 0:
            raise ValueError("Tệp video rỗng, không thể tải lên R2.")

        if file_size <= _SINGLE_UPLOAD_LIMIT_BYTES:
            if progress_callback:
                progress_callback(10.0, f"Đang tải video ({file_size / (1024 * 1024):.1f} MB) lên Cloudflare R2...")
            with open(file_path, "rb") as f:
                res = requests.put(
                    f"{self.base_url}/objects/{clean_key}",
                    data=f,
                    headers=self._headers({
                        "Content-Type": content_type,
                        "Content-Length": str(file_size),
                    }),
                    timeout=(10, 120),
                    allow_redirects=False,
                )
            if res.status_code >= 400:
                raise RuntimeError(f"Upload R2 thất bại (HTTP {res.status_code}): {res.text[:180]}")
            if progress_callback:
                progress_callback(100.0, "Đã đẩy video lên Cloudflare R2 thành công.")
            return res.json()

        # Multipart upload for files > 40 MB
        q_key = urllib.parse.quote(clean_key, safe="")
        create_res = requests.post(
            f"{self.base_url}/mpu/create?key={q_key}",
            headers=self._headers({"Content-Type": content_type}),
            timeout=15,
            allow_redirects=False,
        )
        if create_res.status_code >= 400:
            raise RuntimeError(f"R2 createMultipartUpload thất bại (HTTP {create_res.status_code})")
        upload_id = create_res.json()["uploadId"]

        uploaded_parts: List[Dict[str, Any]] = []
        sent_bytes = 0
        try:
            with open(file_path, "rb") as f:
                part_number = 1
                while True:
                    chunk = f.read(_MULTIPART_CHUNK_BYTES)
                    if not chunk:
                        break
                    part_etag = None
                    last_err = None
                    for attempt in range(3):
                        try:
                            p_res = requests.put(
                                f"{self.base_url}/mpu/part?key={q_key}&uploadId={urllib.parse.quote(upload_id, safe='')}&partNumber={part_number}",
                                data=chunk,
                                headers=self._headers({
                                    "Content-Type": "application/octet-stream",
                                    "Content-Length": str(len(chunk)),
                                }),
                                timeout=(10, 120),
                                allow_redirects=False,
                            )
                            if p_res.status_code >= 400:
                                raise RuntimeError(f"HTTP {p_res.status_code}: {p_res.text[:120]}")
                            part_etag = p_res.json()["etag"]
                            break
                        except Exception as err:
                            last_err = err
                            time.sleep(1.0 * (attempt + 1))
                    if not part_etag:
                        raise RuntimeError(f"Upload phần #{part_number} lên R2 thất bại: {last_err}")

                    uploaded_parts.append({"partNumber": part_number, "etag": part_etag})
                    sent_bytes += len(chunk)
                    pct = min(98.0, (sent_bytes / file_size) * 100.0)
                    if progress_callback:
                        progress_callback(pct, f"Đang đẩy video lên Cloudflare R2 ({pct:.0f}%)...")
                    part_number += 1

            comp_res = requests.post(
                f"{self.base_url}/mpu/complete?key={q_key}&uploadId={urllib.parse.quote(upload_id, safe='')}",
                json={"parts": uploaded_parts},
                headers=self._headers(),
                timeout=30,
                allow_redirects=False,
            )
            if comp_res.status_code >= 400:
                raise RuntimeError(f"R2 completeMultipartUpload thất bại (HTTP {comp_res.status_code}): {comp_res.text[:160]}")
            if progress_callback:
                progress_callback(100.0, "Đã đẩy video lên Cloudflare R2 thành công.")
            return comp_res.json()
        except Exception:
            try:
                requests.post(
                    f"{self.base_url}/mpu/abort?key={q_key}&uploadId={urllib.parse.quote(upload_id, safe='')}",
                    headers=self._headers(),
                    timeout=10,
                    allow_redirects=False,
                )
            except Exception:
                pass
            raise

    def download_object(
        self,
        key: str,
        dest_path: str,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> str:
        if not self.is_configured():
            raise RuntimeError("Chưa cấu hình R2 Gateway.")
        clean_key = _validate_r2_key(key)
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        part_path = f"{dest_path}.part"

        res = requests.get(
            f"{self.base_url}/objects/{clean_key}",
            headers=self._headers(),
            stream=True,
            timeout=(10, 180),
            allow_redirects=False,
        )
        if res.status_code == 404:
            raise FileNotFoundError(f"Không tìm thấy object {clean_key} trên R2.")
        if res.status_code >= 400:
            raise RuntimeError(f"Tải video từ R2 thất bại (HTTP {res.status_code})")

        total = int(res.headers.get("Content-Length") or 0)
        downloaded = 0
        last_report = 0.0
        try:
            with open(part_path, "wb") as f:
                for chunk in res.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()
                    if progress_callback and total > 0 and (now - last_report >= 0.4 or downloaded >= total):
                        pct = min(99.0, (downloaded / total) * 100.0)
                        progress_callback(pct, f"Đang tải video từ Cloudflare R2 ({pct:.0f}%)...")
                        last_report = now
            os.replace(part_path, dest_path)
            if progress_callback:
                progress_callback(100.0, "Đã tải video từ Cloudflare R2.")
            return dest_path
        except Exception:
            if os.path.exists(part_path):
                try:
                    os.remove(part_path)
                except Exception:
                    pass
            raise


class RemoteYouTubeBridgeClient:
    """Async control-plane client for the Windows Tunnel Bridge + R2 data-plane cache."""

    @staticmethod
    def is_server_mode() -> bool:
        return os.environ.get("YT_BRIDGE_SERVER_MODE", "").strip() == "1"

    @staticmethod
    def is_enabled() -> bool:
        if RemoteYouTubeBridgeClient.is_server_mode():
            return False
        return bool(
            (settings.YT_BRIDGE_URL or "").strip()
            and (settings.YT_BRIDGE_SECRET or "").strip()
            and (settings.YT_R2_GATEWAY_URL or "").strip()
        )

    @staticmethod
    def _bridge_base_url() -> str:
        return (settings.YT_BRIDGE_URL or "").strip().rstrip("/")

    @staticmethod
    def _auth_headers() -> Dict[str, str]:
        headers = {"X-Bridge-Secret": (settings.YT_BRIDGE_SECRET or "").strip()}
        cid = (settings.CF_ACCESS_CLIENT_ID or "").strip()
        csec = (settings.CF_ACCESS_CLIENT_SECRET or "").strip()
        if cid and csec:
            headers["CF-Access-Client-Id"] = cid
            headers["CF-Access-Client-Secret"] = csec
        return headers

    @staticmethod
    def _request(method: str, path: str, timeout: int = 8, **kwargs) -> requests.Response:
        base = RemoteYouTubeBridgeClient._bridge_base_url()
        if not base:
            raise RuntimeError("Chưa cấu hình YT_BRIDGE_URL.")
        headers = RemoteYouTubeBridgeClient._auth_headers()
        extra_headers = kwargs.pop("headers", None)
        if extra_headers:
            headers.update(extra_headers)
        res = requests.request(
            method,
            f"{base}{path}",
            headers=headers,
            timeout=timeout,
            allow_redirects=False,
            **kwargs,
        )
        if res.status_code in (301, 302, 303, 307, 308, 401, 403):
            raise PermissionError(
                f"Xác thực Cloudflare Access / Windows Bridge thất bại (HTTP {res.status_code})."
            )
        return res

    @staticmethod
    def get_bridge_diagnostics() -> Dict[str, Any]:
        r2_info = R2GatewayClient().check_health()
        server_mode = RemoteYouTubeBridgeClient.is_server_mode()
        configured = bool(
            (settings.YT_BRIDGE_URL or "").strip()
            and (settings.YT_BRIDGE_SECRET or "").strip()
            and (settings.YT_R2_GATEWAY_URL or "").strip()
        )
        if server_mode or not configured:
            return {
                "configured": configured,
                "server_mode": server_mode,
                "enabled": False,
                "bridge_url": settings.YT_BRIDGE_URL or None,
                "r2_gateway_url": settings.YT_R2_GATEWAY_URL or None,
                "bridge_online": False,
                "bridge_latency_ms": 0,
                "bridge_detail": "Đang chạy ở chế độ Bridge Server" if server_mode else "Chưa cấu hình Windows Bridge",
                "r2_online": r2_info.get("online", False),
                "r2_latency_ms": r2_info.get("latency_ms", 0),
                "r2_detail": r2_info.get("detail", ""),
            }

        t0 = time.perf_counter()
        bridge_online = False
        bridge_detail = ""
        try:
            res = RemoteYouTubeBridgeClient._request("GET", "/v1/health", timeout=4)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            if res.status_code == 200:
                bridge_online = True
                bridge_detail = f"Windows Bridge Online ({latency_ms}ms)"
            else:
                bridge_detail = f"Windows Bridge HTTP {res.status_code}"
        except PermissionError as pe:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            bridge_detail = str(pe)
        except Exception as e:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            bridge_detail = f"Windows Bridge Offline ({str(e)[:100]})"

        return {
            "configured": configured,
            "server_mode": server_mode,
            "enabled": True,
            "bridge_url": RemoteYouTubeBridgeClient._bridge_base_url(),
            "r2_gateway_url": (settings.YT_R2_GATEWAY_URL or "").strip().rstrip("/"),
            "bridge_online": bridge_online,
            "bridge_latency_ms": latency_ms,
            "bridge_detail": bridge_detail,
            "r2_online": r2_info.get("online", False),
            "r2_latency_ms": r2_info.get("latency_ms", 0),
            "r2_detail": r2_info.get("detail", ""),
        }

    @staticmethod
    def fetch_video_info(canonical_url: str, video_id: str) -> Dict[str, Any]:
        if RemoteYouTubeBridgeClient.is_server_mode():
            raise RuntimeError("Bridge client disabled in server mode.")

        r2 = R2GatewayClient()
        if r2.is_configured():
            try:
                cached = r2.get_json(f"yt/{video_id}.info.json")
                cached_quals = (cached.get("available_qualities") or []) if isinstance(cached, dict) else []
                if (
                    cached
                    and cached.get("video_id") == video_id
                    and cached.get("title")
                    and len(cached_quals) > 1
                    and cached.get("quality_schema_v") == 2
                ):
                    return cached
            except Exception:
                pass

        if not RemoteYouTubeBridgeClient.is_enabled():
            raise RuntimeError("Remote Windows Bridge chưa được kích hoạt.")

        res = RemoteYouTubeBridgeClient._request(
            "POST",
            "/v1/info",
            json={"url": canonical_url},
            timeout=15,
        )
        if res.status_code >= 400:
            detail = ""
            try:
                detail = res.json().get("detail", "")
            except Exception:
                detail = res.text[:160]
            raise RuntimeError(f"Windows Bridge /v1/info thất bại (HTTP {res.status_code}): {detail}")
        return res.json()

    @staticmethod
    def download_via_bridge(
        canonical_url: str,
        video_id: str,
        output_dir: str,
        preferred_quality: str = "1080p",
        progress_callback: Optional[Callable[[float, str], None]] = None,
        metadata_out: Optional[Dict[str, Any]] = None,
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> Tuple[str, str]:
        if RemoteYouTubeBridgeClient.is_server_mode():
            raise RuntimeError("Bridge client disabled in server mode.")

        from app.services.video_dubbing_service import VideoDubbingService

        def _emit_log(msg: str) -> None:
            if log_fn:
                log_fn(msg)

        quality = VideoDubbingService.normalize_preferred_quality(preferred_quality)
        r2 = R2GatewayClient()
        video_key = f"yt/{video_id}_{quality}.mp4"
        meta_key = f"yt/{video_id}_{quality}.meta.json"
        mp4_target = os.path.join(output_dir, "input_video.mp4")

        def _scaled_progress(start_pct: float, end_pct: float) -> Optional[Callable[[float, str], None]]:
            if not progress_callback:
                return None

            def _cb(inner_pct: float, msg: str) -> None:
                scaled = start_pct + (max(0.0, min(100.0, inner_pct)) / 100.0) * (end_pct - start_pct)
                progress_callback(scaled, msg)

            return _cb

        # 1. Check 24h Cloudflare R2 cache first (works even if Windows PC is offline)
        if r2.is_configured():
            try:
                head = r2.head_object(video_key)
                if head and head.get("size", 0) > 1024:
                    _emit_log(f"[R2 Cache Hit] Tìm thấy {video_key} ({head['size']} bytes) trên Cloudflare R2.")
                    r2.download_object(video_key, mp4_target, progress_callback=_scaled_progress(10.0, 96.0))
                    meta = {}
                    try:
                        meta = r2.get_json(meta_key) or {}
                    except Exception:
                        pass
                    title = meta.get("title") or f"YouTube Video ({video_id})"
                    if metadata_out is not None and meta:
                        metadata_out.update(meta)
                    final_path = VideoDubbingService._finalize_downloaded_media(output_dir)
                    if progress_callback:
                        progress_callback(100.0, "Đã tải video từ bộ nhớ đệm R2 thành công.")
                    return final_path, title
            except PermissionError:
                raise
            except Exception as cache_err:
                _emit_log(f"[R2 Cache] Chưa có sẵn hoặc bỏ qua cache: {str(cache_err)[:140]}")

        # 2. Dispatch async job to Windows Bridge via Cloudflare Access + Tunnel
        if not RemoteYouTubeBridgeClient.is_enabled():
            raise RuntimeError("Windows Bridge chưa được cấu hình đầy đủ.")

        _emit_log(f"[Windows Bridge] Gửi yêu cầu tải {canonical_url} ({quality}) qua Tunnel yt-bridge.oloka.net...")
        if progress_callback:
            progress_callback(5.0, "Đang gửi yêu cầu tải tới máy Windows (Cloudflare Tunnel)...")

        res = RemoteYouTubeBridgeClient._request(
            "POST",
            "/v1/jobs",
            json={"url": canonical_url, "preferred_quality": quality},
            timeout=8,
        )
        if res.status_code not in (200, 202):
            detail = ""
            try:
                detail = res.json().get("detail", "")
            except Exception:
                detail = res.text[:180]
            raise RuntimeError(f"Windows Bridge từ chối tác vụ (HTTP {res.status_code}): {detail}")

        task_data = res.json()
        task_id = task_data["task_id"]
        _emit_log(f"[Windows Bridge] Đã nhận task_id={task_id}. Bắt đầu theo dõi tiến độ...")

        bridge_budget = float(VideoDubbingService.download_timeout_for_quality(quality))
        deadline = time.monotonic() + bridge_budget
        last_state_msg = ""
        while time.monotonic() < deadline:
            time.sleep(1.5)
            poll_res = RemoteYouTubeBridgeClient._request(
                "GET",
                f"/v1/jobs/{task_id}",
                timeout=8,
            )
            if poll_res.status_code >= 400:
                raise RuntimeError(f"Lỗi theo dõi tác vụ Windows Bridge (HTTP {poll_res.status_code})")

            t_info = poll_res.json()
            t_status = t_info.get("status")
            t_msg = t_info.get("message") or ""
            t_prog = float(t_info.get("progress") or 0.0)

            if t_msg and t_msg != last_state_msg:
                _emit_log(f"[Windows Bridge] ({t_status} {t_prog:.0f}%) {t_msg}")
                last_state_msg = t_msg

            if progress_callback and t_status in ("queued", "downloading", "uploading_r2"):
                mapped = 5.0 + (max(0.0, min(100.0, t_prog)) / 100.0) * 75.0
                progress_callback(mapped, t_msg or "Máy Windows đang tải video YouTube...")

            if t_status == "completed":
                r2_key = t_info.get("r2_key") or video_key
                meta = t_info.get("metadata") or {}
                title = meta.get("title") or t_info.get("title") or f"YouTube Video ({video_id})"
                if metadata_out is not None and meta:
                    metadata_out.update(meta)

                _emit_log(f"[Windows Bridge] Hoàn tất tải lên R2 ({r2_key}). Hugging Face đang kéo MP4 từ R2...")
                r2.download_object(r2_key, mp4_target, progress_callback=_scaled_progress(80.0, 98.0))
                final_path = VideoDubbingService._finalize_downloaded_media(output_dir)
                if progress_callback:
                    progress_callback(100.0, "Đã nhận video từ Cloudflare R2 và kiểm tra MP4 thành công.")
                return final_path, title

            if t_status == "failed":
                raise RuntimeError(t_info.get("error") or "Windows Bridge báo lỗi khi tải YouTube.")

        raise TimeoutError(f"Windows Bridge quá thời gian chờ {int(bridge_budget)} giây.")
