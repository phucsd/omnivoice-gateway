import os
import json
import shutil
from datetime import datetime
from typing import Optional, Union, List, Any
from fastapi import APIRouter, Depends, HTTPException, status, Response
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.models import TTSJob, User, VideoDubbingJob
from app.schemas import JobStatusResponse, PaginatedJobsResponse, BatchJobStatusRequest
from app.utils.auth import get_user_or_api_key
from app.utils.url_security import ensure_within_directory

router = APIRouter(prefix="/v1/jobs", tags=["Generic Jobs"])


def _summarize_dubbing_subtitles(raw_text: Optional[str]) -> Optional[str]:
    if not raw_text:
        return None
    stripped = raw_text.strip()
    if not stripped.startswith("["):
        return raw_text
    try:
        items = json.loads(stripped)
        if isinstance(items, list):
            lines = [str(seg.get("text", "")).strip() for seg in items if isinstance(seg, dict) and str(seg.get("text", "")).strip()]
            if lines:
                return " • ".join(lines)
    except Exception:
        pass
    return raw_text


def _build_dubbing_project_history_item(vd: VideoDubbingJob) -> JobStatusResponse:
    job_dir = os.path.join(settings.dubbing_dir, vd.id)
    out_mp4 = vd.output_video_path if (vd.output_video_path and os.path.exists(vd.output_video_path)) else os.path.join(job_dir, "output_dubbed.mp4")
    in_mp4 = vd.input_file_path if (vd.input_file_path and os.path.exists(vd.input_file_path)) else os.path.join(job_dir, "input_video.mp4")
    dub_wav = os.path.join(job_dir, "dubbed_vocals.wav")
    voc_wav = vd.vocals_audio_path if (vd.vocals_audio_path and os.path.exists(vd.vocals_audio_path)) else os.path.join(job_dir, "vocals.wav")
    orig_wav = vd.original_audio_path if (vd.original_audio_path and os.path.exists(vd.original_audio_path)) else os.path.join(job_dir, "original_audio.wav")

    has_output_video = os.path.exists(out_mp4) and os.path.getsize(out_mp4) > 512
    has_source_video = os.path.exists(in_mp4) and os.path.getsize(in_mp4) > 512
    has_any_audio = any(os.path.exists(p) and os.path.getsize(p) > 128 for p in (dub_wav, voc_wav, orig_wav))

    hist_status = vd.status or "queued"
    if hist_status not in ("completed", "failed"):
        hist_status = "running" if hist_status != "awaiting_review" else "awaiting_review"

    audio_url = f"/v1/video-dubbing/jobs/{vd.id}/dubbed-audio" if has_any_audio else None
    sub_preview = _summarize_dubbing_subtitles(vd.translated_subtitles or vd.original_subtitles)
    title = vd.video_title or vd.source_url or f"Dự án #{vd.id[:8]}"
    display_text = f"{title}\n{sub_preview}" if sub_preview else title

    total_time = None
    if vd.status == "completed" and vd.updated_at and vd.created_at:
        diff = (vd.updated_at - vd.created_at).total_seconds()
        if diff >= 0:
            total_time = diff

    return JobStatusResponse(
        job_id=vd.id,
        status="completed" if (vd.status == "completed" or has_output_video) else vd.status,
        message=vd.message,
        progress=int(vd.progress or 0),
        audio_url=audio_url,
        cdn_audio_url=None,
        error_message=vd.error_message,
        worker_id=None,
        job_type="video_dubbing",
        text=display_text,
        created_at=vd.created_at,
        alignment=None,
        started_at=vd.created_at,
        completed_at=vd.updated_at if vd.status == "completed" else None,
        processing_time=total_time,
        queue_time=None,
        total_time=total_time,
        params={
            "mode": "video_dubbing",
            "dubbing_job_id": vd.id,
            "dubbing_status": vd.status,
            "video_title": vd.video_title,
            "source_url": vd.source_url,
            "target_language": vd.target_language,
            "preferred_quality": vd.preferred_quality or "1080p",
            "has_output_video": has_output_video,
            "has_source_video": has_source_video,
        },
    )


