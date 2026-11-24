import os
os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("WORKER_MODE", "mock")
import unittest.mock as mock
import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import TTSJob, WorkerSession
from app.services.kaggle_orchestrator import KaggleOrchestrator

TEST_DB_URL = "sqlite:///:memory:"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(autouse=True)
def setup_test_db():
    Base.metadata.create_all(bind=engine)
    # Reset KaggleOrchestrator class states
    KaggleOrchestrator._boot_attempts.clear()
    KaggleOrchestrator._boot_started_at.clear()
    KaggleOrchestrator._complete_grace_count.clear()
    KaggleOrchestrator._unknown_status_count.clear()
    KaggleOrchestrator._consecutive_poll_failures.clear()
    KaggleOrchestrator._quota_wait_cycles.clear()
    KaggleOrchestrator._push_cooldown_until = 0.0
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def db():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()

def create_mock_job(db, job_id="test_job_1", status="starting_worker", worker_id="worker_1"):
    job = TTSJob(
        id=job_id,
        user_id="test_user",
        job_type="tts",
        text="Hello world test audio",
        status=status,
        worker_id=worker_id,
        message="Starting..."
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job

@patch.object(KaggleOrchestrator, "get_credentials")
def test_poll_queued_status(mock_creds, db):
    mock_creds.return_value = ("testuser", "testkey", "testuser/omnivoice-worker-1", "/tmp/worker")
    job = create_mock_job(db, status="starting_worker", worker_id="worker_1")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='testuser/omnivoice-worker-1 has status "KernelWorkerStatus.QUEUED"'
        )
        KaggleOrchestrator._poll_booting_worker(db, job)
        db.refresh(job)

        assert job.status == "queued_kaggle"
        assert "xếp hàng" in job.message
        assert job.progress == 15

@patch.object(KaggleOrchestrator, "get_credentials")
def test_poll_running_status(mock_creds, db):
    mock_creds.return_value = ("testuser", "testkey", "testuser/omnivoice-worker-1", "/tmp/worker")
    job = create_mock_job(db, status="queued_kaggle", worker_id="worker_1")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='testuser/omnivoice-worker-1 has status "KernelWorkerStatus.RUNNING"'
        )
        KaggleOrchestrator._poll_booting_worker(db, job)
        db.refresh(job)

        assert job.status == "starting_worker"
        assert "tải môi trường" in job.message
        assert job.progress == 25

@patch.object(KaggleOrchestrator, "get_credentials")
def test_poll_complete_grace_period(mock_creds, db):
    mock_creds.return_value = ("testuser", "testkey", "testuser/omnivoice-worker-1", "/tmp/worker")
    job = create_mock_job(db, status="starting_worker", worker_id="worker_1")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='testuser/omnivoice-worker-1 has status "KernelWorkerStatus.COMPLETE"'
        )
        
        # Check 1, 2, 3 should stay in starting_worker (grace period)
        for i in range(1, 4):
            KaggleOrchestrator._poll_booting_worker(db, job)
            db.refresh(job)
            assert job.status == "starting_worker"
            assert job.status != "failed"
            assert KaggleOrchestrator._complete_grace_count[job.id] == i

@patch.object(KaggleOrchestrator, "get_credentials")
def test_poll_complete_exceeds_grace_failover_to_worker_2(mock_creds, db):
    mock_creds.return_value = ("testuser", "testkey", "testuser/omnivoice-worker-1", "/tmp/worker")
    job = create_mock_job(db, status="starting_worker", worker_id="worker_1")

    # Set grace count to 3 so next check (4th) exceeds grace
    KaggleOrchestrator._complete_grace_count[job.id] = 3

    with patch("subprocess.run") as mock_run, \
         patch.object(KaggleOrchestrator, "_trigger_daemon_worker") as mock_trigger:
        
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='testuser/omnivoice-worker-1 has status "KernelWorkerStatus.COMPLETE"'
        )
        
        KaggleOrchestrator._poll_booting_worker(db, job)
        db.refresh(job)

        # Must NOT be failed!
        assert job.status != "failed"
        # Must failover to worker_2
        assert job.worker_id == "worker_2"
        # Must call trigger daemon worker for the new worker
        assert mock_trigger.called

