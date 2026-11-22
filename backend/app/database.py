import os
import glob
import shutil
import sqlite3
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.event import listens_for
from app.config import settings

def get_sqlite_path(db_url: str) -> str:
    if not db_url.startswith("sqlite"):
        return db_url
    path = db_url
    if "?" in path:
        path = path.split("?")[0]
    for prefix in ["sqlite:////", "sqlite:///", "sqlite://", "sqlite:"]:
        if path.startswith(prefix):
            path = path[len(prefix):]
            if prefix == "sqlite:////" and not path.startswith("/"):
                path = "/" + path
            elif prefix == "sqlite:///" and not path.startswith(".") and not (len(path) > 1 and path[1] == ":") and not path.startswith("/"):
                path = "/" + path
            break
    return path

def test_db_writable(db_url: str) -> bool:
    if not db_url.startswith("sqlite"):
        return True
    
    db_path = get_sqlite_path(db_url)
    parent_dir = os.path.dirname(db_path)
    if parent_dir:
        try:
            os.makedirs(parent_dir, exist_ok=True)
        except Exception as e:
            print(f"[Database Init] Failed to create directory {parent_dir}: {e}")
            return False
            
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=DELETE")
        cursor.execute("CREATE TABLE IF NOT EXISTS _write_test (id INTEGER PRIMARY KEY)")
        cursor.execute("INSERT INTO _write_test DEFAULT VALUES")
        cursor.execute("DROP TABLE _write_test")
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[Database Init] Write verification failed for {db_path}: {e}")
        if conn:
            try:
                conn.close()
            except:
                pass
        return False

def check_and_recover_database(db_url: str):
    if not db_url.startswith("sqlite"):
        return
        
    db_path = get_sqlite_path(db_url)
    if not os.path.exists(db_path):
        return
        
    import sqlite3
    import time
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        result = cursor.fetchone()[0]
        if result == "ok":
            # Also probe critical tables to catch B-tree page corruption not flagged by header check
            for probe_tbl in ("tts_jobs", "users", "voice_samples", "system_settings"):
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (probe_tbl,))
                if cursor.fetchone():
                    cursor.execute(f"SELECT COUNT(*) FROM {probe_tbl}")
                    cursor.fetchone()
            conn.close()
            print(f"[Database Integrity] Check passed for {db_path}")
            return
        conn.close()
        print(f"[Database Integrity] CORRUPTED database found at {db_path}: integrity_check={result}")
    except Exception as e:
        print(f"[Database Integrity] Error verifying database {db_path}: {e}")
        if conn:
            try:
                conn.close()
            except:
                pass

    # Reaching here means database is corrupt. Rename and recover.
    backup_path = f"{db_path}.corrupt_{int(time.time())}"
    print(f"[Database Integrity] Renaming corrupted database to {backup_path}")
    try:
        if os.path.exists(db_path):
            os.rename(db_path, backup_path)
        # Also clean up stale lock/journal files
        for suffix in ["-shm", "-wal", "-journal"]:
            lock_file = db_path + suffix
            if os.path.exists(lock_file):
                os.remove(lock_file)
        print("[Database Integrity] Corrupted database recovered by recreating file.")
    except Exception as ex:
        print(f"[Database Integrity Error] Failed to rename corrupted database: {ex}")

# Check and recover database if corrupted
check_and_recover_database(settings.DATABASE_URL)

# Resolve Database URL with dynamic writeability fallback checks
resolved_db_url = settings.DATABASE_URL
if resolved_db_url.startswith("sqlite"):
    if not test_db_writable(resolved_db_url):
        print(f"[Database Init] Warning: Default database path {resolved_db_url} is not writable.")
        
        # Fallback 1: Try ./storage/ directory
        fallback_storage = "sqlite:///./storage/omnivoice_gateway.db"
        print(f"[Database Init] Attempting fallback 1: {fallback_storage}")
        if test_db_writable(fallback_storage):
            resolved_db_url = fallback_storage
            print(f"[Database Init] Success: Using fallback 1 ({fallback_storage})")
        else:
            # Fallback 2: Try /tmp/
            fallback_2 = "sqlite:////tmp/omnivoice_gateway.db"
            print(f"[Database Init] Attempting fallback 2: {fallback_2}")
            if test_db_writable(fallback_2):
                resolved_db_url = fallback_2
                print(f"[Database Init] Success: Using fallback 2")
            else:
                fallback_3 = "sqlite:///./omnivoice_gateway_fallback.db"
                resolved_db_url = fallback_3
                print(f"[Database Init] Using fallback 3")
        
        # Override the setting globally so all routes and services use the working database path
        settings.DATABASE_URL = resolved_db_url