def _build_job_response(job: TTSJob) -> JobStatusResponse:
    is_dub_subjob = job.job_type in ("separate_audio", "dub_segments") or job.id.startswith(("sep_", "dub_"))
    dub_id = os.path.basename(job.id.split("_", 1)[1]) if job.id.startswith(("sep_", "dub_", "asr_")) and "_" in job.id else None

    audio_url = None
    if job.status == "completed":
        if job.job_type == "voice_design_preview" and job.preview_id:
            audio_url = f"/v1/voice-design/previews/{job.preview_id}/audio"
        else:
            audio_url = f"/v1/tts/jobs/{job.id}/audio"

    # Never expose corrupt CDN URLs when legacy dubbing sub-jobs uploaded temporary .zip archives
    cdn_url = getattr(job, "cdn_audio_url", None) if job.status == "completed" else None
    if is_dub_subjob and ((job.output_audio_path or "").lower().endswith(".zip") or not job.output_audio_path):
        cdn_url = None

    alignment_data = None
    if job.alignment:
        try:
            alignment_data = json.loads(job.alignment)
        except Exception:
            pass

    processing_time = None
    if job.completed_at and job.started_at:
        processing_time = (job.completed_at - job.started_at).total_seconds()
        
    queue_time = None
    if job.started_at:
        queue_time = (job.started_at - job.created_at).total_seconds()
        
    total_time = None
    if job.completed_at:
        total_time = (job.completed_at - job.created_at).total_seconds()

    mode_val = "clone_voice"
    if job.job_type == "auto_voice":
        mode_val = "auto_voice"
    elif job.job_type in ["voice_design_tts", "voice_design_preview"]:
        mode_val = "voice_design"
    elif is_dub_subjob:
        mode_val = "video_dubbing"

    display_text = _summarize_dubbing_subtitles(job.text) if is_dub_subjob else job.text

    params_data: dict[str, Any] = {
        "mode": mode_val,
        "text": display_text,
        "voice_sample_id": job.voice_sample_id,
        "ref_text": job.ref_text,
        "instruct": job.instruct,
        "speed": job.speed,
        "num_step": job.num_step,
        "denoise": job.denoise,
        "guidance_scale": job.guidance_scale,
        "t_shift": job.t_shift,
        "position_temperature": job.position_temperature,
        "class_temperature": job.class_temperature,
        "layer_penalty_factor": job.layer_penalty_factor,
        "duration": job.duration,
        "preprocess_prompt": job.preprocess_prompt,
        "postprocess_output": job.postprocess_output,
        "audio_chunk_duration": job.audio_chunk_duration,
        "audio_chunk_threshold": job.audio_chunk_threshold,
        "with_alignment": job.with_alignment,
        "language": job.language,
        "pad_duration": job.pad_duration,
        "fade_duration": job.fade_duration,
        "normalize_text": getattr(job, "normalize_text", False),
    }
    if dub_id and is_dub_subjob:
        dub_dir = os.path.join(settings.dubbing_dir, dub_id)
        params_data["dubbing_job_id"] = dub_id
        params_data["has_output_video"] = os.path.exists(os.path.join(dub_dir, "output_dubbed.mp4"))
        params_data["has_source_video"] = os.path.exists(os.path.join(dub_dir, "input_video.mp4"))

    params_data = {k: v for k, v in params_data.items() if v is not None}

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        message=job.message,
        progress=job.progress,
        audio_url=audio_url,
        cdn_audio_url=cdn_url,
        error_message=job.error_message,
        worker_id=job.worker_id,
        job_type=job.job_type,
        text=display_text,
        created_at=job.created_at,
        alignment=alignment_data,
        started_at=job.started_at,
        completed_at=job.completed_at,
        processing_time=processing_time,
        queue_time=queue_time,
        total_time=total_time,
        params=params_data
    )