@patch.object(KaggleOrchestrator, "get_credentials")
def test_poll_error_immediate_failover(mock_creds, db):
    mock_creds.return_value = ("testuser", "testkey", "testuser/omnivoice-worker-1", "/tmp/worker")
    job = create_mock_job(db, status="starting_worker", worker_id="worker_1")

    with patch("subprocess.run") as mock_run, \
         patch.object(KaggleOrchestrator, "_trigger_daemon_worker") as mock_trigger:
        
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='testuser/omnivoice-worker-1 has status "KernelWorkerStatus.ERROR"'
        )
        
        KaggleOrchestrator._poll_booting_worker(db, job)
        db.refresh(job)

        assert job.status != "failed"
        assert job.worker_id == "worker_2"
        assert mock_trigger.called

@patch.object(KaggleOrchestrator, "get_credentials")
def test_poll_cancelled_immediate_failover(mock_creds, db):
    mock_creds.return_value = ("testuser", "testkey", "testuser/omnivoice-worker-1", "/tmp/worker")
    job = create_mock_job(db, status="starting_worker", worker_id="worker_1")

    with patch("subprocess.run") as mock_run, \
         patch.object(KaggleOrchestrator, "_trigger_daemon_worker") as mock_trigger:
        
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='testuser/omnivoice-worker-1 has status "KernelWorkerStatus.CANCELLED"'
        )
        
        KaggleOrchestrator._poll_booting_worker(db, job)
        db.refresh(job)

        assert job.status != "failed"
        assert job.worker_id == "worker_2"
        assert mock_trigger.called

@patch.object(KaggleOrchestrator, "get_credentials")
def test_trigger_push_failure_failover(mock_creds, db):
    mock_creds.return_value = ("testuser", "testkey", "testuser/omnivoice-worker-1", "/tmp/worker")
    job = create_mock_job(db, status="starting_worker", worker_id="worker_1")

    with patch("subprocess.Popen") as mock_popen, \
         patch("app.services.kaggle_notebook_builder.KaggleNotebookBuilder.prepare_all"):
        
        # Simulate CLI failure on push for worker_1, then success on worker_2
        process_fail = MagicMock()
        process_fail.communicate.return_value = ("", "Push rejected by Kaggle API")
        process_fail.returncode = 1

        process_success = MagicMock()
        process_success.communicate.return_value = ("Kernel pushed successfully", "")
        process_success.returncode = 0

        mock_popen.side_effect = [process_fail, process_success]

        KaggleOrchestrator._trigger_daemon_worker(db, job)
        db.refresh(job)

        # After failover, worker_id became worker_2 and status became queued_kaggle
        assert job.worker_id == "worker_2"
        assert job.status == "queued_kaggle"

@patch.object(KaggleOrchestrator, "get_credentials")
def test_max_boot_attempts_exhausted(mock_creds, db):
    mock_creds.return_value = ("testuser", "testkey", "testuser/omnivoice-worker-1", "/tmp/worker")
    job = create_mock_job(db, status="starting_worker", worker_id="worker_1")

    # Set boot attempts to 4 (max attempts reached)
    KaggleOrchestrator._boot_attempts[job.id] = 4

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='testuser/omnivoice-worker-1 has status "KernelWorkerStatus.ERROR"'
        )
        
        KaggleOrchestrator._poll_booting_worker(db, job)
        db.refresh(job)

        # Now it should be marked failed
        assert job.status == "failed"
        assert "Không thể khởi động Kaggle Worker sau nhiều lần thử" in job.message
        assert job.id not in KaggleOrchestrator._boot_attempts