# Safely checkpoint any existing WAL frames into main DB before switching to DELETE journal mode
if settings.DATABASE_URL.startswith("sqlite"):
    db_path = get_sqlite_path(settings.DATABASE_URL)
    if os.path.exists(db_path) and (os.path.exists(db_path + "-wal") or os.path.exists(db_path + "-shm")):
        try:
            _ckpt_conn = sqlite3.connect(db_path, timeout=15)
            _ckpt_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            _ckpt_conn.execute("PRAGMA journal_mode=DELETE")
            _ckpt_conn.commit()
            _ckpt_conn.close()
            print(f"[Database] Checkpointed WAL cleanly for: {db_path}")
        except Exception as e:
            print(f"[Database Warning] WAL checkpoint fallback for {db_path}: {e}")
            for suffix in ["-shm", "-wal"]:
                lock_file = db_path + suffix
                if os.path.exists(lock_file):
                    try:
                        os.remove(lock_file)
                    except Exception:
                        pass

# For SQLite, we set timeout=60 and check_same_thread=False
connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {
        "check_same_thread": False,
        "timeout": 60
    }

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    echo=False
)

# Apply performance and lock-avoidance pragmas to SQLite connections
@listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if settings.DATABASE_URL.startswith("sqlite"):
        try:
            cursor = dbapi_connection.cursor()
            # Force DELETE journal mode to avoid .shm / .wal lock files on NFS
            cursor.execute("PRAGMA journal_mode=DELETE")
            cursor.execute("PRAGMA busy_timeout=60000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()
        except Exception as e:
            print(f"[Database Pragma] Failed to set SQLite pragmas: {e}")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def migrate_database(db_url: str):
    if not db_url.startswith("sqlite"):
        return

    db_path = get_sqlite_path(db_url)
    if not os.path.exists(db_path):
        return
    
    import sqlite3
    print(f"[Migration] Checking database file: {db_path}")
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()
    
    try:
        # Check if tts_jobs table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tts_jobs'")
        if not cursor.fetchone():
            conn.close()
            return
            
        cursor.execute("PRAGMA table_info(tts_jobs)")
        columns = [row[1] for row in cursor.fetchall()]
        
        new_cols = {
            "denoise": "BOOLEAN DEFAULT 1",
            "guidance_scale": "FLOAT DEFAULT 2.0",
            "t_shift": "FLOAT DEFAULT 0.1",
            "position_temperature": "FLOAT DEFAULT 5.0",
            "class_temperature": "FLOAT DEFAULT 0.0",
            "layer_penalty_factor": "FLOAT DEFAULT 5.0",
            "duration": "FLOAT",
            "preprocess_prompt": "BOOLEAN DEFAULT 1",
            "postprocess_output": "BOOLEAN DEFAULT 1",
            "audio_chunk_duration": "FLOAT DEFAULT 15.0",
            "audio_chunk_threshold": "FLOAT DEFAULT 30.0",
            "batch_id": "VARCHAR(50)",
            "compat_id": "VARCHAR(100)",
            "with_alignment": "BOOLEAN DEFAULT 0",
            "alignment": "TEXT",
            "started_at": "DATETIME",
            "completed_at": "DATETIME",
            "language": "VARCHAR(50)",
            "pad_duration": "FLOAT",
            "fade_duration": "FLOAT",
            "cdn_audio_url": "TEXT",
            "normalize_text": "BOOLEAN DEFAULT 0"
        }
        
        for col, col_type in new_cols.items():
            if col not in columns:
                cursor.execute(f"ALTER TABLE tts_jobs ADD COLUMN {col} {col_type}")
                print(f"[Migration] Added column {col} to tts_jobs table in {db_path}")
        
        # Check and migrate voice_samples table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='voice_samples'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(voice_samples)")
            vs_columns = [row[1] for row in cursor.fetchall()]
            
            vs_new_cols = {
                "name": "VARCHAR(100)",
                "is_public": "BOOLEAN DEFAULT 0",
                "source_job_id": "VARCHAR(50)",
                "tags": "TEXT",
                "source_job_data": "TEXT"
            }
            for col, col_type in vs_new_cols.items():
                if col not in vs_columns:
                    cursor.execute(f"ALTER TABLE voice_samples ADD COLUMN {col} {col_type}")
                    print(f"[Migration] Added column {col} to voice_samples table in {db_path}")
                    
        # Check and migrate video_dubbing_jobs table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='video_dubbing_jobs'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(video_dubbing_jobs)")
            vd_columns = [row[1] for row in cursor.fetchall()]
            
            vd_new_cols = {
                "llm_profile_id": "VARCHAR(50)",
                "vocals_volume": "FLOAT DEFAULT 1.0",
                "bgm_volume": "FLOAT DEFAULT 0.4",
                "video_title": "TEXT",
                "video_thumbnail": "TEXT",
                "video_duration": "FLOAT",
                "preferred_quality": "VARCHAR(20) DEFAULT '1080p'",
                "voice_mode": "VARCHAR(30) DEFAULT 'auto_native'",
                "voice_sample_id": "VARCHAR(100)",
            }
            for col, col_type in vd_new_cols.items():
                if col not in vd_columns:
                    cursor.execute(f"ALTER TABLE video_dubbing_jobs ADD COLUMN {col} {col_type}")
                    print(f"[Migration] Added column {col} to video_dubbing_jobs table in {db_path}")

        conn.commit()
    except Exception as e:
        print(f"[Migration Error] Failed to migrate database {db_path}: {e}")
    finally:
        conn.close()

def auto_align_legacy_user_ids(db_url: str):
    if not db_url.startswith("sqlite"):
        return
        
    db_path = get_sqlite_path(db_url)
    if not os.path.exists(db_path):
        return
        
    import sqlite3
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM users WHERE email = 'phucsd@gmail.com' OR username = 'phucsd@gmail.com' OR username = 'admin' OR is_admin = 1 ORDER BY is_admin DESC")
        row = cursor.fetchone()
        if row:
            admin_id = row[0]
            legacy_empty_cond = "user_id IS NULL OR user_id = ''"
            dangling_user_cond = "user_id IS NOT NULL AND user_id != '' AND user_id NOT IN (SELECT id FROM users)"
            # Security: NEVER reassign API keys or user_settings of deleted users to Admin
            for sec_tbl in ("api_keys", "user_settings"):
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (sec_tbl,))
                if cursor.fetchone():
                    cursor.execute(f"DELETE FROM {sec_tbl} WHERE {dangling_user_cond}")
                    cursor.execute(f"UPDATE {sec_tbl} SET user_id = ? WHERE {legacy_empty_cond}", (admin_id,))
            orphan_cond = "user_id IS NULL OR user_id = '' OR user_id NOT IN (SELECT id FROM users)"
            for tbl in ("voice_samples", "tts_jobs", "video_dubbing_jobs", "voice_design_previews"):
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,))
                if cursor.fetchone():
                    cursor.execute(f"UPDATE {tbl} SET user_id = ? WHERE {orphan_cond}", (admin_id,))
            conn.commit()
            print(f"[Database Auto-Align] Aligned orphaned legacy records to admin ID: {admin_id}")
    except Exception as e:
        print(f"[Database Auto-Align Error] {e}")
    finally:
        conn.close()

