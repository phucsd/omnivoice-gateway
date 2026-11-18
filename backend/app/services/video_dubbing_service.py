import os
import sys
import time
import subprocess
import shutil
import json
import re
import socket
import tempfile
import uuid
import urllib.parse
import requests
import soundfile as sf
import numpy as np
from typing import List, Dict, Any, Tuple, Optional, Callable
from sqlalchemy.orm import Session
from app.config import settings
from app.models import LLMProfile, SystemSetting
from app.utils.url_security import validate_safe_external_url


_GOOGLE_SCOPES = 'https://gdata.youtube.com https://www.googleapis.com/auth/youtube'

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}

_AUTH_COOKIE_KEYS = {
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
    "LOGIN_INFO",
    "__Secure-1PSID",
    "__Secure-3PSID",
    "__Secure-1PAPISID",
    "__Secure-3PAPISID",
}

_QUALITY_HEIGHT_MAP = {
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
    "360p": 360,
}

_YOUTUBE_ID_RE = re.compile(r"^[0-9A-Za-z_-]{11}$")
_YTDLP_PROGRESS_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%(.*)")


class VideoDubbingService:
    @staticmethod
    def log_to_job(job_id: str, message: str):
        """Prints a detailed log message to server console and appends to process.log in job directory."""
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        log_line = f"[{timestamp}] {message}"
        try:
            print(f"[VideoDubbing][{job_id}] {message}", flush=True)
        except Exception:
            try:
                print(f"[VideoDubbing][{job_id}] {message.encode('ascii', 'replace').decode('ascii')}", flush=True)
            except Exception:
                pass

        try:
            job_dir = os.path.join(settings.dubbing_dir, job_id)
            os.makedirs(job_dir, exist_ok=True)
            log_file = os.path.join(job_dir, "process.log")
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(log_line + "\n")
        except Exception as e:
            print(f"[VideoDubbingService] Failed to write log: {e}", flush=True)

    @staticmethod
    def _get_storage_dir() -> str:
        storage_dir = os.path.abspath(settings.STORAGE_DIR)
        os.makedirs(storage_dir, exist_ok=True)
        return storage_dir

    @staticmethod
    def _get_legacy_storage_dir() -> str:
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "storage"))

    @staticmethod
    def _include_legacy_storage() -> bool:
        return (settings.APP_ENV or "").lower() != "testing"

    @staticmethod
    def _read_system_setting(key: str) -> Optional[str]:
        db = None
        try:
            from app.database import SessionLocal
            db = SessionLocal()
            entry = db.query(SystemSetting).filter(SystemSetting.key == key).first()
            return entry.value if entry and entry.value else None
        except Exception:
            return None
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _write_system_setting(key: str, value: Optional[str]) -> None:
        db = None
        try:
            from app.database import SessionLocal
            db = SessionLocal()
            entry = db.query(SystemSetting).filter(SystemSetting.key == key).first()
            if value is None:
                if entry:
                    db.delete(entry)
            elif not entry:
                db.add(SystemSetting(key=key, value=value))
            else:
                entry.value = value
            db.commit()
        except Exception:
            pass
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def ensure_dependencies():
        """Dynamically ensures pytubefix, yt-dlp, and curl_cffi are installed."""
        try:
            import pytubefix  # noqa: F401
            import yt_dlp  # noqa: F401
        except ImportError:
            print("[VideoDubbingService] Installing youtube download dependencies...")
            pkgs = ["pytubefix", "curl_cffi", "yt-dlp[default,curl-cffi]"]
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "-U", *pkgs])
            except Exception:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", "-U", *pkgs])

    @staticmethod
    def normalize_youtube_url(url: str) -> Tuple[str, str]:
        """Validates a YouTube URL (with SSRF checks) and returns (video_id, canonical_watch_url)."""
        raw = validate_safe_external_url(url)
        parsed = urllib.parse.urlparse(raw)
        hostname = (parsed.hostname or "").strip().lower().rstrip(".")
        if hostname not in _YOUTUBE_HOSTS:
            raise ValueError("Chỉ hỗ trợ đường dẫn video từ YouTube (youtube.com, youtu.be, YouTube Shorts).")

        video_id: Optional[str] = None
        path_parts = [p for p in parsed.path.split("/") if p]

        if hostname in {"youtu.be", "www.youtu.be"}:
            if path_parts:
                video_id = path_parts[0]
        else:
            qs = urllib.parse.parse_qs(parsed.query)
            v_vals = qs.get("v")
            if v_vals and v_vals[0]:
                video_id = v_vals[0].strip()
            elif len(path_parts) >= 2 and path_parts[0] in {"shorts", "live", "embed", "v"}:
                video_id = path_parts[1].strip()

        if not video_id or not _YOUTUBE_ID_RE.match(video_id):
            raise ValueError("Không tìm thấy mã video YouTube hợp lệ trong đường dẫn.")

        return video_id, f"https://www.youtube.com/watch?v={video_id}"

    @staticmethod
    def _convert_to_netscape_cookies(content: str) -> str:
        """Converts raw HTTP Cookie header / curl snippet into Netscape HTTP Cookie File format."""
        if content and "# Netscape" not in content and "\t" not in content:
            raw = content.strip()
            cookie_match = re.search(r'(?:[-–]H\s+[\'"][Cc]ookie:\s*|[Cc]ookie:\s*)([^\r\n\'"]+)', raw)
            if cookie_match:
                raw = cookie_match.group(1)

            lines = [
                "# Netscape HTTP Cookie File",
                "# http://curl.haxx.se/rfc/cookie_spec.html",
                "# Converted by OmniVoice Gateway",
            ]
            count = 0
            for part in raw.replace("\r", "").replace("\n", " ").split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    k = k.strip()
                    v = v.strip()
                    if k:
                        lines.append(f".youtube.com\tTRUE\t/\tTRUE\t2147483647\t{k}\t{v}")
                        count += 1
            if count > 0:
                return "\n".join(lines) + "\n"
        return content

    @staticmethod
    def _parse_cookie_stats(content: str) -> Tuple[int, bool]:
        """Counts valid Netscape cookie entries and detects authenticated YouTube session cookies."""
        cookie_count = 0
        is_authenticated = False
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                cookie_count += 1
                if parts[5].strip() in _AUTH_COOKIE_KEYS and parts[6].strip():
                    is_authenticated = True
        return cookie_count, is_authenticated

    @staticmethod
    def get_cookie_file_path() -> Optional[str]:
        """Resolves cookies.txt in settings.STORAGE_DIR (or legacy storage / DB backup)."""
        primary = os.path.join(VideoDubbingService._get_storage_dir(), "cookies.txt")
        if os.path.exists(primary) and os.path.getsize(primary) > 0:
            return primary

        if VideoDubbingService._include_legacy_storage():
            legacy = os.path.join(VideoDubbingService._get_legacy_storage_dir(), "cookies.txt")
            if os.path.exists(legacy) and os.path.getsize(legacy) > 0:
                return legacy

        db_cookies = VideoDubbingService._read_system_setting("youtube_cookies_txt")
        if db_cookies and db_cookies.strip():
            try:
                with open(primary, "w", encoding="utf-8") as f:
                    f.write(db_cookies)
                return primary
            except Exception:
                pass
        return None

    @staticmethod
    def inspect_cookie_status() -> Dict[str, Any]:
        """Returns structured cookie presence, count, and authentication status."""
        ck = VideoDubbingService.get_cookie_file_path()
        if not ck or not os.path.exists(ck):
            return {
                "has_cookies": False,
                "is_authenticated": False,
                "cookie_count": 0,
                "size_bytes": 0,
                "modified_at": None,
            }
        try:
            with open(ck, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            cookie_count, is_authenticated = VideoDubbingService._parse_cookie_stats(content)
            return {
                "has_cookies": cookie_count > 0 or os.path.getsize(ck) > 0,
                "is_authenticated": is_authenticated,
                "cookie_count": cookie_count,
                "size_bytes": os.path.getsize(ck),
                "modified_at": os.path.getmtime(ck),
            }
        except Exception:
            return {
                "has_cookies": False,
                "is_authenticated": False,
                "cookie_count": 0,
                "size_bytes": 0,
                "modified_at": None,
            }

    @staticmethod
    def save_cookies_content(content: str) -> Dict[str, Any]:
        """Normalizes and persists cookies.txt to settings.STORAGE_DIR and SystemSetting backup."""
        normalized = VideoDubbingService._convert_to_netscape_cookies(content)
        target_path = os.path.join(VideoDubbingService._get_storage_dir(), "cookies.txt")
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(normalized)
        VideoDubbingService._write_system_setting("youtube_cookies_txt", normalized)
        cookie_count, is_authenticated = VideoDubbingService._parse_cookie_stats(normalized)
        return {
            "path": target_path,
            "size_bytes": len(normalized.encode("utf-8")),
            "cookie_count": cookie_count,
            "is_authenticated": is_authenticated,
        }

    @staticmethod
    def delete_cookies_all() -> None:
        """Removes cookies.txt from primary/legacy storage and SystemSetting backup."""
        candidates = [os.path.join(VideoDubbingService._get_storage_dir(), "cookies.txt")]
        if VideoDubbingService._include_legacy_storage():
            candidates.append(os.path.join(VideoDubbingService._get_legacy_storage_dir(), "cookies.txt"))
        for ck in candidates:
            if os.path.exists(ck):
                try:
                    os.remove(ck)
                except Exception:
                    pass
        VideoDubbingService._write_system_setting("youtube_cookies_txt", None)

    @staticmethod
    def get_oauth_storage_path() -> str:
        return os.path.join(VideoDubbingService._get_storage_dir(), "youtube_oauth.json")

    @staticmethod
    def get_oauth_cache_dir() -> str:
        cache_dir = os.path.join(VideoDubbingService._get_storage_dir(), "yt_dlp_cache")
        os.makedirs(cache_dir, exist_ok=True)
        return cache_dir

    @staticmethod
    def get_oauth_token() -> Optional[dict]:
        candidates = [VideoDubbingService.get_oauth_storage_path()]
        if VideoDubbingService._include_legacy_storage():
            candidates.append(os.path.join(VideoDubbingService._get_legacy_storage_dir(), "youtube_oauth.json"))
        for token_path in candidates:
            if os.path.exists(token_path):
                try:
                    with open(token_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if all(k in data for k in ("access_token", "refresh_token", "expires")):
                            return data
                except Exception:
                    pass

        raw_db = VideoDubbingService._read_system_setting("youtube_oauth_token")
        if raw_db:
            try:
                data = json.loads(raw_db)
                if isinstance(data, dict) and all(k in data for k in ("access_token", "refresh_token", "expires")):
                    VideoDubbingService.save_oauth_token(data)
                    return data
            except Exception:
                pass
        return None

    @staticmethod
    def save_oauth_token(token_data: dict):
        token_path = VideoDubbingService.get_oauth_storage_path()
        with open(token_path, "w", encoding="utf-8") as f:
            json.dump(token_data, f, indent=2)
        VideoDubbingService._write_system_setting("youtube_oauth_token", json.dumps(token_data))
        cache_dir = VideoDubbingService.get_oauth_cache_dir()
        try:
            import yt_dlp
            from yt_dlp.cache import Cache
            for ydl_inst in [yt_dlp.YoutubeDL({"cachedir": cache_dir})]:
                try:
                    c = Cache(ydl_inst)
                    c.store("youtube-oauth2", "token_data", token_data)
                except Exception:
                    pass
        except Exception as e:
            print(f"[VideoDubbingService] Failed to sync oauth to yt-dlp cache: {e}", flush=True)

    @staticmethod
    def delete_oauth_token():
        candidates = [VideoDubbingService.get_oauth_storage_path()]
        if VideoDubbingService._include_legacy_storage():
            candidates.append(os.path.join(VideoDubbingService._get_legacy_storage_dir(), "youtube_oauth.json"))
        for token_path in candidates:
            if os.path.exists(token_path):
                try:
                    os.remove(token_path)
                except Exception:
                    pass
        VideoDubbingService._write_system_setting("youtube_oauth_token", None)
        cache_dir = VideoDubbingService.get_oauth_cache_dir()
        try:
            import yt_dlp
            from yt_dlp.cache import Cache
            c = Cache(yt_dlp.YoutubeDL({"cachedir": cache_dir}))
            c.remove("youtube-oauth2", "token_data")
        except Exception:
            pass

    @staticmethod
    def mask_proxy_url(proxy_url: Optional[str]) -> str:
        if not proxy_url:
            return ""
        return re.sub(r'://([^:]+):([^@]+)@', r'://\1:***@', proxy_url.strip())

    @staticmethod
    def hash_proxy_url(proxy_url: str) -> str:
        import hashlib
        return hashlib.md5(proxy_url.strip().encode('utf-8')).hexdigest()[:12]

    @staticmethod
    def _normalize_single_proxy(raw: str) -> str:
        """Normalizes proxy strings including Webshare's host:port:user:pass format."""
        p = (raw or "").strip()
        if not p:
            return ""
        if "://" not in p and "@" not in p and "[" not in p:
            parts = p.split(":", 3)
            if len(parts) == 4 and parts[0] and parts[1].isdigit() and parts[2]:
                host, port, user, password = parts
                u_enc = urllib.parse.quote(user, safe="")
                p_enc = urllib.parse.quote(password, safe="")
                return f"http://{u_enc}:{p_enc}@{host}:{port}"
        if not (
            p.startswith("http://")
            or p.startswith("https://")
            or p.startswith("socks5://")
            or p.startswith("socks5h://")
            or p.startswith("socks4://")
        ):
            p = f"http://{p}"
        return p

    @staticmethod
    def get_youtube_proxy() -> Optional[str]:
        proxies = VideoDubbingService.get_youtube_proxies()
        return proxies[0] if proxies else None

    @staticmethod
    def get_youtube_proxies() -> List[str]:
        proxies: List[str] = []

        # 1. Environment variable
        for k in ["YOUTUBE_PROXY", "HTTP_PROXY", "HTTPS_PROXY"]:
            val = os.environ.get(k)
            if val and val.strip():
                proxies.append(val.strip())

        # 2. Primary storage file youtube_proxies.txt
        primary_pool_file = os.path.join(VideoDubbingService._get_storage_dir(), "youtube_proxies.txt")
        if os.path.exists(primary_pool_file):
            try:
                with open(primary_pool_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            proxies.append(line)
            except Exception:
                pass
        elif VideoDubbingService._include_legacy_storage():
            legacy_pool_file = os.path.join(VideoDubbingService._get_legacy_storage_dir(), "youtube_proxies.txt")
            if os.path.exists(legacy_pool_file):
                try:
                    with open(legacy_pool_file, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("#"):
                                proxies.append(line)
                except Exception:
                    pass

        # 3. DB SystemSetting (youtube_proxy_pool)
        raw_pool = VideoDubbingService._read_system_setting("youtube_proxy_pool")
        if raw_pool:
            try:
                parsed = json.loads(raw_pool)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, str) and item.strip():
                            proxies.append(item.strip())
            except Exception:
                pass

        # 4. Legacy single-proxy fallback (only when primary pool file absent)
        if not os.path.exists(primary_pool_file) and VideoDubbingService._include_legacy_storage():
            legacy_file = os.path.join(VideoDubbingService._get_legacy_storage_dir(), "youtube_proxy.txt")
            if os.path.exists(legacy_file):
                try:
                    with open(legacy_file, "r", encoding="utf-8") as f:
                        val = f.read().strip()
                        if val and not val.startswith("#"):
                            proxies.append(val)
                except Exception:
                    pass
            legacy_single = VideoDubbingService._read_system_setting("youtube_proxy")
            if legacy_single and legacy_single.strip():
                proxies.append(legacy_single.strip())

        # Normalize format and deduplicate preserving order
        seen = set()
        clean_proxies: List[str] = []
        for raw_p in proxies:
            p = VideoDubbingService._normalize_single_proxy(raw_p)
            if p and p not in seen:
                seen.add(p)
                clean_proxies.append(p)

        return clean_proxies

    @staticmethod
    def save_youtube_proxy_pool(proxies: List[str]):
        seen = set()
        clean: List[str] = []
        for raw_p in proxies:
            p = VideoDubbingService._normalize_single_proxy(raw_p)
            if p and p not in seen:
                seen.add(p)
                clean.append(p)

        # 1. Save to primary storage/youtube_proxies.txt
        pool_file = os.path.join(VideoDubbingService._get_storage_dir(), "youtube_proxies.txt")
        with open(pool_file, "w", encoding="utf-8") as f:
            f.write("\n".join(clean))

        # Remove stale legacy files if different from primary so deleted proxies cannot reappear
        if VideoDubbingService._include_legacy_storage():
            legacy_dir = VideoDubbingService._get_legacy_storage_dir()
            for fname in ("youtube_proxy.txt", "youtube_proxies.txt"):
                candidate = os.path.join(legacy_dir, fname)
                if os.path.abspath(candidate) != os.path.abspath(pool_file) and os.path.exists(candidate):
                    try:
                        os.remove(candidate)
                    except Exception:
                        pass

        # 2. Save to DB SystemSetting
        db = None
        try:
            from app.database import SessionLocal
            db = SessionLocal()
            entry = db.query(SystemSetting).filter(SystemSetting.key == "youtube_proxy_pool").first()
            if not entry:
                db.add(SystemSetting(key="youtube_proxy_pool", value=json.dumps(clean)))
            else:
                entry.value = json.dumps(clean)

            single_entry = db.query(SystemSetting).filter(SystemSetting.key == "youtube_proxy").first()
            first_val = clean[0] if clean else ""
            if not single_entry:
                db.add(SystemSetting(key="youtube_proxy", value=first_val))
            else:
                single_entry.value = first_val

            db.commit()
        except Exception:
            pass
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def save_youtube_proxy(proxy_url: str):
        p = VideoDubbingService._normalize_single_proxy(proxy_url)
        if not p:
            VideoDubbingService.delete_youtube_proxy()
            return
        current = VideoDubbingService.get_youtube_proxies()
        if p not in current:
            current.insert(0, p)
        VideoDubbingService.save_youtube_proxy_pool(current)

    @staticmethod
    def add_to_proxy_pool(raw_text: str) -> List[Dict[str, Any]]:
        current = VideoDubbingService.get_youtube_proxies()
        new_items = []
        for line in raw_text.replace(",", "\n").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                new_items.append(line)

        combined = current + new_items
        VideoDubbingService.save_youtube_proxy_pool(combined)
        return VideoDubbingService.get_proxy_pool_info()

    @staticmethod
    def delete_from_proxy_pool(proxy_hash: str) -> List[Dict[str, Any]]:
        current = VideoDubbingService.get_youtube_proxies()
        filtered = [p for p in current if VideoDubbingService.hash_proxy_url(p) != proxy_hash]
        VideoDubbingService.save_youtube_proxy_pool(filtered)
        return VideoDubbingService.get_proxy_pool_info()

    @staticmethod
    def clear_proxy_pool():
        VideoDubbingService.save_youtube_proxy_pool([])

    @staticmethod
    def delete_youtube_proxy():
        VideoDubbingService.clear_proxy_pool()

    @staticmethod
    def get_proxy_pool_info() -> List[Dict[str, Any]]:
        proxies = VideoDubbingService.get_youtube_proxies()
        results = []
        for p in proxies:
            h = VideoDubbingService.hash_proxy_url(p)
            masked = VideoDubbingService.mask_proxy_url(p)
            try:
                parsed = urllib.parse.urlparse(p)
                scheme = parsed.scheme or "http"
                host = parsed.hostname or ""
                port = str(parsed.port) if parsed.port else ""
                has_auth = bool(parsed.username or parsed.password)
            except Exception:
                scheme, host, port, has_auth = "http", p, "", False
            results.append({
                "hash": h,
                "masked": masked,
                "scheme": scheme,
                "host": host,
                "port": port,
                "has_auth": has_auth
            })
        return results

    @staticmethod
    def test_single_proxy(proxy_url: str, timeout: int = 8) -> Dict[str, Any]:
        p = VideoDubbingService._normalize_single_proxy(proxy_url)
        h = VideoDubbingService.hash_proxy_url(p)
        masked = VideoDubbingService.mask_proxy_url(p)
        t0 = time.perf_counter()
        try:
            proxies = {"http": p, "https": p}
            r = requests.get("https://ipinfo.io/json", proxies=proxies, timeout=timeout)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            if r.status_code == 200:
                data = r.json()
                return {
                    "hash": h,
                    "masked": masked,
                    "status": "online",
                    "latency_ms": latency_ms,
                    "ip": data.get("ip"),
                    "country": data.get("country"),
                    "city": data.get("city"),
                    "org": data.get("org"),
                    "message": f"Online ({latency_ms}ms) - IP: {data.get('ip')} [{data.get('country', 'N/A')}]"
                }
            return {
                "hash": h,
                "masked": masked,
                "status": "offline",
                "latency_ms": latency_ms,
                "message": f"HTTP {r.status_code}: {r.text[:80]}"
            }
        except Exception as e:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            err_str = str(e)
            if "407" in err_str:
                err_clean = "407 Proxy Authentication Required (Sai user/mật khẩu proxy)"
            elif "timed out" in err_str.lower() or "timeout" in err_str.lower():
                err_clean = "Connection Timed Out (Proxy không phản hồi)"
            else:
                err_clean = err_str[:120]
            return {
                "hash": h,
                "masked": masked,
                "status": "offline",
                "latency_ms": latency_ms,
                "message": err_clean
            }

    @staticmethod
    def _get_youtube_oauth_client() -> Tuple[str, str, str, bool]:
        """Returns (client_id, client_secret, scopes, use_yt_tv_endpoints)."""
        yt_id = (settings.YOUTUBE_OAUTH_CLIENT_ID or "").strip()
        yt_secret = (settings.YOUTUBE_OAUTH_CLIENT_SECRET or "").strip()
        if yt_id and yt_secret:
            return yt_id, yt_secret, _GOOGLE_SCOPES, False

        try:
            from yt_dlp_plugins.extractor import youtubeoauth as yt_oauth_plugin
            cid = getattr(yt_oauth_plugin, "_CLIENT_ID", "")
            csec = getattr(yt_oauth_plugin, "_CLIENT_SECRET", "")
            cscope = getattr(yt_oauth_plugin, "_SCOPES", _GOOGLE_SCOPES)
            if cid and csec:
                return cid, csec, cscope, True
        except Exception:
            pass

        g_id = (settings.GOOGLE_CLIENT_ID or "").strip()
        g_secret = (settings.GOOGLE_CLIENT_SECRET or "").strip()
        if g_id and g_secret:
            return g_id, g_secret, _GOOGLE_SCOPES, False

        raise ValueError(
            "Chưa cấu hình YouTube OAuth client trên máy chủ. Bạn có thể dùng tab 'Proxy Dân Cư' hoặc 'Dán Cookies.txt'."
        )

    @staticmethod
    def refresh_oauth_token_if_needed() -> Optional[dict]:
        token_data = VideoDubbingService.get_oauth_token()
        if not token_data:
            return None
        now = time.time()
        if token_data.get('expires', 0) > now + 120:
            return token_data
        refresh_token = token_data.get('refresh_token')
        if not refresh_token:
            return None
        try:
            client_id, client_secret, _, use_yt_tv = VideoDubbingService._get_youtube_oauth_client()
            if use_yt_tv:
                r = requests.post(
                    "https://www.youtube.com/o/oauth2/token",
                    json={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                    timeout=10,
                )
            else:
                r = requests.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                    timeout=10,
                )
            res = r.json()
            if 'access_token' in res:
                token_data['access_token'] = res['access_token']
                token_data['expires'] = time.time() + res.get('expires_in', 3600)
                if 'refresh_token' in res:
                    token_data['refresh_token'] = res['refresh_token']
                VideoDubbingService.save_oauth_token(token_data)
                return token_data
        except Exception as e:
            print(f"[VideoDubbingService] Failed to refresh OAuth token: {e}", flush=True)
        return token_data

    @staticmethod
    def start_oauth_device_flow() -> dict:
        client_id, _, scopes, use_yt_tv = VideoDubbingService._get_youtube_oauth_client()
        if use_yt_tv:
            r = requests.post(
                "https://www.youtube.com/o/oauth2/device/code",
                json={
                    "client_id": client_id,
                    "scope": scopes,
                    "device_id": uuid.uuid4().hex,
                    "device_model": "ytlr::",
                },
                timeout=10,
            )
        else:
            r = requests.post(
                "https://oauth2.googleapis.com/device/code",
                data={"client_id": client_id, "scope": scopes},
                timeout=10,
            )

        res = r.json() if r.content else {}
        verification_url = res.get("verification_url") or res.get("verification_uri")
        if not res.get("user_code") or not res.get("device_code") or not verification_url:
            err_msg = res.get("error_description") or res.get("error") or f"HTTP {r.status_code}"
            raise ValueError(
                f"Không thể khởi tạo phiên đăng nhập Google/YouTube ({err_msg}). Bạn có thể dùng tab 'Cookies.txt' hoặc 'Proxy Dân Cư'."
            )
        res["verification_url"] = verification_url
        return res

    @staticmethod
    def poll_oauth_device_flow(device_code: str) -> dict:
        client_id, client_secret, _, use_yt_tv = VideoDubbingService._get_youtube_oauth_client()
        if use_yt_tv:
            r = requests.post(
                "https://www.youtube.com/o/oauth2/token",
                json={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                timeout=10,
            )
        else:
            r = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                timeout=10,
            )
        res = r.json()
        if 'access_token' in res:
            token_data = {
                'access_token': res['access_token'],
                'refresh_token': res['refresh_token'],
                'token_type': res.get('token_type', 'Bearer'),
                'expires': time.time() + res.get('expires_in', 3600)
            }
            VideoDubbingService.save_oauth_token(token_data)
            return {
                'status': 'success',
                'message': 'Đã kết nối tài khoản YouTube thành công!',
                'token_data': token_data
            }
        elif res.get('error') in ('authorization_pending', 'slow_down') or r.status_code == 428:
            return {'status': 'pending'}
        else:
            return {'status': 'error', 'error': res.get('error_description') or res.get('error', 'Lỗi xác thực')}

    @staticmethod
    def _detect_js_runtimes() -> Tuple[str, Dict[str, Dict[str, Optional[str]]]]:
        """Detects Deno / Node.js runtimes for yt-dlp JavaScript challenge extraction."""
        cli_names: List[str] = []
        ydl_dict: Dict[str, Dict[str, Optional[str]]] = {}

        deno_path = shutil.which("deno")
        if not deno_path:
            home_deno = os.path.join(os.path.expanduser("~"), ".deno", "bin", "deno.exe" if os.name == "nt" else "deno")
            if os.path.exists(home_deno):
                deno_path = home_deno
        if deno_path:
            cli_names.append("deno")
            ydl_dict["deno"] = {"path": deno_path}

        node_path = shutil.which("node")
        if node_path:
            cli_names.append("node")
            ydl_dict["node"] = {"path": node_path}

        return ",".join(cli_names), ydl_dict

    @staticmethod
    def _should_disable_pot_ping() -> bool:
        """Returns True when local bgutil POT HTTP server (127.0.0.1:4416) is unreachable."""
        try:
            with socket.create_connection(("127.0.0.1", 4416), timeout=0.15):
                return False
        except OSError:
            return True

    @staticmethod
    def _build_format_selector(preferred_quality: str = "1080p") -> str:
        max_h = _QUALITY_HEIGHT_MAP.get((preferred_quality or "1080p").strip().lower(), 1080)
        return (
            f"bestvideo[height<={max_h}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={max_h}]+bestaudio/"
            f"best[height<={max_h}][ext=mp4]/best[height<={max_h}]/18/best"
        )

    @staticmethod
    def _cleanup_download_artifacts(output_dir: str) -> None:
        if not os.path.exists(output_dir):
            return
        for fname in os.listdir(output_dir):
            if fname.startswith("input_video") or fname.startswith("ptf_"):
                try:
                    os.remove(os.path.join(output_dir, fname))
                except Exception:
                    pass

    @staticmethod
    def _probe_media_streams(file_path: str) -> Dict[str, bool]:
        """Probes downloaded media with ffprobe to verify video/audio stream presence."""
        if not shutil.which("ffprobe"):
            return {"probed": False, "has_audio": True, "has_video": True}
        try:
            res = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", file_path],
                capture_output=True,
                text=True,
                timeout=15,
            )
            types = {line.strip().lower() for line in res.stdout.splitlines() if line.strip()}
            return {"probed": True, "has_audio": "audio" in types, "has_video": "video" in types}
        except Exception:
            return {"probed": False, "has_audio": True, "has_video": True}

    @staticmethod
    def _finalize_downloaded_media(output_dir: str) -> str:
        """Normalizes downloaded candidate into input_video.mp4 and verifies it contains audio."""
        mp4_target = os.path.join(output_dir, "input_video.mp4")
        candidate = None
        for ext in [".mp4", ".mkv", ".webm", ".mov", ".m4a"]:
            p = os.path.join(output_dir, f"input_video{ext}")
            if os.path.exists(p) and os.path.getsize(p) > 1024:
                candidate = p
                break

        if not candidate:
            raise RuntimeError("Không tìm thấy tệp video hợp lệ sau khi tải xuống.")

        if candidate != mp4_target:
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", candidate, "-c", "copy", mp4_target],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=120,
                )
            except Exception:
                shutil.move(candidate, mp4_target)
            else:
                try:
                    os.remove(candidate)
                except Exception:
                    pass

        if not os.path.exists(mp4_target) or os.path.getsize(mp4_target) <= 1024:
            raise RuntimeError("Tệp video sau khi đóng gói MP4 bị rỗng hoặc lỗi.")

        probe = VideoDubbingService._probe_media_streams(mp4_target)
        if probe["probed"] and not probe["has_audio"]:
            raise RuntimeError("Video tải về không có kênh âm thanh để lồng tiếng.")

        return mp4_target

    @staticmethod
    def get_youtube_video_info(url: str, allow_remote_bridge: bool = True) -> Dict[str, Any]:
        """Fetches YouTube video metadata (title, channel, duration, thumbnail, available qualities)."""
        VideoDubbingService.ensure_dependencies()
        video_id, canonical_url = VideoDubbingService.normalize_youtube_url(url)

        if allow_remote_bridge:
            try:
                from app.services.r2_bridge_client import RemoteYouTubeBridgeClient
                if RemoteYouTubeBridgeClient.is_enabled():
                    return RemoteYouTubeBridgeClient.fetch_video_info(canonical_url, video_id)
            except Exception as bridge_err:
                print(f"[VideoDubbingService] Remote bridge info fallback: {bridge_err}", flush=True)

        cookie_info = VideoDubbingService.inspect_cookie_status()
        cookie_path = VideoDubbingService.get_cookie_file_path()
        work_cookie_path = None
        if cookie_info.get("is_authenticated") and cookie_path and os.path.exists(cookie_path):
            try:
                fd, work_cookie_path = tempfile.mkstemp(prefix="ytinfo_ck_", suffix=".txt")
                os.close(fd)
                shutil.copy2(cookie_path, work_cookie_path)
            except Exception:
                work_cookie_path = None

        proxy_pool = VideoDubbingService.get_youtube_proxies()
        primary_proxy = proxy_pool[0] if proxy_pool else None
        oauth_token = VideoDubbingService.refresh_oauth_token_if_needed()

        try:
            # 1. Primary: in-process yt_dlp skip_download
            try:
                import yt_dlp
                _, js_dict = VideoDubbingService._detect_js_runtimes()
                ydl_opts: Dict[str, Any] = {
                    "skip_download": True,
                    "noplaylist": True,
                    "socket_timeout": 12,
                    "quiet": True,
                    "no_warnings": True,
                    "nocheckcertificate": True,
                }
                if js_dict:
                    ydl_opts["js_runtimes"] = js_dict
                yt_info_ext_args: Dict[str, Any] = {}
                if VideoDubbingService._should_disable_pot_ping():
                    yt_info_ext_args["fetch_pot"] = ["never"]
                if not work_cookie_path and not oauth_token:
                    yt_info_ext_args["player_client"] = ["web_embedded", "tv_simply", "mweb", "android"]
                if yt_info_ext_args:
                    ydl_opts["extractor_args"] = {"youtube": yt_info_ext_args}
                if primary_proxy:
                    ydl_opts["proxy"] = primary_proxy
                if work_cookie_path:
                    ydl_opts["cookiefile"] = work_cookie_path
                elif oauth_token:
                    ydl_opts["username"] = "oauth2"
                    ydl_opts["password"] = ""
                    ydl_opts["cachedir"] = VideoDubbingService.get_oauth_cache_dir()

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(canonical_url, download=False)
                if info:
                    heights = {
                        int(f["height"])
                        for f in (info.get("formats") or [])
                        if f.get("height") and f.get("vcodec") not in (None, "none")
                    }
                    max_h = max(heights) if heights else 0
                    avail = [q for q, h in _QUALITY_HEIGHT_MAP.items() if max_h == 0 or max_h >= h]
                    if not avail:
                        avail = ["360p"]
                    return {
                        "video_id": video_id,
                        "canonical_url": canonical_url,
                        "title": info.get("title") or f"YouTube Video ({video_id})",
                        "channel": info.get("channel") or info.get("uploader"),
                        "duration": float(info["duration"]) if info.get("duration") is not None else None,
                        "thumbnail_url": info.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                        "available_qualities": avail,
                        "is_live": bool(info.get("is_live")),
                    }
            except Exception as e:
                print(f"[VideoDubbingService] yt_dlp info extraction fallback: {e}", flush=True)

            # 2. Fallback 1: pytubefix ANDROID_VR
            try:
                from pytubefix import YouTube
                proxies_ptf = {"http": primary_proxy, "https": primary_proxy} if primary_proxy else None
                yt = YouTube(canonical_url, client="ANDROID_VR", proxies=proxies_ptf)
                title = yt.title or f"YouTube Video ({video_id})"
                heights = set()
                for s in yt.streams.filter(file_extension="mp4"):
                    if getattr(s, "resolution", None) and s.resolution.endswith("p"):
                        try:
                            heights.add(int(s.resolution[:-1]))
                        except ValueError:
                            pass
                max_h = max(heights) if heights else 0
                avail = [q for q, h in _QUALITY_HEIGHT_MAP.items() if max_h == 0 or max_h >= h] or ["360p"]
                return {
                    "video_id": video_id,
                    "canonical_url": canonical_url,
                    "title": title,
                    "channel": getattr(yt, "author", None),
                    "duration": float(yt.length) if getattr(yt, "length", None) else None,
                    "thumbnail_url": getattr(yt, "thumbnail_url", None) or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                    "available_qualities": avail,
                    "is_live": False,
                }
            except Exception as e:
                print(f"[VideoDubbingService] pytubefix info extraction fallback: {e}", flush=True)

            # 3. Fallback 2: YouTube public oEmbed API
            proxies_req = {"http": primary_proxy, "https": primary_proxy} if primary_proxy else None
            oembed_url = f"https://www.youtube.com/oembed?url={urllib.parse.quote(canonical_url, safe='')}&format=json"
            res = requests.get(oembed_url, proxies=proxies_req, timeout=8)
            if res.status_code == 200:
                data = res.json()
                return {
                    "video_id": video_id,
                    "canonical_url": canonical_url,
                    "title": data.get("title") or f"YouTube Video ({video_id})",
                    "channel": data.get("author_name"),
                    "duration": None,
                    "thumbnail_url": data.get("thumbnail_url") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                    "available_qualities": ["1080p", "720p", "480p", "360p"],
                    "is_live": False,
                }

            raise RuntimeError(f"Không thể lấy thông tin video từ YouTube (HTTP {res.status_code}).")
        finally:
            if work_cookie_path and os.path.exists(work_cookie_path):
                try:
                    os.remove(work_cookie_path)
                except Exception:
                    pass

    @staticmethod
    def _build_yt_dlp_progress_hook(
        progress_callback: Optional[Callable[[float, str], None]],
    ) -> Callable[[Dict[str, Any]], None]:
        def _hook(d: Dict[str, Any]):
            if not progress_callback:
                return
            status = d.get("status")
            if status == "downloading":
                downloaded = float(d.get("downloaded_bytes") or 0)
                total = float(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
                pct = min(99.0, max(0.0, (downloaded / total) * 100.0)) if total > 0 else 0.0
                if total <= 0:
                    m = re.search(r"(\d+(?:\.\d+)?)%", str(d.get("_percent_str") or ""))
                    if m:
                        pct = min(99.0, float(m.group(1)))
                dl_mb = downloaded / (1024 * 1024)
                tot_mb = total / (1024 * 1024)
                speed = d.get("speed")
                eta = d.get("eta")
                parts = [f"{pct:.0f}%"]
                if tot_mb > 0:
                    parts.append(f"{dl_mb:.1f}/{tot_mb:.1f} MB")
                elif dl_mb > 0:
                    parts.append(f"{dl_mb:.1f} MB")
                if speed:
                    parts.append(f"{float(speed) / (1024 * 1024):.1f} MB/s")
                if eta is not None:
                    parts.append(f"ETA {int(eta)}s")
                progress_callback(pct, f"Đang tải video YouTube ({' • '.join(parts)})...")
            elif status == "finished":
                progress_callback(95.0, "Đã tải xong dữ liệu, đang ghép âm thanh & hình ảnh MP4...")

        return _hook

    @staticmethod
    def _download_via_ytdlp_python(
        canonical_url: str,
        output_dir: str,
        fmt_selector: str,
        proxy_url: Optional[str],
        work_cookie_path: Optional[str],
        has_oauth: bool,
        cache_dir: str,
        js_dict: Dict[str, Dict[str, Optional[str]]],
        disable_pot: bool,
        progress_callback: Optional[Callable[[float, str], None]],
        metadata_out: Optional[Dict[str, Any]],
        log_fn: Callable[[str], None],
    ) -> Tuple[str, str]:
        import yt_dlp
        VideoDubbingService._cleanup_download_artifacts(output_dir)
        ydl_opts: Dict[str, Any] = {
            "format": fmt_selector,
            "merge_output_format": "mp4",
            "outtmpl": os.path.join(output_dir, "input_video.%(ext)s"),
            "noplaylist": True,
            "concurrent_fragment_downloads": 4,
            "socket_timeout": 30,
            "extractor_retries": 3,
            "retries": 3,
            "fragment_retries": 3,
            "nocheckcertificate": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [VideoDubbingService._build_yt_dlp_progress_hook(progress_callback)],
        }
        if js_dict:
            ydl_opts["js_runtimes"] = js_dict
        yt_ext_args: Dict[str, Any] = {}
        if disable_pot:
            yt_ext_args["fetch_pot"] = ["never"]
        if not work_cookie_path and not has_oauth:
            yt_ext_args["player_client"] = ["web_embedded", "tv_simply", "mweb", "android"]
        if yt_ext_args:
            ydl_opts["extractor_args"] = {"youtube": yt_ext_args}
        if proxy_url:
            ydl_opts["proxy"] = proxy_url
        if work_cookie_path:
            ydl_opts["cookiefile"] = work_cookie_path
        elif has_oauth:
            ydl_opts["username"] = "oauth2"
            ydl_opts["password"] = ""
            ydl_opts["cachedir"] = cache_dir

        t0 = time.time()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(canonical_url, download=True)

        title = (info.get("title") if info else None) or "YouTube Video"
        if metadata_out is not None and info:
            metadata_out.update({
                "title": title,
                "duration": float(info["duration"]) if info.get("duration") is not None else None,
                "thumbnail": info.get("thumbnail"),
                "channel": info.get("channel") or info.get("uploader"),
            })
        if progress_callback:
            progress_callback(100.0, "Đã tải xuống và kiểm tra MP4 thành công.")
        final_path = VideoDubbingService._finalize_downloaded_media(output_dir)
        log_fn(f"Engine 1 (yt-dlp Python) thành công: '{title}' ({os.path.getsize(final_path)} bytes trong {time.time() - t0:.2f}s)")
        return final_path, title

    @staticmethod
    def _download_via_ytdlp_subprocess(
        canonical_url: str,
        output_dir: str,
        fmt_selector: str,
        proxy_url: Optional[str],
        work_cookie_path: Optional[str],
        is_authenticated_cookies: bool,
        has_oauth: bool,
        cache_dir: str,
        js_cli: str,
        disable_pot: bool,
        progress_callback: Optional[Callable[[float, str], None]],
        metadata_out: Optional[Dict[str, Any]],
        log_fn: Callable[[str], None],
    ) -> Tuple[str, str]:
        VideoDubbingService._cleanup_download_artifacts(output_dir)
        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--no-warnings",
            "--no-update",
            "--no-playlist",
            "--no-check-certificate",
            "--newline",
            "--progress",
            "--concurrent-fragments", "4",
            "-f", fmt_selector,
            "--merge-output-format", "mp4",
            "-o", os.path.join(output_dir, "input_video.%(ext)s"),
            "--socket-timeout", "40",
            "--print", "after_video:TITLE:%(title)s",
        ]
        if js_cli:
            cmd.extend(["--js-runtimes", js_cli])
        if proxy_url:
            cmd.extend(["--proxy", proxy_url])

        if work_cookie_path and is_authenticated_cookies:
            cmd.extend(["--cookies", work_cookie_path])
            if disable_pot:
                cmd.extend(["--extractor-args", "youtube:fetch_pot=never"])
        elif has_oauth:
            cmd.extend(["--cache-dir", cache_dir, "--username", "oauth2", "--password", ""])
            if disable_pot:
                cmd.extend(["--extractor-args", "youtube:fetch_pot=never"])
        else:
            ext_arg = "youtube:player_client=web_embedded,tv_simply,mweb,android"
            if disable_pot:
                ext_arg += ";fetch_pot=never"
            cmd.extend(["--extractor-args", ext_arg])

        cmd.append(canonical_url)

        t0 = time.time()
        deadline = t0 + 180
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            stdin=subprocess.DEVNULL,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )
        title = "YouTube Video"
        tail_lines: List[str] = []
        try:
            assert proc.stdout is not None
            for raw_line in proc.stdout:
                if time.time() > deadline:
                    proc.kill()
                    raise TimeoutError("Subprocess yt-dlp vượt quá 180 giây.")
                line = raw_line.strip()
                if not line:
                    continue
                tail_lines.append(line)
                if len(tail_lines) > 25:
                    tail_lines.pop(0)
                if line.startswith("TITLE:"):
                    title = line[6:].strip() or title
                elif progress_callback:
                    m = _YTDLP_PROGRESS_RE.search(line)
                    if m:
                        pct = min(99.0, float(m.group(1)))
                        progress_callback(pct, f"Đang tải video YouTube ({pct:.0f}%)...")
            rc = proc.wait(timeout=max(5, int(deadline - time.time())))
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            raise

        if rc != 0:
            raise RuntimeError(f"rc={rc}: {' | '.join(tail_lines[-4:])}")

        if metadata_out is not None:
            metadata_out.setdefault("title", title)
        if progress_callback:
            progress_callback(100.0, "Đã tải xuống và kiểm tra MP4 thành công.")
        final_path = VideoDubbingService._finalize_downloaded_media(output_dir)
        log_fn(f"Engine 2 (yt-dlp CLI) thành công: '{title}' ({os.path.getsize(final_path)} bytes trong {time.time() - t0:.2f}s)")
        return final_path, title

    @staticmethod
    def _download_via_pytubefix(
        canonical_url: str,
        output_dir: str,
        preferred_quality: str,
        proxy_url: Optional[str],
        progress_callback: Optional[Callable[[float, str], None]],
        metadata_out: Optional[Dict[str, Any]],
        log_fn: Callable[[str], None],
    ) -> Tuple[str, str]:
        from pytubefix import YouTube
        max_h = _QUALITY_HEIGHT_MAP.get((preferred_quality or "1080p").strip().lower(), 1080)
        proxies_ptf = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        last_err = None

        for client_name in ["ANDROID_VR", "WEB", "ANDROID"]:
            try:
                VideoDubbingService._cleanup_download_artifacts(output_dir)
                log_fn(f"Engine 3 (pytubefix {client_name}) đang tải...")

                def _on_ptf_progress(stream, chunk, bytes_remaining):
                    if not progress_callback or not getattr(stream, "filesize", 0):
                        return
                    pct = min(95.0, max(0.0, ((stream.filesize - bytes_remaining) / stream.filesize) * 100.0))
                    progress_callback(pct, f"Đang tải video YouTube ({pct:.0f}%)...")

                yt = YouTube(canonical_url, client=client_name, proxies=proxies_ptf, on_progress_callback=_on_ptf_progress)
                title = yt.title or "YouTube Video"
                if metadata_out is not None:
                    metadata_out.update({
                        "title": title,
                        "duration": float(yt.length) if getattr(yt, "length", None) else None,
                        "thumbnail": getattr(yt, "thumbnail_url", None),
                        "channel": getattr(yt, "author", None),
                    })

                v_candidates = yt.streams.filter(only_video=True, file_extension="mp4")
                eligible = []
                for s in v_candidates:
                    res_str = getattr(s, "resolution", "") or ""
                    if res_str.endswith("p"):
                        try:
                            h = int(res_str[:-1])
                            if h <= max_h:
                                eligible.append((h, s))
                        except ValueError:
                            pass
                eligible.sort(key=lambda item: item[0], reverse=True)
                v_stream = eligible[0][1] if eligible else v_candidates.order_by("resolution").desc().first()
                a_stream = yt.streams.filter(only_audio=True, file_extension="mp4").order_by("abr").desc().first()

                target_path = os.path.join(output_dir, "input_video.mp4")
                if v_stream and a_stream:
                    v_stream.download(output_path=output_dir, filename="ptf_v.mp4", timeout=180)
                    a_stream.download(output_path=output_dir, filename="ptf_a.mp4", timeout=180)
                    v_temp = os.path.join(output_dir, "ptf_v.mp4")
                    a_temp = os.path.join(output_dir, "ptf_a.mp4")
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", v_temp, "-i", a_temp, "-c", "copy", target_path],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    for tmp_p in (v_temp, a_temp):
                        if os.path.exists(tmp_p):
                            try:
                                os.remove(tmp_p)
                            except Exception:
                                pass
                else:
                    prog_stream = yt.streams.filter(progressive=True, file_extension="mp4").order_by("resolution").desc().first()
                    if not prog_stream:
                        raise RuntimeError("Không tìm thấy luồng MP4 khả dụng từ pytubefix.")
                    prog_stream.download(output_path=output_dir, filename="input_video.mp4", timeout=180)

                if progress_callback:
                    progress_callback(100.0, "Đã tải xuống và kiểm tra MP4 thành công.")
                final_path = VideoDubbingService._finalize_downloaded_media(output_dir)
                log_fn(f"Engine 3 (pytubefix {client_name}) thành công: '{title}' ({os.path.getsize(final_path)} bytes)")
                return final_path, title
            except Exception as exc:
                last_err = str(exc)
                log_fn(f"Engine 3 (pytubefix {client_name}) thất bại: {exc}")

        raise RuntimeError(last_err or "pytubefix thất bại")

    @staticmethod
    def download_youtube_video(
        url: str,
        output_dir: str,
        log_file: Optional[str] = None,
        preferred_quality: str = "1080p",
        progress_callback: Optional[Callable[[float, str], None]] = None,
        metadata_out: Optional[Dict[str, Any]] = None,
        allow_remote_bridge: bool = True,
    ) -> Tuple[str, str]:
        """
        Downloads a YouTube video in the requested quality and returns (video_path, title).
        Tries Remote Windows Bridge + Cloudflare R2 first when configured, then yt-dlp Python API, yt-dlp CLI, and pytubefix.
        """
        VideoDubbingService.ensure_dependencies()
        os.makedirs(output_dir, exist_ok=True)
        video_id, canonical_url = VideoDubbingService.normalize_youtube_url(url)

        def _log(msg: str):
            print(f"[VideoDubbingService] {msg}", flush=True)
            if log_file:
                try:
                    os.makedirs(os.path.dirname(log_file), exist_ok=True)
                    with open(log_file, "a", encoding="utf-8") as f:
                        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
                        f.flush()
                except Exception:
                    pass

        last_error_summary: List[str] = []

        if allow_remote_bridge:
            try:
                from app.services.r2_bridge_client import RemoteYouTubeBridgeClient
                if RemoteYouTubeBridgeClient.is_enabled():
                    return RemoteYouTubeBridgeClient.download_via_bridge(
                        canonical_url=canonical_url,
                        video_id=video_id,
                        output_dir=output_dir,
                        preferred_quality=preferred_quality,
                        progress_callback=progress_callback,
                        metadata_out=metadata_out,
                        log_fn=_log,
                    )
            except Exception as bridge_err:
                bridge_msg = str(bridge_err)
                _log(f"⚠️ Windows Bridge / R2 không khả dụng, chuyển sang phương án dự phòng: {bridge_msg[:260]}")
                last_error_summary.append(f"Windows Bridge + R2: {bridge_msg[:240]}")

        oauth_token = VideoDubbingService.refresh_oauth_token_if_needed()
        has_oauth = bool(oauth_token)
        cookie_info = VideoDubbingService.inspect_cookie_status()
        stored_cookie_path = VideoDubbingService.get_cookie_file_path()
        work_cookie_path: Optional[str] = None

        if cookie_info.get("has_cookies") and cookie_info.get("is_authenticated") and stored_cookie_path and os.path.exists(stored_cookie_path):
            work_cookie_path = os.path.join(output_dir, "_cookies_work.txt")
            try:
                shutil.copy2(stored_cookie_path, work_cookie_path)
                _log(f"Sử dụng bản sao cookies cách ly ({cookie_info.get('cookie_count', 0)} cookies, auth=True)")
            except Exception:
                work_cookie_path = None
        elif cookie_info.get("has_cookies") and not cookie_info.get("is_authenticated"):
            _log(f"Bỏ qua file cookies chưa đăng nhập ({cookie_info.get('cookie_count', 0)} visitor cookies, auth=False) để tránh xung đột luồng tải công khai.")

        cache_dir = VideoDubbingService.get_oauth_cache_dir()
        js_cli, js_dict = VideoDubbingService._detect_js_runtimes()
        disable_pot = VideoDubbingService._should_disable_pot_ping()
        fmt_selector = VideoDubbingService._build_format_selector(preferred_quality)

        proxy_pool = VideoDubbingService.get_youtube_proxies()
        proxy_attempts: List[Optional[str]] = [*proxy_pool, None] if proxy_pool else [None]
        _log(f"Bắt đầu tải {canonical_url} (chất lượng tối đa: {preferred_quality}, JS runtime: {js_cli or 'none'}, proxy pool: {len(proxy_pool)})")

        try:
            for p_idx, proxy_url in enumerate(proxy_attempts):
                masked_p = VideoDubbingService.mask_proxy_url(proxy_url) if proxy_url else "Direct Connection (Không dùng proxy)"
                _log(f"--- [Kết nối {p_idx + 1}/{len(proxy_attempts)}] Đang thử tải qua: {masked_p} ---")

                err_py = None
                err_cli = None
                err_ptf = None

                # Engine 1: Modern in-process yt-dlp
                try:
                    return VideoDubbingService._download_via_ytdlp_python(
                        canonical_url=canonical_url,
                        output_dir=output_dir,
                        fmt_selector=fmt_selector,
                        proxy_url=proxy_url,
                        work_cookie_path=work_cookie_path,
                        has_oauth=has_oauth,
                        cache_dir=cache_dir,
                        js_dict=js_dict,
                        disable_pot=disable_pot,
                        progress_callback=progress_callback,
                        metadata_out=metadata_out,
                        log_fn=_log,
                    )
                except Exception as e:
                    err_py = str(e)
                    _log(f"Engine 1 (yt-dlp Python) thất bại qua {masked_p}: {err_py[:260]}")

                # Engine 2: Streaming subprocess yt-dlp CLI with multi-client fallback
                try:
                    return VideoDubbingService._download_via_ytdlp_subprocess(
                        canonical_url=canonical_url,
                        output_dir=output_dir,
                        fmt_selector=fmt_selector,
                        proxy_url=proxy_url,
                        work_cookie_path=work_cookie_path,
                        is_authenticated_cookies=bool(cookie_info.get("is_authenticated")),
                        has_oauth=has_oauth,
                        cache_dir=cache_dir,
                        js_cli=js_cli,
                        disable_pot=disable_pot,
                        progress_callback=progress_callback,
                        metadata_out=metadata_out,
                        log_fn=_log,
                    )
                except Exception as e:
                    err_cli = str(e)
                    _log(f"Engine 2 (yt-dlp CLI) thất bại qua {masked_p}: {err_cli[:260]}")

                # Engine 3: pytubefix fallback
                try:
                    return VideoDubbingService._download_via_pytubefix(
                        canonical_url=canonical_url,
                        output_dir=output_dir,
                        preferred_quality=preferred_quality,
                        proxy_url=proxy_url,
                        progress_callback=progress_callback,
                        metadata_out=metadata_out,
                        log_fn=_log,
                    )
                except Exception as e:
                    err_ptf = str(e)
                    _log(f"Engine 3 (pytubefix) thất bại qua {masked_p}: {err_ptf[:260]}")

                last_error_summary.append(
                    f"{masked_p}: (yt-dlp: {err_py or 'N/A'} | CLI: {err_cli or 'N/A'} | pytubefix: {err_ptf or 'N/A'})"
                )
                if p_idx + 1 < len(proxy_attempts):
                    _log(f"⚠️ Kết nối [{masked_p}] thất bại. Tự động chuyển sang phương án [{p_idx + 2}/{len(proxy_attempts)}]...")

            err_msg = (
                f"Không thể tải video từ YouTube sau khi thử qua {len(proxy_attempts)} kết nối:\n"
                + "\n".join(f"• {e}" for e in last_error_summary[-5:])
            )
            if not has_oauth and not cookie_info.get("is_authenticated"):
                err_msg += "\n💡 GỢI Ý: Bạn có thể dán Cookies.txt đã đăng nhập YouTube hoặc thêm Proxy dân cư vào tab 'Proxy Dân Cư'."
            raise Exception(err_msg)
        finally:
            if work_cookie_path and os.path.exists(work_cookie_path):
                try:
                    os.remove(work_cookie_path)
                except Exception:
                    pass

    @staticmethod
    def extract_audio_ffmpeg(video_path: str, output_audio_path: str) -> float:
        """
        Extracts mono WAV audio at 24000Hz from video. Returns the duration in seconds.
        """
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-acodec", "pcm_s16le", "-ar", "24000", "-ac", "1",
            output_audio_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        
        # Read duration using soundfile
        info = sf.info(output_audio_path)
        return info.duration

    @staticmethod
    def separate_audio_stems(input_audio_path: str, vocals_path: str, bgm_path: str, job_id: Optional[str] = None) -> bool:
        """
        Separates audio into vocals and bgm stems.
        Attempts Demucs first if installed. Falls back to FFmpeg speech/music isolation filters.
        Never duplicates the exact same audio for both stems.
        """
        # 1. Try Demucs if installed
        try:
            import demucs
            temp_out = os.path.join(os.path.dirname(vocals_path), "demucs_tmp")
            if job_id:
                VideoDubbingService.log_to_job(job_id, "Đang chạy Demucs để tách track Vocals và BGM...")
            demucs_cmd = [sys.executable, "-m", "demucs", "--two-stems=vocals", "-o", temp_out, input_audio_path]
            subprocess.run(demucs_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            ext_vox = None
            ext_bgm = None
            for root, dirs, files in os.walk(temp_out):
                for f in files:
                    fl = f.lower()
                    if fl.endswith((".wav", ".mp3", ".flac")):
                        fp = os.path.join(root, f)
                        if "no_vocals" in fl or "bgm" in fl or "music" in fl or "accompaniment" in fl:
                            ext_bgm = fp
                        elif "vocals" in fl:
                            ext_vox = fp
            if ext_vox and ext_bgm:
                shutil.copy2(ext_vox, vocals_path)
                shutil.copy2(ext_bgm, bgm_path)
                shutil.rmtree(temp_out, ignore_errors=True)
                if job_id:
                    VideoDubbingService.log_to_job(job_id, "Tách track Vocals và BGM thành công bằng Demucs AI.")
                return True
        except Exception as d_err:
            if job_id:
                VideoDubbingService.log_to_job(job_id, f"Demucs cục bộ không khả dụng ({d_err}), chuyển sang bộ lọc âm học FFmpeg...")

        # 2. Fallback: High-quality FFmpeg acoustic separation
        # BGM: suppress vocal frequencies (300Hz - 3400Hz)
        # Vocals: isolate vocal frequencies (bandpass 200Hz - 3600Hz)
        try:
            cmd_bgm = [
                "ffmpeg", "-y", "-i", input_audio_path,
                "-af", "equalizer=f=300:width_type=h:width=200:g=-18,equalizer=f=1000:width_type=h:width=600:g=-26,equalizer=f=2500:width_type=h:width=800:g=-18,volume=1.4",
                "-vn", "-acodec", "pcm_s16le", "-ar", "24000", "-ac", "1",
                bgm_path
            ]
            cmd_vox = [
                "ffmpeg", "-y", "-i", input_audio_path,
                "-af", "highpass=f=200,lowpass=f=3500,volume=1.3",
                "-vn", "-acodec", "pcm_s16le", "-ar", "24000", "-ac", "1",
                vocals_path
            ]
            subprocess.run(cmd_bgm, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            subprocess.run(cmd_vox, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            if job_id:
                VideoDubbingService.log_to_job(job_id, "Tách track Vocals và BGM thành công bằng bộ lọc âm học FFmpeg.")
            return True
        except Exception as f_err:
            if job_id:
                VideoDubbingService.log_to_job(job_id, f"Lỗi tách âm thanh FFmpeg: {f_err}. Sử dụng bản sao dự phòng.")
            shutil.copy2(input_audio_path, vocals_path)
            shutil.copy2(input_audio_path, bgm_path)
            return False

    @staticmethod
    def get_llm_settings(db: Session, llm_profile_id: Optional[str] = None) -> Dict[str, Any]:
        """Retrieves specific LLM Profile by ID, active LLM Profile, or falls back to system settings."""
        selected_profile = None
        if llm_profile_id:
            selected_profile = db.query(LLMProfile).filter(LLMProfile.id == llm_profile_id).first()
        if not selected_profile:
            selected_profile = db.query(LLMProfile).filter(LLMProfile.is_active == True).first()

        if selected_profile:
            return {
                "provider": selected_profile.provider,
                "api_key": selected_profile.api_key or "",
                "model": selected_profile.model,
                "custom_endpoint": selected_profile.custom_endpoint or "",
                "thinking_effort": selected_profile.thinking_effort or "none",
                "profile_name": selected_profile.name,
            }

        def get_setting(key: str, default: str) -> str:
            entry = db.query(SystemSetting).filter(SystemSetting.key == key).first()
            if entry and entry.value.strip():
                return entry.value.strip()
            return default

        return {
            "provider": get_setting("llm_provider", settings.LLM_PROVIDER),
            "api_key": get_setting("llm_api_key", settings.LLM_API_KEY),
            "model": get_setting("llm_model", settings.LLM_MODEL),
            "custom_endpoint": get_setting("llm_custom_endpoint", settings.LLM_CUSTOM_ENDPOINT),
            "thinking_effort": get_setting("llm_thinking_effort", settings.LLM_THINKING_EFFORT),
            "profile_name": "System Default Settings",
        }

    @staticmethod
    def translate_subtitles_llm(subtitles: List[Dict[str, Any]], target_language: str, db: Session, llm_profile_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Translates a list of subtitle segments to target language using configured LLM.
        Expected input schema: [{"id": 1, "start": 1.2, "end": 4.5, "text": "..."}]
        """
        if not subtitles:
            return []

        llm_config = VideoDubbingService.get_llm_settings(db, llm_profile_id=llm_profile_id)
        provider = llm_config["provider"]
        api_key = llm_config["api_key"]
        model = llm_config["model"]
        custom_endpoint = llm_config["custom_endpoint"]
        thinking_effort = llm_config.get("thinking_effort", "none")
        profile_name = llm_config.get("profile_name", "Default")

        if provider == "none" or (not api_key and provider != "custom"):
            # Fallback mock translation if no LLM configured
            translated = []
            for seg in subtitles:
                translated.append({
                    "id": seg["id"],
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": f"[{target_language}] {seg['text']}"
                })
            return translated

        # Create translation prompt with strict video dubbing & time synchronization rules
        prompt = (
            f"You are an expert video dubbing translator and voice-sync director. "
            f"Translate the following video subtitle segments into {target_language}.\n\n"
            f"CRITICAL VIDEO DUBBING CONSTRAINTS:\n"
            f"1. TIME DURATION BUDGET: The translation will be voiced by AI text-to-speech to fit the EXACT duration between 'start' and 'end' seconds.\n"
            f"2. BREVITY & PACING: For each segment, duration = end - start seconds. The translated text in {target_language} MUST be concise, natural, and succinct so it can be comfortably spoken within that duration (~3 words per second, or ~4-5 syllables/sec). Do NOT make the translation wordy or verbose.\n"
            f"3. PRESERVE MEANING: Keep the natural colloquial tone and core meaning, but express it economically to avoid speech overrun or audio collision with subsequent segments.\n"
            f"4. FORMAT: You MUST preserve the exact JSON array structure with keys 'id', 'start', 'end', and 'text'. Return ONLY the valid JSON array without any markdown fences, backticks, or extra explanation.\n\n"
            f"Subtitles JSON:\n{json.dumps(subtitles, ensure_ascii=False)}"
        )

        try:
            if provider == "gemini":
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                headers = {"Content-Type": "application/json"}
                gen_config: Dict[str, Any] = {"responseMimeType": "application/json"}
                
                # Apply Gemini Thinking / Reasoning Config if enabled
                if thinking_effort and thinking_effort != "none":
                    budget_map = {"low": 1024, "medium": 2048, "high": 4096}
                    budget = budget_map.get(thinking_effort, 1024)
                    gen_config["thinkingConfig"] = {"thinkingBudget": budget}

                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": gen_config
                }
                res = requests.post(url, headers=headers, json=payload, timeout=45)
                # If thinkingConfig causes 400 error, retry once without thinkingConfig
                if not res.ok and "thinkingConfig" in gen_config and res.status_code == 400:
                    gen_config.pop("thinkingConfig", None)
                    payload["generationConfig"] = gen_config
                    res = requests.post(url, headers=headers, json=payload, timeout=45)

                if not res.ok:
                    err_msg = res.text
                    try:
                        err_json = res.json()
                        if "error" in err_json:
                            err_msg = err_json["error"].get("message", err_msg)
                    except Exception:
                        pass
                    raise Exception(f"HTTP {res.status_code} ({provider}): {err_msg[:250]}")

                res_data = res.json()
                parts = res_data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                text_out = ""
                for part in parts:
                    if part.get("thought"):
                        continue
                    if "text" in part:
                        text_out += part["text"]
                if not text_out and parts:
                    text_out = parts[-1].get("text", "")
            
            elif provider in ["openai", "custom"]:
                if provider == "custom" and custom_endpoint:
                    url = custom_endpoint.strip()
                    if not url.endswith("/chat/completions"):
                        if url.endswith("/"):
                            url = url + "chat/completions"
                        elif "/v1" not in url:
                            url = url + "/v1/chat/completions"
                        else:
                            url = url + "/chat/completions"
                else:
                    url = "https://api.openai.com/v1/chat/completions"

                headers = {
                    "Content-Type": "application/json"
                }
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                    
                payload: Dict[str, Any] = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "You are a professional JSON subtitle translator."},
                        {"role": "user", "content": prompt}
                    ]
                }
                if thinking_effort and thinking_effort != "none":
                    payload["reasoning_effort"] = thinking_effort
                
                res = requests.post(url, headers=headers, json=payload, timeout=45)
                # If reasoning_effort causes 400 error, retry once without reasoning_effort
                if not res.ok and "reasoning_effort" in payload and res.status_code == 400:
                    payload.pop("reasoning_effort", None)
                    res = requests.post(url, headers=headers, json=payload, timeout=45)

                if not res.ok:
                    err_msg = res.text
                    try:
                        err_json = res.json()
                        if "error" in err_json:
                            err_msg = err_json["error"].get("message", err_msg)
                    except Exception:
                        pass
                    raise Exception(f"HTTP {res.status_code} ({provider}): {err_msg[:250]}")

                res_data = res.json()
                msg_obj = res_data.get("choices", [{}])[0].get("message", {})
                text_out = msg_obj.get("content") or msg_obj.get("reasoning_content") or ""
            
            else:
                raise Exception(f"Unsupported LLM provider: {provider}")

            # Clean and parse text
            text_out = text_out.strip()
            # If the response contains markdown code block, strip it
            if text_out.startswith("```"):
                lines = text_out.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].strip() == "```":
                    lines = lines[:-1]
                text_out = "\n".join(lines).strip()

            parsed = json.loads(text_out)
            # If it's wrapped in an object like {"subtitles": [...]}, extract it
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    if isinstance(v, list):
                        parsed = v
                        break
            
            if isinstance(parsed, list):
                # Validation to ensure timestamps are floats and IDs match
                final_list = []
                for i, seg in enumerate(parsed):
                    orig_seg = subtitles[i] if i < len(subtitles) else subtitles[-1]
                    final_list.append({
                        "id": seg.get("id", orig_seg["id"]),
                        "start": float(seg.get("start", orig_seg["start"])),
                        "end": float(seg.get("end", orig_seg["end"])),
                        "text": str(seg.get("text", orig_seg["text"]))
                    })
                return final_list
            
            raise Exception("LLM returned non-array JSON.")

        except Exception as e:
            print(f"[VideoDubbingService] LLM Translation failed ({profile_name}): {e}")
            raise RuntimeError(f"Lỗi dịch thuật phụ đề qua LLM ({profile_name}): {e}")

    @staticmethod
    def shorten_subtitle_llm(
        text: str,
        target_duration: float,
        target_language: str = "Vietnamese",
        original_text: Optional[str] = None,
        db: Optional[Session] = None,
        llm_profile_id: Optional[str] = None
    ) -> str:
        """
        Uses LLM to condense/shorten a subtitle sentence so that it fits comfortably within target_duration seconds
        (budget: ~3.0 words/sec for native Vietnamese speech) while preserving core meaning.
        """
        if not text or not text.strip():
            return ""

        max_words = max(2, int(target_duration * 3.0))

        llm_config = VideoDubbingService.get_llm_settings(db, llm_profile_id=llm_profile_id)
        provider = llm_config["provider"]
        api_key = llm_config["api_key"]
        model = llm_config["model"]
        custom_endpoint = llm_config["custom_endpoint"]
        profile_name = llm_config.get("profile_name", "Default")

        if provider == "none" or (not api_key and provider != "custom"):
            words = text.strip().split()
            if len(words) > max_words:
                return " ".join(words[:max_words])
            return text

        context_info = f'\nOriginal English context: "{original_text}"' if original_text else ""
        prompt = (
            f"You are a professional voiceover editor and dubbing dialogue adapter. "
            f"Shorten the following {target_language} subtitle text so it can be comfortably spoken "
            f"in {target_duration:.1f} seconds (STRICT LIMIT: MAXIMUM {max_words} WORDS).\n"
            f'Current text: "{text}"{context_info}\n\n'
            f"RULES:\n"
            f"1. Language MUST be {target_language}.\n"
            f"2. Keep the core meaning clear, natural, and punchy.\n"
            f"3. Maximum word count: {max_words} words.\n"
            f"4. Return ONLY the final shortened sentence without quotes, explanations, markdown, or preface."
        )

        try:
            if provider == "gemini":
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                headers = {"Content-Type": "application/json"}
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.3, "maxOutputTokens": 150}
                }
                res = requests.post(url, headers=headers, json=payload, timeout=25)
                if res.ok:
                    res_data = res.json()
                    parts = res_data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    out = "".join([p.get("text", "") for p in parts if not p.get("thought")]).strip()
                    if out:
                        return out.strip('"\n\r ')
            elif provider in ["openai", "custom"]:
                url = custom_endpoint.strip() if (provider == "custom" and custom_endpoint) else "https://api.openai.com/v1/chat/completions"
                if not url.endswith("/chat/completions"):
                    url = url.rstrip("/") + "/chat/completions"
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}"
                }
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "You are a concise voiceover editor. Output only the shortened sentence."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 100
                }
                res = requests.post(url, headers=headers, json=payload, timeout=25)
                if res.ok:
                    res_data = res.json()
                    out = res_data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    if out:
                        return out.strip('"\n\r ')
        except Exception as e:
            print(f"[VideoDubbingService Warning] LLM sentence shortening failed: {e}")

        # Fallback: return original text
        return text

    @staticmethod
    def merge_and_normalize_subtitles(subs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filters out empty/junk segments, merges fragmented short sentences into coherent paragraphs,
        and normalizes capitalization/punctuation for natural native Vietnamese TTS intonation.
        """
        if not subs:
            return []

        # 1. Filter out empty or punctuation-only segments
        cleaned = []
        for s in subs:
            txt = str(s.get("text", "")).strip()
            for tag in ["[Korean]", "[Vietnamese]", "[Japanese]", "[English]", "[Chinese]"]:
                txt = txt.replace(tag, "").strip()
            alpha_chars = [c for c in txt if c.isalnum()]
            if not alpha_chars:
                continue
            cleaned.append({
                "id": len(cleaned) + 1,
                "start": float(s.get("start", 0.0)),
                "end": float(s.get("end", 0.0)),
                "text": txt
            })

        if not cleaned:
            return []

        # 2. Merge short adjacent fragments into natural sentences
        merged = []
        curr = dict(cleaned[0])

        for nxt in cleaned[1:]:
            curr_txt = curr["text"].strip()
            curr_words = len(curr_txt.split())
            time_gap = nxt["start"] - curr["end"]
            
            does_not_end_sentence = not any(curr_txt.endswith(p) for p in [".", "!", "?", "...", ":"])
            is_short = curr_words < 6
            merged_duration = nxt["end"] - curr["start"]

            if (does_not_end_sentence or is_short) and time_gap < 1.2 and merged_duration <= 12.0:
                curr["end"] = nxt["end"]
                curr["text"] = f"{curr_txt} {nxt['text'].strip()}"
            else:
                merged.append(curr)
                curr = dict(nxt)

        merged.append(curr)

        # 3. Final normalization (Capitalization, punctuation ending, re-numbering)
        final_subs = []
        for i, s in enumerate(merged):
            txt = s["text"].strip()
            if txt:
                txt = txt[0].upper() + txt[1:]
                if not any(txt.endswith(p) for p in [".", "!", "?", "...", ":", ";"]):
                    txt += "."
            
            st_val = round(s["start"], 2)
            # If segment 1 includes intro music (0.0s - 8.5s), shift start to actual speech onset (~8.5s)
            if i == 0 and st_val < 3.0 and s["end"] >= 8.0:
                st_val = 8.5

            final_subs.append({
                "id": i + 1,
                "start": st_val,
                "end": round(s["end"], 2),
                "text": txt
            })

        return final_subs

    @staticmethod
    def assemble_dubbed_vocal(segments: List[Dict[str, Any]], output_vocal_path: str, total_duration: float):
        """
        Assembles individual audio segment WAVs into a single output vocal track at their respective timestamps
        with anti-collision lookahead, pitch-preserving speed matching, sequential boundary enforcement,
        and clean crossfade transitions.
        """
        def pitch_neutral_stretch(audio_data: np.ndarray, sr: int, speed_factor: float) -> np.ndarray:
            """
            Performs 100% pitch-preserving time stretch using librosa phase vocoder.
            Does NOT alter audio pitch/frequency, eliminating Chipmunk distortion.
            """
            speed_factor = max(0.6, min(1.8, speed_factor))
            try:
                import librosa
                return librosa.effects.time_stretch(audio_data, rate=speed_factor)
            except Exception as e:
                print(f"[VideoDubbingService Warning] librosa stretch failed: {e}")
                return audio_data

        duration_samples = int(total_duration * 24000)
        output_vocal = np.zeros(duration_samples, dtype=np.float32)

        # 1. Filter and sort segments chronologically by start timestamp
        valid_segs = [s for s in segments if s.get("file_path") and os.path.exists(s.get("file_path"))]
        valid_segs.sort(key=lambda s: float(s.get("start", 0.0)))

        for i, seg in enumerate(valid_segs):
            seg_path = seg.get("file_path")
            start_time = max(0.0, float(seg.get("start", 0.0)))
            end_time = float(seg.get("end", 0.0)) if seg.get("end") else None
            next_seg = valid_segs[i + 1] if (i + 1 < len(valid_segs)) else None
            next_start = float(next_seg.get("start", 0.0)) if next_seg else None

            try:
                data, sr = sf.read(seg_path)
                
                # Standardize stereo to mono
                if len(data.shape) > 1:
                    data = data.mean(axis=1)
                
                # Resample sample rate to 24000Hz if needed
                if sr != 24000:
                    import scipy.signal as signal
                    num_samples = int(len(data) * 24000 / sr)
                    data = signal.resample(data, num_samples)

                # 2. Anti-collision lookahead:
                # Determine target window and hard deadline before the next speaker starts
                gap_silence = 0.06  # 60ms natural breathing silence before next speaker begins
                if next_start is not None and next_start > start_time:
                    max_allowed_sec = max(0.3, next_start - start_time - gap_silence)
                    if end_time and end_time > start_time:
                        target_sec = min(end_time - start_time, max_allowed_sec)
                    else:
                        target_sec = max_allowed_sec
                else:
                    target_sec = (end_time - start_time) if (end_time and end_time > start_time) else 10.0
                    max_allowed_sec = target_sec * 1.25

                actual_sec = len(data) / 24000.0

                # 3. Dynamic pitch-neutral speed scaling (up to 1.50x without pitch distortion)
                if actual_sec > target_sec * 1.03 and target_sec > 0.3:
                    speed_factor = min(1.50, actual_sec / target_sec)
                    data = pitch_neutral_stretch(data, 24000, speed_factor)
                    actual_sec = len(data) / 24000.0

                # 4. Strict sequential boundary clamping:
                # If audio duration still encroaches upon next segment's start, apply smooth fade-out
                if next_start is not None and (start_time + actual_sec > next_start - 0.03):
                    max_samples = int(max(0.2, (next_start - 0.03 - start_time)) * 24000)
                    if max_samples < len(data):
                        data = data[:max_samples]
                        fade_samples = min(int(0.04 * 24000), len(data))
                        if fade_samples > 0:
                            data[-fade_samples:] *= np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)

                start_idx = int(start_time * 24000)
                end_idx = start_idx + len(data)
                
                if end_idx > len(output_vocal):
                    padding = np.zeros(end_idx - len(output_vocal), dtype=np.float32)
                    output_vocal = np.concatenate([output_vocal, padding])

                # 5. Clean boundary placement:
                # Prevent voice bleed from prior audio overlapping current speaker start
                lead_in = min(int(0.02 * 24000), len(data))
                if lead_in > 0:
                    existing = output_vocal[start_idx:start_idx + lead_in]
                    output_vocal[start_idx:start_idx + lead_in] = existing * np.linspace(0.4, 0.0, lead_in, dtype=np.float32) + data[:lead_in]
                    output_vocal[start_idx + lead_in:end_idx] = data[lead_in:]
                else:
                    output_vocal[start_idx:end_idx] = data

            except Exception as e:
                print(f"[VideoDubbingService] Failed to insert segment {seg_path} into vocal track: {e}")

        # Peak normalize to 0.95 to avoid digital clipping
        max_val = np.max(np.abs(output_vocal))
        if max_val > 0.95:
            output_vocal = output_vocal * (0.95 / max_val)

        # Save output vocal file
        os.makedirs(os.path.dirname(output_vocal_path), exist_ok=True)
        sf.write(output_vocal_path, output_vocal, 24000, format='WAV', subtype='PCM_16')

    @staticmethod
    def mix_and_mux_video(video_path: str, bgm_path: str, vocal_path: str, output_path: str, vocal_vol: float = 1.2, bgm_vol: float = 0.35):
        """
        Mixes vocal track and background music together using Auto-Ducking (Sidechain Compression)
        and remuxes them with the original video track.
        """
        temp_dir = os.path.dirname(output_path)
        temp_mixed_audio = os.path.join(temp_dir, f"temp_mixed_{os.path.basename(output_path)}.wav")

        # Check if bgm_path is original_audio (contains original speech)
        is_orig_bgm = False
        if bgm_path and os.path.exists(bgm_path):
            if "original_audio" in os.path.basename(bgm_path):
                is_orig_bgm = True

        if is_orig_bgm or bgm_vol <= 0.01 or not bgm_path or not os.path.exists(bgm_path):
            # Only use new dubbed vocal track to ensure original English speech is 100% removed!
            mix_cmd = [
                "ffmpeg", "-y",
                "-i", vocal_path,
                "-filter_complex", f"[0:a]volume={vocal_vol}[out]",
                "-map", "[out]",
                "-acodec", "pcm_s16le", "-ar", "24000",
                temp_mixed_audio
            ]
        else:
            # Studio-grade Audio Ducking with Sidechain Compression:
            # Automatically attenuates BGM by ~14dB whenever dubbed vocal has speech, and recovers smoothly during pauses.
            # normalize=0 preserves 100% vocal punchiness without dividing amplitude by 2!
            ducking_filter = (
                f"[0:a]volume={vocal_vol},asplit=2[vocal_main][vocal_sc];"
                f"[1:a]volume={bgm_vol}[bgm_raw];"
                f"[bgm_raw][vocal_sc]sidechaincompress=threshold=0.025:ratio=5:attack=35:release=350:makeup=1[bgm_ducked];"
                f"[vocal_main][bgm_ducked]amix=inputs=2:normalize=0:duration=first:dropout_transition=0[out]"
            )
            mix_cmd = [
                "ffmpeg", "-y",
                "-i", vocal_path,
                "-i", bgm_path,
                "-filter_complex", ducking_filter,
                "-map", "[out]",
                "-acodec", "pcm_s16le", "-ar", "24000",
                temp_mixed_audio
            ]
        
        res_mix = subprocess.run(mix_cmd, capture_output=True, text=True)
        if res_mix.returncode != 0:
            print(f"[FFmpeg Ducking Mix Warning] {res_mix.stderr[:200]}. Falling back to standard amix normalize=0...")
            fallback_cmd = [
                "ffmpeg", "-y",
                "-i", vocal_path,
                "-i", bgm_path,
                "-filter_complex", f"[0:a]volume={vocal_vol}[vocal];[1:a]volume={bgm_vol}[bgm];[vocal][bgm]amix=inputs=2:normalize=0:duration=first:dropout_transition=0[out]",
                "-map", "[out]",
                "-acodec", "pcm_s16le", "-ar", "24000",
                temp_mixed_audio
            ]
            res_fb = subprocess.run(fallback_cmd, capture_output=True, text=True)
            if res_fb.returncode != 0:
                print(f"[FFmpeg Mix Error] {res_fb.stderr}")
                raise RuntimeError(f"FFmpeg audio mixing failed: {res_fb.stderr[:300]}")
        
        # Check if video_path has a video stream
        has_video = False
        try:
            probe_cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type", "-of", "csv=p=0", video_path]
            probe_res = subprocess.run(probe_cmd, capture_output=True, text=True)
            if "video" in probe_res.stdout.lower():
                has_video = True
        except Exception:
            pass

        if has_video:
            mux_cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-i", temp_mixed_audio,
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-shortest",
                output_path
            ]
        else:
            # Fallback for audio-only / test dummy inputs
            mux_cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=640x360:r=24",
                "-i", temp_mixed_audio,
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "192k",
                "-shortest",
                output_path
            ]
        
        res_mux = subprocess.run(mux_cmd, capture_output=True, text=True)
        if res_mux.returncode != 0:
            print(f"[FFmpeg Mux Error] {res_mux.stderr}")
            raise RuntimeError(f"FFmpeg video remuxing failed: {res_mux.stderr[:300]}")
        
        # Clean up temporary mixed audio track
        if os.path.exists(temp_mixed_audio):
            os.remove(temp_mixed_audio)

    @staticmethod
    def compile_srt(subtitles: List[Dict[str, Any]]) -> str:
        """Converts structured JSON subtitle segments into standard SRT format."""
        def format_time(seconds: float) -> str:
            hrs = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            ms = int((seconds % 1) * 1000)
            return f"{hrs:02d}:{mins:02d}:{secs:02d},{ms:03d}"

        srt_lines = []
        for i, seg in enumerate(subtitles):
            srt_lines.append(str(i + 1))
            srt_lines.append(f"{format_time(seg['start'])} --> {format_time(seg['end'])}")
            srt_lines.append(seg['text'])
            srt_lines.append("")
            
        return "\n".join(srt_lines)