def test_ensure_worker_running():
    # Should not raise exception
    KaggleOrchestrator.ensure_worker_running()


@patch.object(KaggleOrchestrator, "get_credentials")
def test_trigger_push_stdout_error_with_zero_exit_code_triggers_failover(mock_creds, db):
    mock_creds.return_value = ("testuser", "testkey", "testuser/omnivoice-worker-1", "/tmp/worker")
    job = create_mock_job(db, status="starting_worker", worker_id="worker_1")

    with patch("subprocess.Popen") as mock_popen, \
         patch("app.services.kaggle_notebook_builder.KaggleNotebookBuilder.prepare_all"):
        # Kaggle CLI returns exit code 0 even when stdout reports "Kernel push error: ..."
        process_cli_err = MagicMock()
        process_cli_err.communicate.return_value = ("Kernel push error: Maximum batch GPU session count of 2 reached.", "")
        process_cli_err.returncode = 0

        process_ok = MagicMock()
        process_ok.communicate.return_value = ("Kernel version 12 successfully pushed.", "")
        process_ok.returncode = 0

        mock_popen.side_effect = [process_cli_err, process_ok]

        KaggleOrchestrator._trigger_daemon_worker(db, job)
        db.refresh(job)

        assert job.worker_id == "worker_2"
        assert job.status == "queued_kaggle"


@patch.object(KaggleOrchestrator, "get_credentials")
def test_poll_running_boot_timeout_triggers_failover(mock_creds, db):
    import time as _time
    mock_creds.return_value = ("testuser", "testkey", "testuser/omnivoice-worker-1", "/tmp/worker")
    job = create_mock_job(db, status="starting_worker", worker_id="worker_1")
    KaggleOrchestrator._boot_started_at[job.id] = _time.monotonic() - 305.0

    with patch("subprocess.run") as mock_run, \
         patch.object(KaggleOrchestrator, "_trigger_daemon_worker") as mock_trigger:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='testuser/omnivoice-worker-1 has status "KernelWorkerStatus.RUNNING"'
        )
        KaggleOrchestrator._poll_booting_worker(db, job)
        db.refresh(job)

        assert job.worker_id == "worker_2"
        assert job.status == "starting_worker"
        assert mock_trigger.called


def test_notebook_builder_avoids_fuse_dataset_and_generates_valid_ast(tmp_path):
    import ast
    import json
    from app.services.kaggle_notebook_builder import KaggleNotebookBuilder

    wdir = str(tmp_path / "worker_ast_check")
    KaggleNotebookBuilder.ensure_worker_dir(wdir)
    meta_path = KaggleNotebookBuilder.generate_metadata(wdir, "testuser", "omnivoice-worker-1", "Omnivoice Worker 1", "NvidiaTeslaT4")
    meta = json.loads(open(meta_path, encoding="utf-8").read())
    assert meta["dataset_sources"] == []

    code_path = KaggleNotebookBuilder.generate_worker_code(
        wdir,
        job={"worker_id": "worker_1"},
        public_api_url="https://example.hf.space",
        worker_token="tok_test_123",
        is_daemon=True,
    )
    code_src = open(code_path, encoding="utf-8").read()
    ast.parse(code_src)
    assert "import numpy as np" in code_src
    assert "start_heartbeat_daemon()" in code_src
    assert "get_asr_whisper_model()" in code_src


