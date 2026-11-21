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
        req_content = "omnivoice\nsoundfile\nnumpy\nrequests\nfaster-whisper\ndemucs\n"
        
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
            
            # Optimized Turbo Daemon script template (v3.0 - Overlapped /dev/shm + Xet + uv)
            code = f"""import os
import sys
import io
import glob
import time
import uuid
import ctypes
import tempfile
import traceback
import requests
import warnings
import shutil
import zipfile
import json
import subprocess
import threading
import importlib
import importlib.util
import importlib.metadata
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

_BOOT_T0 = time.perf_counter()

os.environ["CUDA_MODULE_LOADING"] = "LAZY"
os.environ["SAFETENSORS_FAST_GPU"] = "1"
os.environ["USE_TF"] = "0"
os.environ["USE_JAX"] = "0"
os.environ["USE_FLAX"] = "0"
os.environ["USE_TORCH"] = "1"
os.environ["PYTHONWARNINGS"] = "ignore"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["TRANSFORMERS_NO_ADVISORY_WARNINGS"] = "1"
os.environ["HF_HUB_DISABLE_XET"] = "0"
os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["DISABLE_TELEMETRY"] = "1"
os.environ["TQDM_DISABLE"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "true"
os.environ["HF_HUB_ETAG_TIMEOUT"] = "15"
if {repr(hf_token)}:
    os.environ["HF_TOKEN"] = {repr(hf_token)}
    os.environ["HUGGING_FACE_HUB_TOKEN"] = {repr(hf_token)}
warnings.filterwarnings("ignore")

# PEP-451 compliant PathFinder filter: skips probing unused heavy ML/CV packages during cold import
try:
    import importlib._bootstrap_external as _ibe
    _orig_pf_find_spec = _ibe.PathFinder.find_spec
    _SKIP_UNUSED_TOP = frozenset({{
        "tensorflow", "tensorboard", "keras", "jax", "jaxlib", "flax",
        "torchvision", "sklearn", "scikit_learn", "matplotlib", "pandas",
        "cv2", "timm", "peft", "bitsandbytes", "optimum",
    }})
    @classmethod
    def _fast_pf_find_spec(cls, fullname, path=None, target=None):
        if fullname.split(".")[0] in _SKIP_UNUSED_TOP:
            return None
        return _orig_pf_find_spec(fullname, path, target)
    _ibe.PathFinder.find_spec = _fast_pf_find_spec
except Exception:
    pass

PUBLIC_API_BASE_URL = {repr(public_api_url)}.rstrip("/")
WORKER_TOKEN = {repr(worker_token)}
WORKER_ID = {repr(worker_id)}
IDLE_TIMEOUT = {idle_timeout}
POLL_INTERVAL = {poll_interval}

HEADERS = {{
    "Authorization": f"Bearer {{WORKER_TOKEN}}",
    "X-Worker-Version": "3.0.0"
}}

WORKER_STATE = {{
    "status": "starting",
    "current_job_id": None,
    "message": "OmniVoice turbo worker booting...",
    "stop": False,
}}

def log(msg: str):
    print(f"[{{time.strftime('%Y-%m-%d %H:%M:%S')}}] [+{{time.perf_counter()-_BOOT_T0:5.1f}}s] [Worker-{{WORKER_ID}}] {{msg}}", flush=True)

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

def _select_fast_scratch_dir() -> str:
    try:
        if os.path.exists("/dev/shm"):
            st = os.statvfs("/dev/shm")
            if (st.f_bavail * st.f_frsize) / (1024 ** 3) >= 6.0:
                return "/dev/shm"
    except Exception:
        pass
    return tempfile.gettempdir()

FAST_SCRATCH_DIR = _select_fast_scratch_dir()
FAST_MODEL_DIR = os.path.join(FAST_SCRATCH_DIR, "omnivoice_fast_weights")
FAST_PKGS_DIR = os.path.join(FAST_SCRATCH_DIR, "omnivoice_fast_pkgs")
os.makedirs(FAST_PKGS_DIR, exist_ok=True)

def _warm_filesystem_caches():
    \"\"\"Triggers async Linux kernel readahead on CUDA/PyTorch .so libs and warms .dist-info inodes.\"\"\"
    py_ver = f"python{{sys.version_info.major}}.{{sys.version_info.minor}}"
    base = f"/usr/local/lib/{{py_ver}}/dist-packages"
    if hasattr(os, "posix_fadvise") and hasattr(os, "POSIX_FADV_WILLNEED"):
        for pat in (f"{{base}}/torch/lib/*.so*", f"{{base}}/torchaudio/lib/*.so*", f"{{base}}/nvidia/*/lib/*.so*"):
            for p in glob.glob(pat):
                try:
                    fd = os.open(p, os.O_RDONLY)
                    try:
                        os.posix_fadvise(fd, 0, os.fstat(fd).st_size, os.POSIX_FADV_WILLNEED)
                    finally:
                        os.close(fd)
                except Exception:
                    pass
    meta_files = glob.glob(f"{{base}}/*.dist-info/METADATA")
    if meta_files:
        def _read_meta(p):
            try:
                with open(p, "rb") as f:
                    f.read(4096)
            except Exception:
                pass
        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(_read_meta, meta_files, chunksize=32))

def _pip_or_uv_install(packages):
    if not packages:
        return
    uv_bin = shutil.which("uv")
    if uv_bin:
        subprocess.check_call([uv_bin, "pip", "install", "--system", "--no-compile", "--quiet"] + list(packages))
    else:
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "-q",
            "--no-cache-dir", "--no-compile", "--prefer-binary", "--no-warn-script-location"
        ] + list(packages))

def _prepare_core_packages():
    \"\"\"Installs only minimal core requirements for OmniVoice in ~0.2-5s without blocking torch import.\"\"\"
    need_tf_upgrade = False
    try:
        tf_ver = importlib.metadata.version("transformers")
        major, minor = [int(x) for x in tf_ver.split(".")[:2]]
        if (major, minor) < (5, 3):
            need_tf_upgrade = True
    except Exception:
        need_tf_upgrade = True

    ov_ready = importlib.util.find_spec("omnivoice") is not None

    if not ov_ready:
        try:
            req = urllib.request.Request(
                "https://codeload.github.com/k2-fsa/OmniVoice/zip/refs/heads/master",
                headers={{"User-Agent": "OmniVoiceWorker/3.0"}}
            )
            raw = urllib.request.urlopen(req, timeout=20).read()
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                for member in zf.infolist():
                    if member.filename.startswith("OmniVoice-master/omnivoice/") and not member.is_dir():
                        rel = member.filename[len("OmniVoice-master/"):]
                        dst = os.path.join(FAST_PKGS_DIR, rel)
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        with zf.open(member) as src, open(dst, "wb") as out:
                            shutil.copyfileobj(src, out)
            ov_ready = True
        except Exception as zip_err:
            log(f"Notice: Direct OmniVoice zip unpack fallback ({{zip_err}}), using uv/pip...")
            _pip_or_uv_install(["omnivoice"])

    extra_core = []
    if need_tf_upgrade:
        extra_core.append("transformers>=5.3.0")
    try:
        importlib.metadata.version("soundfile")
    except Exception:
        extra_core.append("soundfile")
    if extra_core:
        _pip_or_uv_install(extra_core)

def _spawn_parallel_weights_download():
    \"\"\"Spawns a separate OS process using native Rust hf_xet to download 3.12GB into /dev/shm with zero GIL contention.\"\"\"
    dl_script = f\"\"\"
import os
os.environ["HF_HUB_DISABLE_XET"] = "0"
os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
from huggingface_hub import snapshot_download
snapshot_download(
    "k2-fsa/OmniVoice",
    local_dir={{repr(FAST_MODEL_DIR)}},
    allow_patterns=[
        "config.json",
        "chat_template.jinja",
        "tokenizer_config.json",
        "tokenizer.json",
        "model.safetensors",
        "audio_tokenizer/config.json",
        "audio_tokenizer/preprocessor_config.json",
        "audio_tokenizer/model.safetensors",
    ],
    max_workers=16,
)
\"\"\"
    return subprocess.Popen([sys.executable, "-c", dl_script])

# Register early startup with Gateway & launch parallel warmup + download BEFORE heavy torch import!
_EARLY_REGISTERED = False
if PUBLIC_API_BASE_URL:
    def _early_register():
        global _EARLY_REGISTERED
        try:
            make_request(
                "POST",
                "/v1/internal/workers/register",
                json={{"worker_id": WORKER_ID, "status": "starting", "message": "OmniVoice turbo worker booting..."}},
                timeout=(10, 20),
            )
            _EARLY_REGISTERED = True
        except Exception as reg_e:
            log(f"Notice: early startup register deferred: {{reg_e}}")
    threading.Thread(target=_early_register, daemon=True).start()
    start_heartbeat_daemon()

_warm_filesystem_caches()
_WEIGHTS_DL_PROC = _spawn_parallel_weights_download()
_CORE_PKG_POOL = ThreadPoolExecutor(max_workers=1)
_CORE_PKG_FUT = _CORE_PKG_POOL.submit(_prepare_core_packages)

# Import pre-installed PyTorch & initialize CUDA immediately while download & core pkgs finish in parallel!
import numpy as np
import torch
if torch.cuda.is_available():
    try:
        torch.cuda.init()
        _ = torch.empty(1, device="cuda:0")
    except Exception:
        pass

def ensure_dependencies():
    _CORE_PKG_FUT.result()
    _CORE_PKG_POOL.shutdown(wait=False)
    if FAST_PKGS_DIR not in sys.path:
        sys.path.insert(0, FAST_PKGS_DIR)
    importlib.invalidate_caches()

ensure_dependencies()

import soundfile as sf
from omnivoice import OmniVoice

{KaggleNotebookBuilder.get_normalizer_code()}

_SECONDARY_TOOLS_LOCK = threading.Lock()
_SECONDARY_TOOLS_INSTALLED = False

def ensure_secondary_tools(need_whisper: bool = True, need_demucs: bool = False):
    \"\"\"Thread-safely installs faster-whisper / demucs via uv (~2s) on-demand or in idle background.\"\"\"
    global _SECONDARY_TOOLS_INSTALLED
    with _SECONDARY_TOOLS_LOCK:
        to_install = []
        if need_whisper:
            try:
                import faster_whisper
            except ImportError:
                to_install.append("faster-whisper")
        if need_demucs:
            try:
                import demucs
            except ImportError:
                to_install.append("demucs")
        if to_install:
            log(f"Installing secondary audio tools via uv: {{', '.join(to_install)}}")
            _pip_or_uv_install(to_install)
        if need_whisper and need_demucs:
            _SECONDARY_TOOLS_INSTALLED = True

def _preload_cuda_runtime_libs():
    \"\"\"Exposes PyTorch's bundled cuBLAS/cuDNN shared libraries globally for CTranslate2 compatibility.\"\"\"
    py_ver = f"python{{sys.version_info.major}}.{{sys.version_info.minor}}"
    base = f"/usr/local/lib/{{py_ver}}/dist-packages/nvidia"
    for pat in (f"{{base}}/cublas/lib/libcublas*.so*", f"{{base}}/cudnn/lib/libcudnn*.so*"):
        for lib_path in sorted(glob.glob(pat)):
            try:
                ctypes.CDLL(lib_path, mode=ctypes.RTLD_GLOBAL)
            except Exception:
                pass

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

def _verify_whisper_instance(wm):
    \"\"\"Runs a tiny 0.1s dummy audio pass to verify CTranslate2 cuBLAS runtime linkage before returning.\"\"\"
    segs, _ = wm.transcribe(np.zeros(3200, dtype=np.float32), language="en", without_timestamps=True)
    list(segs)
    return wm

def get_whisper_model():
    global WHISPER_MODEL
    if WHISPER_MODEL is None:
        ensure_secondary_tools(need_whisper=True, need_demucs=False)
        _preload_cuda_runtime_libs()
        log("Loading Whisper model for word alignment / prompt transcription...")
        from faster_whisper import WhisperModel
        
        if torch.cuda.is_available():
            try:
                log("Attempting to load WhisperModel ('small') on GPU (cuda)...")
                cand = WhisperModel("small", device="cuda", compute_type="float16")
                WHISPER_MODEL = _verify_whisper_instance(cand)
                log("Whisper model ('small') loaded & verified on GPU successfully.")
                return WHISPER_MODEL
            except Exception as gpu_err:
                log(f"Warning: WhisperModel GPU runtime unavailable ({{gpu_err}}). Falling back to CPU int8...")
                
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
        ensure_secondary_tools(need_whisper=True, need_demucs=False)
        _preload_cuda_runtime_libs()
        from faster_whisper import WhisperModel
        if torch.cuda.is_available():
            for m_name in ("large-v3-turbo", "small"):
                try:
                    log(f"Loading faster-whisper '{{m_name}}' on GPU for full-length ASR...")
                    cand = WhisperModel(m_name, device="cuda", compute_type="float16")
                    ASR_WHISPER_MODEL = _verify_whisper_instance(cand)
                    log(f"faster-whisper '{{m_name}}' loaded & verified on GPU successfully.")
                    return ASR_WHISPER_MODEL
                except Exception as err:
                    log(f"Warning: Could not run faster-whisper '{{m_name}}' on GPU: {{err}}")
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
    \"\"\"Copies a mounted /kaggle/input model directory sequentially onto /dev/shm (RAM) or local SSD to avoid 20+ min gcsfuse mmap stalls.\"\"\"
    dst_root = os.path.join(FAST_SCRATCH_DIR, "omnivoice_local_weights")
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

def _attach_fast_asr_bridge(model):
    \"\"\"
    1. Binds faster-whisper onto model.transcribe so load_asr=False saves 1.62GB download & 1.6GB VRAM.
    2. Wraps create_voice_clone_prompt in torch.inference_mode() (upstream PR #283 fix preventing autograd VRAM leak/OOM).
    \"\"\"
    orig_load_asr = model.load_asr_model
    orig_transcribe = model.transcribe
    orig_create_prompt = getattr(model, "create_voice_clone_prompt", None)
    model._orig_load_asr_model = orig_load_asr
    model._asr_pipe = True

    def _fast_transcribe(audio):
        try:
            wm = get_whisper_model()
            if isinstance(audio, str):
                raw_wav, raw_sr = sf.read(audio, dtype="float32")
                audio_16k = resample_to_16k(raw_wav, orig_sr=raw_sr)
            else:
                waveform, sr = audio
                audio_16k = resample_to_16k(waveform, orig_sr=sr)
            segs, _ = wm.transcribe(audio_16k, without_timestamps=True)
            return " ".join(s.text.strip() for s in segs if s.text).strip()
        except Exception as fw_err:
            log(f"Notice: fast transcribe bridge fallback to HF ASR ({{fw_err}})")
            if not callable(model._asr_pipe):
                model._asr_pipe = None
                orig_load_asr()
            return orig_transcribe(audio)

    model.transcribe = _fast_transcribe

    if callable(orig_create_prompt):
        def _inference_mode_create_prompt(*args, **kwargs):
            with torch.inference_mode():
                return orig_create_prompt(*args, **kwargs)
        model.create_voice_clone_prompt = _inference_mode_create_prompt

    return model

VOICE_PROMPT_CACHE = {{}}

def main():
    if not PUBLIC_API_BASE_URL:
        print("ERROR: PUBLIC_API_BASE_URL is not set. Exiting.", flush=True)
        sys.exit(1)

    log(f"Starting Kaggle Turbo Worker Daemon (scratch={{FAST_SCRATCH_DIR}}). Gateway: {{PUBLIC_API_BASE_URL}}")
    
    # 1. Ensure startup registration succeeded (if early async register hasn't completed yet)
    if not _EARLY_REGISTERED:
        try:
            set_worker_state("starting", None, "OmniVoice turbo worker starting up...")
            make_request(
                "POST", 
                "/v1/internal/workers/register", 
                json={{"worker_id": WORKER_ID, "status": "starting", "message": "OmniVoice turbo worker starting up..."}}
            )
        except Exception as e:
            print(f"Failed to register startup with gateway: {{e}}", flush=True)
            sys.exit(1)

    # 2. Load OmniVoice from /dev/shm RAM disk -> GPU VRAM (~2.5s load after overlapped Xet download!)
    log("Loading OmniVoice model into GPU VRAM...")
    try:
        set_worker_state("loading_model", None, "Loading OmniVoice weights into GPU VRAM...")
        threading.Thread(
            target=lambda: make_request(
                "POST",
                "/v1/internal/workers/heartbeat",
                json={{"worker_id": WORKER_ID, "status": "loading_model", "message": "Loading OmniVoice weights into GPU VRAM..."}},
                timeout=(8, 15),
            ),
            daemon=True,
        ).start()

        model = None
        model_loaded = False

        # 1. First priority: Overlapped Xet High-Perf download in /dev/shm RAM disk -> SAFETENSORS_FAST_GPU=1 (~2.5s load!)
        try:
            t_load0 = time.time()
            dl_rc = _WEIGHTS_DL_PROC.wait()
            if dl_rc == 0 and os.path.exists(os.path.join(FAST_MODEL_DIR, "model.safetensors")):
                log(f"Loading pre-fetched OmniVoice weights from {{FAST_MODEL_DIR}}...")
                model = OmniVoice.from_pretrained(
                    FAST_MODEL_DIR,
                    device_map="cuda:0",
                    dtype=torch.float16,
                    load_asr=False,
                    local_files_only=True,
                )
            else:
                log(f"Subprocess prefetch exited {{dl_rc}}; downloading directly from Hugging Face Hub...")
                model = OmniVoice.from_pretrained(
                    "k2-fsa/OmniVoice",
                    device_map="cuda:0",
                    dtype=torch.float16,
                    load_asr=False,
                )
            log(f"OmniVoice model loaded into GPU VRAM in {{time.time() - t_load0:.2f}}s.")
            model_loaded = True
        except Exception as hf_err:
            log(f"Warning: Hugging Face Hub load failed ({{hf_err}}). Trying ModelScope fallback...")
            model_loaded = False

        # 2. Second priority: Lazy-installed ModelScope fallback onto FAST_SCRATCH_DIR
        if not model_loaded:
            try:
                _pip_or_uv_install(["modelscope"])
                from modelscope import snapshot_download
                t_ms0 = time.time()
                log("Downloading model weights via ModelScope...")
                ms_dir = snapshot_download("k2-fsa/OmniVoice", cache_dir=FAST_SCRATCH_DIR)
                model = OmniVoice.from_pretrained(
                    ms_dir,
                    device_map="cuda:0",
                    dtype=torch.float16,
                    load_asr=False,
                )
                log(f"OmniVoice model loaded via ModelScope in {{time.time() - t_ms0:.1f}}s.")
                model_loaded = True
            except Exception as ms_err:
                log(f"Warning: ModelScope fallback failed ({{ms_err}}). Checking mounted /kaggle/input...")
                model_loaded = False

        # 3. Third priority: Mounted Kaggle Dataset copied sequentially to /dev/shm
        if not model_loaded and os.path.exists("/kaggle/input"):
            mounted_dir = None
            for root, dirs, files in os.walk("/kaggle/input"):
                if "model.safetensors" in files and "config.json" in files and not root.endswith("audio_tokenizer"):
                    mounted_dir = root
                    break
            if mounted_dir:
                log(f"Copying mounted weights from {{mounted_dir}} to {{FAST_SCRATCH_DIR}}...")
                local_ssd_dir = _copy_dataset_to_local_ssd(mounted_dir)
                model = OmniVoice.from_pretrained(
                    local_ssd_dir,
                    device_map="cuda:0",
                    dtype=torch.float16,
                    load_asr=False,
                )
                log(f"OmniVoice model loaded from local copy: {{local_ssd_dir}}.")
                model_loaded = True

        if not model_loaded or model is None:
            raise RuntimeError("Could not load OmniVoice model from Hugging Face Hub, ModelScope, or local dataset.")

        if hasattr(model, "llm") and hasattr(model.llm, "config"):
            model.llm.config.use_cache = False
        _attach_fast_asr_bridge(model)

        log("OmniVoice model ready in GPU memory.")

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

    # Register as Ready IMMEDIATELY before starting any secondary background tasks!
    set_worker_state("ready", None, "OmniVoice model ready for requests.")
    make_request(
        "POST", 
        "/v1/internal/workers/register", 
        json={{"worker_id": WORKER_ID, "status": "ready", "message": "OmniVoice model ready for requests."}}
    )

    # Background install & preload secondary tools ONLY when worker is NOT busy with an active synthesis job!
    def preload_secondary_when_idle():
        try:
            time.sleep(0.5)
            ensure_secondary_tools(need_whisper=True, need_demucs=True)
            while WORKER_STATE.get("status") == "busy" and not WORKER_STATE.get("stop"):
                time.sleep(0.5)
            if not WORKER_STATE.get("stop"):
                log("Background preloading faster-whisper model during idle window...")
                get_whisper_model()
                log("Background faster-whisper model preloaded successfully.")
        except Exception as wp_err:
            log(f"Notice: secondary tools background preload deferred: {{wp_err}}")

    threading.Thread(target=preload_secondary_when_idle, daemon=True).start()

    idle_seconds = 0
    first_poll = True
    cuda_warmed = False
    log("Entering job polling loop...")
    
    while True:
        try:
            set_worker_state("idle", None, f"Worker polling. Idle time: {{idle_seconds}}s")
            if not first_poll and idle_seconds % 15 == 0:
                make_request(
                    "POST",
                    "/v1/internal/workers/heartbeat",
                    json={{"worker_id": WORKER_ID, "status": "idle", "message": f"Worker polling. Idle time: {{idle_seconds}}s"}}
                )
            first_poll = False

            # Poll for job immediately
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
                if not cuda_warmed:
                    cuda_warmed = True
                    try:
                        log("Running 1-step idle CUDA micro-warmup for instant first-token synthesis...")
                        _ = model.generate(text="Alo.", num_step=1, postprocess_output=False)
                        if torch.cuda.is_available():
                            torch.cuda.synchronize()
                        log("Idle CUDA micro-warmup completed.")
                    except Exception:
                        pass
                    continue
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
            cuda_warmed = True
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
                cached_voice_prompt = None
                ref_audio_url = job.get("ref_audio_url")
                parsed_ref = urlparse(ref_audio_url) if ref_audio_url else None
                ref_path = ""
                if parsed_ref:
                    ref_path = parsed_ref.path + (f"?{{parsed_ref.query}}" if parsed_ref.query else "")

                # Reuse cached VoiceClonePrompt (OmniVoice 0.2.1+) for saved voice samples — skips HTTP download & tokenizer encode!
                prompt_cache_key = None
                if job_type == "clone_voice" and ref_path and "/voice-samples/" in ref_path and job.get("preprocess_prompt", True) is not False:
                    prompt_cache_key = (ref_path, job.get("ref_text") or "")
                    cached_voice_prompt = VOICE_PROMPT_CACHE.get(prompt_cache_key)
                    if cached_voice_prompt is not None:
                        log(f"Reusing cached VoiceClonePrompt for {{ref_path}} (zero download/encode latency)")

                if job_type in ["clone_voice", "asr", "separate_audio", "dub_segments"] and ref_audio_url and cached_voice_prompt is None:
                    make_request(
                        "POST",
                        f"/v1/internal/jobs/{{job_id}}/status",
                        json={{"status": "preparing_input", "message": "Đang tải tệp âm thanh lên GPU Worker...", "progress": 45}}
                    )
                    
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
                        ensure_secondary_tools(need_whisper=False, need_demucs=True)
                            
                        demucs_device = "cuda" if torch.cuda.is_available() else "cpu"
                        log(f"Running Demucs (--two-stems=vocals, overlap=0.25, device={{demucs_device}}) on {{local_ref_path}}...")
                        t_sep0 = time.time()
                        demucs_cmd = [
                            sys.executable, "-m", "demucs",
                            "--two-stems=vocals",
                            "--overlap", "0.25",
                            "--clip-mode", "clamp",
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
                            # Post-clean vocals: remove DC offset & attenuate residual noise floor in silent gaps
                            try:
                                v_arr, v_sr = sf.read(vocals_path, dtype="float32")
                                v_arr = v_arr - np.mean(v_arr, axis=0, keepdims=True)
                                win = max(1, int(v_sr * 0.05))
                                mono_env = np.mean(np.abs(v_arr), axis=1) if v_arr.ndim > 1 else np.abs(v_arr)
                                kernel = np.ones(win, dtype=np.float32) / win
                                smooth_env = np.convolve(mono_env, kernel, mode="same")
                                gate = np.clip((smooth_env - 0.003) / 0.012, 0.08, 1.0).astype(np.float32)
                                if v_arr.ndim > 1:
                                    v_arr = v_arr * gate[:, None]
                                else:
                                    v_arr = v_arr * gate
                                sf.write(vocals_path, v_arr, v_sr, format="WAV", subtype="PCM_16")
                            except Exception as vc_err:
                                log(f"Notice: vocal post-gate skipped: {{vc_err}}")
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
                    
                    # Pre-compute VoiceClonePrompt ONCE:
                    # - For saved VoiceSample (/voice-samples/* or <=22s): pads digital silence + auto-transcribes exact native ref_text via large-v3-turbo if empty!
                    # - For full-video vocals.wav: selects cleanest sentence with post-utterance pause + exact orig_text!
                    voice_clone_prompt = None
                    clean_ref_clip_path = local_ref_path
                    chosen_ref_text = (job.get("ref_text") or "").strip() or None
                    job_lang = (job.get("language") or "").strip() or None
                    job_instruct = (job.get("instruct") or "").strip() or None
                    is_voice_sample_url = bool(ref_path and "/voice-samples/" in ref_path)
                    sample_cache_key = (ref_path, job_lang or "", chosen_ref_text or "") if is_voice_sample_url else None
                    if sample_cache_key and sample_cache_key in VOICE_PROMPT_CACHE:
                        voice_clone_prompt = VOICE_PROMPT_CACHE[sample_cache_key]
                        log(f"Reusing cached native VoiceClonePrompt for {{ref_path}}")

                    if voice_clone_prompt is None and local_ref_path and os.path.exists(local_ref_path):
                        try:
                            full_vox, full_sr = sf.read(local_ref_path, dtype="float32")
                            mono_vox = np.mean(full_vox, axis=1) if full_vox.ndim > 1 else full_vox
                            total_vox_sec = len(mono_vox) / float(full_sr)
                            best_slice = None
                            best_orig_txt = None

                            if is_voice_sample_url or total_vox_sec <= 22.0:
                                # Short curated VoiceSample (e.g. tuan_anh / bao_mien_nam_ro): use entire clip + digital silence padding!
                                cand_raw = mono_vox[: min(len(mono_vox), int(12.0 * full_sr))].copy()
                                fade_in_n = min(len(cand_raw) // 10, int(0.020 * full_sr))
                                fade_out_n = min(len(cand_raw) // 10, int(0.040 * full_sr))
                                if fade_in_n > 1:
                                    cand_raw[:fade_in_n] *= np.linspace(0.0, 1.0, fade_in_n, dtype=np.float32)
                                if fade_out_n > 1:
                                    cand_raw[-fade_out_n:] *= np.linspace(1.0, 0.0, fade_out_n, dtype=np.float32)
                                peak_v = float(np.max(np.abs(cand_raw)))
                                if peak_v > 1e-4:
                                    cand_raw = cand_raw * min(2.5, 0.88 / peak_v)
                                pad_pre = np.zeros(int(0.10 * full_sr), dtype=np.float32)
                                pad_post = np.zeros(int(0.30 * full_sr), dtype=np.float32)
                                best_slice = np.concatenate([pad_pre, cand_raw, pad_post])

                                # If VoiceSample in DB had empty ref_text, transcribe accurately with large-v3-turbo using job_lang ('vi')!
                                if not chosen_ref_text:
                                    try:
                                        asr_m = get_asr_whisper_model()
                                        r_segs, _ = asr_m.transcribe(
                                            resample_to_16k(cand_raw, full_sr),
                                            language=job_lang if job_lang in ("vi", "en", "ja", "ko", "zh", "fr", "de", "es") else None,
                                            beam_size=5,
                                            condition_on_previous_text=False,
                                        )
                                        tr_ref = " ".join(s.text.strip() for s in r_segs if s.text).strip()
                                        if tr_ref:
                                            if not tr_ref.endswith((".", "!", "?")):
                                                tr_ref += "."
                                            best_orig_txt = tr_ref
                                            log(f"Transcribed native VoiceSample ref_text ({{job_lang}}): '{{tr_ref[:80]}}'")
                                    except Exception as vs_tr_err:
                                        log(f"Notice: VoiceSample ASR ref_text fallback: {{vs_tr_err}}")
                            else:
                                best_score = -1.0
                                for c_idx, s_cand in enumerate(segments[:15]):
                                    if not isinstance(s_cand, dict):
                                        continue
                                    cs = float(s_cand.get("start", 0.0) or 0.0)
                                    ce = float(s_cand.get("end", cs + 4.0) or (cs + 4.0))
                                    prev_ce = float(segments[c_idx - 1].get("end", 0.0)) if (c_idx > 0 and isinstance(segments[c_idx - 1], dict)) else 0.0
                                    nxt_cs = float(segments[c_idx + 1].get("start", total_vox_sec)) if (c_idx + 1 < len(segments) and isinstance(segments[c_idx + 1], dict)) else total_vox_sec
                                    gap_before = max(0.0, cs - prev_ce)
                                    gap_after = max(0.0, nxt_cs - ce)
                                    dur_c = ce - cs
                                    if 3.2 <= dur_c <= 9.0 and gap_after >= 0.25:
                                        pre_ext = min(0.10, gap_before * 0.5)
                                        post_ext = min(0.38, max(0.18, gap_after * 0.65))
                                        i0 = max(0, int((cs - pre_ext) * full_sr))
                                        i1 = min(len(mono_vox), int((ce + post_ext) * full_sr))
                                        if i1 - i0 > int(2.8 * full_sr):
                                            cand_raw = mono_vox[i0:i1].copy()
                                            rms = float(np.sqrt(np.mean(np.square(cand_raw))))
                                            orig_t = str(s_cand.get("orig_text") or "").strip()
                                            pause_bonus = 1.35 if gap_after >= 0.50 else 1.0
                                            txt_bonus = 1.20 if (orig_t and orig_t.endswith((".", "!", "?"))) else 1.0
                                            score = rms * pause_bonus * txt_bonus
                                            if rms > 0.005 and score > best_score:
                                                best_score = score
                                                fade_in_n = min(len(cand_raw) // 8, int(0.025 * full_sr))
                                                fade_out_n = min(len(cand_raw) // 8, int(0.045 * full_sr))
                                                if fade_in_n > 1:
                                                    cand_raw[:fade_in_n] *= np.linspace(0.0, 1.0, fade_in_n, dtype=np.float32)
                                                if fade_out_n > 1:
                                                    cand_raw[-fade_out_n:] *= np.linspace(1.0, 0.0, fade_out_n, dtype=np.float32)
                                                peak_v = float(np.max(np.abs(cand_raw)))
                                                if peak_v > 1e-4:
                                                    cand_raw = cand_raw * min(2.5, 0.88 / peak_v)
                                                pad_pre = np.zeros(int(0.12 * full_sr), dtype=np.float32)
                                                pad_post = np.zeros(int(0.30 * full_sr), dtype=np.float32)
                                                best_slice = np.concatenate([pad_pre, cand_raw, pad_post])
                                                best_orig_txt = orig_t if orig_t else None

                            if best_slice is not None:
                                clean_ref_clip_path = "clean_voice_ref.wav"
                                sf.write(clean_ref_clip_path, best_slice, full_sr, format="WAV", subtype="PCM_16")
                                if best_orig_txt:
                                    chosen_ref_text = best_orig_txt
                                log(f"Prepared clean reference clip ({{len(best_slice)/full_sr:.2f}}s, ref_text={{bool(chosen_ref_text)}}) for VoiceClonePrompt.")
                        except Exception as sl_err:
                            log(f"Notice: reference preparation fallback to raw audio: {{sl_err}}")

                        if hasattr(model, "create_voice_clone_prompt"):
                            try:
                                log(f"Pre-computing reusable VoiceClonePrompt (ref_text={{chosen_ref_text[:60] if chosen_ref_text else 'auto-ASR'}})...")
                                voice_clone_prompt = model.create_voice_clone_prompt(
                                    ref_audio=clean_ref_clip_path,
                                    ref_text=chosen_ref_text,
                                )
                                if sample_cache_key is not None and voice_clone_prompt is not None:
                                    if len(VOICE_PROMPT_CACHE) >= 32:
                                        VOICE_PROMPT_CACHE.pop(next(iter(VOICE_PROMPT_CACHE)), None)
                                    VOICE_PROMPT_CACHE[sample_cache_key] = voice_clone_prompt
                                log("VoiceClonePrompt successfully pre-computed.")
                            except Exception as p_err:
                                log(f"Notice: create_voice_clone_prompt fallback to raw audio: {{p_err}}")
                                voice_clone_prompt = None
                        if clean_ref_clip_path != local_ref_path and os.path.exists(clean_ref_clip_path):
                            try:
                                os.remove(clean_ref_clip_path)
                            except Exception:
                                pass

                    def _gentle_trim_dub_wav(wav_arr, sr=24000):
                        \"\"\"Trims only dead digital silence while preserving 35ms onset pre-roll and 150ms final consonant decay.\"\"\"
                        arr = np.asarray(wav_arr, dtype=np.float32).reshape(-1)
                        if len(arr) < int(0.15 * sr):
                            return arr
                        hop = max(1, int(0.01 * sr))
                        win = max(hop, int(0.02 * sr))
                        rms_env = np.array([
                            float(np.sqrt(np.mean(np.square(arr[p:p + win]))))
                            for p in range(0, max(1, len(arr) - win + 1), hop)
                        ])
                        active = np.where(rms_env > 0.0038)[0]  # ~ -48.4 dBFS
                        if len(active) == 0:
                            return arr
                        s_idx = max(0, int(active[0] * hop - 0.035 * sr))
                        e_idx = min(len(arr), int((active[-1] + 1) * hop + 0.150 * sr))
                        out = arr[s_idx:e_idx].copy()
                        fi = min(len(out) // 6, int(0.015 * sr))
                        fo = min(len(out) // 6, int(0.028 * sr))
                        if fi > 1:
                            out[:fi] *= np.linspace(0.0, 1.0, fi, dtype=np.float32)
                        if fo > 1:
                            out[-fo:] *= np.linspace(1.0, 0.0, fo, dtype=np.float32)
                        return out

                    vi_diacritics = set("áàảãạăắằẳẵặâấầẩẫậéèẻẽẹêếềểễệíìỉĩịóòỏõọôốồổỗợúùủũụưứừửữựýỳỷỹỵđ")
                    base_speed = float(job.get("speed") or 0.92)
                    base_guidance = float(job.get("guidance_scale") or 2.0)

                    def _qa_check_dub_wav(wav_arr, exp_w_list, s_lang):
                        if len(exp_w_list) < 4:
                            return 1.0, False, ""
                        try:
                            wm_qa = get_whisper_model()
                            q_segs, _ = wm_qa.transcribe(
                                resample_to_16k(wav_arr, 24000),
                                language=s_lang if s_lang in ("vi", "en", "ja", "ko", "zh", "fr", "de", "es") else None,
                                without_timestamps=True,
                            )
                            q_txt = " ".join(s.text.strip() for s in q_segs if s.text).strip()
                            q_words = [w.strip(".,!?;:()-").lower() for w in q_txt.split() if w.strip(".,!?;:()-")]
                            oh_leak = bool(q_words and q_words[0] in ("oh", "ồ", "ờ"))
                            cov = len(q_words) / float(max(1, len(exp_w_list)))
                            return cov, oh_leak, q_txt
                        except Exception:
                            return 1.0, False, ""

                    for idx, seg in enumerate(segments):
                        seg_id = seg.get("id", idx + 1) if isinstance(seg, dict) else (idx + 1)
                        seg_text = seg.get("text", "") if isinstance(seg, dict) else str(seg)
                        start_t = float(seg.get("start", 0.0)) if isinstance(seg, dict) else 0.0
                        end_t = float(seg.get("end", start_t + 3.0)) if isinstance(seg, dict) else (start_t + 3.0)
                        target_dur = max(0.5, end_t - start_t)
                        if isinstance(seg, dict) and seg.get("gap_after") is not None:
                            gap_after = max(0.0, float(seg.get("gap_after") or 0.0))
                        elif idx + 1 < len(segments) and isinstance(segments[idx + 1], dict):
                            nxt_s = float(segments[idx + 1].get("start", end_t) or end_t)
                            gap_after = max(0.0, nxt_s - end_t)
                        else:
                            gap_after = 4.0
                        if isinstance(seg, dict) and seg.get("allowed_dur") is not None:
                            allowed_dur = max(target_dur, float(seg.get("allowed_dur") or target_dur))
                        else:
                            allowed_dur = target_dur + min(0.80, max(0.0, gap_after - 0.30) * 0.42)

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

                        is_vi_seg = (job_lang == "vi") or any(ch in vi_diacritics for ch in seg_text.lower())
                        seg_lang = job_lang or ("vi" if is_vi_seg else None)
                        if seg_text and (is_vi_seg or job.get("normalize_text", True)):
                            if is_vi_seg:
                                seg_text = re.sub(r'(\\d),(\\d)', r'\\1.\\2', seg_text)
                            seg_text = normalize_vietnamese_text(seg_text)

                        # Pre-pass Adaptive Natural Acoustic Speed: nail the target duration natively on Pass 1!
                        init_speed = base_speed
                        if is_vi_seg and seg_text:
                            try:
                                pred_dur = estimate_vietnamese_spoken_duration(seg_text, speed=base_speed, already_normalized=True)
                                if pred_dur > allowed_dur * 1.03:
                                    init_speed = min(1.16, max(base_speed, round(base_speed * (pred_dur / allowed_dur), 3)))
                                elif target_dur >= 2.6 and pred_dur > 0.4 and pred_dur < target_dur * 0.86:
                                    init_speed = max(0.84, min(base_speed, round(base_speed * (pred_dur / (target_dur * 0.93)), 3)))
                            except Exception:
                                init_speed = base_speed

                        log(f"Dubbing segment {{seg_id}} ({{idx+1}}/{{total_segs}}, lang={{seg_lang}}, speed={{init_speed:.2f}}): '{{seg_text[:75]}}' (slot={{target_dur:.2f}}s, allowed={{allowed_dur:.2f}}s)")

                        gen_kwargs = {{
                            "text": seg_text,
                            "speed": init_speed,
                            "guidance_scale": base_guidance,
                            "postprocess_output": False,
                        }}
                        if seg_lang:
                            gen_kwargs["language"] = seg_lang
                        if voice_clone_prompt is not None:
                            gen_kwargs["voice_clone_prompt"] = voice_clone_prompt
                        elif local_ref_path and os.path.exists(local_ref_path):
                            gen_kwargs["ref_audio"] = local_ref_path
                            if chosen_ref_text:
                                gen_kwargs["ref_text"] = chosen_ref_text
                        elif job_instruct:
                            gen_kwargs["instruct"] = job_instruct

                        def _run_omni_gen(kw):
                            try:
                                return model.generate(**kw)
                            except (TypeError, ImportError, Exception) as t_err:
                                err_str = str(t_err).lower()
                                if any(k in err_str for k in ["normalize_text", "wetextprocessing", "pynini", "no module named 'tn'", "module 'tn'"]):
                                    kw.pop("normalize_text", None)
                                    return model.generate(**kw)
                                if "language" in err_str and "unexpected keyword" in err_str:
                                    kw.pop("language", None)
                                    return model.generate(**kw)
                                raise

                        audio_res = _run_omni_gen(dict(gen_kwargs))
                        wav_seg = _gentle_trim_dub_wav(audio_res[0], 24000)

                        # Fast GPU Whisper QA verification: catch & retry any stochastic word-skip or prompt prefix artifact
                        exp_words = [w for w in seg_text.split() if any(c.isalnum() for c in w)]
                        cov1, oh1, qa_txt1 = _qa_check_dub_wav(wav_seg, exp_words, seg_lang)
                        if len(exp_words) >= 4 and (oh1 or cov1 < 0.70):
                            log(f"QA retry on segment {{seg_id}} (coverage={{cov1:.2f}}, oh_leak={{oh1}}, heard='{{qa_txt1[:55]}}')...")
                            retry_kw = dict(gen_kwargs)
                            retry_kw["position_temperature"] = 2.0
                            retry_kw["guidance_scale"] = 2.8
                            retry_res = _run_omni_gen(retry_kw)
                            retry_wav = _gentle_trim_dub_wav(retry_res[0], 24000)
                            cov2, oh2, qa_txt2 = _qa_check_dub_wav(retry_wav, exp_words, seg_lang)
                            if (not oh2 and oh1) or cov2 >= cov1:
                                wav_seg = retry_wav
                                cov1, oh1 = cov2, oh2
                                log(f"QA retry improved segment {{seg_id}} -> coverage={{cov2:.2f}}, heard='{{qa_txt2[:55]}}'")

                        synth_dur = len(wav_seg) / 24000.0
                        log(f"Segment {{seg_id}} synthesized: {{synth_dur:.2f}}s (slot={{target_dur:.2f}}s, allowed={{allowed_dur:.2f}}s, ratio={{synth_dur/target_dur:.2f}}x)")

                        # Closed-Loop Native Acoustic Speed Calibration (bidirectional, QA-guarded):
                        if synth_dur > max(allowed_dur + 0.28, allowed_dur * 1.12):
                            speed_val = min(1.22, max(init_speed + 0.06, round(init_speed * (synth_dur / (allowed_dur * 1.02)), 3)))
                            log(f"Acoustic speed-up retry seg {{seg_id}}: {{init_speed:.2f}} -> {{speed_val:.2f}} ({{synth_dur:.2f}}s > allowed {{allowed_dur:.2f}}s)")
                            fast_kw = dict(gen_kwargs)
                            fast_kw["speed"] = speed_val
                            fast_res = _run_omni_gen(fast_kw)
                            fast_wav = _gentle_trim_dub_wav(fast_res[0], 24000)
                            fast_dur = len(fast_wav) / 24000.0
                            fcov, foh, _ = _qa_check_dub_wav(fast_wav, exp_words, seg_lang)
                            if (not foh) and fcov >= 0.72 and fast_dur < synth_dur:
                                wav_seg = fast_wav
                                synth_dur = fast_dur
                                log(f"Acoustic speed-up accepted seg {{seg_id}} -> {{synth_dur:.2f}}s (ratio={{synth_dur/target_dur:.2f}}x)")
                        elif target_dur >= 2.8 and synth_dur < target_dur * 0.78 and init_speed > 0.85:
                            speed_val = max(0.84, min(init_speed - 0.04, round(init_speed * (synth_dur / (target_dur * 0.92)), 3)))
                            log(f"Acoustic calm-pace retry seg {{seg_id}}: {{init_speed:.2f}} -> {{speed_val:.2f}} ({{synth_dur:.2f}}s < 0.78x of {{target_dur:.2f}}s)")
                            slow_kw = dict(gen_kwargs)
                            slow_kw["speed"] = speed_val
                            slow_res = _run_omni_gen(slow_kw)
                            slow_wav = _gentle_trim_dub_wav(slow_res[0], 24000)
                            slow_dur = len(slow_wav) / 24000.0
                            scov, soh, _ = _qa_check_dub_wav(slow_wav, exp_words, seg_lang)
                            if (not soh) and scov >= 0.72 and slow_dur > synth_dur and slow_dur <= allowed_dur * 1.06:
                                wav_seg = slow_wav
                                synth_dur = slow_dur
                                log(f"Acoustic calm-pace accepted seg {{seg_id}} -> {{synth_dur:.2f}}s (ratio={{synth_dur/target_dur:.2f}}x)")

                        seg_wav_name = f"segment_{{seg_id}}.wav"
                        sf.write(seg_wav_name, wav_seg, 24000, format='WAV', subtype='PCM_16')
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
                            beam_size=5,
                            best_of=5,
                            temperature=(0.0, 0.2),
                            condition_on_previous_text=False,
                            compression_ratio_threshold=2.2,
                            log_prob_threshold=-0.8,
                            no_speech_threshold=0.55,
                            hallucination_silence_threshold=1.5,
                            word_timestamps=True,
                            vad_filter=True,
                            vad_parameters=dict(
                                threshold=0.55,
                                min_speech_duration_ms=250,
                                min_silence_duration_ms=500,
                                speech_pad_ms=200,
                            ),
                        )
                        seg_list = list(seg_iter)
                        text_parts = []
                        for seg_obj in seg_list:
                            s_txt = (seg_obj.text or "").strip()
                            if not s_txt:
                                continue
                            # Segment-level anti-hallucination filter
                            no_speech_p = float(getattr(seg_obj, "no_speech_prob", 0.0) or 0.0)
                            avg_lp = float(getattr(seg_obj, "avg_logprob", 0.0) or 0.0)
                            comp_r = float(getattr(seg_obj, "compression_ratio", 1.0) or 1.0)
                            if no_speech_p > 0.60 or avg_lp < -0.95 or comp_r > 2.4:
                                log(f"Skipping hallucinated ASR segment '{{s_txt[:40]}}' (no_speech={{no_speech_p:.2f}}, logprob={{avg_lp:.2f}})")
                                continue

                            # Verify actual audio RMS energy in segment window
                            i0 = max(0, int(float(seg_obj.start) * 16000))
                            i1 = min(len(audio_16k), int(float(seg_obj.end) * 16000))
                            if i1 > i0:
                                seg_rms = float(np.sqrt(np.mean(np.square(audio_16k[i0:i1]))))
                                if seg_rms < 0.0035:
                                    log(f"Skipping near-silent ASR segment '{{s_txt[:40]}}' (rms={{seg_rms:.5f}})")
                                    continue

                            if seg_obj.words:
                                valid_w_objs = [w for w in seg_obj.words if (w.word or "").strip()]
                                if not valid_w_objs:
                                    continue
                                mean_w_prob = sum(float(getattr(w, "probability", 1.0) or 1.0) for w in valid_w_objs) / len(valid_w_objs)
                                if mean_w_prob < 0.42 or (no_speech_p > 0.35 and mean_w_prob < 0.55):
                                    log(f"Skipping low-confidence ASR segment '{{s_txt[:40]}}' (mean_prob={{mean_w_prob:.2f}})")
                                    continue
                                kept_words_text = []
                                for wi_idx, w in enumerate(valid_w_objs):
                                    w_str = (w.word or "").strip()
                                    w_prob = float(getattr(w, "probability", 1.0) or 1.0)
                                    if w_prob < 0.18:
                                        continue
                                    s_val = round(float(w.start), 3)
                                    e_val = round(float(w.end), 3)
                                    kept_words_text.append(w_str)
                                    chunks.append({{
                                        "word": w_str,
                                        "text": w_str,
                                        "start": s_val,
                                        "end": e_val,
                                        "timestamp": [s_val, e_val],
                                        "prob": round(w_prob, 3),
                                        "seg_end": (wi_idx == len(valid_w_objs) - 1),
                                    }})
                                if kept_words_text:
                                    text_parts.append(" ".join(kept_words_text))
                            else:
                                text_parts.append(s_txt)
                                s_val = round(float(seg_obj.start), 3)
                                e_val = round(float(seg_obj.end), 3)
                                seg_words = s_txt.split()
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
                                        "seg_end": (wi == len(seg_words) - 1),
                                    }})
                        transcribed_text = " ".join(text_parts).strip()
                        log(f"faster-whisper ASR completed in {{time.time() - t_asr0:.1f}}s ({{len(chunks)}} words, lang={{getattr(asr_info, 'language', 'auto')}}).")
                    except Exception as fw_asr_err:
                        log(f"Warning: faster-whisper ASR failed ({{fw_asr_err}}). Falling back to chunked HF pipeline...")
                        if not callable(getattr(model, "_asr_pipe", None)):
                            model._asr_pipe = None
                            model._orig_load_asr_model()
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
                    if cached_voice_prompt is not None:
                        generate_args["voice_clone_prompt"] = cached_voice_prompt
                    elif prompt_cache_key is not None and local_ref_path and hasattr(model, "create_voice_clone_prompt"):
                        try:
                            cached_voice_prompt = model.create_voice_clone_prompt(
                                ref_audio=local_ref_path,
                                ref_text=job.get("ref_text"),
                            )
                            if len(VOICE_PROMPT_CACHE) >= 32:
                                VOICE_PROMPT_CACHE.pop(next(iter(VOICE_PROMPT_CACHE)), None)
                            VOICE_PROMPT_CACHE[prompt_cache_key] = cached_voice_prompt
                            generate_args["voice_clone_prompt"] = cached_voice_prompt
                        except Exception as cp_err:
                            log(f"Notice: VoiceClonePrompt cache fallback ({{cp_err}})")
                            generate_args["ref_audio"] = local_ref_path
                            if job.get("ref_text"):
                                generate_args["ref_text"] = job["ref_text"]
                    else:
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
os.environ["SAFETENSORS_FAST_GPU"] = "1"
os.environ["USE_TF"] = "0"
os.environ["USE_JAX"] = "0"
os.environ["USE_FLAX"] = "0"
os.environ["PYTHONWARNINGS"] = "ignore"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["HF_HUB_DISABLE_XET"] = "0"
os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["HF_HUB_ETAG_TIMEOUT"] = "15"
if {repr(hf_token)}:
    os.environ["HF_TOKEN"] = {repr(hf_token)}
    os.environ["HUGGING_FACE_HUB_TOKEN"] = {repr(hf_token)}
warnings.filterwarnings("ignore")

def ensure_dependencies():
    \"\"\"Dynamically installs minimal required packages inside Kaggle using uv or pip.\"\"\"
    import io, importlib.metadata, urllib.request
    fast_pkgs = "/dev/shm/omnivoice_fast_pkgs" if os.path.exists("/dev/shm") else os.path.join(tempfile.gettempdir(), "omnivoice_fast_pkgs")
    os.makedirs(fast_pkgs, exist_ok=True)
    uv_bin = shutil.which("uv")
    def _inst(pkgs):
        if not pkgs:
            return
        if uv_bin:
            subprocess.check_call([uv_bin, "pip", "install", "--system", "--no-compile", "--quiet"] + list(pkgs))
        else:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "--no-cache-dir", "--no-compile", "--prefer-binary"] + list(pkgs))
    try:
        import omnivoice
        from omnivoice.models.omnivoice import VoiceClonePrompt
    except Exception:
        try:
            raw = urllib.request.urlopen("https://codeload.github.com/k2-fsa/OmniVoice/zip/refs/heads/master", timeout=20).read()
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                for m in zf.infolist():
                    if m.filename.startswith("OmniVoice-master/omnivoice/") and not m.is_dir():
                        dst = os.path.join(fast_pkgs, m.filename[len("OmniVoice-master/"):])
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        with zf.open(m) as s, open(dst, "wb") as o:
                            shutil.copyfileobj(s, o)
            sys.path.insert(0, fast_pkgs)
        except Exception:
            _inst(["omnivoice"])
    need = []
    try:
        tf_v = importlib.metadata.version("transformers")
        if tuple(int(x) for x in tf_v.split(".")[:2]) < (5, 3):
            need.append("transformers>=5.3.0")
    except Exception:
        need.append("transformers>=5.3.0")
    try:
        import soundfile
    except ImportError:
        need.append("soundfile")
    if need:
        _inst(need)

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
