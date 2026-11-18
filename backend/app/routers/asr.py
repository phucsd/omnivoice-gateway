import os
import shutil
import uuid
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import TTSJob, User
from app.schemas import TTSJobResponse
from app.services.audio_service import AudioService
from app.services.job_service import JobService
from app.utils.auth import get_user_or_api_key
from app.utils.files import save_upload_with_limit
from app.utils.url_security import ensure_within_directory

router = APIRouter(prefix="/v1/asr", tags=["ASR (Speech-to-Text)"])

MAX_ASR_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB


@router.post("", response_model=TTSJobResponse)
async def create_asr_job(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key),
):
    """
    Tải lên một file âm thanh và khởi tạo một tác vụ ASR (Speech-to-Text).
    """
    AudioService.ensure_directories()

    temp_id = f"asr_temp_{uuid.uuid4().hex[:8]}"
    filename = os.path.basename(file.filename or f"{temp_id}_raw")
    ext = os.path.splitext(filename)[1] or ".bin"
    upload_temp_path = ensure_within_directory(
        settings.uploads_dir,
        os.path.join(settings.uploads_dir, f"{temp_id}_raw{ext}"),
    )

    await save_upload_with_limit(file, upload_temp_path, max_bytes=MAX_ASR_UPLOAD_BYTES)

    target_filename = f"{temp_id}.wav"
    final_dest = ensure_within_directory(
        settings.uploads_dir,
        os.path.join(settings.uploads_dir, target_filename),
    )
    try:
        saved_path, _duration, _sample_rate = AudioService.process_and_save_upload(
            upload_temp_path, target_filename
        )
        if os.path.exists(saved_path) and os.path.realpath(saved_path) != os.path.realpath(final_dest):
            shutil.move(saved_path, final_dest)
        elif not os.path.exists(final_dest):
            final_dest = saved_path
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Định dạng tệp âm thanh không hợp lệ hoặc lỗi xử lý: {e}",
        )
    finally:
        if os.path.exists(upload_temp_path):
            try:
                os.remove(upload_temp_path)
            except Exception:
                pass

    try:
        job = JobService.create_asr_job(
            db=db,
            ref_audio_path=final_dest,
            user_id=current_user.id,
        )
        actual_dest = ensure_within_directory(
            settings.uploads_dir,
            os.path.join(settings.uploads_dir, f"{job.id}.wav"),
        )
        shutil.move(final_dest, actual_dest)
        job.ref_audio_path = actual_dest
        db.commit()

        return TTSJobResponse(
            job_id=job.id,
            status=job.status,
            message=job.message,
        )
    except Exception as e:
        if os.path.exists(final_dest):
            try:
                os.remove(final_dest)
            except Exception:
                pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi tạo ASR Job: {e}",
        )


@router.get("/jobs/{job_id}/audio")
def get_asr_audio(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key),
):
    """
    Phát lại file âm thanh gốc đã tải lên của một ASR Job.
    """
    job = (
        db.query(TTSJob)
        .filter(
            TTSJob.id == job_id,
            TTSJob.user_id == current_user.id,
            TTSJob.job_type == "asr",
        )
        .first()
    )
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy Job {job_id}",
        )

    if not job.ref_audio_path or not os.path.exists(job.ref_audio_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tệp âm thanh gốc không tồn tại trên máy chủ.",
        )

    try:
        safe_path = ensure_within_directory(settings.STORAGE_DIR, job.ref_audio_path)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Đường dẫn âm thanh không hợp lệ.",
        ) from exc

    return FileResponse(
        safe_path,
        media_type="audio/wav",
        filename=f"asr_{job_id}.wav",
        content_disposition_type="inline",
    )
