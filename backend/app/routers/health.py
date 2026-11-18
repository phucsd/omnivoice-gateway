import os
import subprocess
from fastapi import APIRouter, Depends

from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User
from app.routers.admin import get_admin_user

router = APIRouter()


@router.get("/health")
@router.get("/v1/health")
def get_health():
    """Simple check validating backend and orchestration layers are active."""
    git_hash = (os.environ.get("GIT_COMMIT") or os.environ.get("SPACE_SHA") or "").strip()
    if not git_hash:
        try:
            git_hash = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except Exception:
            git_hash = "hf-space" if os.environ.get("SPACE_ID") else "unknown"
    else:
        git_hash = git_hash[:12]

    return {
        "status": "ok",
        "app": "OmniVoice On-Demand Gateway",
        "git_commit": git_hash,
    }


@router.get("/health/db")
@router.get("/v1/health/db")
def get_db_health(
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_admin_user),
):
    """Admin-only database health, tables check, and storage verification (no secret leakage)."""
    _ = current_admin
    result = {
        "is_sqlite": settings.DATABASE_URL.startswith("sqlite"),
        "engine_session": "ok",
        "tables": {},
        "raw_sqlite": {},
        "writable": False,
        "errors": [],
    }

    # 1. Query SQLAlchemy session
    try:
        from app.models import ApiKey, SystemSetting, TTSJob, VoiceSample, WorkerSession

        result["tables"]["users"] = db.query(User).count()
        result["tables"]["api_keys"] = db.query(ApiKey).count()
        result["tables"]["voice_samples"] = db.query(VoiceSample).count()
        result["tables"]["tts_jobs"] = db.query(TTSJob).count()
        result["tables"]["worker_sessions"] = db.query(WorkerSession).count()
        result["tables"]["system_settings"] = db.query(SystemSetting).count()

        admin = db.query(User).filter(User.is_admin == True).first()
        result["admin_user"] = {
            "exists": admin is not None,
            "username": admin.username if admin else None,
            "is_verified": admin.is_verified if admin else None,
            "is_approved": admin.is_approved if admin else None,
        }
    except Exception as e:
        result["engine_session"] = f"error: {e}"
        result["errors"].append(f"SQLAlchemy query error: {e}")

    # 2. Test raw SQLite direct inspection
    try:
        import sqlite3
        from app.database import get_sqlite_path

        db_path = get_sqlite_path(settings.DATABASE_URL)
        result["db_file_exists"] = os.path.exists(db_path)
        if os.path.exists(db_path):
            result["db_file_size_bytes"] = os.path.getsize(db_path)

            raw_conn = sqlite3.connect(db_path, timeout=5)
            c = raw_conn.cursor()
            c.execute("PRAGMA integrity_check")
            result["raw_sqlite"]["integrity"] = c.fetchone()[0]
            c.execute("PRAGMA journal_mode")
            result["raw_sqlite"]["journal_mode"] = c.fetchone()[0]
            c.execute("SELECT name FROM sqlite_master WHERE type='table'")
            result["raw_sqlite"]["tables"] = [r[0] for r in c.fetchall()]
            raw_conn.close()

            test_conn = sqlite3.connect(db_path, timeout=5)
            tc = test_conn.cursor()
            tc.execute("CREATE TABLE IF NOT EXISTS _health_write_test (id INTEGER PRIMARY KEY)")
            tc.execute("INSERT INTO _health_write_test DEFAULT VALUES")
            tc.execute("DROP TABLE _health_write_test")
            test_conn.commit()
            test_conn.close()
            result["writable"] = True
    except Exception as e:
        result["raw_sqlite"]["error"] = str(e)
        result["errors"].append(f"Raw SQLite error: {e}")

    return result
