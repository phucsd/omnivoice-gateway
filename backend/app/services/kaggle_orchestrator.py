import os
import subprocess
import threading
from app.config import settings

class KaggleOrchestrator:
    @staticmethod
    def get_credentials(db=None, user_id=None) -> tuple[str, str, str, str]:
        """Resolves credentials, checking database override settings then env configurations."""
        username = settings.KAGGLE_USERNAME
        key = settings.KAGGLE_KEY
        kernel_ref = settings.KAGGLE_KERNEL_REF
        worker_dir = settings.KAGGLE_WORKER_DIR
        
        own_db = False
        if db is None:
            from app.database import SessionLocal
            db = SessionLocal()
            own_db = True
            
        try:
            from app.models import SystemSetting
            db_username = db.query(SystemSetting).filter(SystemSetting.key == "kaggle_username").first()
            db_key = db.query(SystemSetting).filter(SystemSetting.key == "kaggle_key").first()
            db_kernel_ref = db.query(SystemSetting).filter(SystemSetting.key == "kaggle_kernel_ref").first()
            db_worker_dir = db.query(SystemSetting).filter(SystemSetting.key == "kaggle_worker_dir").first()
            
            if db_username and db_username.value.strip():
                username = db_username.value.strip()
            if db_key and db_key.value.strip():
                key = db_key.value.strip()
            if db_kernel_ref and db_kernel_ref.value.strip():
                kernel_ref = db_kernel_ref.value.strip()
            if db_worker_dir and db_worker_dir.value.strip():
                worker_dir = db_worker_dir.value.strip()

            if user_id:
                from app.models import UserSetting
                u_username = db.query(UserSetting).filter(UserSetting.user_id == user_id, UserSetting.key == "kaggle_username").first()
                u_key = db.query(UserSetting).filter(UserSetting.user_id == user_id, UserSetting.key == "kaggle_key").first()
                u_kernel_ref = db.query(UserSetting).filter(UserSetting.user_id == user_id, UserSetting.key == "kaggle_kernel_ref").first()
                u_worker_dir = db.query(UserSetting).filter(UserSetting.user_id == user_id, UserSetting.key == "kaggle_worker_dir").first()
                
                if u_username and u_username.value.strip():
                    username = u_username.value.strip()
                if u_key and u_key.value.strip():
                    key = u_key.value.strip()
                if u_kernel_ref and u_kernel_ref.value.strip():
                    kernel_ref = u_kernel_ref.value.strip()
                if u_worker_dir and u_worker_dir.value.strip():
                    worker_dir = u_worker_dir.value.strip()
        except Exception as e:
            print(f"[KaggleOrchestrator] Error reading settings from DB: {e}")
        finally:
            if own_db:
                db.close()
                
        # Sanitize kernel_ref if it is a URL or construct it
        if kernel_ref:
            if "kaggle.com/code/" in kernel_ref:
                parts = kernel_ref.split("kaggle.com/code/")[-1].strip("/").split("/")
                if len(parts) >= 2:
                    kernel_ref = f"{parts[0]}/{parts[1]}"
        
        if not kernel_ref or "/" not in kernel_ref:
            kernel_ref = f"{username}/omnivoice-worker"
            
        return username, key, kernel_ref, worker_dir

    @staticmethod
    def is_configured(db=None, user_id=None) -> bool:
        """Checks if all necessary Kaggle configuration variables are set."""
        username, key, kernel_ref, worker_dir = KaggleOrchestrator.get_credentials(db, user_id)
        return bool(username and key and kernel_ref and worker_dir)

    @staticmethod
    def has_live_worker(db) -> bool:
        """
        Queries the database to check if there is an active worker session 
        that is not stopped or failed and has sent a heartbeat recently.
        """
        from app.models import WorkerSession
        from datetime import datetime, timedelta
        
        # Consider a worker "live" if it is in an active status and has heartbeat within the last 180 seconds
        cutoff = datetime.utcnow() - timedelta(seconds=180)
        
        session = db.query(WorkerSession).filter(
            WorkerSession.status.in_(["starting", "loading_model", "ready", "busy", "idle"]),
            WorkerSession.last_heartbeat_at >= cutoff
        ).first()
        
        return session is not None

    _runner_thread = None
    _runner_stop_event = None
    _wake_event = threading.Event()
    _consecutive_poll_failures = {}
    _unknown_status_count = {}
    _complete_grace_count = {}
    _boot_attempts = {}
    _boot_started_at = {}
    _last_poll_at = {}
    _quota_wait_cycles = {}
    _push_cooldown_until = 0.0
    _prewarm_lock = threading.Lock()
    _last_prewarm_at = 0.0

    @classmethod
    def _cleanup_job_tracking(cls, job_id: str):
        """Cleans up in-memory tracking states for a finished or departed job."""
        cls._consecutive_poll_failures.pop(job_id, None)
        cls._unknown_status_count.pop(job_id, None)
        cls._complete_grace_count.pop(job_id, None)
        cls._boot_attempts.pop(job_id, None)
        cls._boot_started_at.pop(job_id, None)
        cls._last_poll_at.pop(job_id, None)
        cls._quota_wait_cycles.pop(job_id, None)

    @classmethod
    def notify_new_job(cls):
        """Wakes up the background queue runner immediately (<1ms) instead of waiting for sleep timeout."""
        if cls._wake_event:
            cls._wake_event.set()

    @classmethod
    def ensure_worker_running(cls, db=None):
        """Ensures background queue runner is active and wakes it immediately."""
        cls.start_queue_runner()
        cls.notify_new_job()

    @classmethod
    def _select_best_worker_slot(cls, db, total_active_or_booting: set) -> str:
        """
        Selects the optimal worker slot. When both slots are free, prefers the slot
        that has been stopped/idle longest to avoid Kaggle's ~30-35s container teardown queue.
        """
        if "worker_1" in total_active_or_booting:
            return "worker_2"
        if "worker_2" in total_active_or_booting:
            return "worker_1"

        try:
            from app.models import WorkerSession
            s1 = db.query(WorkerSession).filter(WorkerSession.worker_id == "worker_1").first()
            s2 = db.query(WorkerSession).filter(WorkerSession.worker_id == "worker_2").first()
            if s1 and not s2:
                # worker_2 has never recently shut down -> guaranteed clean slot if worker_1 stopped recently
                t1 = s1.stopped_at or s1.last_heartbeat_at
                if t1:
                    from datetime import datetime
                    if (datetime.utcnow() - t1).total_seconds() < 60:
                        return "worker_2"
            elif s1 and s2:
                s1_bad = any(k in (s1.message or "").lower() for k in ("timeout", "push failed", "error"))
                s2_bad = any(k in (s2.message or "").lower() for k in ("timeout", "push failed", "error"))
                if s1_bad and not s2_bad:
                    return "worker_2"
                if s2_bad and not s1_bad:
                    return "worker_1"
                t1 = s1.stopped_at or s1.last_heartbeat_at
                t2 = s2.stopped_at or s2.last_heartbeat_at
                if t1 and t2 and t2 < t1:
                    return "worker_2"
        except Exception:
            pass
        return "worker_1"

    @classmethod
    def prewarm_worker_async(cls, user_id: str = None):
        """
        Proactively boots an idle Kaggle GPU worker slot in the background (e.g. during YouTube download)
        so the GPU container is already warm or booting by the time audio tasks are queued.
        """
        if settings.WORKER_MODE != "kaggle" or os.environ.get("APP_ENV") == "testing":
            return
        import time as _time
        with cls._prewarm_lock:
            now = _time.monotonic()
            if now - cls._last_prewarm_at < 30.0:
                return
            cls._last_prewarm_at = now

        def _bg_prewarm():
            from app.database import SessionLocal
            from app.models import TTSJob
            from app.services.worker_session_service import WorkerSessionService
            from app.services.kaggle_notebook_builder import KaggleNotebookBuilder

            db = SessionLocal()
            try:
                if cls.has_live_worker(db):
                    return
                booting = db.query(TTSJob).filter(TTSJob.status.in_(["starting_worker", "queued_kaggle"])).first()
                if booting:
                    return
                username, key, _, default_worker_dir = cls.get_credentials(db, user_id)
                if not username or not key:
                    return
                slot = cls._select_best_worker_slot(db, set())
                worker_dir_path = f"{default_worker_dir}-2" if slot == "worker_2" else f"{default_worker_dir}-1"
                worker_dir_abs = os.path.abspath(worker_dir_path)

                KaggleNotebookBuilder.prepare_all(job={"worker_id": slot, "user_id": user_id}, db=db, is_daemon=True, user_id=user_id)
                WorkerSessionService.register_worker(db, slot, "starting", "Pre-warming Kaggle GPU worker...")

                env = os.environ.copy()
                env["KAGGLE_USERNAME"] = username
                env["KAGGLE_KEY"] = key
                env["KAGGLE_API_TOKEN"] = key
                env["PYTHONUTF8"] = "1"
                import sys
                cmd = [
                    sys.executable, "-c", "from kaggle.cli import main; main()",
                    "kernels", "push",
                    "-p", worker_dir_abs,
                    "--timeout", str(settings.KAGGLE_TIMEOUT_SECONDS),
                    "--accelerator", "NvidiaTeslaT4",
                ]
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True, shell=False)
                out, err = proc.communicate()
                out_str = (out or err or "").strip()
                out_low = out_str.lower()
                print(f"[KaggleOrchestrator] Pre-warm push on {slot} exited {proc.returncode}: {out_str}")
                if (
                    proc.returncode != 0
                    or "kernel push error" in out_low
                    or "maximum batch gpu session count" in out_low
                    or out_low.startswith("error:")
                ):
                    WorkerSessionService.shutdown_worker(db, slot, f"Pre-warm skipped: {out_str[:160]}")
            except Exception as pw_err:
                print(f"[KaggleOrchestrator] Pre-warm notice: {pw_err}")
            finally:
                db.close()

        threading.Thread(target=_bg_prewarm, daemon=True).start()

    @classmethod
    def _release_wedged_kaggle_gpu_sessions(cls, db, user_id: str = None, job_id: str = None):
        """
        Inspects owned Kaggle omnivoice* worker kernels when Kaggle's 2-GPU batch session cap is reached
        while 0 live workers are heartbeating with the Gateway, and cancels wedged QUEUED/zombie GPU sessions
        by pushing a lightweight CPU-only (enable_gpu=False) release script.
        """
        import csv
        import io
        import json
        import shutil
        import sys
        import tempfile

        if os.environ.get("APP_ENV") == "testing":
            return

        username, key, _, _ = cls.get_credentials(db, user_id)
        if not username or not key:
            return

        env = os.environ.copy()
        env["KAGGLE_USERNAME"] = username
        env["KAGGLE_KEY"] = key
        env["KAGGLE_API_TOKEN"] = key
        env["PYTHONUTF8"] = "1"

        candidate_refs = [
            f"{username}/omnivoice-worker-1",
            f"{username}/omnivoice-worker-2",
            f"{username}/omnivoice-worker",
            f"{username}/omnivoice-dubbing-worker",
        ]
        try:
            ls_res = subprocess.run(
                [sys.executable, "-c", "from kaggle.cli import main; main()", "kernels", "list", "--mine", "--page-size", "20", "--csv"],
                capture_output=True, env=env, text=True, timeout=20,
            )
            if ls_res.returncode == 0 and ls_res.stdout:
                for row in csv.DictReader(io.StringIO(ls_res.stdout)):
                    ref = (row.get("ref") or "").strip()
                    if ref and "omnivoice" in ref.lower() and ref not in candidate_refs:
                        candidate_refs.append(ref)
        except Exception as ls_err:
            print(f"[KaggleOrchestrator] Notice listing mine kernels: {ls_err}")

        released = []
        statuses_log = []
        for k_ref in candidate_refs:
            try:
                st_res = subprocess.run(
                    [sys.executable, "-c", "from kaggle.cli import main; main()", "kernels", "status", k_ref],
                    capture_output=True, env=env, text=True, timeout=15,
                )
                st_out = (st_res.stdout or st_res.stderr or "").strip()
                statuses_log.append(f"{k_ref}={st_out}")
                st_low = st_out.lower()
                if "queued" in st_low or "running" in st_low:
                    tmp_d = tempfile.mkdtemp(prefix="kag_rel_")
                    try:
                        pull_r = subprocess.run(
                            [sys.executable, "-c", "from kaggle.cli import main; main()", "kernels", "pull", "-m", "-p", tmp_d, k_ref],
                            capture_output=True, env=env, text=True, timeout=20,
                        )
                        meta_p = os.path.join(tmp_d, "kernel-metadata.json")
                        if pull_r.returncode == 0 and os.path.exists(meta_p):
                            with open(meta_p, "r", encoding="utf-8") as mf:
                                meta = json.load(mf)
                        else:
                            slug_part = k_ref.split("/")[-1]
                            meta = {
                                "id": k_ref,
                                "title": slug_part.replace("-", " ").title(),
                                "code_file": "omnivoice_worker.py",
                                "language": "python",
                                "kernel_type": "script",
                                "is_private": True,
                            }
                        meta["enable_gpu"] = False
                        meta["enable_internet"] = False
                        meta.pop("accelerator", None)
                        meta.pop("machine_shape", None)
                        code_f = meta.get("code_file") or "omnivoice_worker.py"
                        meta["code_file"] = code_f
                        with open(meta_p, "w", encoding="utf-8") as mf:
                            json.dump(meta, mf, indent=2)
                        code_p = os.path.join(tmp_d, code_f)
                        if code_f.endswith(".ipynb"):
                            nb = {
                                "cells": [{"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": ["print('Released wedged GPU session')\n"]}],
                                "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
                                "nbformat": 4, "nbformat_minor": 5,
                            }
                            with open(code_p, "w", encoding="utf-8") as cf:
                                json.dump(nb, cf)
                        else:
                            with open(code_p, "w", encoding="utf-8") as cf:
                                cf.write("print('Released wedged GPU session')\n")

                        push_r = subprocess.run(
                            [sys.executable, "-c", "from kaggle.cli import main; main()", "kernels", "push", "-p", tmp_d],
                            capture_output=True, env=env, text=True, timeout=25,
                        )
                        p_out = (push_r.stdout or push_r.stderr or "").strip()
                        print(f"[KaggleOrchestrator] CPU release push on {k_ref} -> rc={push_r.returncode}: {p_out}")
                        released.append(f"{k_ref} ({p_out[:60]})")
                    finally:
                        shutil.rmtree(tmp_d, ignore_errors=True)
            except Exception as rel_err:
                print(f"[KaggleOrchestrator] Error checking/releasing {k_ref}: {rel_err}")

        summary_msg = f"Checked Kaggle kernels [{'; '.join(statuses_log)}] -> Released: {released}"
        print(f"[KaggleOrchestrator] {summary_msg}")
        if job_id and job_id.startswith(("sep_", "asr_", "dub_")):
            try:
                from app.services.video_dubbing_service import VideoDubbingService
                _, dub_id = job_id.split("_", 1)
                VideoDubbingService.log_to_job(dub_id, f"[KAGGLE AUTO-HEAL] {summary_msg}")
            except Exception:
                pass

    @classmethod
    def _failover_or_retry_worker(cls, db, job, status_output: str, reason_label: str):
        """
        Handles worker failures (COMPLETE after grace, ERROR, CANCELLED, push failure)
        by shutting down the failed worker session (unless actively heartbeating),
        failing over or deferring when Kaggle's 2-GPU batch session cap is active,
        and syncing terminal failures to parent VideoDubbingJob via JobService.
        """
        import time as _time
        from datetime import datetime, timedelta
        from app.models import WorkerSession
        from app.services.worker_session_service import WorkerSessionService
        from app.services.job_service import JobService

        old_worker_id = job.worker_id or "worker_1"
        cutoff = datetime.utcnow() - timedelta(seconds=180)
        old_sess = db.query(WorkerSession).filter(WorkerSession.worker_id == old_worker_id).first()
        is_old_actively_serving = (
            old_sess is not None
            and old_sess.status in ("ready", "busy", "idle")
            and old_sess.last_heartbeat_at is not None
            and old_sess.last_heartbeat_at >= cutoff
        )
        if not is_old_actively_serving:
            WorkerSessionService.shutdown_worker(db, old_worker_id, f"{reason_label}: {status_output}")

        cls._complete_grace_count.pop(job.id, None)
        cls._unknown_status_count.pop(job.id, None)
        cls._consecutive_poll_failures.pop(job.id, None)

        attempts = cls._boot_attempts.get(job.id, 0)
        max_attempts = 4  # Allows alternating worker_1 <-> worker_2 up to 2 rounds
        status_lower = (status_output or "").lower()
        is_batch_cap_reached = "maximum batch gpu session count" in status_lower

        # If Kaggle reports 2 batch GPU sessions are already active:
        # - Allow 1 alternate-slot probe only when no live worker is registered yet
        # - Otherwise park the job in 'queued' with a 15s push cooldown so the already-running
        #   Kaggle GPU worker(s) can claim it or finish teardown without killing the job in 7s!
        if is_batch_cap_reached and (cls.has_live_worker(db) or attempts >= 2):
            wait_cycles = cls._quota_wait_cycles.get(job.id, 0)
            if wait_cycles < 15:
                cls._quota_wait_cycles[job.id] = wait_cycles + 1
                cls._boot_attempts[job.id] = 0
                if not cls.has_live_worker(db) and wait_cycles in (1, 4, 8):
                    threading.Thread(
                        target=cls._release_wedged_kaggle_gpu_sessions,
                        args=(None, job.user_id, job.id),
                        daemon=True,
                    ).start()
                cls._push_cooldown_until = _time.monotonic() + 15.0
                print(
                    f"[KaggleOrchestrator] Kaggle GPU session limit (2) active for job {job.id} "
                    f"(wait cycle {wait_cycles + 1}/15). Parking job in 'queued' for running GPU workers to claim."
                )
                JobService.update_job_status(
                    db=db,
                    job_id=job.id,
                    status="queued",
                    message="Đang chờ máy chủ GPU Kaggle đang chạy nhận tác vụ hoặc giải phóng slot...",
                    progress=12,
                )
                job.worker_id = None
                db.commit()
                return

        if attempts < max_attempts:
            new_worker_id = "worker_2" if old_worker_id == "worker_1" else "worker_1"
            print(f"[KaggleOrchestrator] Worker {old_worker_id} {reason_label} ('{status_output}'). Failing over to {new_worker_id} for job {job.id} (attempt {attempts + 1}/{max_attempts})...")

            job.worker_id = new_worker_id
            JobService.update_job_status(
                db=db,
                job_id=job.id,
                status="starting_worker",
                message=f"Máy chủ {old_worker_id} ({reason_label}). Đang tự động chuyển sang máy chủ dự phòng ({new_worker_id}) và tạo GPU mới...",
                progress=10,
            )
            db.refresh(job)

            cls._trigger_daemon_worker(db, job)
        else:
            print(f"[KaggleOrchestrator] Job {job.id} exceeded max boot attempts ({max_attempts}). Failing job.")
            JobService.update_job_status(
                db=db,
                job_id=job.id,
                status="failed",
                message="Không thể khởi động Kaggle Worker sau nhiều lần thử lại trên cả worker-1 và worker-2.",
                progress=100,
                error_message=f"Kaggle boot error after {max_attempts} attempts: {status_output}",
            )
            cls._cleanup_job_tracking(job.id)

    @classmethod
    def start_queue_runner(cls):
        """Starts the background queue runner if it is not already running."""
        if settings.WORKER_MODE != "kaggle" or os.environ.get("APP_ENV") == "testing":
            print("[KaggleOrchestrator] Not in 'kaggle' mode or in testing env. Queue runner skipped.")
            return

        if cls._runner_thread and cls._runner_thread.is_alive():
            print("[KaggleOrchestrator] Queue runner is already running.")
            return

        print("[KaggleOrchestrator] Starting queue runner background thread...")
        cls._runner_stop_event = threading.Event()
        cls._runner_thread = threading.Thread(target=cls._queue_loop, daemon=True)
        cls._runner_thread.start()
        print("[KaggleOrchestrator] Queue runner background thread started successfully.")

    @classmethod
    def stop_queue_runner(cls):
        """Stops the background queue runner."""
        if cls._runner_stop_event:
            cls._runner_stop_event.set()
        if cls._wake_event:
            cls._wake_event.set()
        if cls._runner_thread:
            cls._runner_thread.join(timeout=5)
            print("[KaggleOrchestrator] Queue runner background thread stopped.")

    @classmethod
    def _queue_loop(cls):
        import time
        from app.database import SessionLocal
        from app.models import TTSJob, WorkerSession
        from datetime import datetime, timedelta

        print("[KaggleOrchestrator] Entering queue runner loop.")
        while cls._runner_stop_event and not cls._runner_stop_event.is_set():
            db = SessionLocal()
            try:
                # 1. Recover stuck jobs from dead workers (no heartbeat for 180s)
                cutoff = datetime.utcnow() - timedelta(seconds=180)
                dead_sessions = db.query(WorkerSession).filter(
                    WorkerSession.status.in_(["starting", "loading_model", "ready", "busy", "idle"]),
                    WorkerSession.last_heartbeat_at < cutoff
                ).all()
                
                for session in dead_sessions:
                    print(f"[KaggleOrchestrator] Worker {session.worker_id} has died (no heartbeat for 180s). Stopping session.")
                    session.status = "stopped"
                    session.stopped_at = datetime.utcnow()
                    session.message = "Stuck worker detected and stopped by Gateway."
                    
                    # Reset its current job to queued if it was in progress
                    if session.current_job_id:
                        stuck_job = db.query(TTSJob).filter(TTSJob.id == session.current_job_id).first()
                        if stuck_job and stuck_job.status not in ["completed", "failed"]:
                            print(f"[KaggleOrchestrator] Resetting stuck job {stuck_job.id} to 'queued'")
                            stuck_job.status = "queued"
                            stuck_job.message = "Hàng đợi tự động reset do máy chủ xử lý mất kết nối."
                            stuck_job.progress = 0
                            stuck_job.worker_id = None
                    db.commit()

                # 2. Count active/live worker sessions
                active_sessions = db.query(WorkerSession).filter(
                    WorkerSession.status.in_(["starting", "loading_model", "ready", "busy", "idle"]),
                    WorkerSession.last_heartbeat_at >= cutoff
                ).all()
                active_worker_ids = {s.worker_id for s in active_sessions}

                # 3. Find and check booting jobs
                booting_jobs = db.query(TTSJob).filter(
                    TTSJob.status.in_(["starting_worker", "queued_kaggle"])
                ).all()

                # Poll status for each booting job (throttled to ~6s per job so rapid wakeups don't spam Kaggle API)
                now_mono = time.monotonic()
                for b_job in booting_jobs:
                    last_p = cls._last_poll_at.get(b_job.id, 0.0)
                    if now_mono - last_p >= 6.0:
                        cls._last_poll_at[b_job.id] = now_mono
                        cls._poll_booting_worker(db, b_job)

                # Clean up boot-attempt tracking for jobs that have left the booting states
                booting_job_ids = {j.id for j in booting_jobs}
                queued_jobs = db.query(TTSJob).filter(
                    TTSJob.status == "queued"
                ).order_by(TTSJob.created_at.asc()).all()
                queued_job_ids = {j.id for j in queued_jobs}

                tracked_boot_ids = (
                    set(cls._boot_attempts.keys())
                    | set(cls._complete_grace_count.keys())
                    | set(cls._consecutive_poll_failures.keys())
                    | set(cls._unknown_status_count.keys())
                )
                for j_id in (tracked_boot_ids - booting_job_ids):
                    cls._consecutive_poll_failures.pop(j_id, None)
                    cls._unknown_status_count.pop(j_id, None)
                    cls._complete_grace_count.pop(j_id, None)
                    cls._boot_attempts.pop(j_id, None)
                    cls._boot_started_at.pop(j_id, None)
                    cls._last_poll_at.pop(j_id, None)

                for q_id in (set(cls._quota_wait_cycles.keys()) - booting_job_ids - queued_job_ids):
                    cls._quota_wait_cycles.pop(q_id, None)

                # Determine active or booting worker IDs
                booting_worker_ids = {j.worker_id for j in booting_jobs if j.worker_id}
                total_active_or_booting = active_worker_ids.union(booting_worker_ids)
                free_or_warming_workers = {
                    s.worker_id
                    for s in active_sessions
                    if s.status in ("starting", "loading_model", "ready", "idle")
                }

                # Trigger a new worker slot only when needed:
                # - Never push during a quota cooldown window
                # - Do NOT redundantly push a 2nd GPU container for 1 queued job when a ready/idle/pre-warming worker is already available
                should_boot_another = (
                    len(total_active_or_booting) < 2
                    and now_mono >= cls._push_cooldown_until
                    and len(queued_jobs) > 0
                    and (len(total_active_or_booting) == 0 or len(queued_jobs) > len(free_or_warming_workers))
                )
                if should_boot_another:
                    # Pick a queued job beyond those already covered by free/warming workers
                    target_idx = min(len(free_or_warming_workers), len(queued_jobs) - 1)
                    next_job = queued_jobs[target_idx]
                    assigned_worker_id = cls._select_best_worker_slot(db, total_active_or_booting)

                    print(f"[KaggleOrchestrator] Queued job {next_job.id} needs worker. Active/booting: {total_active_or_booting} (free/warming={free_or_warming_workers}). Triggering slot {assigned_worker_id}...")
                    next_job.worker_id = assigned_worker_id
                    db.commit()
                    db.refresh(next_job)

                    cls._trigger_daemon_worker(db, next_job)
            except Exception as e:
                print(f"[KaggleOrchestrator] Exception in queue loop: {e}")
                err_low = str(e).lower()
                if "malformed" in err_low or "corrupt" in err_low or "disk image" in err_low:
                    try:
                        db.close()
                    except Exception:
                        pass
                    try:
                        from app.database import verify_and_heal_database
                        verify_and_heal_database(settings.DATABASE_URL, force_rebuild=True)
                    except Exception as heal_e:
                        print(f"[KaggleOrchestrator] Self-heal error: {heal_e}")
            finally:
                try:
                    db.close()
                except Exception:
                    pass

            # Event-driven wait: wakes immediately (<1ms) on new job, or polls every 3s
            if cls._wake_event:
                cls._wake_event.wait(timeout=3.0)
                cls._wake_event.clear()
            else:
                time.sleep(3.0)

    @classmethod
    def _trigger_daemon_worker(cls, db, job):
        """Prepares daemon worker code and pushes it to Kaggle."""
        import time as _time
        cls._boot_attempts[job.id] = cls._boot_attempts.get(job.id, 0) + 1
        cls._boot_started_at[job.id] = _time.monotonic()
        current_attempt = cls._boot_attempts[job.id]
        print(f"[KaggleOrchestrator] Triggering daemon worker for job context {job.id} on slot {job.worker_id} (attempt {current_attempt}/4)")
        
        # Update status to starting_worker
        job.status = "starting_worker"
        job.message = f"Đang chuẩn bị khởi động máy chủ Kaggle ({job.worker_id})..."
        job.progress = 5
        db.commit()
        db.refresh(job)

        # Register or update worker session in DB to immediately mark it as starting
        from app.services.worker_session_service import WorkerSessionService
        WorkerSessionService.register_worker(db, job.worker_id, "starting", f"Kaggle kernel is being pushed (attempt {current_attempt})...")

        # Resolve credentials
        username, key, default_kernel_ref, default_worker_dir = cls.get_credentials(db, job.user_id)
        if not username or not key:
            job.status = "failed"
            job.message = "Chưa cấu hình tài khoản Kaggle."
            job.error_message = "Kaggle username or key missing in settings."
            db.commit()
            return

        # Resolve dynamic slot slug and directory
        if job.worker_id == "worker_2":
            kernel_ref = f"{username}/omnivoice-worker-2"
            worker_dir_path = f"{default_worker_dir}-2"
        else:
            kernel_ref = f"{username}/omnivoice-worker-1"
            worker_dir_path = f"{default_worker_dir}-1"

        worker_dir_abs = os.path.abspath(worker_dir_path)

        # Call builder to prepare files
        try:
            from app.services.kaggle_notebook_builder import KaggleNotebookBuilder
            KaggleNotebookBuilder.prepare_all(job=job, db=db, is_daemon=True, user_id=job.user_id)
        except Exception as e:
            err_str = str(e)
            print(f"[KaggleOrchestrator] Builder failed for {job.worker_id}: {err_str}")
            cls._failover_or_retry_worker(db, job, err_str, "Builder Error")
            return

        # Prepare CLI environment with credentials
        env = os.environ.copy()
        env["KAGGLE_USERNAME"] = username
        env["KAGGLE_KEY"] = key
        env["KAGGLE_API_TOKEN"] = key
        env["PYTHONUTF8"] = "1"

        # Resolve accelerator and timeout settings
        accelerator = settings.KAGGLE_ACCELERATOR
        timeout = settings.KAGGLE_TIMEOUT_SECONDS
        
        db_acc = None
        db_timeout = None
        if job.user_id:
            from app.models import UserSetting
            db_acc = db.query(UserSetting).filter(UserSetting.user_id == job.user_id, UserSetting.key == "kaggle_accelerator").first()
            db_timeout = db.query(UserSetting).filter(UserSetting.user_id == job.user_id, UserSetting.key == "kaggle_timeout_seconds").first()
        else:
            from app.models import SystemSetting
            db_acc = db.query(SystemSetting).filter(SystemSetting.key == "kaggle_accelerator").first()
            db_timeout = db.query(SystemSetting).filter(SystemSetting.key == "kaggle_timeout_seconds").first()
            
        if db_acc and db_acc.value.strip():
            accelerator = db_acc.value.strip()
        if db_timeout and db_timeout.value.strip():
            try:
                timeout = int(db_timeout.value.strip())
            except ValueError:
                pass

        # Map accelerator
        mapped_acc = "NvidiaTeslaT4"
        if accelerator:
            acc_lower = accelerator.lower()
            if "p100" in acc_lower:
                mapped_acc = "NvidiaTeslaP100"
            elif "t4" in acc_lower:
                mapped_acc = "NvidiaTeslaT4"
            else:
                mapped_acc = accelerator

        import sys
        cmd = [
            sys.executable, "-c", "from kaggle.cli import main; main()", 
            "kernels", "push", 
            "-p", worker_dir_abs, 
            "--timeout", str(timeout), 
            "--accelerator", mapped_acc
        ]

        print(f"[KaggleOrchestrator] Pushing daemon worker kernel: {' '.join(cmd)}")
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                shell=False
            )
            stdout, stderr = process.communicate()
            
            stdout_clean = (stdout or "").strip()
            stderr_clean = (stderr or "").strip()
            stdout_lower = stdout_clean.lower()
            has_cli_push_error = (
                "kernel push error" in stdout_lower
                or stdout_lower.startswith("error:")
                or "maximum batch gpu session count" in stdout_lower
                or "403 - forbidden" in stdout_lower
                or "401 - unauthorized" in stdout_lower
            )

            if process.returncode == 0 and not has_cli_push_error:
                print(f"[KaggleOrchestrator] Daemon worker kernel pushed successfully on {job.worker_id}. Output: {stdout_clean}")
                job.status = "queued_kaggle"
                job.message = f"Khởi động máy chủ Kaggle ({job.worker_id}). Đang chờ hàng đợi..."
                job.progress = 10
                db.commit()
            else:
                err_msg = stderr_clean or stdout_clean or "Unknown error."
                print(f"[KaggleOrchestrator] Daemon pushing failed on {job.worker_id}: {err_msg}")
                cls._failover_or_retry_worker(db, job, err_msg, "Push failed")
        except Exception as e:
            print(f"[KaggleOrchestrator] Exception pushing daemon worker on {job.worker_id}: {e}")
            cls._failover_or_retry_worker(db, job, str(e), "Exception pushing")

    @classmethod
    def _poll_booting_worker(cls, db, job):
        """Polls Kaggle for the booting worker's status."""
        username, key, default_kernel_ref, default_worker_dir = cls.get_credentials(db, job.user_id)
        if not username or not key:
            job.status = "failed"
            job.message = "Chưa cấu hình tài khoản Kaggle."
            job.error_message = "Kaggle username or key missing during boot poll."
            db.commit()
            return

        if job.worker_id == "worker_2":
            kernel_ref = f"{username}/omnivoice-worker-2"
        else:
            kernel_ref = f"{username}/omnivoice-worker-1"

        env = os.environ.copy()
        env["KAGGLE_USERNAME"] = username
        env["KAGGLE_KEY"] = key
        env["KAGGLE_API_TOKEN"] = key
        env["PYTHONUTF8"] = "1"

        import sys
        cmd = [sys.executable, "-c", "from kaggle.cli import main; main()", "kernels", "status", kernel_ref]
        
        try:
            res = subprocess.run(cmd, capture_output=True, env=env, text=True, shell=False)
            if res.returncode != 0:
                err_msg = res.stderr.strip() or res.stdout.strip() or "Kaggle status command failed."
                print(f"[KaggleOrchestrator] Warning: kaggle kernels status CLI failed for {job.worker_id}: {err_msg}")
                # Increment failure count
                cls._consecutive_poll_failures[job.id] = cls._consecutive_poll_failures.get(job.id, 0) + 1
                if cls._consecutive_poll_failures[job.id] >= 15:  # 15 checks * 10s = 150s
                    cls._failover_or_retry_worker(db, job, err_msg, "Status check failed")
                return

            cls._consecutive_poll_failures[job.id] = 0
            status_output = res.stdout.strip()
            print(f"[KaggleOrchestrator] Kaggle status for booting worker ({job.worker_id}): {status_output}")

            status_lower = status_output.lower()
            
            if "queued" in status_lower:
                cls._unknown_status_count.pop(job.id, None)
                cls._complete_grace_count.pop(job.id, None)
                import time as _time
                boot_t0 = cls._boot_started_at.get(job.id)
                if boot_t0 is None:
                    cls._boot_started_at[job.id] = _time.monotonic()
                    queued_elapsed = 0.0
                else:
                    queued_elapsed = _time.monotonic() - boot_t0

                if queued_elapsed > 60:
                    print(f"[KaggleOrchestrator] Worker {job.worker_id} stuck in QUEUED for {queued_elapsed:.0f}s. Failing over to alternate slot...")
                    cls._failover_or_retry_worker(db, job, f"Queued timeout ({int(queued_elapsed)}s)", "Worker Queued Timeout")
                else:
                    job.status = "queued_kaggle"
                    job.message = f"Kaggle ({job.worker_id}) chưa cấp runtime/GPU, đang xếp hàng..."
                    job.progress = 15
                    db.commit()
            elif "running" in status_lower:
                cls._unknown_status_count.pop(job.id, None)
                cls._complete_grace_count.pop(job.id, None)
                import time as _time
                boot_t0 = cls._boot_started_at.get(job.id)
                if boot_t0 is None:
                    cls._boot_started_at[job.id] = _time.monotonic()
                    boot_elapsed = 0.0
                else:
                    boot_elapsed = _time.monotonic() - boot_t0

                if boot_elapsed > 300:
                    print(f"[KaggleOrchestrator] Worker {job.worker_id} stuck booting for {boot_elapsed:.0f}s without claiming job {job.id}. Triggering failover...")
                    cls._failover_or_retry_worker(db, job, f"Boot timeout ({int(boot_elapsed)}s)", "Worker Boot Timeout")
                else:
                    job.status = "starting_worker"
                    job.message = f"Kaggle Worker ({job.worker_id}) đang tải môi trường chạy và mô hình..."
                    job.progress = 25
                    db.commit()
            elif "error" in status_lower or "failed" in status_lower:
                cls._unknown_status_count.pop(job.id, None)
                cls._complete_grace_count.pop(job.id, None)
                print(f"[KaggleOrchestrator] Worker {job.worker_id} reported ERROR/FAILED: {status_output}")
                cls._failover_or_retry_worker(db, job, status_output, "Worker Error")
            elif "complete" in status_lower:
                cls._unknown_status_count.pop(job.id, None)
                # Kaggle API may report 'complete' from previous session for 20-30s after kernel push.
                # Allow a grace period (e.g. 4 checks = 40s) before treating it as a true termination.
                cls._complete_grace_count[job.id] = cls._complete_grace_count.get(job.id, 0) + 1
                if cls._complete_grace_count[job.id] < 4:
                    print(f"[KaggleOrchestrator] Worker {job.worker_id} status is 'complete' (likely stale from prior session). Grace check {cls._complete_grace_count[job.id]}/4, waiting for Kaggle transition...")
                    job.message = f"Đang chờ Kaggle ({job.worker_id}) hoàn tất cấp phát container GPU mới..."
                    db.commit()
                else:
                    print(f"[KaggleOrchestrator] Worker {job.worker_id} status remained 'complete' after grace period. Triggering failover/re-push...")
                    cls._failover_or_retry_worker(db, job, status_output, "Worker Complete without restart")
            elif "cancel" in status_lower or "stop" in status_lower:
                cls._unknown_status_count.pop(job.id, None)
                cls._complete_grace_count.pop(job.id, None)
                print(f"[KaggleOrchestrator] Worker {job.worker_id} reported CANCELLED/STOPPED: {status_output}")
                cls._failover_or_retry_worker(db, job, status_output, "Worker Stopped/Cancelled")
            else:
                print(f"[KaggleOrchestrator] Warning: unknown Kaggle status: {status_output}")
                cls._unknown_status_count[job.id] = cls._unknown_status_count.get(job.id, 0) + 1
                if cls._unknown_status_count[job.id] >= 20:  # 20 checks * 10s = 200s
                    cls._failover_or_retry_worker(db, job, status_output, "Unknown status timeout")
                
        except Exception as e:
            print(f"[KaggleOrchestrator] Exception polling booting worker: {e}")

    @staticmethod
    def get_status(db=None) -> str:
        """Retrieves status of the Kaggle notebook via CLI."""
        if not KaggleOrchestrator.is_configured(db):
            return "Unconfigured"

        try:
            username, key, kernel_ref, _ = KaggleOrchestrator.get_credentials(db)
            env = os.environ.copy()
            env["KAGGLE_USERNAME"] = username
            env["KAGGLE_KEY"] = key
            env["KAGGLE_API_TOKEN"] = key
            env["PYTHONUTF8"] = "1"

            import sys
            cmd = [sys.executable, "-c", "from kaggle.cli import main; main()", "kernels", "status", kernel_ref]
            res = subprocess.run(cmd, capture_output=True, env=env, text=True, shell=False)
            if res.returncode == 0:
                return res.stdout.strip()
            return f"Error: {res.stderr.strip()}"
        except Exception as e:
            return f"Exception: {str(e)}"