@router.get("", response_model=Any)
def list_jobs(
    response: Response,
    page: Optional[int] = None,
    page_size: Optional[int] = 15,
    job_type: Optional[str] = None,
    status_filter: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key)
):
    """
    Returns list of jobs belonging to the current user with optional pagination, category filtering, and search.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

    # Dedicated rich listing for Video Dubbing tab in History
    if job_type == "video_dubbing":
        if current_user.is_admin:
            from app.routers.video_dubbing import reconcile_dubbing_jobs_from_disk
            reconcile_dubbing_jobs_from_disk(db, fallback_user_id=current_user.id)
            vd_query = db.query(VideoDubbingJob)
        else:
            vd_query = db.query(VideoDubbingJob).filter(VideoDubbingJob.user_id == current_user.id)

        if status_filter and status_filter != "all":
            if status_filter == "failed":
                vd_query = vd_query.filter(VideoDubbingJob.status == "failed")
            elif status_filter == "completed":
                vd_query = vd_query.filter(VideoDubbingJob.status == "completed")
            elif status_filter == "running":
                vd_query = vd_query.filter(VideoDubbingJob.status.notin_(["completed", "failed"]))

        if search and search.strip():
            term = f"%{search.strip()}%"
            vd_query = vd_query.filter(
                (VideoDubbingJob.id.ilike(term))
                | (VideoDubbingJob.video_title.ilike(term))
                | (VideoDubbingJob.source_url.ilike(term))
                | (VideoDubbingJob.translated_subtitles.ilike(term))
            )

        vd_rows = vd_query.order_by(VideoDubbingJob.created_at.desc()).all()
        covered_vd_ids = {v.id for v in vd_rows}
        combined_items: List[JobStatusResponse] = [_build_dubbing_project_history_item(v) for v in vd_rows]

        # Include any standalone TTSJob sub-jobs whose parent VideoDubbingJob row no longer exists
        sub_query = db.query(TTSJob).filter(TTSJob.job_type.in_(["separate_audio", "dub_segments"]))
        if not current_user.is_admin:
            sub_query = sub_query.filter(TTSJob.user_id == current_user.id)
        if status_filter and status_filter != "all":
            if status_filter == "failed":
                sub_query = sub_query.filter(TTSJob.status == "failed")
            elif status_filter == "completed":
                sub_query = sub_query.filter(TTSJob.status == "completed")
            elif status_filter == "running":
                sub_query = sub_query.filter(TTSJob.status.notin_(["completed", "failed"]))
        for sj in sub_query.order_by(TTSJob.created_at.desc()).all():
            parent_id = sj.id.split("_", 1)[1] if "_" in sj.id else ""
            if parent_id not in covered_vd_ids:
                combined_items.append(_build_job_response(sj))

        combined_items.sort(key=lambda x: x.created_at or datetime.min, reverse=True)

        if page is not None:
            p = max(1, page)
            ps = max(1, min(100, page_size or 15))
            total = len(combined_items)
            total_pages = (total + ps - 1) // ps if total > 0 else 1
            sliced = combined_items[(p - 1) * ps : p * ps]
            return PaginatedJobsResponse(
                items=sliced,
                total=total,
                page=p,
                page_size=ps,
                total_pages=total_pages,
            )
        return combined_items

    query = db.query(TTSJob).filter(TTSJob.user_id == current_user.id)

    # Filtering by job_type (category)
    if job_type and job_type != "all":
        if job_type == "clone_voice":
            query = query.filter(TTSJob.job_type == "clone_voice")
        elif job_type == "auto_voice":
            query = query.filter(TTSJob.job_type == "auto_voice")
        elif job_type == "voice_design":
            query = query.filter(TTSJob.job_type.in_(["voice_design_preview", "voice_design_tts"]))
        elif job_type == "asr":
            query = query.filter(TTSJob.job_type == "asr")
            
    # Filtering by status
    if status_filter and status_filter != "all":
        if status_filter == "failed":
            query = query.filter(TTSJob.status == "failed")
        elif status_filter == "completed":
            query = query.filter(TTSJob.status == "completed")
        elif status_filter == "running":
            query = query.filter(TTSJob.status.notin_(["completed", "failed"]))

    # Search by job_id or text content
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            (TTSJob.id.ilike(term)) | (TTSJob.text.ilike(term)) | (TTSJob.instruct.ilike(term))
        )

    query = query.order_by(TTSJob.created_at.desc())

    if page is not None:
        p = max(1, page)
        ps = max(1, min(100, page_size or 15))
        total = query.count()
        total_pages = (total + ps - 1) // ps if total > 0 else 1
        jobs = query.offset((p - 1) * ps).limit(ps).all()
        
        return PaginatedJobsResponse(
            items=[_build_job_response(job) for job in jobs],
            total=total,
            page=p,
            page_size=ps,
            total_pages=total_pages
        )

    # Legacy unpaginated fallback
    jobs = query.all()
    return [_build_job_response(job) for job in jobs]

@router.post("/batch", response_model=dict[str, JobStatusResponse])
def get_batch_jobs_status(
    payload: BatchJobStatusRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key)
):
    """
    Query the status of multiple jobs in a single request.
    Returns a dictionary mapping job_id to its detailed status.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    if not payload.job_ids:
        return {}
    
    jobs = db.query(TTSJob).filter(
        TTSJob.id.in_(payload.job_ids),
        TTSJob.user_id == current_user.id
    ).all()
    
    return {job.id: _build_job_response(job) for job in jobs}

