"""URL, SSRF, and safe filesystem path validation utilities."""

from __future__ import annotations

import ipaddress
import os
import socket
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse

import httpx


BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
    "169.254.169.254",
}

BLOCKED_HOST_SUFFIXES = (
    ".local",
    ".localhost",
    ".internal",
    ".localdomain",
    ".arpa",
)


def _is_disallowed_ip(ip_obj: ipaddress._BaseAddress) -> bool:
    """Return True if an IP address is internal, link-local, loopback, multicast, or reserved."""
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
        if _is_disallowed_ip(ip_obj.ipv4_mapped):
            return True
    return (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
        or not ip_obj.is_global
    )


def validate_safe_external_url(
    url: str,
    *,
    allowed_schemes: Iterable[str] = ("http", "https"),
    allow_literal_ip: bool = False,
) -> str:
    """Validate that a user-supplied URL resolves only to public global IPs.

    Raises ValueError if the URL has an invalid scheme, targets localhost/metadata,
    or resolves to any private, loopback, link-local, or reserved IP address.
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("URL không được để trống.")

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in set(allowed_schemes):
        raise ValueError(f"Scheme URL không được hỗ trợ: {scheme}")

    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        raise ValueError("URL thiếu tên miền hợp lệ.")

    if hostname in BLOCKED_HOSTNAMES or hostname.endswith(BLOCKED_HOST_SUFFIXES):
        raise ValueError("Không được phép truy cập địa chỉ nội bộ hoặc metadata.")

    port = parsed.port or (443 if scheme == "https" else 80)

    # Reject literal private/special IPs immediately (and optional all literal IPs)
    try:
        ip_literal = ipaddress.ip_address(hostname)
    except ValueError:
        ip_literal = None

    if ip_literal is not None:
        if not allow_literal_ip or _is_disallowed_ip(ip_literal):
            raise ValueError("Không được phép truy cập địa chỉ IP nội bộ hoặc IP trực tiếp.")
        return raw

    try:
        addr_infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Không thể phân giải tên miền URL: {hostname}") from exc

    if not addr_infos:
        raise ValueError(f"Không tìm thấy địa chỉ IP cho tên miền: {hostname}")

    for info in addr_infos:
        sockaddr = info[4]
        ip_str = sockaddr[0].split("%")[0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError as exc:
            raise ValueError("Địa chỉ IP phân giải không hợp lệ.") from exc
        if _is_disallowed_ip(ip_obj):
            raise ValueError("URL trỏ tới địa chỉ mạng nội bộ không được phép.")

    return raw


def fetch_external_bytes(
    url: str,
    *,
    max_bytes: int = 25 * 1024 * 1024,
    timeout: float = 30.0,
    max_redirects: int = 3,
    allowed_schemes: Iterable[str] = ("http", "https"),
) -> bytes:
    """Safely download external URL bytes with per-hop SSRF validation and size caps."""
    current_url = validate_safe_external_url(url, allowed_schemes=allowed_schemes)

    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        for _ in range(max_redirects + 1):
            validate_safe_external_url(current_url, allowed_schemes=allowed_schemes)
            with client.stream("GET", current_url) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise ValueError("Redirect URL không hợp lệ.")
                    current_url = urljoin(current_url, location)
                    continue

                resp.raise_for_status()
                content_length = resp.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > max_bytes:
                            raise ValueError(f"Tài nguyên từ URL vượt quá giới hạn {max_bytes // (1024 * 1024)}MB.")
                    except ValueError as exc:
                        if "vượt quá" in str(exc):
                            raise

                chunks = []
                total = 0
                for chunk in resp.iter_bytes(chunk_size=65536):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"Tài nguyên từ URL vượt quá giới hạn {max_bytes // (1024 * 1024)}MB.")
                    chunks.append(chunk)
                return b"".join(chunks)

    raise ValueError("Quá nhiều chuyển hướng HTTP khi tải URL.")


def ensure_within_directory(base_dir: str, target_path: str) -> str:
    """Verify target_path stays inside base_dir and return its real normalized path."""
    real_base = os.path.realpath(base_dir)
    real_target = os.path.realpath(target_path)
    if real_target != real_base and os.path.commonpath([real_base, real_target]) != real_base:
        raise ValueError("Đường dẫn tệp vượt ra ngoài thư mục lưu trữ được phép.")
    return real_target