def test_any_ready_worker_claims_booting_job_and_syncs_dubbing_failure(db):
    from app.models import VideoDubbingJob
    from app.services.job_service import JobService

    dub = VideoDubbingJob(
        id="vd_sync123",
        user_id="test_user",
        status="separating_audio",
        progress=30,
        message="Waiting...",
        source_type="youtube",
        target_language="Vietnamese",
    )
    db.add(dub)
    sub = TTSJob(
        id="sep_vd_sync123",
        user_id="test_user",
        job_type="separate_audio",
        status="starting_worker",
        worker_id="worker_1",
        message="Booting worker_1...",
    )
    db.add(sub)
    db.commit()

    # worker_2 is ready first and should immediately claim sep_vd_sync123
    claimed = JobService.get_next_job(db, "worker_2")
    assert claimed is not None
    assert claimed.id == "sep_vd_sync123"
    assert claimed.worker_id == "worker_2"

    # Progress update syncs to VideoDubbingJob
    JobService.update_job_status(db, "sep_vd_sync123", "separating_audio", "Đang chạy Demucs...", 65)
    db.refresh(dub)
    assert dub.progress > 30
    assert "Demucs" in dub.message

    # Failure update marks VideoDubbingJob as failed instead of leaving it stuck
    JobService.update_job_status(db, "sep_vd_sync123", "failed", "Lỗi Demucs", 100, "CUDA OOM")
    db.refresh(dub)
    assert dub.status == "failed"
    assert "CUDA OOM" in (dub.error_message or "")


def test_smart_worker_slot_selection_avoids_recently_stopped_slot(db):
    from datetime import datetime, timedelta

    now = datetime.utcnow()
    # worker_1 stopped 10s ago (likely still in Kaggle CANCEL_ACKNOWLEDGED teardown), worker_2 stopped 10 mins ago
    s1 = WorkerSession(id="ws_1", worker_id="worker_1", status="stopped", stopped_at=now - timedelta(seconds=10), last_heartbeat_at=now - timedelta(seconds=10))
    s2 = WorkerSession(id="ws_2", worker_id="worker_2", status="stopped", stopped_at=now - timedelta(minutes=10), last_heartbeat_at=now - timedelta(minutes=10))
    db.add_all([s1, s2])
    db.commit()

    chosen = KaggleOrchestrator._select_best_worker_slot(db, set())
    assert chosen == "worker_2"


def test_notify_new_job_wakes_queue_event():
    KaggleOrchestrator._wake_event.clear()
    assert not KaggleOrchestrator._wake_event.is_set()
    KaggleOrchestrator.notify_new_job()
    assert KaggleOrchestrator._wake_event.is_set()
    KaggleOrchestrator._wake_event.clear()


def test_turbo_worker_code_ast_and_optimizations_present(tmp_path):
    import ast
    from app.services.kaggle_notebook_builder import KaggleNotebookBuilder

    wdir = str(tmp_path / "turbo_worker_check")
    KaggleNotebookBuilder.ensure_worker_dir(wdir)

    daemon_path = KaggleNotebookBuilder.generate_worker_code(
        wdir,
        job={"worker_id": "worker_1"},
        public_api_url="https://example.hf.space",
        worker_token="tok_turbo_123",
        is_daemon=True,
    )
    daemon_src = open(daemon_path, encoding="utf-8").read()
    ast.parse(daemon_src)
    assert 'os.environ["SAFETENSORS_FAST_GPU"] = "1"' in daemon_src
    assert 'os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"' in daemon_src
    assert 'load_asr=False' in daemon_src
    assert '_attach_fast_asr_bridge(model)' in daemon_src
    assert '_preload_cuda_runtime_libs()' in daemon_src
    assert 'model.llm.config.use_cache = False' in daemon_src
    assert '"/dev/shm"' in daemon_src

    batch_path = KaggleNotebookBuilder.generate_worker_code(
        wdir,
        job={"id": "job_b1", "job_type": "auto_voice", "text": "Xin chào"},
        public_api_url="https://example.hf.space",
        worker_token="tok_turbo_123",
        is_daemon=False,
    )
    batch_src = open(batch_path, encoding="utf-8").read()
    ast.parse(batch_src)
    assert 'os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"' in batch_src