import threading

_heal_lock = threading.Lock()


def _copy_tables_from_sources(target_conn: sqlite3.Connection, source_files: list[str], insert_verb: str = "INSERT OR REPLACE"):
    """Copies core tables from one or more source SQLite files into target_conn safely."""
    target_cursor = target_conn.cursor()
    # Intentionally exclude ephemeral 'api_usage_logs' (130k+ rows) and 'worker_sessions'
    # to avoid 30s NFS write storms during container rolling restarts.
    tables_to_restore = [
        "users", "api_keys", "voice_samples", "llm_profiles",
        "system_settings", "user_settings", "voice_design_previews",
        "video_dubbing_jobs", "tts_jobs",
    ]

    for src_file in source_files:
        if not os.path.exists(src_file):
            continue
        print(f"[Database Salvage] Rescuing data from {src_file}...")
        source_conn = None
        try:
            source_conn = sqlite3.connect(f"file:{src_file}?mode=ro", uri=True, timeout=10)
            source_cursor = source_conn.cursor()

            for table in tables_to_restore:
                try:
                    source_cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name=?", (table,))
                    s_row = source_cursor.fetchone()
                    if not s_row:
                        continue
                    table_sql = s_row[1]

                    target_cursor.execute(f"PRAGMA table_info({table})")
                    target_cols = [row[1] for row in target_cursor.fetchall()]
                    if not target_cols and table_sql:
                        try:
                            target_cursor.execute(table_sql)
                            target_conn.commit()
                            target_cursor.execute(f"PRAGMA table_info({table})")
                            target_cols = [row[1] for row in target_cursor.fetchall()]
                        except Exception as create_err:
                            print(f"[Database Salvage] Failed to create table '{table}': {create_err}")
                            continue

                    if not target_cols:
                        continue

                    source_cursor.execute(f"PRAGMA table_info({table})")
                    source_cols = [row[1] for row in source_cursor.fetchall()]

                    common_cols = [c for c in source_cols if c in target_cols]
                    if not common_cols:
                        continue

                    col_names = ", ".join(common_cols)
                    placeholders = ", ".join(["?"] * len(common_cols))
                    limit_clause = " ORDER BY rowid DESC LIMIT 150" if table == "tts_jobs" else ""

                    rows = []
                    try:
                        source_cursor.execute(f"SELECT {col_names} FROM {table}{limit_clause}")
                        rows = source_cursor.fetchall()
                    except Exception as select_err:
                        print(f"[Database Salvage] Table fetch error for '{table}': {select_err}. Attempting rowid fallback...")
                        try:
                            source_cursor.execute(f"SELECT MAX(rowid) FROM {table}")
                            max_r = source_cursor.fetchone()
                            max_rid = int(max_r[0]) if (max_r and max_r[0]) else (3500 if table == "tts_jobs" else 200)
                        except Exception:
                            max_rid = 3500 if table == "tts_jobs" else 200
                        if table == "tts_jobs":
                            rowids = list(range(max_rid, max(0, max_rid - 150), -1))
                        else:
                            rowids = list(range(1, min(max_rid + 1, 500)))
                        for rid in rowids:
                            try:
                                source_cursor.execute(f"SELECT {col_names} FROM {table} WHERE rowid=?", (rid,))
                                r_item = source_cursor.fetchone()
                                if r_item:
                                    rows.append(r_item)
                            except Exception:
                                pass

                    if rows:
                        insert_sql = f"{insert_verb} INTO {table} ({col_names}) VALUES ({placeholders})"
                        restored_count = 0
                        for row in rows:
                            try:
                                target_cursor.execute(insert_sql, row)
                                restored_count += 1
                            except Exception:
                                pass
                        target_conn.commit()
                        print(f"[Database Salvage] Restored {restored_count}/{len(rows)} records into '{table}'")
                except Exception as table_err:
                    print(f"[Database Salvage Error] Table '{table}': {table_err}")
        except Exception as file_err:
            print(f"[Database Salvage Error] Opening {src_file}: {file_err}")
        finally:
            if source_conn:
                try:
                    source_conn.close()
                except Exception:
                    pass