@router.get("/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str, response: Response, db: Session = Depends(get_db), current_user: User = Depends(get_user_or_api_key)):
    """
    Polled generic job status endpoint returning current state, progress rate,
    any error messages, and the resolved audio download URL upon completion for the user's job.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    job = db.query(TTSJob).filter(TTSJob.id == job_id, TTSJob.user_id == current_user.id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy Job với ID: {job_id}"
        )
    return _build_job_response(job)

@router.delete("/{job_id}")
def delete_job(job_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_user_or_api_key)):
    """
    Xóa/Hủy một tác vụ khỏi hàng chờ hoặc lịch sử. Nếu tác vụ có file âm thanh kết quả,
    file đó cũng sẽ được dọn dẹp khỏi disk.
    """
    if job_id.startswith("vd_"):
        vd = db.query(VideoDubbingJob).filter(VideoDubbingJob.id == job_id).first()
        if not vd or (vd.user_id != current_user.id and not current_user.is_admin):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy Job với ID: {job_id}"
            )
        try:
            job_dir = ensure_within_directory(settings.dubbing_dir, os.path.join(settings.dubbing_dir, job_id))
            if os.path.exists(job_dir):
                shutil.rmtree(job_dir, ignore_errors=True)
        except Exception as e:
            print(f"Lỗi dọn dẹp thư mục dubbing {job_id}: {e}")
        db.delete(vd)
        db.commit()
        return {"status": "success", "message": f"Đã xóa dự án lồng tiếng {job_id} thành công."}

    job = db.query(TTSJob).filter(TTSJob.id == job_id).first()
    if not job or (job.user_id != current_user.id and not current_user.is_admin):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy Job với ID: {job_id}"
        )
    
    # Dọn dẹp file âm thanh đầu ra nếu có (bảo vệ file dùng chung trong thư mục dubbing)
    if (
        job.output_audio_path
        and not job.id.startswith(("sep_", "dub_", "asr_"))
        and os.path.exists(job.output_audio_path)
    ):
        try:
            os.remove(job.output_audio_path)
        except Exception as e:
            print(f"Lỗi xóa file âm thanh đầu ra: {e}")
            
    # Dọn dẹp file preview thiết kế giọng nếu có
    if job.job_type == "voice_design_preview" and job.preview_id:
        from app.models import VoiceDesignPreview
        preview = db.query(VoiceDesignPreview).filter(VoiceDesignPreview.id == job.preview_id).first()
        if preview:
            if preview.preview_audio_path and os.path.exists(preview.preview_audio_path):
                try:
                    os.remove(preview.preview_audio_path)
                except Exception as e:
                    print(f"Lỗi xóa file preview: {e}")
            db.delete(preview)

    db.delete(job)
    db.commit()
    return {"status": "success", "message": f"Đã xóa tác vụ {job_id} thành công."}
