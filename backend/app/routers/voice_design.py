import os
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import TTSJob, User, VoiceDesignPreview
from app.schemas import (
    AcceptPreviewRequest,
    AcceptPreviewResponse,
    VoiceDesignAssistRequest,
    VoiceDesignAssistResponse,
    VoiceDesignLLMStatusResponse,
    VoiceDesignPreviewCreate,
    VoiceDesignPreviewDetail,
    VoiceDesignPreviewHistoryItem,
    VoiceDesignPreviewResponse,
    VoiceDesignScriptRequest,
    VoiceDesignScriptResponse,
)
from app.services.job_service import JobService
from app.services.voice_design_service import VoiceDesignService
from app.utils.auth import get_user_or_api_key
from app.utils.url_security import ensure_within_directory

router = APIRouter(prefix="/v1/voice-design", tags=["Voice Design"])


@router.get("/llm-status", response_model=VoiceDesignLLMStatusResponse)
def get_voice_design_llm_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key),
):
    """Returns configured LLM status and selectable LLM profiles for Voice Design."""
    _ = current_user
    return VoiceDesignService.get_llm_status(db)


@router.post("/assist", response_model=VoiceDesignAssistResponse)
def assist_voice_design(
    payload: VoiceDesignAssistRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key),
):
    """
    Uses the configured LLM (with smart deterministic fallback) to translate a natural
    voice description into valid OmniVoice instruct attributes, calibrated prosody
    parameters, an optimal 6-8s audition script, library metadata, and 3 persona variants.
    """
    _ = current_user
    try:
        result = VoiceDesignService.assist_voice_design(
            db=db,
            voice_request=payload.voice_request,
            target_language=payload.target_language or "auto",
            current_preview_text=payload.current_preview_text,
            preserve_preview_text=bool(payload.preserve_preview_text),
            llm_profile_id=payload.llm_profile_id,
        )
        return VoiceDesignAssistResponse(**result)
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi phân tích thiết kế giọng nói: {exc}",
        )


@router.post("/generate-script", response_model=VoiceDesignScriptResponse)
def generate_voice_design_script(
    payload: VoiceDesignScriptRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key),
):
    """Generates a tailored ~6-8s preview script optimized for Voice Design & Cloning."""
    _ = current_user
    try:
        result = VoiceDesignService.generate_preview_script(
            db=db,
            voice_request=payload.voice_request or "",
            instruct=payload.instruct or "",
            style_or_topic=payload.style_or_topic or "Kể chuyện tự nhiên",
            language=payload.language or "vi",
            include_paralinguistic=bool(payload.include_paralinguistic),
            llm_profile_id=payload.llm_profile_id,
        )
        return VoiceDesignScriptResponse(**result)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi sinh kịch bản nghe thử: {exc}",
        )


@router.get("/previews", response_model=List[VoiceDesignPreviewHistoryItem])
def list_voice_design_previews(
    response: Response,
    limit: int = Query(15, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key),
):
    """Lists recent Voice Design previews for the authenticated user for A/B auditioning."""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    previews = (
        db.query(VoiceDesignPreview)
        .filter(VoiceDesignPreview.user_id == current_user.id)
        .order_by(VoiceDesignPreview.created_at.desc())
        .limit(limit)
        .all()
    )
    if not previews:
        return []

    preview_ids = [p.id for p in previews]
    jobs = db.query(TTSJob).filter(TTSJob.preview_id.in_(preview_ids)).all()
    job_map = {j.preview_id: j for j in jobs if j.preview_id}

    items: List[VoiceDesignPreviewHistoryItem] = []
    for p in previews:
        j = job_map.get(p.id)
        effective_status = j.status if j else p.status
        has_file = bool(p.preview_audio_path and os.path.exists(p.preview_audio_path))
        if not has_file and j and j.output_audio_path and os.path.exists(j.output_audio_path):
            has_file = True
        audio_url = (
            f"/v1/voice-design/previews/{p.id}/audio"
            if (effective_status == "completed" and has_file)
            else None
        )
        items.append(
            VoiceDesignPreviewHistoryItem(
                id=p.id,
                job_id=j.id if j else None,
                voice_request=p.voice_request,
                instruct=p.instruct,
                preview_text=p.preview_text,
                status=effective_status,
                progress=int(j.progress or 0) if j else (100 if p.status == "completed" else 0),
                audio_url=audio_url,
                cdn_audio_url=(j.cdn_audio_url if (j and effective_status == "completed") else None),
                accepted_sample_id=p.accepted_sample_id,
                speed=j.speed if j else 1.0,
                num_step=j.num_step if j else 32,
                guidance_scale=j.guidance_scale if j else 2.0,
                position_temperature=j.position_temperature if j else 5.0,
                class_temperature=j.class_temperature if j else 0.0,
                t_shift=j.t_shift if j else 0.1,
                language=j.language if j else None,
                error_message=j.error_message if j else None,
                created_at=p.created_at,
            )
        )
    return items