def verify_and_heal_database(db_url: str, force_rebuild: bool = False) -> bool:
    """
    Verifies SQLite B-tree health across critical tables. If malformed or force_rebuild=True,
    rebuilds a clean database in /tmp (avoiding NFS lock/write contention), rescues intact rows
    from the active DB and newest backups, atomically replaces db_path, and disposes engine pool.
    """
    if not db_url.startswith("sqlite"):
        return False

    with _heal_lock:
        db_path = get_sqlite_path(db_url)
        db_dir = os.path.dirname(db_path) or "."
        base_name = os.path.basename(db_path)

        if not force_rebuild and os.path.exists(db_path):
            conn = None
            try:
                conn = sqlite3.connect(db_path, timeout=10)
                cur = conn.cursor()
                cur.execute("PRAGMA quick_check")
                qc = cur.fetchone()[0]
                if qc != "ok":
                    raise sqlite3.DatabaseError(f"quick_check returned {qc}")
                for tbl in ("tts_jobs", "users", "voice_samples", "system_settings", "worker_sessions"):
                    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,))
                    if cur.fetchone():
                        cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                        cur.fetchone()
                # Specifically test the queue runner status query on tts_jobs
                cur.execute("SELECT id FROM tts_jobs WHERE status IN ('starting_worker', 'queued_kaggle', 'queued') LIMIT 5")
                cur.fetchall()
                conn.close()
                return False
            except Exception as check_err:
                print(f"[Database Self-Heal] Detected malformed database at {db_path}: {check_err}. Initiating fast /tmp rebuild...")
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

        import time
        import tempfile
        try:
            engine.dispose()
        except Exception:
            pass

        tmp_fd, tmp_db_path = tempfile.mkstemp(prefix="omnivoice_healed_", suffix=".db")
        os.close(tmp_fd)
        if os.path.exists(tmp_db_path):
            os.remove(tmp_db_path)

        try:
            import app.models  # Ensure all models are registered on Base.metadata
            tmp_engine = create_engine(f"sqlite:///{tmp_db_path}", connect_args={"check_same_thread": False, "timeout": 30})
            Base.metadata.create_all(bind=tmp_engine)
            tmp_engine.dispose()

            tmp_conn = sqlite3.connect(tmp_db_path, timeout=30)
            tmp_conn.execute("PRAGMA journal_mode=DELETE")
            tmp_conn.execute("PRAGMA synchronous=NORMAL")

            # Gather source files: active db_path first (for latest jobs/settings), then newest backups
            backup_candidates = sorted(
                [
                    f for f in glob.glob(os.path.join(db_dir, f"{base_name}.corrupt_*"))
                    if not f.endswith("-shm") and not f.endswith("-wal") and not f.endswith("-journal")
                    and "-shm." not in f and "-wal." not in f
                ],
                key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
                reverse=True,
            )
            sources = ([db_path] if os.path.exists(db_path) else []) + backup_candidates[:2]
            _copy_tables_from_sources(tmp_conn, sources, insert_verb="INSERT OR IGNORE")

            # Expire any ancient booting jobs salvaged from old backups (>10 mins old) so they don't zombie-loop
            tmp_conn.execute(
                "UPDATE tts_jobs SET status = 'failed', error_message = 'Expired stale job during DB heal' "
                "WHERE status IN ('starting_worker', 'queued_kaggle', 'preparing_input', 'running') "
                "AND created_at < datetime('now', '-10 minutes')"
            )
            tmp_conn.commit()

            cur = tmp_conn.cursor()
            cur.execute("PRAGMA integrity_check")
            ic = cur.fetchone()[0]
            tmp_conn.close()
            if ic != "ok":
                raise RuntimeError(f"Rebuilt /tmp DB failed integrity_check: {ic}")

            # Backup broken db_path and replace with clean tmp_db_path
            if os.path.exists(db_path):
                archived = f"{db_path}.corrupt_{int(time.time())}.restored"
                try:
                    os.replace(db_path, archived)
                except Exception:
                    try:
                        os.remove(db_path)
                    except Exception:
                        pass
            for suffix in ("-shm", "-wal", "-journal"):
                lf = db_path + suffix
                if os.path.exists(lf):
                    try:
                        os.remove(lf)
                    except Exception:
                        pass

            shutil.copy2(tmp_db_path, db_path)
            try:
                os.remove(tmp_db_path)
            except Exception:
                pass

            auto_align_legacy_user_ids(db_url)
            engine.dispose()
            print(f"[Database Self-Heal] Successfully rebuilt clean database at {db_path}!")
            return True
        except Exception as heal_err:
            print(f"[Database Self-Heal Error] Failed to heal {db_path}: {heal_err}")
            if os.path.exists(tmp_db_path):
                try:
                    os.remove(tmp_db_path)
                except Exception:
                    pass
            return False


