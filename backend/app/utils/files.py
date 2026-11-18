"""Safe file upload and storage containment utilities."""

from __future__ import annotations

import os
from fastapi import HTTPException, UploadFile, status


async def save_upload_with_limit(
    upload_file: UploadFile,
    dest_path: str,
    *,
    max_bytes: int,
    chunk_size: int = 1024 * 1024,
) -> int:
    """Stream an UploadFile to dest_path enforcing max_bytes without buffering unbounded RAM."""
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
    total = 0
    try:
        with open(dest_path, "wb") as out:
            while True:
                chunk = await upload_file.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Tệp tải lên vượt quá giới hạn tối đa ({max_bytes // (1024 * 1024)} MB).",
                    )
                out.write(chunk)
        return total
    except HTTPException:
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except Exception:
                pass
        raise
    except Exception as exc:
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except Exception:
                pass
        raise exc
