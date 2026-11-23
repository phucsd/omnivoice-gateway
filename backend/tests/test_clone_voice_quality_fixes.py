import ast
import os
import tempfile

os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("WORKER_MODE", "mock")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import TTSJob, User, VoiceSample
from app.routers.internal_worker import get_next_job
from app.schemas import TTSJobCreate
from app.services.job_service import JobService
from app.services.kaggle_notebook_builder import KaggleNotebookBuilder


def _make_memory_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal(), engine


def test_empty_ref_text_normalized_to_none_across_schema_and_job_service():
    # Schema strips empty/whitespace ref_text and language to None
    req = TTSJobCreate(
        mode="clone_voice",
        text="Xin chào Việt Nam",
        voice_sample_id="nhat",
        ref_text="   ",
        language="  ",
    )
    assert req.ref_text is None
    assert req.language is None

    db, engine = _make_memory_db()
    try:
        user = User(
            id="usr_ref_test",
            username="reftest",
            hashed_password="x",
            is_verified=True,
            is_approved=True,
        )
        # Simulate legacy DB row that stored ref_text="" (empty string)
        legacy_sample = VoiceSample(
            id="nhat_empty",
            user_id=user.id,
            name="Nhật (legacy empty)",
            source_type="uploaded",
            file_path="storage/voice_samples/nhat.wav",
            ref_text="",
            is_public=True,
            status="ready",
        )
        good_sample = VoiceSample(
            id="nhat_filled",
            user_id=user.id,
            name="Nhật (filled)",
            source_type="uploaded",
            file_path="storage/voice_samples/nhat.wav",
            ref_text="Xin kính chào quý bạn và các vị.",
            is_public=True,
            status="ready",
        )
        db.add_all([user, legacy_sample, good_sample])
        db.commit()

        # 1. Passing ref_text="" with a legacy empty sample MUST store None (not "")
        job1 = JobService.create_tts_job(
            db=db,
            mode="clone_voice",
            text="Hội An là minh chứng lịch sử.",
            voice_sample_id="nhat_empty",
            ref_text="",
            user_id=user.id,
        )
        assert job1.ref_text is None

        # 2. Passing ref_text="" with a populated sample MUST inherit sample.ref_text
        job2 = JobService.create_tts_job(
            db=db,
            mode="clone_voice",
            text="Hội An là minh chứng lịch sử.",
            voice_sample_id="nhat_filled",
            ref_text="   ",
            user_id=user.id,
        )
        assert job2.ref_text == "Xin kính chào quý bạn và các vị."

        # 3. Even if a legacy queued job already had ref_text="" in DB, internal_worker must sanitize it
        legacy_job = TTSJob(
            id="job_legacy_empty_rt",
            user_id=user.id,
            job_type="clone_voice",
            text="Kiểm tra nội dung cũ",
            voice_sample_id="nhat_filled",
            ref_text="",
            status="queued",
        )
        db.add(legacy_job)
        db.commit()

        # Drain job1 & job2 first, then legacy_job via get_next_job
        class _DummyReq:
            base_url = "https://voice.oloka.net/"

        res1 = get_next_job(worker_id="worker_1", request=_DummyReq(), db=db, token="ok")
        assert res1.job is not None
        assert res1.job.job_id == job1.id
        assert res1.job.ref_text is None

        res2 = get_next_job(worker_id="worker_1", request=_DummyReq(), db=db, token="ok")
        assert res2.job is not None
        assert res2.job.job_id == job2.id
        assert res2.job.ref_text == "Xin kính chào quý bạn và các vị."

        res3 = get_next_job(worker_id="worker_1", request=_DummyReq(), db=db, token="ok")
        assert res3.job is not None
        assert res3.job.job_id == legacy_job.id
        assert res3.job.ref_text == "Xin kính chào quý bạn và các vị."
    finally:
        db.close()
        engine.dispose()


def test_generated_worker_contains_clone_voice_prompt_guard_and_qa_gate():
    with tempfile.TemporaryDirectory() as td:
        worker_path = KaggleNotebookBuilder.generate_worker_code(
            td,
            job={"worker_id": "worker_1"},
            public_api_url="https://voice.oloka.net",
            worker_token="tok_check",
            is_daemon=True,
        )
        src = open(worker_path, encoding="utf-8").read()
        ast.parse(src)

        assert "def _prepare_clean_clone_prompt(" in src
        assert "def _evaluate_tts_output_qa(" in src
        assert "def _detect_target_language(" in src
        assert "def _gentle_trim_tts_wav(" in src
        # Empty ref_text guard inside create_voice_clone_prompt wrapper
        assert 'kwargs["ref_text"] = None' in src
        # Cache guard prevents caching empty-ref_text prompt
        assert "if prompt_cache_key is not None and built_prompt is not None and eff_ref:" in src

        # Also validate batch template normalizes blank ref_text to None
        batch_path = KaggleNotebookBuilder.generate_worker_code(
            td,
            job={"id": "job_b_rt", "job_type": "clone_voice", "text": "Xin chào", "ref_text": "   "},
            public_api_url="https://voice.oloka.net",
            worker_token="tok_check",
            is_daemon=False,
        )
        bsrc = open(batch_path, encoding="utf-8").read()
        ast.parse(bsrc)
        assert "REF_TEXT = None" in bsrc


def test_file_sqlite_engine_uses_nullpool():
    from sqlalchemy.pool import NullPool
    from app.config import settings
    from app.database import engine

    if settings.DATABASE_URL.startswith("sqlite") and ":memory:" not in settings.DATABASE_URL:
        assert isinstance(engine.pool, NullPool)