def salvage_corrupted_databases(db_url: str):
    if not db_url.startswith("sqlite"):
        return
        
    db_path = get_sqlite_path(db_url)
    db_dir = os.path.dirname(db_path) or "."
    base_name = os.path.basename(db_path)
    
    if not os.path.exists(db_dir):
        return
        
    # Clean up any leftover lock backup files (keep .restored files as safe backups)
    for f in glob.glob(os.path.join(db_dir, f"{base_name}.corrupt_*")):
        if f.endswith("-shm") or f.endswith("-wal") or f.endswith("-journal") or "-shm." in f or "-wal." in f:
            try: os.remove(f)
            except: pass
            
    corrupt_files = sorted(
        [
            f for f in glob.glob(os.path.join(db_dir, f"{base_name}.corrupt_*"))
            if not f.endswith("-shm") and not f.endswith("-wal") and not f.endswith(".restored") and ".restored" not in f and "-shm." not in f and "-wal." not in f
        ],
        key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
        reverse=True,
    )
    
    try:
        engine.dispose()
    except Exception:
        pass

    need_salvage = bool(corrupt_files)
    if not need_salvage:
        try:
            conn = sqlite3.connect(db_path, timeout=15)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM voice_samples")
            vs_cnt = cur.fetchone()[0]
            conn.close()
        except Exception:
            vs_cnt = 0
        if vs_cnt < 6:
            restored_candidates = sorted(
                [
                    f for f in glob.glob(os.path.join(db_dir, f"{base_name}.corrupt_*.restored"))
                    if not f.endswith("-shm") and not f.endswith("-wal")
                ],
                key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
                reverse=True,
            )
            if restored_candidates:
                print(f"[Database Salvage] Active database has only {vs_cnt} voice samples. Rescuing from newest backup(s)...")
                corrupt_files.extend(restored_candidates[:2])
                need_salvage = True

    if need_salvage:
        verify_and_heal_database(db_url, force_rebuild=True)
        for cf in corrupt_files:
            if os.path.exists(cf) and not cf.endswith(".restored"):
                try:
                    os.replace(cf, f"{cf}.restored")
                except Exception:
                    pass
    else:
        verify_and_heal_database(db_url, force_rebuild=False)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
