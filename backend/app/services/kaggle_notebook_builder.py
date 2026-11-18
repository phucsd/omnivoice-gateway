import os
import json
from app.config import settings

class KaggleNotebookBuilder:
    @staticmethod
    def get_normalizer_code() -> str:
        """Loads the pure-Python Vietnamese text normalizer code to embed into worker scripts."""
        normalizer_file = os.path.join(os.path.dirname(__file__), "..", "utils", "vietnamese_normalizer.py")
        if os.path.exists(normalizer_file):
            with open(normalizer_file, "r", encoding="utf-8") as f:
                return f.read()
        return ""
    @staticmethod
    def ensure_worker_dir(worker_dir: str) -> str:
        """Ensures that the kaggle worker directory exists and returns its absolute path."""
        worker_dir_abs = os.path.abspath(worker_dir)
        os.makedirs(worker_dir_abs, exist_ok=True)
        return worker_dir_abs

    @staticmethod
    def generate_metadata(worker_dir: str, username: str, slug: str, title: str, accelerator: str = None) -> str:
        """Generates or updates kernel-metadata.json from configurations."""
        metadata_path = os.path.join(worker_dir, "kernel-metadata.json")
        
        # IMPORTANT: Do NOT attach Kaggle Datasets (e.g. omnivoice-original-2) to dataset_sources!
        # On Kaggle GPU containers, /kaggle/input is backed by gcsfuse; random safetensors mmap() page-faults
        # over FUSE stall model loading for 21-23 minutes (vs ~15 seconds downloading directly from HF Hub / ModelScope to local SSD).
        dataset_sources = []

        # Build metadata structure
        metadata = {
            "id": f"{username}/{slug}",
            "title": title,
            "code_file": "omnivoice_worker.py",
            "language": "python",
            "kernel_type": "script",
            "is_private": True,
            "enable_gpu": True,
            "enable_internet": True,
            "dataset_sources": dataset_sources,
            "kernel_sources": [],
            "competition_sources": []
        }
        
        # Map accelerator values to Kaggle API expected format
        mapped_acc = "NvidiaTeslaT4"
        if accelerator:
            acc_lower = accelerator.lower()
            if "p100" in acc_lower:
                mapped_acc = "NvidiaTeslaP100"
            elif "t4" in acc_lower:
                mapped_acc = "NvidiaTeslaT4"
            else:
                mapped_acc = accelerator
        
        metadata["machine_shape"] = mapped_acc
        metadata["accelerator"] = mapped_acc
        
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
            
        print(f"[KaggleNotebookBuilder] Generated metadata at: {metadata_path}")
        return metadata_path

    @staticmethod
    def generate_requirements(worker_dir: str) -> str:
        """Generates or updates the requirements.txt file."""
        req_path = os.path.join(worker_dir, "requirements.txt")
        req_content = "omnivoice @ git+https://github.com/k2-fsa/OmniVoice.git\nsoundfile\nnumpy\nrequests\nfaster-whisper\ndemucs\nnum2words\n"
        
        with open(req_path, "w", encoding="utf-8") as f:
            f.write(req_content)
            
        print(f"[KaggleNotebookBuilder] Generated requirements.txt at: {req_path}")
        return req_path

    @staticmethod
    def generate_worker_code(worker_dir: str, job=None, public_api_url: str = "", worker_token: str = "", is_daemon: bool = True, db=None) -> str:
        """Generates the omnivoice_worker.py Python script containing the OmniVoice batch or daemon generation logic."""
        worker_path = os.path.join(worker_dir, "omnivoice_worker.py")
        
        if not worker_token:
            worker_token = settings.WORKER_TOKEN
            if not worker_token:
                raise ValueError("WORKER_TOKEN is not configured.")

        hf_token = ""
        if db:
            from app.models import SystemSetting
            db_hf = db.query(SystemSetting).filter(SystemSetting.key == "hf_token").first()
            if db_hf and db_hf.value.strip():
                hf_token = db_hf.value.strip()
        if not hf_token:
            hf_token = settings.HF_TOKEN

        if is_daemon:
            worker_id = None
            if job:
                if isinstance(job, dict):
                    worker_id = job.get("worker_id")
                else:
                    worker_id = getattr(job, "worker_id", None)
            if not worker_id:
                if "worker-2" in worker_dir or "worker_2" in worker_dir:
                    worker_id = "worker_2"
                else:
                    worker_id = "worker_1"
            idle_timeout = settings.WORKER_IDLE_TIMEOUT_SECONDS
            if db:
                from app.models import SystemSetting
                db_idle = db.query(SystemSetting).filter(SystemSetting.key == "kaggle_idle_timeout_seconds").first()
                if db_idle and db_idle.value.strip():
                    try:
                        idle_timeout = int(db_idle.value.strip())
                    except ValueError:
                        pass
            poll_interval = settings.WORKER_POLL_INTERVAL_SECONDS
            
            # Optimized Daemon script template
            code = f"""import os
import sys
import time
import uuid
import tempfile
import traceback
import requests
import warnings
import shutil
import zipfile
import json
import subprocess
import threading
from urllib.parse import urlparse

os.environ["CUDA_MODULE_LOADING"] = "LAZY"
os.environ["PYTHONWARNINGS"] = "ignore"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["TQDM_DISABLE"] = "1"
os.environ["HF_HUB_ETAG_TIMEOUT"] = "15"
os.environ["HF_TOKEN"] = {repr(hf_token)}
os.environ["HUGGING_FACE_HUB_TOKEN"] = {repr(hf_token)}
warnings.filterwarnings("ignore")

def ensure_dependencies():
    \"\"\"Dynamically checks and installs required packages inside the Kaggle environment if missing or outdated.\"\"\"
    missing = []
    need_omnivoice_upgrade = False
    try:
        import omnivoice
        from omnivoice.models.omnivoice import VoiceClonePrompt
    except (ImportError, AttributeError):
        need_omnivoice_upgrade = True

    try:
        import soundfile
    except ImportError:
        missing.append("soundfile")
    try:
        import num2words
    except ImportError:
        missing.append("num2words")
    try:
        import modelscope
    except ImportError:
        missing.append("modelscope")
    try:
        import sentencepiece
    except ImportError:
        missing.append("sentencepiece")
    try:
        import tiktoken
    except ImportError:
        missing.append("tiktoken")
    try:
        import faster_whisper
    except ImportError:
        missing.append("faster-whisper")
    try:
        import demucs
    except ImportError:
        missing.append("demucs")
        
    import subprocess
    packages_to_install = []
    if need_omnivoice_upgrade:
        packages_to_install.append("git+https://github.com/k2-fsa/OmniVoice.git")
    packages_to_install.extend(missing)

    if packages_to_install:
        print(f"Installing missing dependencies: {{', '.join(packages_to_install)}}", flush=True)
        try:
            # Install packages silently with fast flags
            subprocess.check_call([
                sys.executable, "-m", "pip", "install", "-q", 
                "--no-cache-dir", "--prefer-binary", 
                "--no-warn-script-location"
            ] + packages_to_install)
            print("Dependencies installed successfully.", flush=True)
        except Exception as e:
            if need_omnivoice_upgrade:
                print(f"Notice: Batch install failed ({{e}}), falling back to omnivoice[tn]...", flush=True)
                try:
                    fallback_packages = ["omnivoice[tn]"] + missing
                    subprocess.check_call([
                        sys.executable, "-m", "pip", "install", "-q",
                        "--no-cache-dir", "--prefer-binary",
                        "--no-warn-script-location"
                    ] + fallback_packages)
                    print("Fallback dependencies installed successfully.", flush=True)
                except Exception as fb_err:
                    print(f"Failed to install fallback dependencies: {{fb_err}}", flush=True)
                    sys.exit(1)
            else:
                print(f"Failed to install dependencies: {{e}}", flush=True)
                sys.exit(1)

# Ensure dependencies are available before anything else runs
ensure_dependencies()

import numpy as np
import torch
import soundfile as sf
from omnivoice import OmniVoice

{KaggleNotebookBuilder.get_normalizer_code()}

PUBLIC_API_BASE_URL = {repr(public_api_url)}.rstrip("/")
WORKER_TOKEN = {repr(worker_token)}
WORKER_ID = {repr(worker_id)}
IDLE_TIMEOUT = {idle_timeout}
POLL_INTERVAL = {poll_interval}

HEADERS = {{
    "Authorization": f"Bearer {{WORKER_TOKEN}}",
    "X-Worker-Version": "2.2.0"
}}

WORKER_STATE = {{
    "status": "starting",
    "current_job_id": None,
    "message": "OmniVoice worker starting up...",
    "stop": False,
}}

def log(msg: str):
    print(f"[{{time.strftime('%Y-%m-%d %H:%M:%S')}}] [Worker-{{WORKER_ID}}] {{msg}}", flush=True)

def make_request(method: str, path: str, **kwargs) -> requests.Response:
    url = f"{{PUBLIC_API_BASE_URL}}{{path}}"
    if "headers" in kwargs:
        kwargs["headers"].update(HEADERS)
    else:
        kwargs["headers"] = HEADERS.copy()
    kwargs.setdefault("timeout", (15, 180))
    return requests.request(method, url, **kwargs)

def set_worker_state(status: str, current_job_id=None, message: str = ""):
    WORKER_STATE["status"] = status
    WORKER_STATE["current_job_id"] = current_job_id
    WORKER_STATE["message"] = message

def start_heartbeat_daemon():
    def _hb_loop():
        while not WORKER_STATE.get("stop"):
            time.sleep(25)
            if WORKER_STATE.get("stop"):
                break
            try:
                make_request(
                    "POST",
                    "/v1/internal/workers/heartbeat",
                    json={{
                        "worker_id": WORKER_ID,
                        "status": WORKER_STATE["status"],
                        "current_job_id": WORKER_STATE["current_job_id"],
                        "message": WORKER_STATE["message"],
                    }},
                    timeout=(10, 20),
                )
            except Exception as hb_err:
                log(f"Notice: background heartbeat warning: {{hb_err}}")
    t = threading.Thread(target=_hb_loop, daemon=True)
    t.start()
    return t

WHISPER_MODEL = None
ASR_WHISPER_MODEL = None

ONES_MAP = {{
    "0": "không", "1": "một", "2": "hai", "3": "ba", "4": "bốn",
    "5": "năm", "6": "sáu", "7": "bảy", "8": "tám", "9": "chín",
    "%": "phần trăm", "đ": "đồng", "$": "đô la"
}}

def levenshtein_ratio(s1, s2):
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    s1_norm = ONES_MAP.get(s1, s1)
    s2_norm = ONES_MAP.get(s2, s2)
    if s1_norm == s2_norm:
        return 1.0
    matrix = [[0] * (len(s2) + 1) for _ in range(len(s1) + 1)]
    for i in range(len(s1) + 1):
        matrix[i][0] = i
    for j in range(len(s2) + 1):
        matrix[0][j] = j
    for i in range(1, len(s1) + 1):
        for j in range(1, len(s2) + 1):
            cost = 0 if s1[i-1] == s2[j-1] else 1
            matrix[i][j] = min(matrix[i-1][j] + 1, matrix[i][j-1] + 1, matrix[i-1][j-1] + cost)
    max_len = max(len(s1), len(s2))
    return 1.0 - (matrix[len(s1)][len(s2)] / max_len)

def resample_to_16k(audio_arr, orig_sr=24000):
    if hasattr(audio_arr, "detach"):
        audio_arr = audio_arr.detach().cpu().numpy()
    audio_arr = np.asarray(audio_arr, dtype=np.float32)
    if audio_arr.ndim > 1:
        if audio_arr.shape[-1] <= 8:
            audio_arr = audio_arr.mean(axis=-1)
        else:
            audio_arr = audio_arr.mean(axis=0)
    if orig_sr == 16000 or len(audio_arr) == 0:
        return audio_arr.astype(np.float32)
    orig_len = len(audio_arr)
    target_len = max(1, int(orig_len * 16000 / orig_sr))
    orig_idx = np.linspace(0, orig_len, orig_len, endpoint=False)
    target_idx = np.linspace(0, orig_len, target_len, endpoint=False)
    return np.interp(target_idx, orig_idx, audio_arr).astype(np.float32)

def get_whisper_model():
    global WHISPER_MODEL
    if WHISPER_MODEL is None:
        log("Loading Whisper model for word alignment...")
        from faster_whisper import WhisperModel
        
        if torch.cuda.is_available():
            try:
                log("Attempting to load WhisperModel ('small') on GPU (cuda)...")
                WHISPER_MODEL = WhisperModel("small", device="cuda", compute_type="float16")
                log("Whisper model ('small') loaded on GPU successfully.")
                return WHISPER_MODEL
            except Exception as gpu_err:
                log(f"Warning: Failed to load WhisperModel on GPU: {{gpu_err}}. Falling back to CPU...")
                
        try:
            log("Attempting to load WhisperModel ('small') on CPU...")
            WHISPER_MODEL = WhisperModel("small", device="cpu", compute_type="int8")
            log("Whisper model ('small') loaded on CPU successfully.")
        except Exception as cpu_err:
            log(f"Error: Failed to load WhisperModel on CPU: {{cpu_err}}")
            raise cpu_err
            
    return WHISPER_MODEL

def get_asr_whisper_model():
    global ASR_WHISPER_MODEL
    if ASR_WHISPER_MODEL is None:
        from faster_whisper import WhisperModel
        if torch.cuda.is_available():
            for m_name in ("large-v3-turbo", "small"):
                try:
                    log(f"Loading faster-whisper '{{m_name}}' on GPU for full-length ASR...")
                    ASR_WHISPER_MODEL = WhisperModel(m_name, device="cuda", compute_type="float16")
                    log(f"faster-whisper '{{m_name}}' loaded on GPU successfully.")
                    return ASR_WHISPER_MODEL
                except Exception as err:
                    log(f"Warning: Could not load faster-whisper '{{m_name}}' on GPU: {{err}}")
        ASR_WHISPER_MODEL = get_whisper_model()
    return ASR_WHISPER_MODEL

def align_words(original_words, transcribed_words, audio_duration):
    n = len(original_words)
    m = len(transcribed_words)
    if n == 0:
        return []
    if m == 0:
        word_dur = audio_duration / n
        return [
            dict(word=w, start=round(i * word_dur, 3), end=round((i + 1) * word_dur, 3))
            for i, w in enumerate(original_words)
        ]

    # Normalize Whisper timestamps if they drift beyond audio_duration
    whisper_max = max(w["end"] for w in transcribed_words) if transcribed_words else audio_duration
    if whisper_max > audio_duration and whisper_max > 0:
        scale = audio_duration / whisper_max
        for w in transcribed_words:
            w["start"] = w["start"] * scale
            w["end"] = w["end"] * scale
    
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    parent = [[None] * (m + 1) for _ in range(n + 1)]
    
    for i in range(1, n + 1):
        dp[i][0] = dp[i-1][0] + 1.0
        parent[i][0] = (i-1, 0, "skip_orig")
    for j in range(1, m + 1):
        dp[0][j] = dp[0][j-1] + 1.0
        parent[0][j] = (0, j-1, "skip_trans")
        
    for i in range(1, n + 1):
        orig_w = original_words[i-1].lower().strip(".,!?\\"'`”“_-;:*()[]{{}}<>")
        for j in range(1, m + 1):
            trans_w = transcribed_words[j-1]["word"].lower().strip(".,!?\\"'`”“_-;:*()[]{{}}<>")
            
            ratio = levenshtein_ratio(orig_w, trans_w)
            if ratio >= 0.7:
                match_cost = 0.0
            elif ratio >= 0.4:
                match_cost = 0.5
            else:
                match_cost = 1.0
                
            cost_match = dp[i-1][j-1] + match_cost
            cost_skip_orig = dp[i-1][j] + 1.0
            cost_skip_trans = dp[i][j-1] + 1.0
            
            min_cost = min(cost_match, cost_skip_orig, cost_skip_trans)
            dp[i][j] = min_cost
            
            if min_cost == cost_match:
                parent[i][j] = (i-1, j-1, "match")
            elif min_cost == cost_skip_orig:
                parent[i][j] = (i-1, j, "skip_orig")
            else:
                parent[i][j] = (i, j-1, "skip_trans")
                
    i, j = n, m
    matches = dict()
    while i > 0 or j > 0:
        p = parent[i][j]
        if p is None:
            break
        pi, pj, op = p
        if op == "match":
            matches[pi] = pj
        i, j = pi, pj
        
    matched_times = []
    for idx in range(n):
        if idx in matches:
            t_word = transcribed_words[matches[idx]]
            start = max(0.0, min(t_word["start"], audio_duration))
            end = max(start, min(t_word["end"], audio_duration))
            matched_times.append((idx, start, end))
            
    if not matched_times:
        word_dur = audio_duration / n
        return [
            dict(word=w, start=round(k * word_dur, 3), end=round((k + 1) * word_dur, 3))
            for k, w in enumerate(original_words)
        ]
        
    first_matched_idx, first_start, first_end = matched_times[0]
    last_matched_idx, last_start, last_end = matched_times[-1]
    
    unmatched_tail_count = n - 1 - last_matched_idx
    if unmatched_tail_count > 0:
        min_tail_dur = min(audio_duration * 0.4, unmatched_tail_count * 0.25)
        if (audio_duration - last_end) < min_tail_dur:
            last_end = max(first_start + 0.2, audio_duration - min_tail_dur)
            matched_times[-1] = (last_matched_idx, min(matched_times[-1][1], last_end - 0.1), last_end)

    if first_matched_idx > 0:
        min_head_dur = min(audio_duration * 0.3, first_matched_idx * 0.25)
        if first_start < min_head_dur:
            first_start = min_head_dur
            matched_times[0] = (first_matched_idx, first_start, max(first_start + 0.1, matched_times[0][2]))

    matched_lookup = {{idx: (s, e) for idx, s, e in matched_times}}
    
    aligned = []
    for idx in range(n):
        if idx in matched_lookup:
            start, end = matched_lookup[idx]
            aligned.append(dict(
                word=original_words[idx],
                start=round(start, 3),
                end=round(end, 3)
            ))
        elif idx < first_matched_idx:
            total_chars = sum(len(original_words[k]) for k in range(first_matched_idx)) or 1
            chars_before = sum(len(original_words[k]) for k in range(idx))
            start = first_start * (chars_before / total_chars)
            word_dur = first_start * (len(original_words[idx]) / total_chars)
            end = start + word_dur
            aligned.append(dict(
                word=original_words[idx],
                start=round(start, 3),
                end=round(end, 3)
            ))
        elif idx > last_matched_idx:
            rem_chars = sum(len(original_words[k]) for k in range(last_matched_idx + 1, n)) or 1
            chars_before = sum(len(original_words[k]) for k in range(last_matched_idx + 1, idx))
            rem_duration = max(0.2, audio_duration - last_end)
            start = last_end + rem_duration * (chars_before / rem_chars)
            word_dur = rem_duration * (len(original_words[idx]) / rem_chars)
            end = start + word_dur
            aligned.append(dict(
                word=original_words[idx],
                start=round(start, 3),
                end=round(end, 3)
            ))
        else:
            pre_idx = -1
            pre_end = 0.0
            for o_idx, s, e in matched_times:
                if o_idx < idx:
                    pre_idx = o_idx
                    pre_end = e
                else:
                    break
                    
            succ_idx = -1
            succ_start = audio_duration
            for o_idx, s, e in matched_times:
                if o_idx > idx:
                    succ_idx = o_idx
                    succ_start = s
                    break
                    
            gap_chars = sum(len(original_words[k]) for k in range(pre_idx + 1, succ_idx)) or 1
            chars_before = sum(len(original_words[k]) for k in range(pre_idx + 1, idx))
            gap_duration = max(0.15 * (succ_idx - pre_idx - 1), succ_start - pre_end)
            
            start = pre_end + gap_duration * (chars_before / gap_chars)
            word_dur = gap_duration * (len(original_words[idx]) / gap_chars)
            end = start + word_dur
            
            aligned.append(dict(
                word=original_words[idx],
                start=round(start, 3),
                end=round(end, 3)
            ))

    # Final pass to ensure strictly monotonic and min duration >= 0.08s
    for k in range(len(aligned)):
        if k > 0 and aligned[k]["start"] < aligned[k-1]["end"]:
            aligned[k]["start"] = aligned[k-1]["end"]
        if aligned[k]["end"] <= aligned[k]["start"] + 0.05:
            aligned[k]["end"] = round(aligned[k]["start"] + 0.1, 3)
                
    return aligned

def _copy_dataset_to_local_ssd(src_root: str) -> str:
    \"\"\"Copies a mounted /kaggle/input model directory sequentially onto local SSD (/tmp) to avoid 20+ min gcsfuse mmap stalls.\"\"\"
    dst_root = os.path.join(tempfile.gettempdir(), "omnivoice_local_weights")
    if os.path.exists(dst_root):
        shutil.rmtree(dst_root, ignore_errors=True)
    os.makedirs(dst_root, exist_ok=True)
    for rel_root, dirs, files in os.walk(src_root):
        if "/.cache" in rel_root.replace("\\\\", "/"):
            continue
        rel_sub = os.path.relpath(rel_root, src_root)
        target_dir = dst_root if rel_sub == "." else os.path.join(dst_root, rel_sub)
        os.makedirs(target_dir, exist_ok=True)
        for fname in files:
            if fname.endswith(".metadata"):
                continue
            s_file = os.path.join(rel_root, fname)
            d_file = os.path.join(target_dir, fname)
            with open(s_file, "rb") as fsrc, open(d_file, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst, length=16 * 1024 * 1024)
    for filename in ["tokenizer.json", "tokenizer_config.json"]:
        dst_file = os.path.join(dst_root, filename)
        if not os.path.exists(dst_file):
            try:
                res = requests.get(f"https://huggingface.co/k2-fsa/OmniVoice/resolve/main/{{filename}}", timeout=20)
                if res.status_code == 200:
                    with open(dst_file, "wb") as out_f:
                        out_f.write(res.content)
            except Exception:
                pass
    return dst_root

def main():
    if not PUBLIC_API_BASE_URL:
        print("ERROR: PUBLIC_API_BASE_URL is not set. Exiting.", flush=True)
        sys.exit(1)

    log(f"Starting Kaggle Worker Daemon. Gateway: {{PUBLIC_API_BASE_URL}}")
    
    # 1. Register starting & launch background heartbeat thread so Gateway never times out during long tasks
    try:
        set_worker_state("starting", None, "OmniVoice worker starting up...")
        make_request(
            "POST", 
            "/v1/internal/workers/register", 
            json={{"worker_id": WORKER_ID, "status": "starting", "message": "OmniVoice worker starting up..."}}
        )
    except Exception as e:
        print(f"Failed to register startup with gateway: {{e}}", flush=True)
        sys.exit(1)

    start_heartbeat_daemon()

    # 2. Load OmniVoice onto local SSD -> GPU VRAM (~15s from HF Hub; avoids 22-min /kaggle/input gcsfuse mmap stall)
    log("Loading OmniVoice model into GPU VRAM...")
    try:
        set_worker_state("loading_model", None, "Loading OmniVoice weights into GPU VRAM...")
        make_request(
            "POST", 
            "/v1/internal/workers/heartbeat", 
            json={{"worker_id": WORKER_ID, "status": "loading_model", "message": "Loading OmniVoice weights into GPU VRAM..."}}
        )

        model = None
        model_loaded = False

        # 1. First priority: Hugging Face Hub directly onto container local SSD (~15s total on Kaggle 10Gbps link)
        try:
            t_load0 = time.time()
            log("Downloading & loading OmniVoice from Hugging Face Hub onto local SSD...")
            model = OmniVoice.from_pretrained(
                "k2-fsa/OmniVoice",
                device_map="cuda:0",
                dtype=torch.float16,
                load_asr=True,
            )
            log(f"OmniVoice model loaded from Hugging Face Hub in {{time.time() - t_load0:.1f}}s.")
            model_loaded = True
        except Exception as hf_err:
            log(f"Warning: Hugging Face Hub load failed ({{hf_err}}). Trying ModelScope fallback...")
            model_loaded = False

        # 2. Second priority: ModelScope fallback onto local SSD
        if not model_loaded:
            try:
                from modelscope import snapshot_download
                t_ms0 = time.time()
                log("Downloading model weights via ModelScope onto local SSD...")
                ms_dir = snapshot_download("k2-fsa/OmniVoice")
                model = OmniVoice.from_pretrained(
                    ms_dir,
                    device_map="cuda:0",
                    dtype=torch.float16,
                    load_asr=True,
                )
                log(f"OmniVoice model loaded via ModelScope in {{time.time() - t_ms0:.1f}}s.")
                model_loaded = True
            except Exception as ms_err:
                log(f"Warning: ModelScope fallback failed ({{ms_err}}). Checking mounted /kaggle/input...")
                model_loaded = False

        # 3. Third priority: Mounted Kaggle Dataset (MUST copy sequentially to /tmp local SSD first, NEVER mmap on /kaggle/input!)
        if not model_loaded and os.path.exists("/kaggle/input"):
            mounted_dir = None
            for root, dirs, files in os.walk("/kaggle/input"):
                if "model.safetensors" in files and "config.json" in files and not root.endswith("audio_tokenizer"):
                    mounted_dir = root
                    break
            if mounted_dir:
                log(f"Copying mounted weights from {{mounted_dir}} to local /tmp SSD...")
                local_ssd_dir = _copy_dataset_to_local_ssd(mounted_dir)
                model = OmniVoice.from_pretrained(
                    local_ssd_dir,
                    device_map="cuda:0",
                    dtype=torch.float16,
                    load_asr=True,
                )
                log(f"OmniVoice model loaded from local SSD copy: {{local_ssd_dir}}.")
                model_loaded = True

        if not model_loaded or model is None:
            raise RuntimeError("Could not load OmniVoice model from Hugging Face Hub, ModelScope, or local dataset.")

        log("OmniVoice model ready in GPU memory.")

        # Background preload faster-whisper so ASR & alignment run with zero cold-start delay
        def preload_whisper():
            try:
                log("Background preloading faster-whisper model...")
                get_whisper_model()
                log("Background faster-whisper model preloaded successfully.")
            except Exception as wp_err:
                log(f"Notice: faster-whisper background preload deferred: {{wp_err}}")

        threading.Thread(target=preload_whisper, daemon=True).start()

    except Exception as e:
        log(f"CRITICAL ERROR loading OmniVoice model: {{e}}")
        WORKER_STATE["stop"] = True
        try:
            make_request(
                "POST", 
                "/v1/internal/workers/register", 
                json={{"worker_id": WORKER_ID, "status": "failed", "message": f"Model load failed: {{str(e)}}"}}
            )
        except Exception:
            pass
        sys.exit(1)

    # Register as Ready
    set_worker_state("ready", None, "OmniVoice model ready for requests.")
    make_request(
        "POST", 
        "/v1/internal/workers/register", 
        json={{"worker_id": WORKER_ID, "status": "ready", "message": "OmniVoice model ready for requests."}}
    )

    idle_seconds = 0
    log("Entering job polling loop...")
    
    while True:
        try:
            set_worker_state("idle", None, f"Worker polling. Idle time: {{idle_seconds}}s")
            make_request(
                "POST",
                "/v1/internal/workers/heartbeat",
                json={{"worker_id": WORKER_ID, "status": "idle", "message": f"Worker polling. Idle time: {{idle_seconds}}s"}}
            )

            # Poll for job
            response = make_request("GET", f"/v1/internal/jobs/next?worker_id={{WORKER_ID}}")
            if response.status_code == 401:
                log("Unauthorized (401). Worker token invalid. Exiting.")
                break
                
            if response.status_code != 200:
                log(f"Warning: Poll returned status code {{response.status_code}}")
                time.sleep(POLL_INTERVAL)
                idle_seconds += POLL_INTERVAL
                continue

            data = response.json()
            job = data.get("job")

            if not job:
                time.sleep(POLL_INTERVAL)
                idle_seconds += POLL_INTERVAL
                if idle_seconds >= IDLE_TIMEOUT:
                    log(f"Idle timeout of {{IDLE_TIMEOUT}}s reached. Initiating shutdown.")
                    WORKER_STATE["stop"] = True
                    make_request(
                        "POST", 
                        "/v1/internal/workers/shutdown", 
                        json={{"worker_id": WORKER_ID, "reason": "idle_timeout"}}
                    )
                    break
                continue

            # Reset idle counter on job receipt
            idle_seconds = 0
            job_id = job["job_id"]
            job_type = job["job_type"]
            log(f"Processing job {{job_id}} ({{job_type}})")

            # Report busy status (also maintained continuously by background heartbeat daemon)
            set_worker_state("busy", job_id, f"Running {{job_type}}")
            make_request(
                "POST",
                "/v1/internal/workers/heartbeat",
                json={{"worker_id": WORKER_ID, "status": "busy", "current_job_id": job_id, "message": f"Running {{job_type}}"}}
            )

            # Update job status
            make_request(
                "POST",
                f"/v1/internal/jobs/{{job_id}}/status",
                json={{"status": "loading_model", "message": f"GPU Worker ({{WORKER_ID}}) đã nhận tác vụ {{job_type}}...", "progress": 35}}
            )

            # Process job based on type
            try:
                local_ref_path = None
                ref_audio_url = job.get("ref_audio_url")
                
                if job_type in ["clone_voice", "asr", "separate_audio", "dub_segments"] and ref_audio_url:
                    make_request(
                        "POST",
                        f"/v1/internal/jobs/{{job_id}}/status",
                        json={{"status": "preparing_input", "message": "Đang tải tệp âm thanh lên GPU Worker...", "progress": 45}}
                    )
                    
                    parsed_ref = urlparse(ref_audio_url)
                    ref_path = parsed_ref.path
                    if parsed_ref.query:
                        ref_path = ref_path + "?" + parsed_ref.query
                    res = make_request("GET", ref_path, stream=True, timeout=(15, 300))
                    if res.status_code == 200:
                        temp_fd, local_ref_path = tempfile.mkstemp(suffix=".wav")
                        os.close(temp_fd)
                        with open(local_ref_path, "wb") as f:
                            for chunk in res.iter_content(chunk_size=65536):
                                if chunk:
                                    f.write(chunk)
                        log(f"Downloaded audio to {{local_ref_path}} ({{os.path.getsize(local_ref_path)}} bytes)")
                    else:
                        raise Exception(f"Failed to download audio from gateway: {{res.status_code}} - {{res.text}}")

                # Handle separate_audio job type directly
                if job_type == "separate_audio":
                    make_request(
                        "POST",
                        f"/v1/internal/jobs/{{job_id}}/status",
                        json={{"status": "separating_audio", "message": "Đang chạy Demucs GPU tách giọng nói và nhạc nền...", "progress": 65}}
                    )
                    
                    vocals_path = "vocals.wav"
                    bgm_path = "bgm.wav"
                    
                    try:
                        try:
                            import demucs
                        except ImportError:
                            log("Installing demucs dynamically...")
                            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "--prefer-binary", "demucs"])
                            
                        demucs_device = "cuda" if torch.cuda.is_available() else "cpu"
                        log(f"Running Demucs (--two-stems=vocals, device={{demucs_device}}) on {{local_ref_path}}...")
                        t_sep0 = time.time()
                        demucs_cmd = [
                            sys.executable, "-m", "demucs",
                            "--two-stems=vocals",
                            "-d", demucs_device,
                            "-o", "demucs_out",
                            local_ref_path,
                        ]
                        proc_sep = subprocess.run(demucs_cmd, capture_output=True, text=True)
                        if proc_sep.returncode != 0:
                            log(f"Demucs module invocation exited {{proc_sep.returncode}}: {{proc_sep.stderr[-400:]}}. Retrying CLI entrypoint...")
                            subprocess.run([
                                "demucs", "--two-stems=vocals",
                                "-d", demucs_device,
                                "-o", "demucs_out",
                                local_ref_path
                            ], check=True)
                        
                        extracted_vocals = None
                        extracted_no_vocals = None
                        for root, dirs, files in os.walk("demucs_out"):
                            for file in files:
                                fl = file.lower()
                                if fl.endswith((".wav", ".mp3", ".flac")):
                                    full_p = os.path.join(root, file)
                                    if "no_vocals" in fl or "bgm" in fl or "music" in fl or "accompaniment" in fl:
                                        extracted_no_vocals = full_p
                                    elif "vocals" in fl:
                                        extracted_vocals = full_p
                                        
                        if extracted_vocals and extracted_no_vocals:
                            shutil.copy2(extracted_vocals, vocals_path)
                            shutil.copy2(extracted_no_vocals, bgm_path)
                            log(f"Demucs separation finished in {{time.time() - t_sep0:.1f}}s. Vocals: {{extracted_vocals}}, BGM: {{extracted_no_vocals}}")
                        else:
                            raise Exception(f"Could not locate demucs output files (vocals={{extracted_vocals}}, no_vocals={{extracted_no_vocals}})")
                            
                    except Exception as sep_err:
                        log(f"Warning: Demucs separation failed ({{sep_err}}). Using center-channel vocal attenuation fallback so speech never bleeds into BGM.")
                        shutil.copy2(local_ref_path, vocals_path)
                        try:
                            raw_wav, raw_sr = sf.read(local_ref_path, dtype="float32")
                            if raw_wav.ndim == 2 and raw_wav.shape[1] >= 2:
                                side = (raw_wav[:, 0] - raw_wav[:, 1]) * 0.5
                                bgm_arr = np.stack([side, side], axis=1)
                            else:
                                bgm_arr = np.zeros_like(raw_wav)
                            sf.write(bgm_path, bgm_arr, raw_sr, format="WAV", subtype="PCM_16")
                        except Exception:
                            shutil.copy2(local_ref_path, bgm_path)

                    make_request(
                        "POST",
                        f"/v1/internal/jobs/{{job_id}}/status",
                        json={{"status": "separating_audio", "message": "Đang đóng gói và tải kết quả tách nhạc về Gateway...", "progress": 90}}
                    )

                    # Zip vocals and bgm
                    zip_path = "separation.zip"
                    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zipf:
                        zipf.write(vocals_path, "vocals.wav")
                        zipf.write(bgm_path, "bgm.wav")
                        
                    for path_to_del in [vocals_path, bgm_path, local_ref_path]:
                        if path_to_del and os.path.exists(path_to_del):
                            os.remove(path_to_del)
                    if os.path.exists("demucs_out"):
                        shutil.rmtree("demucs_out")
                        
                    with open(zip_path, "rb") as zip_f:
                        files = {{"file": ("separation.zip", zip_f, "application/zip")}}
                        upload_res = make_request(
                            "POST",
                            f"/v1/internal/jobs/{{job_id}}/output",
                            files=files,
                            timeout=(15, 300),
                        )
                        
                    if upload_res.status_code == 200:
                        log(f"Successfully uploaded job {{job_id}} separation output.")
                    else:
                        raise Exception(f"Failed to upload separation output: {{upload_res.status_code}} - {{upload_res.text}}")
                        
                    if os.path.exists(zip_path):
                        os.remove(zip_path)
                    continue

                # Handle dub_segments job type directly
                if job_type == "dub_segments":
                    make_request(
                        "POST",
                        f"/v1/internal/jobs/{{job_id}}/status",
                        json={{"status": "generating_tts", "message": "Đang trích xuất đặc trưng giọng mẫu (VoiceClonePrompt)...", "progress": 55}}
                    )
                    
                    segments = json.loads(job["text"])
                    zip_path = "dubbed_segments.zip"
                    created_files = []
                    total_segs = max(1, len(segments))
                    
                    # Pre-compute VoiceClonePrompt ONCE for all segments to save massive processing time
                    voice_clone_prompt = None
                    if local_ref_path and os.path.exists(local_ref_path):
                        if hasattr(model, "create_voice_clone_prompt"):
                            try:
                                log("Pre-computing reusable VoiceClonePrompt for all segments...")
                                voice_clone_prompt = model.create_voice_clone_prompt(
                                    ref_audio=local_ref_path,
                                    ref_text=job.get("ref_text")
                                )
                                log("VoiceClonePrompt successfully pre-computed.")
                            except Exception as p_err:
                                log(f"Notice: create_voice_clone_prompt fallback to raw audio: {{p_err}}")
                                voice_clone_prompt = None

                    for idx, seg in enumerate(segments):
                        seg_id = seg.get("id", idx + 1) if isinstance(seg, dict) else (idx + 1)
                        seg_text = seg.get("text", "") if isinstance(seg, dict) else str(seg)
                        start_t = float(seg.get("start", 0.0)) if isinstance(seg, dict) else 0.0
                        end_t = float(seg.get("end", start_t + 3.0)) if isinstance(seg, dict) else (start_t + 3.0)
                        target_dur = max(0.5, end_t - start_t)

                        if idx == 0 or (idx + 1) % 3 == 0 or (idx + 1) == total_segs:
                            seg_pct = min(92, 60 + int(((idx + 1) / total_segs) * 32))
                            set_worker_state("busy", job_id, f"Dubbing segment {{idx+1}}/{{total_segs}}")
                            try:
                                make_request(
                                    "POST",
                                    f"/v1/internal/jobs/{{job_id}}/status",
                                    json={{
                                        "status": "generating_tts",
                                        "message": f"Đang sinh giọng clone phân đoạn {{idx + 1}}/{{total_segs}}...",
                                        "progress": seg_pct,
                                    }},
                                )
                            except Exception:
                                pass
                        
                        log(f"Dubbing segment {{seg_id}} ({{idx+1}}/{{total_segs}}): '{{seg_text[:60]}}' (target: {{target_dur:.2f}}s)")
                        if job.get("normalize_text", True) and seg_text:
                            seg_text = normalize_vietnamese_text(seg_text)
                        
                        gen_kwargs = {{"text": seg_text}}
                        if voice_clone_prompt is not None:
                            gen_kwargs["voice_clone_prompt"] = voice_clone_prompt
                        elif local_ref_path and os.path.exists(local_ref_path):
                            gen_kwargs["ref_audio"] = local_ref_path
                        
                        try:
                            audio_res = model.generate(**gen_kwargs)
                        except (TypeError, ImportError, Exception) as t_err:
                            err_str = str(t_err).lower()
                            if any(k in err_str for k in ["normalize_text", "wetextprocessing", "pynini", "no module named 'tn'", "module 'tn'"]):
                                log(f"Notice: normalize_text failed ({{t_err}}). Retrying without text normalization...")
                                gen_kwargs.pop("normalize_text", None)
                                audio_res = model.generate(**gen_kwargs)
                            else:
                                raise
                        
                        synth_dur = len(audio_res[0]) / 24000.0
                        log(f"Segment {{seg_id}} synthesized duration: {{synth_dur:.2f}}s")
                        
                        if synth_dur > target_dur + 0.2:
                            speed_val = min(2.5, max(1.1, synth_dur / target_dur))
                            log(f"Re-generating segment {{seg_id}} with speed={{speed_val:.2f}}...")
                            gen_kwargs["speed"] = speed_val
                            try:
                                audio_res = model.generate(**gen_kwargs)
                            except (TypeError, ImportError, Exception) as t_err:
                                err_str = str(t_err).lower()
                                if any(k in err_str for k in ["normalize_text", "wetextprocessing", "pynini", "no module named 'tn'", "module 'tn'"]):
                                    log(f"Notice: normalize_text failed ({{t_err}}). Retrying without text normalization...")
                                    gen_kwargs.pop("normalize_text", None)
                                    audio_res = model.generate(**gen_kwargs)
                                else:
                                    raise
                        
                        seg_wav_name = f"segment_{{seg_id}}.wav"
                        sf.write(seg_wav_name, audio_res[0], 24000, format='WAV', subtype='PCM_16')
                        created_files.append(seg_wav_name)
                        
                    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zipf:
                        for f_name in created_files:
                            zipf.write(f_name, f_name)
                            
                    for f_name in created_files:
                        if os.path.exists(f_name):
                            os.remove(f_name)
                    if local_ref_path and os.path.exists(local_ref_path):
                        os.remove(local_ref_path)
                        
                    with open(zip_path, "rb") as zip_f:
                        files = {{"file": ("dubbed_segments.zip", zip_f, "application/zip")}}
                        upload_res = make_request(
                            "POST",
                            f"/v1/internal/jobs/{{job_id}}/output",
                            files=files,
                            timeout=(15, 300),
                        )
                        
                    if upload_res.status_code == 200:
                        log(f"Successfully uploaded job {{job_id}} dubbed segments output.")
                    else:
                        raise Exception(f"Failed to upload dubbed segments output: {{upload_res.status_code}} - {{upload_res.text}}")
                        
                    if os.path.exists(zip_path):
                        os.remove(zip_path)
                    continue

                # Handle ASR job type directly (uses faster-whisper CTranslate2 for arbitrary-length audio + word timestamps)
                if job_type == "asr":
                    make_request(
                        "POST",
                        f"/v1/internal/jobs/{{job_id}}/status",
                        json={{"status": "transcribing", "message": "Đang nhận dạng giọng nói & mốc thời gian bằng Whisper GPU...", "progress": 70}}
                    )
                    
                    log(f"Transcribing audio {{local_ref_path}} with faster-whisper word timestamps...")
                    t_asr0 = time.time()
                    transcribed_text = ""
                    chunks = []
                    audio_dur_sec = None

                    try:
                        raw_audio, raw_sr = sf.read(local_ref_path, dtype="float32")
                        audio_16k = resample_to_16k(raw_audio, orig_sr=raw_sr)
                        audio_dur_sec = round(float(len(audio_16k) / 16000.0), 3)
                        asr_model = get_asr_whisper_model()
                        seg_iter, asr_info = asr_model.transcribe(
                            audio_16k,
                            word_timestamps=True,
                            vad_filter=True,
                            vad_parameters=dict(min_silence_duration_ms=400),
                        )
                        seg_list = list(seg_iter)
                        text_parts = []
                        for seg_obj in seg_list:
                            if seg_obj.text and seg_obj.text.strip():
                                text_parts.append(seg_obj.text.strip())
                            if seg_obj.words:
                                for w in seg_obj.words:
                                    w_str = (w.word or "").strip()
                                    if not w_str:
                                        continue
                                    s_val = round(float(w.start), 3)
                                    e_val = round(float(w.end), 3)
                                    chunks.append({{
                                        "word": w_str,
                                        "text": w_str,
                                        "start": s_val,
                                        "end": e_val,
                                        "timestamp": [s_val, e_val],
                                    }})
                            elif seg_obj.text and seg_obj.text.strip():
                                s_val = round(float(seg_obj.start), 3)
                                e_val = round(float(seg_obj.end), 3)
                                seg_words = seg_obj.text.strip().split()
                                step_w = max(0.05, (e_val - s_val) / max(1, len(seg_words)))
                                for wi, sw in enumerate(seg_words):
                                    ws = round(s_val + wi * step_w, 3)
                                    we = round(min(e_val, ws + step_w), 3)
                                    chunks.append({{
                                        "word": sw,
                                        "text": sw,
                                        "start": ws,
                                        "end": we,
                                        "timestamp": [ws, we],
                                    }})
                        transcribed_text = " ".join(text_parts).strip()
                        log(f"faster-whisper ASR completed in {{time.time() - t_asr0:.1f}}s ({{len(chunks)}} words, lang={{getattr(asr_info, 'language', 'auto')}}).")
                    except Exception as fw_asr_err:
                        log(f"Warning: faster-whisper ASR failed ({{fw_asr_err}}). Falling back to chunked HF pipeline...")
                        asr_res = model._asr_pipe(local_ref_path, chunk_length_s=30, return_timestamps=True)
                        transcribed_text = (asr_res.get("text") or "").strip()
                        raw_chunks = asr_res.get("chunks") or []
                        for rc in raw_chunks:
                            c_txt = (rc.get("text") or "").strip()
                            ts = rc.get("timestamp") or [0.0, 0.0]
                            cs = float(ts[0]) if ts[0] is not None else 0.0
                            ce = float(ts[1]) if (len(ts) > 1 and ts[1] is not None) else (cs + 2.0)
                            c_words = c_txt.split()
                            step_w = max(0.05, (ce - cs) / max(1, len(c_words)))
                            for wi, sw in enumerate(c_words):
                                ws = round(cs + wi * step_w, 3)
                                we = round(min(ce, ws + step_w), 3)
                                chunks.append({{
                                    "word": sw,
                                    "text": sw,
                                    "start": ws,
                                    "end": we,
                                    "timestamp": [ws, we],
                                }})
                    
                    chunks_json = json.dumps(chunks)
                    log(f"ASR complete. Text preview: {{transcribed_text[:120]}}")
                    
                    if local_ref_path and os.path.exists(local_ref_path):
                        os.remove(local_ref_path)
                        
                    asr_payload = {{"text": transcribed_text, "alignment": chunks_json}}
                    if audio_dur_sec is not None:
                        asr_payload["duration"] = audio_dur_sec
                    upload_res = make_request(
                        "POST",
                        f"/v1/internal/jobs/{{job_id}}/asr",
                        json=asr_payload
                    )
                    if upload_res.status_code == 200:
                        log(f"Successfully uploaded job {{job_id}} ASR results.")
                    else:
                        raise Exception(f"Failed to upload ASR result to gateway: {{upload_res.status_code}} - {{upload_res.text}}")
                    continue

                make_request(
                    "POST",
                    f"/v1/internal/jobs/{{job_id}}/status",
                    json={{"status": "generating_audio", "message": "Đang xử lý âm thanh...", "progress": 70}}
                )

                # Build generate arguments
                generate_args = {{
                    "text": job["text"]
                }}
                
                if job_type == "clone_voice":
                    generate_args["ref_audio"] = local_ref_path
                    if job.get("ref_text"):
                        generate_args["ref_text"] = job["ref_text"]
                elif job_type in ["voice_design_preview", "voice_design_tts"]:
                    generate_args["instruct"] = job.get("instruct")
                elif job_type != "auto_voice":
                    raise Exception(f"Unknown job type: {{job_type}}")
                
                optional_keys = [
                    "num_step", "denoise", "guidance_scale", "t_shift",
                    "position_temperature", "class_temperature", "layer_penalty_factor",
                    "duration", "speed", "preprocess_prompt", "postprocess_output",
                    "audio_chunk_duration", "audio_chunk_threshold",
                    "language", "pad_duration", "fade_duration"
                ]
                for key in optional_keys:
                    if key in job and job[key] is not None:
                        generate_args[key] = job[key]

                log(f"Calling model.generate with arguments: {{list(generate_args.keys())}}")
                try:
                    audio_result = model.generate(**generate_args)
                except (TypeError, ImportError, Exception) as gen_err:
                    err_str = str(gen_err).lower()
                    if any(k in err_str for k in ["normalize_text", "wetextprocessing", "pynini", "no module named 'tn'", "module 'tn'"]):
                        log(f"Notice: normalize_text failed ({{gen_err}}). Retrying model.generate without text normalization...")
                        generate_args.pop("normalize_text", None)
                        audio_result = model.generate(**generate_args)
                    else:
                        raise

                # Clean up local ref path if exists
                if local_ref_path and os.path.exists(local_ref_path):
                    os.remove(local_ref_path)

                # Export WAV
                make_request(
                    "POST",
                    f"/v1/internal/jobs/{{job_id}}/status",
                    json={{"status": "exporting_wav", "message": "Đang xuất WAV...", "progress": 90}}
                )
                
                temp_out_fd, local_out_path = tempfile.mkstemp(suffix=".wav")
                os.close(temp_out_fd)
                
                sf.write(local_out_path, audio_result[0], 24000, format='WAV', subtype='PCM_16')
                log(f"Generated audio saved to {{local_out_path}}")

                # Generate word alignments
                alignment_str = None
                if job.get("with_alignment"):
                    log("Generating word alignment...")
                    alignment_list = None
                    try:
                        log("Attempting precise word alignment using faster-whisper...")
                        w_model = get_whisper_model()
                        log(f"Transcribing audio {{local_out_path}} with word timestamps...")
                        
                        # Dynamic language detection
                        job_text = job.get("text") or ""
                        vi_chars = set("áàảãạăắằẳẵặâấầẩẫậéèẻẽẹêếềểễệíìỉĩịóòỏõọôốồổỗộơớờởỡợúùủũụưứừửữựýỳỷỹỵđ")
                        is_vi = any(c in vi_chars for c in job_text.lower())
                        lang = "vi" if is_vi else None
                        log(f"Language detection: is_vi={{is_vi}}, using language_param={{lang}}")
                        
                        duration_sec = len(audio_result[0]) / 24000.0
                        
                        # Resample to 16kHz float32 numpy array to bypass PyAV av.open bug
                        resampled_16k = resample_to_16k(audio_result[0], 24000)
                        segments, info = w_model.transcribe(
                            resampled_16k, 
                            word_timestamps=True,
                            language=lang
                        )
                        
                        transcribed_words = []
                        for segment in segments:
                            if segment.words:
                                for w in segment.words:
                                    transcribed_words.append(dict(
                                        word=w.word.strip(),
                                        start=w.start,
                                        end=w.end
                                    ))
                        
                        log(f"Whisper transcribed {{len(transcribed_words)}} words.")
                        
                        original_words = job_text.split()
                        if original_words and transcribed_words:
                            alignment_list = align_words(original_words, transcribed_words, duration_sec)
                            if alignment_list:
                                log(f"Successfully aligned {{len(alignment_list)}} original words with Whisper timestamps.")
                    except Exception as whisper_err:
                        log(f"Warning: Precise alignment failed: {{whisper_err}}. Falling back to proportional spacing.")
                        log(traceback.format_exc())
                        
                    # Fallback to proportional spacing
                    if not alignment_list:
                        words = (job.get("text") or "").split()
                        if words:
                            duration_sec = len(audio_result[0]) / 24000.0
                            word_dur = duration_sec / len(words)
                            alignment_list = []
                            curr_time = 0.0
                            for w in words:
                                clean_w = w.strip(".,!?\\"'")
                                alignment_list.append(dict(
                                    word=clean_w,
                                    start=round(curr_time, 3),
                                    end=round(curr_time + word_dur, 3)
                                ))
                                curr_time += word_dur
                                
                    if alignment_list:
                        alignment_str = json.dumps(alignment_list)
                        log(f"Generated alignment data: {{len(alignment_list)}} words")

                # Upload output WAV along with any alignment data
                data_payload = {{}}
                if alignment_str:
                    data_payload["alignment"] = alignment_str

                with open(local_out_path, "rb") as out_file:
                    files = {{"file": (f"{{job_id}}.wav", out_file, "audio/wav")}}
                    upload_res = make_request(
                        "POST", 
                        f"/v1/internal/jobs/{{job_id}}/output", 
                        files=files,
                        data=data_payload
                    )
                    
                if upload_res.status_code == 200:
                    log(f"Successfully uploaded job {{job_id}} output audio.")
                else:
                    raise Exception(f"Failed to upload audio to gateway: {{upload_res.status_code}} - {{upload_res.text}}")

                if os.path.exists(local_out_path):
                    os.remove(local_out_path)

            except Exception as inner_e:
                err_str = str(inner_e)
                trace = traceback.format_exc()
                log(f"Error executing job {{job_id}}: {{err_str}}\\n{{trace}}")
                
                make_request(
                    "POST",
                    f"/v1/internal/jobs/{{job_id}}/status",
                    json={{
                        "status": "failed",
                        "message": "Lỗi xử lý âm thanh.",
                        "progress": 100,
                        "error_message": f"{{err_str}}\\n{{trace}}"
                    }}
                )

        except Exception as e:
            log(f"Network or loop error: {{e}}")
            time.sleep(POLL_INTERVAL)

    log("Worker execution finished.")

if __name__ == '__main__':
    main()
"""
        else:
            # Fallback values for job parameters
            # Fallback values for job parameters
            job_params = {
                "id": "test_push",
                "job_type": "auto_voice",
                "text": "Xin chào, đây là bản thử nghiệm đẩy kết nối Kaggle.",
                "ref_text": None,
                "instruct": None,
                "voice_sample_id": None,
                "speed": 1.0,
                "num_step": 32,
                "denoise": True,
                "guidance_scale": 2.0,
                "t_shift": 0.1,
                "position_temperature": 5.0,
                "class_temperature": 0.0,
                "layer_penalty_factor": 5.0,
                "duration": None,
                "preprocess_prompt": True,
                "postprocess_output": True,
                "audio_chunk_duration": 15.0,
                "audio_chunk_threshold": 30.0,
                "language": None,
                "pad_duration": None,
                "fade_duration": None,
                "normalize_text": True
            }

            if job:
                if isinstance(job, dict):
                    for k in job_params.keys():
                        if k in job:
                            job_params[k] = job[k]
                else:
                    for k in job_params.keys():
                        attr_val = getattr(job, k if k != "id" else "id", None)
                        if attr_val is not None:
                            job_params[k] = attr_val

            job_id = job_params["id"]
            job_type = job_params["job_type"]
            text = job_params["text"]
            ref_text = job_params["ref_text"]
            instruct = job_params["instruct"]
            voice_sample_id = job_params["voice_sample_id"]
            speed = job_params["speed"]
            num_step = job_params["num_step"]
            denoise = job_params["denoise"]
            guidance_scale = job_params["guidance_scale"]
            t_shift = job_params["t_shift"]
            position_temperature = job_params["position_temperature"]
            class_temperature = job_params["class_temperature"]
            layer_penalty_factor = job_params["layer_penalty_factor"]
            duration = job_params["duration"]
            preprocess_prompt = job_params["preprocess_prompt"]
            postprocess_output = job_params["postprocess_output"]
            audio_chunk_duration = job_params["audio_chunk_duration"]
            audio_chunk_threshold = job_params["audio_chunk_threshold"]
            language = job_params["language"]
            pad_duration = job_params["pad_duration"]
            fade_duration = job_params["fade_duration"]
            normalize_text = job_params.get("normalize_text", True)
            if normalize_text and text:
                from app.utils.vietnamese_normalizer import normalize_vietnamese_text
                text = normalize_vietnamese_text(text)

            ref_audio_url = None
            if voice_sample_id:
                base_url = public_api_url or settings.PUBLIC_API_BASE_URL
                ref_audio_url = f"{base_url.rstrip('/')}/v1/internal/files/voice-samples/{voice_sample_id}"

            # Pure Python batch execution script template
            code = f"""import os
import sys
import time
import uuid
import tempfile
import traceback
import requests
import warnings
import shutil
import zipfile
import json
import subprocess
from urllib.parse import urlparse

os.environ["CUDA_MODULE_LOADING"] = "LAZY"
os.environ["PYTHONWARNINGS"] = "ignore"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["HF_HUB_ETAG_TIMEOUT"] = "15"
os.environ["HF_TOKEN"] = {repr(hf_token)}
os.environ["HUGGING_FACE_HUB_TOKEN"] = {repr(hf_token)}
warnings.filterwarnings("ignore")

def ensure_dependencies():
    \"\"\"Dynamically checks and installs required packages inside the Kaggle environment if missing or outdated.\"\"\"
    missing = []
    need_omnivoice_upgrade = False
    try:
        import omnivoice
        from omnivoice.models.omnivoice import VoiceClonePrompt
    except (ImportError, AttributeError):
        need_omnivoice_upgrade = True

    try:
        import soundfile
    except ImportError:
        missing.append("soundfile")
    try:
        import num2words
    except ImportError:
        missing.append("num2words")
    try:
        import modelscope
    except ImportError:
        missing.append("modelscope")
    try:
        import sentencepiece
    except ImportError:
        missing.append("sentencepiece")
    try:
        import tiktoken
    except ImportError:
        missing.append("tiktoken")
    try:
        import faster_whisper
    except ImportError:
        missing.append("faster-whisper")
        
    import subprocess
    packages_to_install = []
    if need_omnivoice_upgrade:
        packages_to_install.append("git+https://github.com/k2-fsa/OmniVoice.git")
    packages_to_install.extend(missing)

    if packages_to_install:
        print(f"Installing missing dependencies: {{', '.join(packages_to_install)}}")
        try:
            # Install packages silently with fast flags
            subprocess.check_call([
                sys.executable, "-m", "pip", "install", "-q", 
                "--no-cache-dir", "--prefer-binary", 
                "--no-warn-script-location"
            ] + packages_to_install)
            print("Dependencies installed successfully.")
        except Exception as e:
            if need_omnivoice_upgrade:
                print(f"Notice: Batch install failed ({{e}}), falling back to omnivoice[tn]...")
                try:
                    fallback_packages = ["omnivoice[tn]"] + missing
                    subprocess.check_call([
                        sys.executable, "-m", "pip", "install", "-q",
                        "--no-cache-dir", "--prefer-binary",
                        "--no-warn-script-location"
                    ] + fallback_packages)
                    print("Fallback dependencies installed successfully.")
                except Exception as fb_err:
                    print(f"Failed to install fallback dependencies: {{fb_err}}")
                    sys.exit(1)
            else:
                print(f"Failed to install dependencies: {{e}}")
                sys.exit(1)

# Ensure dependencies are available before anything else runs
ensure_dependencies()

import torch
import soundfile as sf
from omnivoice import OmniVoice

JOB_ID = {repr(job_id)}
JOB_TYPE = {repr(job_type)}
TEXT = {repr(text)}
REF_AUDIO_URL = {repr(ref_audio_url)}
REF_TEXT = {repr(ref_text)}
INSTRUCT = {repr(instruct)}
SPEED = {speed}
NUM_STEP = {num_step}
DENOISE = {denoise}
GUIDANCE_SCALE = {guidance_scale}
T_SHIFT = {t_shift}
POSITION_TEMPERATURE = {position_temperature}
CLASS_TEMPERATURE = {class_temperature}
LAYER_PENALTY_FACTOR = {layer_penalty_factor}
DURATION = {duration}
PREPROCESS_PROMPT = {preprocess_prompt}
POSTPROCESS_OUTPUT = {postprocess_output}
AUDIO_CHUNK_DURATION = {audio_chunk_duration}
AUDIO_CHUNK_THRESHOLD = {audio_chunk_threshold}
LANGUAGE = {repr(language)}
PAD_DURATION = {pad_duration}
FADE_DURATION = {fade_duration}
NORMALIZE_TEXT = {repr(normalize_text)}
WORKER_TOKEN = {repr(worker_token)}

def main():
    print(f"Starting Kaggle Batch Worker for Job {{JOB_ID}} ({{JOB_TYPE}})")
    sys.stdout.flush()
    
    local_ref_path = None
    if JOB_TYPE in ["clone_voice", "asr"] and REF_AUDIO_URL:
        print(f"Downloading audio from {{REF_AUDIO_URL}}...")
        sys.stdout.flush()
        headers = {{}}
        if WORKER_TOKEN:
            headers["Authorization"] = f"Bearer {{WORKER_TOKEN}}"
        
        try:
            res = requests.get(REF_AUDIO_URL, headers=headers, stream=True)
            if res.status_code == 200:
                local_ref_path = "ref_audio.wav"
                with open(local_ref_path, "wb") as f:
                    for chunk in res.iter_content(chunk_size=8192):
                        f.write(chunk)
                print(f"Downloaded reference voice sample successfully to {{local_ref_path}}")
            else:
                print(f"Failed to download reference audio: {{res.status_code}} - {{res.text}}")
                sys.exit(1)
        except Exception as e:
            print(f"Error downloading reference audio: {{e}}")
            sys.exit(1)

    print("Loading OmniVoice model...")
    sys.stdout.flush()
    try:
        model_loaded = False
        
        # 1. First priority: Hugging Face Hub onto local SSD (~15s total; avoids 22-min /kaggle/input gcsfuse mmap stall)
        try:
            print("Attempting to load model from Hugging Face Hub...")
            sys.stdout.flush()
            model = OmniVoice.from_pretrained(
                "k2-fsa/OmniVoice",
                device_map="cuda:0",
                dtype=torch.float16,
                load_asr=True,
            )
            print("OmniVoice model loaded successfully from Hugging Face Hub.")
            sys.stdout.flush()
            model_loaded = True
        except Exception as hf_err:
            print(f"Warning: Failed to load from Hugging Face Hub (CDN issue?): {{hf_err}}")
            sys.stdout.flush()
            model_loaded = False

        # 2. Second priority: ModelScope fallback (Stable alternative source if HF is down)
        if not model_loaded:
            try:
                from modelscope import snapshot_download
                print("Falling back to ModelScope to download model weights...")
                sys.stdout.flush()
                ms_dir = snapshot_download("k2-fsa/OmniVoice")
                model = OmniVoice.from_pretrained(
                    ms_dir,
                    device_map="cuda:0",
                    dtype=torch.float16,
                    load_asr=True,
                )
                print(f"OmniVoice model loaded successfully via ModelScope from: {{ms_dir}}")
                sys.stdout.flush()
                model_loaded = True
            except Exception as ms_err:
                print(f"CRITICAL: Failed to download from ModelScope: {{ms_err}}")
                sys.stdout.flush()
                raise ms_err

        print("OmniVoice model loaded successfully.")
        sys.stdout.flush()

    except Exception as e:
        print(f"CRITICAL ERROR loading OmniVoice model: {{e}}")
        sys.stdout.flush()
        sys.exit(1)

    print("Generating audio...")
    sys.stdout.flush()
    try:
        if JOB_TYPE == "asr":
            if not local_ref_path:
                raise Exception("Missing audio path for ASR")
            print(f"Transcribing audio {{local_ref_path}}...")
            sys.stdout.flush()
            
            asr_res = model._asr_pipe(local_ref_path, return_timestamps="word")
            transcribed_text = asr_res.get("text", "").strip()
            chunks = asr_res.get("chunks", [])
            
            import json
            chunks_json = json.dumps(chunks)
            
            print(f"ASR complete. Text: {{transcribed_text}}")
            sys.stdout.flush()
            
            if local_ref_path and os.path.exists(local_ref_path):
                os.remove(local_ref_path)
                
            headers = {{}}
            if WORKER_TOKEN:
                headers["Authorization"] = f"Bearer {{WORKER_TOKEN}}"
                
            res = requests.post(
                f"{{PUBLIC_API_URL}}/v1/internal/jobs/{{JOB_ID}}/asr",
                headers=headers,
                json={{"text": transcribed_text, "alignment": chunks_json}}
            )
            if res.status_code == 200:
                print("ASR result uploaded successfully.")
                sys.stdout.flush()
                sys.exit(0)
            else:
                print(f"Failed to upload ASR result: {{res.status_code}} - {{res.text}}")
                sys.stdout.flush()
                sys.exit(1)

        generate_args = {{
            "text": TEXT
        }}
        
        if JOB_TYPE == "clone_voice":
            if not local_ref_path:
                raise Exception("Missing reference audio path for clone_voice")
            print(f"Generating voice cloning for: {{TEXT}}")
            generate_args["ref_audio"] = local_ref_path
            if REF_TEXT:
                generate_args["ref_text"] = REF_TEXT
        elif JOB_TYPE in ["voice_design_preview", "voice_design_tts"]:
            print(f"Generating voice design for: {{TEXT}} with instruct: {{INSTRUCT}}")
            generate_args["instruct"] = INSTRUCT
        elif JOB_TYPE == "auto_voice":
            print(f"Generating auto voice for: {{TEXT}}")
        else:
            raise Exception(f"Unknown job type: {{JOB_TYPE}}")

        # Add optional parameter inputs if they are not None
        params_map = {{
            "num_step": NUM_STEP,
            "denoise": DENOISE,
            "guidance_scale": GUIDANCE_SCALE,
            "t_shift": T_SHIFT,
            "position_temperature": POSITION_TEMPERATURE,
            "class_temperature": CLASS_TEMPERATURE,
            "layer_penalty_factor": LAYER_PENALTY_FACTOR,
            "duration": DURATION,
            "speed": SPEED,
            "preprocess_prompt": PREPROCESS_PROMPT,
            "postprocess_output": POSTPROCESS_OUTPUT,
            "audio_chunk_duration": AUDIO_CHUNK_DURATION,
            "audio_chunk_threshold": AUDIO_CHUNK_THRESHOLD,
            "language": LANGUAGE,
            "pad_duration": PAD_DURATION,
            "fade_duration": FADE_DURATION
        }}
        for key, val in params_map.items():
            if val is not None:
                generate_args[key] = val

        print(f"Calling model.generate with arguments: {{list(generate_args.keys())}}")
        try:
            audio_result = model.generate(**generate_args)
        except (TypeError, ImportError, Exception) as gen_err:
            err_str = str(gen_err).lower()
            if any(k in err_str for k in ["normalize_text", "wetextprocessing", "pynini", "no module named 'tn'", "module 'tn'"]):
                print(f"Notice: normalize_text failed ({{gen_err}}). Retrying model.generate without text normalization...")
                generate_args.pop("normalize_text", None)
                audio_result = model.generate(**generate_args)
            else:
                raise

        # Save to output.wav in the current directory (which is /kaggle/working/ output folder)
        output_filename = "output.wav"
        sf.write(output_filename, audio_result[0], 24000, format='WAV', subtype='PCM_16')
        print(f"Generated audio saved successfully to {{output_filename}}")
        sys.stdout.flush()

    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"Generation failed: {{e}}\\n{{error_trace}}")
        sys.stdout.flush()
        sys.exit(1)

if __name__ == '__main__':
    main()
"""

        with open(worker_path, "w", encoding="utf-8") as f:
            f.write(code)
            
        print(f"[KaggleNotebookBuilder] Generated worker script at: {worker_path}")
        return worker_path

    @staticmethod
    def prepare_all(job=None, db=None, public_api_url: str = None, is_daemon: bool = True, user_id: str = None) -> str:
        """
        Runs the full prepare pipeline: ensures directory,
        writes requirements.txt, kernel-metadata.json, and omnivoice_worker.py.
        Returns the absolute path of the worker directory.
        """
        if not user_id and job:
            if isinstance(job, dict):
                user_id = job.get("user_id")
            else:
                user_id = getattr(job, "user_id", None)

        # Resolve settings/DB configs
        from app.services.kaggle_orchestrator import KaggleOrchestrator
        username, _, kernel_ref, worker_dir = KaggleOrchestrator.get_credentials(db, user_id)
        
        # Resolve public_api_url dynamically if not passed
        if not public_api_url:
            public_api_url = settings.PUBLIC_API_BASE_URL
            
        # Hugging Face Space auto-detection
        if not public_api_url and os.environ.get("SPACE_HOST"):
            public_api_url = f"https://{os.environ.get('SPACE_HOST')}"
            print(f"[KaggleNotebookBuilder] Auto-detected Hugging Face Space Host URL: {public_api_url}")
            
        # Resolve accelerator settings
        accelerator = settings.KAGGLE_ACCELERATOR
        if db:
            from app.models import SystemSetting
            db_acc = db.query(SystemSetting).filter(SystemSetting.key == "kaggle_accelerator").first()
            if db_acc and db_acc.value.strip():
                accelerator = db_acc.value.strip()
                
            if user_id:
                from app.models import UserSetting
                u_acc = db.query(UserSetting).filter(UserSetting.user_id == user_id, UserSetting.key == "kaggle_accelerator").first()
                if u_acc and u_acc.value.strip():
                    accelerator = u_acc.value.strip()
        
        # Construct dynamic slug and directory based on worker_id
        worker_id = None
        if job:
            if isinstance(job, dict):
                worker_id = job.get("worker_id")
            else:
                worker_id = getattr(job, "worker_id", None)
                
        if worker_id == "worker_2":
            slug = "omnivoice-worker-2"
            worker_dir = f"{worker_dir}-2"
        else:
            slug = "omnivoice-worker-1"
            worker_dir = f"{worker_dir}-1"
            
        title = slug.replace("-", " ").title()

        worker_dir_abs = KaggleNotebookBuilder.ensure_worker_dir(worker_dir)
        KaggleNotebookBuilder.generate_requirements(worker_dir_abs)
        KaggleNotebookBuilder.generate_metadata(worker_dir_abs, username, slug, title, accelerator)
        
        # Workers always authenticate using the gateway system WORKER_TOKEN (never user API keys)
        _ = user_id
        worker_token = settings.WORKER_TOKEN

        # Generate worker script with embedded credentials and API URLs
        KaggleNotebookBuilder.generate_worker_code(worker_dir_abs, job, public_api_url, worker_token, is_daemon=is_daemon, db=db)
        
        return worker_dir_abs