@patch.object(KaggleOrchestrator, "get_credentials")
def test_batch_gpu_cap_parks_job_in_queued_and_terminal_boot_failure_syncs_dubbing_job(mock_creds, db):
    from app.models import VideoDubbingJob

    mock_creds.return_value = ("testuser", "testkey", "testuser/omnivoice-worker-1", "/tmp/worker")

    dub = VideoDubbingJob(
        id="vd_capcheck",
        user_id="test_user",
        status="separating_audio",
        progress=30,
        message="Waiting for GPU...",
        source_type="youtube",
        target_language="Vietnamese",
    )
    db.add(dub)
    sub = TTSJob(
        id="sep_vd_capcheck",
        user_id="test_user",
        job_type="separate_audio",
        status="queued",
        worker_id="worker_1",
        message="Queued...",
    )
    db.add(sub)
    db.commit()

    # 1. Both worker_1 and worker_2 return "Maximum batch GPU session count of 2 reached."
    # Job must NOT be marked failed! It must be parked back in "queued" with worker_id=None and cooldown > 0
    with patch("subprocess.Popen") as mock_popen, \
         patch("app.services.kaggle_notebook_builder.KaggleNotebookBuilder.prepare_all"):
        p_cap1 = MagicMock()
        p_cap1.communicate.return_value = ("Kernel push error: Maximum batch GPU session count of 2 reached.", "")
        p_cap1.returncode = 0
        p_cap2 = MagicMock()
        p_cap2.communicate.return_value = ("Kernel push error: Maximum batch GPU session count of 2 reached.", "")
        p_cap2.returncode = 0
        mock_popen.side_effect = [p_cap1, p_cap2]

        KaggleOrchestrator._trigger_daemon_worker(db, sub)
        db.refresh(sub)
        db.refresh(dub)

        assert sub.status == "queued"
        assert sub.worker_id is None
        assert dub.status == "separating_audio"
        assert KaggleOrchestrator._quota_wait_cycles.get(sub.id) == 1
        assert KaggleOrchestrator._push_cooldown_until > 0.0

    # 2. If a non-quota hard boot error genuinely exhausts all 4 attempts, BOTH TTSJob and parent VideoDubbingJob mark failed
    sub.status = "starting_worker"
    sub.worker_id = "worker_1"
    db.commit()
    KaggleOrchestrator._boot_attempts[sub.id] = 4
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='testuser/omnivoice-worker-1 has status "KernelWorkerStatus.ERROR"',
        )
        KaggleOrchestrator._poll_booting_worker(db, sub)
        db.refresh(sub)
        db.refresh(dub)

        assert sub.status == "failed"
        assert dub.status == "failed"
        assert "Kaggle boot error" in (dub.error_message or "")


def test_booting_worker_session_not_counted_as_confirmed_live_worker(db):
    from app.services.worker_session_service import WorkerSessionService

    # Gateway pre-push registers status="booting" (unstarted on Kaggle) -> must NOT count as confirmed live worker!
    WorkerSessionService.register_worker(db, "worker_1", "booting", "Pushing kernel...")
    assert KaggleOrchestrator.has_live_worker(db) is False
    assert KaggleOrchestrator.has_live_or_booting_worker(db) is True

    # Once Python actually boots on Kaggle and registers "starting" / "ready", it counts as confirmed live worker
    WorkerSessionService.register_worker(db, "worker_1", "ready", "Ready on GPU")
    assert KaggleOrchestrator.has_live_worker(db) is True