@router.post("/previews", response_model=VoiceDesignPreviewResponse)
def create_voice_design_preview(
    payload: VoiceDesignPreviewCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key),
):
    """
    Creates a voice design request, maps natural language or attributes to OmniVoice instruct tags,
    sets up a preview profile, and queues a preview job.
    """
    if not (payload.voice_request or payload.instruct):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vui lòng cung cấp mô tả giọng nói (voice_request) hoặc chuỗi instruct.",
        )
    if not (payload.preview_text or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vui lòng nhập nội dung nghe thử (preview_text).",
        )

    try:
        preview, job = JobService.create_voice_design_preview(
            db,
            payload.voice_request or (payload.instruct or ""),
            payload.preview_text,
            str(request.base_url).rstrip("/"),
            speed=payload.speed,
            num_step=payload.num_step,
            user_id=current_user.id,
            denoise=payload.denoise,
            guidance_scale=payload.guidance_scale,
            t_shift=payload.t_shift,
            position_temperature=payload.position_temperature,
            class_temperature=payload.class_temperature,
            layer_penalty_factor=payload.layer_penalty_factor,
            duration=payload.duration,
            preprocess_prompt=payload.preprocess_prompt,
            postprocess_output=payload.postprocess_output,
            audio_chunk_duration=payload.audio_chunk_duration,
            audio_chunk_threshold=payload.audio_chunk_threshold,
            language=payload.language,
            pad_duration=payload.pad_duration,
            fade_duration=payload.fade_duration,
            normalize_text=payload.normalize_text if payload.normalize_text is not None else True,
            instruct_override=payload.instruct,
            use_llm=payload.use_llm if payload.use_llm is not None else True,
            llm_profile_id=payload.llm_profile_id,
        )
        return VoiceDesignPreviewResponse(
            preview_id=preview.id,
            job_id=job.id,
            status=job.status,
            message=job.message,
            instruct=preview.instruct,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi tạo voice design preview: {e}",
        )


@router.get("/previews/{preview_id}", response_model=VoiceDesignPreviewDetail)
def get_preview_info(
    preview_id: str,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key),
):
    """Fetches details and current processing state of a voice design preview."""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    q = db.query(VoiceDesignPreview).filter(VoiceDesignPreview.id == preview_id)
    if not current_user.is_admin:
        q = q.filter(VoiceDesignPreview.user_id == current_user.id)
    preview = q.first()
    if not preview:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy Voice Design Preview với ID: {preview_id}",
        )
    return preview


@router.get("/previews/{preview_id}/audio")
def get_preview_audio(
    preview_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key),
):
    """Serves the generated WAV preview audio file using FileResponse."""
    q = db.query(VoiceDesignPreview).filter(VoiceDesignPreview.id == preview_id)
    if not current_user.is_admin:
        q = q.filter(VoiceDesignPreview.user_id == current_user.id)
    preview = q.first()
    if not preview:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy Voice Design Preview với ID: {preview_id}",
        )

    audio_path = preview.preview_audio_path
    if not audio_path or not os.path.exists(audio_path):
        linked_job = db.query(TTSJob).filter(TTSJob.preview_id == preview_id).first()
        if linked_job and linked_job.output_audio_path and os.path.exists(linked_job.output_audio_path):
            audio_path = linked_job.output_audio_path

    if preview.status != "completed" and not (audio_path and os.path.exists(audio_path)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tệp âm thanh nghe thử chưa hoàn thành hoặc không có.",
        )

    if not audio_path or not os.path.exists(audio_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tệp âm thanh không tồn tại trên bộ lưu trữ của máy chủ.",
        )

    return FileResponse(
        audio_path,
        media_type="audio/wav",
        filename=f"preview_{preview_id}.wav",
        content_disposition_type="inline",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Accept-Ranges": "bytes",
        },
    )


@router.post("/previews/{preview_id}/accept", response_model=AcceptPreviewResponse)
def accept_voice_preview(
    preview_id: str,
    payload: Optional[AcceptPreviewRequest] = Body(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key),
):
    """
    Accepts the generated preview voice, copies its audio file to voice_samples,
    and returns a ready-to-use voice_sample_id.
    """
    try:
        preview = (
            db.query(VoiceDesignPreview)
            .filter(
                VoiceDesignPreview.id == preview_id,
                VoiceDesignPreview.user_id == current_user.id,
            )
            .first()
        )
        if not preview:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy Voice Design Preview với ID: {preview_id}",
            )

        sample = JobService.accept_preview(
            db,
            preview_id,
            user_id=current_user.id,
            name=payload.name if payload else None,
            tags=payload.tags if payload else None,
            is_public=payload.is_public if payload else False,
            custom_id=payload.custom_id if payload else None,
        )
        return AcceptPreviewResponse(
            voice_sample_id=sample.id,
            status="ready",
            message="Đã tạo voice sample từ preview.",
        )
    except HTTPException as e:
        raise e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi hệ thống khi chấp nhận bản nghe thử: {e}",
        )


@router.delete("/previews/{preview_id}", response_model=dict)
def delete_voice_design_preview(
    preview_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_or_api_key),
):
    """Deletes a Voice Design preview take and its linked preview job."""
    q = db.query(VoiceDesignPreview).filter(VoiceDesignPreview.id == preview_id)
    if not current_user.is_admin:
        q = q.filter(VoiceDesignPreview.user_id == current_user.id)
    preview = q.first()
    if not preview:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy Voice Design Preview với ID: {preview_id}",
        )

    audio_to_remove = preview.preview_audio_path
    linked_job = db.query(TTSJob).filter(TTSJob.preview_id == preview_id).first()
    if linked_job:
        db.delete(linked_job)
    db.delete(preview)
    db.commit()

    if audio_to_remove and os.path.exists(audio_to_remove):
        try:
            safe_path = ensure_within_directory(settings.STORAGE_DIR, audio_to_remove)
            # Only remove if it resides inside previews/outputs (never voice_samples!)
            voice_samples_abs = os.path.realpath(settings.voice_samples_dir)
            if not safe_path.startswith(voice_samples_abs):
                os.remove(safe_path)
        except Exception:
            pass

    return {"status": "success", "message": "Đã xóa bản nghe thử thiết kế giọng."}