@patch.object(KaggleOrchestrator, "get_credentials")
def test_batch_cap_immediately_purges_wedged_kernels_on_first_cycle_when_zero_live_workers(mock_creds, db):
    mock_creds.return_value = ("testuser", "testkey", "testuser/omnivoice-worker-1", "/tmp/worker")
    job = create_mock_job(db, job_id="job_wedged_2gpu", status="queued", worker_id="worker_1")

    with patch("subprocess.Popen") as mock_popen, \
         patch("app.services.kaggle_notebook_builder.KaggleNotebookBuilder.prepare_all"), \
         patch.object(KaggleOrchestrator, "_purge_wedged_worker_kernels") as mock_purge:
        p1 = MagicMock()
        p1.communicate.return_value = ("Kernel push error: Maximum batch GPU session count of 2 reached.", "")
        p1.returncode = 0
        p2 = MagicMock()
        p2.communicate.return_value = ("Kernel push error: Maximum batch GPU session count of 2 reached.", "")
        p2.returncode = 0
        mock_popen.side_effect = [p1, p2]

        KaggleOrchestrator._trigger_daemon_worker(db, job)
        db.refresh(job)

        # Must immediately purge wedged QUEUED kernels on first quota hit (wait_cycles == 0 -> 1)
        assert mock_purge.called
        assert job.status == "queued"
        assert job.worker_id is None


@patch.object(KaggleOrchestrator, "get_credentials")
def test_poll_queued_timeout_purges_stuck_slot_before_failover(mock_creds, db):
    import time as _time

    mock_creds.return_value = ("testuser", "testkey", "testuser/omnivoice-worker-1", "/tmp/worker")
    job = create_mock_job(db, job_id="job_q_stuck", status="queued_kaggle", worker_id="worker_1")
    KaggleOrchestrator._boot_started_at[job.id] = _time.monotonic() - 95.0

    with patch("subprocess.run") as mock_run, \
         patch.object(KaggleOrchestrator, "_purge_wedged_worker_kernels") as mock_purge, \
         patch.object(KaggleOrchestrator, "_trigger_daemon_worker") as mock_trigger:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='testuser/omnivoice-worker-1 has status "KernelWorkerStatus.QUEUED"',
        )
        KaggleOrchestrator._poll_booting_worker(db, job)
        db.refresh(job)

        mock_purge.assert_called_once_with(user_id="test_user", job_id="job_q_stuck", slugs=("omnivoice-worker-1",))
        assert job.worker_id == "worker_2"
        assert mock_trigger.called


def test_dead_worker_recovered_at_75s_heartbeat_timeout_and_resets_orphan_jobs(db):
    from datetime import datetime, timedelta
    from app.services.job_service import JobService

    assert KaggleOrchestrator.HEARTBEAT_TIMEOUT_SECONDS == 75

    stale_ts = datetime.utcnow() - timedelta(seconds=80)
    ws = WorkerSession(
        id="ws_dead_80s",
        worker_id="worker_1",
        status="busy",
        started_at=stale_ts,
        last_heartbeat_at=stale_ts,
        current_job_id=None,  # Even if killed before current_job_id heartbeat arrived
        message="Running clone_voice",
    )
    stuck_job = TTSJob(
        id="job_killed_midflight",
        user_id="test_user",
        job_type="clone_voice",
        text="Kiểm tra tự phục hồi sau 75 giây",
        status="preparing_input",
        worker_id="worker_1",
        started_at=stale_ts,
        progress=35,
    )
    db.add_all([ws, stuck_job])
    db.commit()

    assert KaggleOrchestrator.has_live_worker(db) is False

    KaggleOrchestrator._recover_dead_worker_sessions(db)
    db.refresh(ws)
    db.refresh(stuck_job)

    assert ws.status == "stopped"
    assert stuck_job.status == "queued"
    assert stuck_job.worker_id is None
    assert stuck_job.started_at is None

    # Also verify a force-reset worker session cannot steal jobs or resurrect via heartbeat
    ws_reset = WorkerSession(
        id="ws_reset_guard",
        worker_id="worker_2",
        status="stopped",
        started_at=stale_ts,
        last_heartbeat_at=stale_ts,
        stopped_at=datetime.utcnow(),
        message="Reset via /v1/settings/reset-workers",
    )
    db.add(ws_reset)
    db.commit()

    assert JobService.get_next_job(db, "worker_2") is None



