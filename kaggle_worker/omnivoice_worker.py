import os
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
os.environ["HF_TOKEN"] = ''
os.environ["HUGGING_FACE_HUB_TOKEN"] = ''
warnings.filterwarnings("ignore")

def ensure_dependencies():
    """Dynamically checks and installs required packages inside the Kaggle environment if missing or outdated."""
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
    if need_omnivoice_upgrade:
        print("Installing/Upgrading OmniVoice to latest upstream with VoiceClonePrompt...")
        try:
            subprocess.check_call([
                sys.executable, "-m", "pip", "install", "-q",
                "--no-cache-dir", "--prefer-binary",
                "git+https://github.com/k2-fsa/OmniVoice.git"
            ])
            print("OmniVoice latest upstream installed successfully.")
        except Exception as git_err:
            print(f"Notice: git install failed ({git_err}), falling back to omnivoice[tn]...")
            missing.append("omnivoice[tn]")

    if missing:
        print(f"Installing missing dependencies: {', '.join(missing)}")
        try:
            # Install packages silently with fast flags
            subprocess.check_call([
                sys.executable, "-m", "pip", "install", "-q", 
                "--no-cache-dir", "--prefer-binary", 
                "--no-warn-script-location"
            ] + missing)
            print("Dependencies installed successfully.")
        except Exception as e:
            print(f"Failed to install dependencies: {e}")
            sys.exit(1)



# Ensure dependencies are available before anything else runs
ensure_dependencies()

import torch
import soundfile as sf
from omnivoice import OmniVoice

# ==================== VIETNAMESE TEXT NORMALIZER ====================
ONES = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]

def read_two_digits(val: int, has_hundred: bool = False) -> str:
    chuc = val // 10
    donvi = val % 10
    res = []
    if chuc == 0:
        if has_hundred and donvi > 0:
            res.append("lẻ " + ONES[donvi])
        elif not has_hundred:
            res.append(ONES[donvi])
    elif chuc == 1:
        res.append("mười")
        if donvi == 1:
            res.append("một")
        elif donvi == 5:
            res.append("lăm")
        elif donvi > 0:
            res.append(ONES[donvi])
    else:
        res.append(ONES[chuc] + " mươi")
        if donvi == 1:
            res.append("mốt")
        elif donvi == 4:
            res.append("tư")
        elif donvi == 5:
            res.append("lăm")
        elif donvi > 0:
            res.append(ONES[donvi])
    return " ".join(res)

def read_three_digits(val: int, is_highest: bool = False) -> str:
    tram = val // 100
    du = val % 100
    res = []
    if tram > 0 or not is_highest:
        res.append(ONES[tram] + " trăm")
    if du > 0:
        res.append(read_two_digits(du, has_hundred=(tram > 0 or not is_highest)))
    return " ".join(res)

def read_int(n) -> str:
    try:
        n = int(n)
    except (ValueError, TypeError):
        return str(n)
    if n == 0:
        return "không"
    if n < 0:
        return "âm " + read_int(-n)
    units = ["", "nghìn", "triệu", "tỷ"]
    groups = []
    temp = n
    while temp > 0:
        groups.append(temp % 1000)
        temp //= 1000
    words = []
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if g == 0:
            continue
        is_highest = (i == len(groups) - 1)
        g_words = read_three_digits(g, is_highest=is_highest)
        unit = units[i % 4]
        ty_repeat = (i // 4)
        suffix = (" " + unit if unit else "") + (" tỷ" * ty_repeat if (i % 4 == 0 and ty_repeat > 0) else "")
        words.append(g_words + suffix)
    return " ".join(words).strip()

def read_digits_verbatim(digits_str: str) -> str:
    return " ".join(ONES[int(d)] for d in digits_str if d.isdigit())

def normalize_vietnamese_text(text: str) -> str:
    import re
    if not text or not isinstance(text, str):
        return text
    s = text.replace("\u00a0", " ")
    s = re.sub(r'\b(?:v|ver|phiên bản\s*v)\s*(\d+)\.(\d+)\b', r'phiên bản \1.\2', s, flags=re.IGNORECASE)
    s = re.sub(r'(\d+(?:[.,]\d+)?)\s*%', r'\1 phần trăm', s)
    s = re.sub(r'(\d+(?:[.,]\d+)?)\s*(?:đ|vnđ|vnd)\b', r'\1 đồng', s, flags=re.IGNORECASE)
    s = re.sub(r'\$\s*(\d+(?:[.,]\d+)?)', r'\1 đô la', s)
    s = re.sub(r'€\s*(\d+(?:[.,]\d+)?)', r'\1 ơ-rô', s)
    def repl_date(m):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"ngày {read_int(d)} tháng {read_int(mo)} năm {read_int(y)}"
    s = re.sub(r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b', repl_date, s)
    def repl_time(m):
        h = int(m.group(1))
        m_val = m.group(2)
        if m_val:
            return f"{read_int(h)} giờ {read_int(int(m_val))} phút"
        return f"{read_int(h)} giờ"
    s = re.sub(r'\b(\d{1,2})h(\d{1,2})?\b', repl_time, s, flags=re.IGNORECASE)
    s = re.sub(r'\b(\d{1,2}):(\d{2})\b', lambda m: f"{read_int(int(m.group(1)))} giờ {read_int(int(m.group(2)))} phút", s)
    def repl_thousands(m):
        full_num_str = m.group(0).replace(".", "")
        try:
            return read_int(int(full_num_str))
        except ValueError:
            return m.group(0)
    s = re.sub(r'\b\d{1,3}(?:\.\d{3})+\b', repl_thousands, s)
    def repl_decimal_comma(m):
        int_part = m.group(1)
        dec_part = m.group(2)
        return f"{read_int(int(int_part))} phẩy {read_digits_verbatim(dec_part)}"
    s = re.sub(r'\b(\d+),(\d+)\b', repl_decimal_comma, s)
    def repl_decimal_dot(m):
        int_part = m.group(1)
        dec_part = m.group(2)
        return f"{read_int(int(int_part))} chấm {read_digits_verbatim(dec_part)}"
    s = re.sub(r'\b(\d+)\.(\d+)\b', repl_decimal_dot, s)
    s = re.sub(r'\b(\d+)\s*[-–—]\s*(\d+)\b', lambda m: f"{read_int(int(m.group(1)))} đến {read_int(int(m.group(2)))}", s)
    s = re.sub(r'\b(\d+)\s*[xX]\s*(\d+)\b', lambda m: f"{read_int(int(m.group(1)))} nhân {read_int(int(m.group(2)))}", s)
    def repl_lone_int(m):
        val_str = m.group(0)
        try:
            return read_int(int(val_str))
        except ValueError:
            return val_str
    s = re.sub(r'\b\d+\b', repl_lone_int, s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s
# =====================================================================


PUBLIC_API_BASE_URL = 'https://voice.oloka.net'.rstrip("/")
WORKER_TOKEN = 'ovg_live_017cb79711c97630fe4115677a004eb5a915dd5ba3f6283b'
WORKER_ID = 'worker_1'
IDLE_TIMEOUT = 300
POLL_INTERVAL = 1

HEADERS = {
    "Authorization": f"Bearer {WORKER_TOKEN}",
    "X-Worker-Version": "2.1.0"
}

def log(msg: str):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [Worker-{WORKER_ID}] {msg}")
    sys.stdout.flush()

def make_request(method: str, path: str, **kwargs) -> requests.Response:
    url = f"{PUBLIC_API_BASE_URL}{path}"
    if "headers" in kwargs:
        kwargs["headers"].update(HEADERS)
    else:
        kwargs["headers"] = HEADERS.copy()
    return requests.request(method, url, **kwargs)

WHISPER_MODEL = None

ONES_MAP = {
    "0": "không", "1": "một", "2": "hai", "3": "ba", "4": "bốn",
    "5": "năm", "6": "sáu", "7": "bảy", "8": "tám", "9": "chín",
    "%": "phần trăm", "đ": "đồng", "$": "đô la"
}

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
    if orig_sr == 16000:
        return audio_arr.astype(np.float32)
    orig_len = len(audio_arr)
    target_len = int(orig_len * 16000 / orig_sr)
    orig_idx = np.linspace(0, orig_len, orig_len, endpoint=False)
    target_idx = np.linspace(0, orig_len, target_len, endpoint=False)
    return np.interp(target_idx, orig_idx, audio_arr).astype(np.float32)

def get_whisper_model():
    global WHISPER_MODEL
    if WHISPER_MODEL is None:
        log("Loading Whisper model for word alignment...")
        from faster_whisper import WhisperModel
        import torch
        
        # Try GPU first
        if torch.cuda.is_available():
            try:
                log("Attempting to load WhisperModel on GPU (cuda)...")
                WHISPER_MODEL = WhisperModel("small", device="cuda", compute_type="float16")
                log("Whisper model loaded on GPU successfully.")
                return WHISPER_MODEL
            except Exception as gpu_err:
                log(f"Warning: Failed to load WhisperModel on GPU: {gpu_err}. Falling back to CPU...")
                
        # Fallback to CPU
        try:
            log("Attempting to load WhisperModel on CPU...")
            WHISPER_MODEL = WhisperModel("small", device="cpu", compute_type="int8")
            log("Whisper model loaded on CPU successfully.")
        except Exception as cpu_err:
            log(f"Error: Failed to load WhisperModel on CPU: {cpu_err}")
            raise cpu_err
            
    return WHISPER_MODEL

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
        orig_w = original_words[i-1].lower().strip(".,!?\"'`”“_-;:*()[]{}<>")
        for j in range(1, m + 1):
            trans_w = transcribed_words[j-1]["word"].lower().strip(".,!?\"'`”“_-;:*()[]{}<>")
            
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

    matched_lookup = {idx: (s, e) for idx, s, e in matched_times}
    
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

def main():
    if not PUBLIC_API_BASE_URL:
        print("ERROR: PUBLIC_API_BASE_URL is not set. Exiting.")
        sys.exit(1)

    log(f"Starting Kaggle Worker Daemon. Gateway: {PUBLIC_API_BASE_URL}")
    
    # 1. Register starting
    try:
        make_request(
            "POST", 
            "/v1/internal/workers/register", 
            json={"worker_id": WORKER_ID, "status": "starting", "message": "OmniVoice worker starting up..."}
        )
    except Exception as e:
        print(f"Failed to register startup with gateway: {e}")
        sys.exit(1)

    # 2. Load OmniVoice
    log("Loading OmniVoice model into memory...")
    try:
        # Send heartbeat reporting loading_model status
        make_request(
            "POST", 
            "/v1/internal/workers/heartbeat", 
            json={"worker_id": WORKER_ID, "status": "loading_model", "message": "Loading model weights..."}
        )

        model_dir = None
        model_loaded = False
        
        # 1. First priority: Original Hugging Face Hub (direct download)
        try:
            log("Attempting to load model from Hugging Face Hub...")
            model = OmniVoice.from_pretrained(
                "k2-fsa/OmniVoice",
                device_map="cuda:0",
                dtype=torch.float16,
                load_asr=True,
            )
            log("OmniVoice model loaded successfully from Hugging Face Hub.")
            model_loaded = True
        except Exception as hf_err:
            log(f"Warning: Failed to load from Hugging Face Hub (CDN issue?): {hf_err}")
            model_loaded = False
        
        # 2. Second priority: Mounted Kaggle Dataset (Fallback)
        if not model_loaded:
            if os.path.exists("/kaggle/input"):
                for root, dirs, files in os.walk("/kaggle/input"):
                    if "model.safetensors" in files and "config.json" in files:
                        if not root.endswith("audio_tokenizer"):
                            model_dir = root
                            log(f"Found mounted model weights at: {model_dir}. Loading instantly...")
                            break
        
        # 3. Third priority: ModelScope fallback (Stable alternative source if HF is down)
        if not model_loaded and not model_dir:
            try:
                from modelscope import snapshot_download
                log("Falling back to ModelScope to download model weights...")
                model_dir = snapshot_download("k2-fsa/OmniVoice")
                log(f"Model weights loaded locally via ModelScope at: {model_dir}")
            except Exception as ms_err:
                log(f"CRITICAL: Failed to download from ModelScope: {ms_err}")
                model_dir = "k2-fsa/OmniVoice"

        # Load from model_dir if it was not loaded from Hugging Face directly
        if not model_loaded:
            # Check if tokenizer files are missing from model_dir
            tokenizer_json = os.path.join(model_dir, "tokenizer.json")
            tokenizer_config = os.path.join(model_dir, "tokenizer_config.json")
            if not os.path.exists(tokenizer_json) or not os.path.exists(tokenizer_config):
                log("Tokenizer files are missing from local directory. Merging with downloaded tokenizer files...")
                temp_model_dir = tempfile.mkdtemp()
                # Create symbolic links to all files in model_dir
                for f in os.listdir(model_dir):
                    src_file = os.path.join(model_dir, f)
                    dst_file = os.path.join(temp_model_dir, f)
                    if os.path.isfile(src_file):
                        try:
                            os.symlink(src_file, dst_file)
                        except Exception:
                            shutil.copy2(src_file, dst_file)
                # Download missing tokenizer files from Hugging Face Hub (or ModelScope fallback)
                for filename in ["tokenizer.json", "tokenizer_config.json"]:
                    dst_file = os.path.join(temp_model_dir, filename)
                    if not os.path.exists(dst_file):
                        success = False
                        # 1. Try Hugging Face first
                        try:
                            log(f"Downloading {filename} from Hugging Face...")
                            res = requests.get(f"https://huggingface.co/k2-fsa/OmniVoice/resolve/main/{filename}", timeout=30)
                            if res.status_code == 200:
                                with open(dst_file, "wb") as out_f:
                                    out_f.write(res.content)
                                success = True
                                log(f"Successfully downloaded {filename} from Hugging Face.")
                            else:
                                log(f"Warning: Failed to download {filename} from HF (status: {res.status_code})")
                        except Exception as dl_err:
                            log(f"Warning: Failed to download {filename} from HF: {dl_err}")
                        
                        # 2. Try ModelScope fallback
                        if not success:
                            try:
                                log(f"Attempting to download {filename} from ModelScope...")
                                from modelscope.hub.file_download import model_file_download
                                cache_file = model_file_download("k2-fsa/OmniVoice", file_path=filename)
                                if cache_file and os.path.exists(cache_file):
                                    shutil.copy2(cache_file, dst_file)
                                    success = True
                                    log(f"Successfully retrieved {filename} from ModelScope.")
                            except Exception as ms_dl_err:
                                log(f"Warning: Failed to download {filename} from ModelScope: {ms_dl_err}")
                                
                        if not success:
                            raise FileNotFoundError(f"Tokenizer file '{filename}' is missing and could not be downloaded from Hugging Face or ModelScope. Aborting.")
                model_dir = temp_model_dir

            model = OmniVoice.from_pretrained(
                model_dir,
                device_map="cuda:0",
                dtype=torch.float16,
                load_asr=True,
            )
            log("OmniVoice model loaded successfully from local path.")
        log("OmniVoice model loaded successfully.")

    except Exception as e:
        log(f"CRITICAL ERROR loading OmniVoice model: {e}")
        try:
            make_request(
                "POST", 
                "/v1/internal/workers/register", 
                json={"worker_id": WORKER_ID, "status": "failed", "message": f"Model load failed: {str(e)}"}
            )
        except Exception:
            pass
        sys.exit(1)

    # Register as Ready
    make_request(
        "POST", 
        "/v1/internal/workers/register", 
        json={"worker_id": WORKER_ID, "status": "ready", "message": "OmniVoice model ready for requests."}
    )

    idle_seconds = 0
    log("Entering job polling loop...")
    
    while True:
        try:
            # Send heartbeat
            make_request(
                "POST",
                "/v1/internal/workers/heartbeat",
                json={"worker_id": WORKER_ID, "status": "idle", "message": f"Worker polling. Idle time: {idle_seconds}s"}
            )

            # Poll for job
            response = make_request("GET", f"/v1/internal/jobs/next?worker_id={WORKER_ID}")
            if response.status_code == 401:
                log("Unauthorized (401). Worker token invalid. Exiting.")
                break
                
            if response.status_code != 200:
                log(f"Warning: Poll returned status code {response.status_code}")
                time.sleep(POLL_INTERVAL)
                idle_seconds += POLL_INTERVAL
                continue

            data = response.json()
            job = data.get("job")

            if not job:
                # No job available
                time.sleep(POLL_INTERVAL)
                idle_seconds += POLL_INTERVAL
                if idle_seconds >= IDLE_TIMEOUT:
                    log(f"Idle timeout of {IDLE_TIMEOUT}s reached. Initiating shutdown.")
                    make_request(
                        "POST", 
                        "/v1/internal/workers/shutdown", 
                        json={"worker_id": WORKER_ID, "reason": "idle_timeout"}
                    )
                    break
                continue

            # Reset idle counter on job receipt
            idle_seconds = 0
            job_id = job["job_id"]
            job_type = job["job_type"]
            log(f"Processing job {job_id} ({job_type})")

            # Report busy status
            make_request(
                "POST",
                "/v1/internal/workers/heartbeat",
                json={"worker_id": WORKER_ID, "status": "busy", "current_job_id": job_id, "message": f"Running {job_type}"}
            )

            # Update job status: loading_model
            make_request(
                "POST",
                f"/v1/internal/jobs/{job_id}/status",
                json={"status": "loading_model", "message": "Đang tải OmniVoice...", "progress": 30}
            )

            # Process job based on type
            try:
                local_ref_path = None
                ref_audio_url = job.get("ref_audio_url")
                
                if job_type in ["clone_voice", "asr", "separate_audio", "dub_segments"] and ref_audio_url:
                    make_request(
                        "POST",
                        f"/v1/internal/jobs/{job_id}/status",
                        json={"status": "preparing_input", "message": "Đang tải tệp âm thanh...", "progress": 45}
                    )
                    
                    # Securely download voice sample file using the parsed URL path
                    parsed_ref = urlparse(ref_audio_url)
                    ref_path = parsed_ref.path
                    if parsed_ref.query:
                        ref_path = ref_path + "?" + parsed_ref.query
                    res = make_request("GET", ref_path, stream=True)
                    if res.status_code == 200:
                        temp_fd, local_ref_path = tempfile.mkstemp(suffix=".wav")
                        os.close(temp_fd)
                        with open(local_ref_path, "wb") as f:
                            for chunk in res.iter_content(chunk_size=8192):
                                f.write(chunk)
                        log(f"Downloaded audio to {local_ref_path}")
                    else:
                        raise Exception(f"Failed to download audio from gateway: {res.status_code} - {res.text}")

                # Handle separate_audio job type directly
                if job_type == "separate_audio":
                    make_request(
                        "POST",
                        f"/v1/internal/jobs/{job_id}/status",
                        json={"status": "separating_audio", "message": "Đang chạy Demucs tách nhạc và lời...", "progress": 60}
                    )
                    
                    vocals_path = "vocals.wav"
                    bgm_path = "bgm.wav"
                    
                    try:
                        import subprocess
                        import sys
                        
                        # Try importing demucs or install
                        try:
                            import demucs
                        except ImportError:
                            log("Installing demucs dynamically...")
                            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "demucs"])
                            
                        # Run demucs
                        log(f"Running demucs on {local_ref_path}...")
                        demucs_cmd = [sys.executable, "-m", "demucs", "--two-stems=vocals", "-o", "demucs_out", local_ref_path]
                        try:
                            subprocess.run(demucs_cmd, check=True)
                        except Exception:
                            subprocess.run([
                                "demucs", "--two-stems=vocals",
                                "-o", "demucs_out",
                                local_ref_path
                            ], check=True)
                        
                        # Find the output files
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
                            log(f"Demucs audio separation successful. Vocals: {extracted_vocals}, BGM: {extracted_no_vocals}")
                        else:
                            raise Exception(f"Could not locate demucs output files (vocals={extracted_vocals}, no_vocals={extracted_no_vocals})")
                            
                    except Exception as sep_err:
                        log(f"Warning: Audio separation failed: {sep_err}. Falling back to mock separation.")
                        shutil.copy2(local_ref_path, vocals_path)
                        shutil.copy2(local_ref_path, bgm_path)

                    # Zip vocals and bgm
                    zip_path = "separation.zip"
                    with zipfile.ZipFile(zip_path, 'w') as zipf:
                        zipf.write(vocals_path, "vocals.wav")
                        zipf.write(bgm_path, "bgm.wav")
                        
                    # Clean up
                    for path_to_del in [vocals_path, bgm_path, local_ref_path]:
                        if path_to_del and os.path.exists(path_to_del):
                            os.remove(path_to_del)
                    if os.path.exists("demucs_out"):
                        shutil.rmtree("demucs_out")
                        
                    # Upload zip
                    with open(zip_path, "rb") as zip_f:
                        files = {"file": ("separation.zip", zip_f, "application/zip")}
                        upload_res = make_request(
                            "POST",
                            f"/v1/internal/jobs/{job_id}/output",
                            files=files
                        )
                        
                    if upload_res.status_code == 200:
                        log(f"Successfully uploaded job {job_id} separation output.")
                    else:
                        raise Exception(f"Failed to upload separation output: {upload_res.status_code} - {upload_res.text}")
                        
                    if os.path.exists(zip_path):
                        os.remove(zip_path)
                    continue

                # Handle dub_segments job type directly
                if job_type == "dub_segments":
                    make_request(
                        "POST",
                        f"/v1/internal/jobs/{job_id}/status",
                        json={"status": "generating_tts", "message": "Đang sinh giọng lồng tiếng từng phân đoạn...", "progress": 60}
                    )
                    
                    segments = json.loads(job["text"])
                    zip_path = "dubbed_segments.zip"
                    
                    created_files = []
                    
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
                                log(f"Notice: create_voice_clone_prompt fallback to raw audio: {p_err}")
                                voice_clone_prompt = None

                    for idx, seg in enumerate(segments):
                        seg_id = seg.get("id", idx + 1) if isinstance(seg, dict) else (idx + 1)
                        seg_text = seg.get("text", "") if isinstance(seg, dict) else str(seg)
                        start_t = float(seg.get("start", 0.0)) if isinstance(seg, dict) else 0.0
                        end_t = float(seg.get("end", start_t + 3.0)) if isinstance(seg, dict) else (start_t + 3.0)
                        target_dur = max(0.5, end_t - start_t)
                        
                        log(f"Dubbing segment {seg_id}: '{seg_text}' (target duration: {target_dur}s)")
                        if job.get("normalize_text", True) and seg_text:
                            seg_text = normalize_vietnamese_text(seg_text)
                        
                        gen_kwargs = {"text": seg_text}
                        if voice_clone_prompt is not None:
                            gen_kwargs["voice_clone_prompt"] = voice_clone_prompt
                        elif local_ref_path and os.path.exists(local_ref_path):
                            gen_kwargs["ref_audio"] = local_ref_path
                        
                        # Generate first try
                        try:
                            audio_res = model.generate(**gen_kwargs)
                        except (TypeError, ImportError, Exception) as t_err:
                            err_str = str(t_err).lower()
                            if any(k in err_str for k in ["normalize_text", "wetextprocessing", "pynini", "no module named 'tn'", "module 'tn'"]):
                                log(f"Notice: normalize_text failed ({t_err}). Retrying without text normalization...")
                                gen_kwargs.pop("normalize_text", None)
                                audio_res = model.generate(**gen_kwargs)
                            else:
                                raise
                        
                        # Check duration
                        synth_dur = len(audio_res[0]) / 24000.0
                        log(f"Segment {seg_id} synthesized duration: {synth_dur}s")
                        
                        # If too long, speed it up
                        if synth_dur > target_dur + 0.2:
                            speed_val = min(2.5, max(1.1, synth_dur / target_dur))
                            log(f"Re-generating segment {seg_id} with speed={speed_val}...")
                            gen_kwargs["speed"] = speed_val
                            try:
                                audio_res = model.generate(**gen_kwargs)
                            except (TypeError, ImportError, Exception) as t_err:
                                err_str = str(t_err).lower()
                                if any(k in err_str for k in ["normalize_text", "wetextprocessing", "pynini", "no module named 'tn'", "module 'tn'"]):
                                    log(f"Notice: normalize_text failed ({t_err}). Retrying without text normalization...")
                                    gen_kwargs.pop("normalize_text", None)
                                    audio_res = model.generate(**gen_kwargs)
                                else:
                                    raise
                        
                        seg_wav_name = f"segment_{seg_id}.wav"
                        sf.write(seg_wav_name, audio_res[0], 24000, format='WAV', subtype='PCM_16')
                        created_files.append(seg_wav_name)
                        
                    # Zip all
                    with zipfile.ZipFile(zip_path, 'w') as zipf:
                        for f_name in created_files:
                            zipf.write(f_name, f_name)
                            
                    # Clean up
                    for f_name in created_files:
                        if os.path.exists(f_name):
                            os.remove(f_name)
                    if local_ref_path and os.path.exists(local_ref_path):
                        os.remove(local_ref_path)
                        
                    # Upload
                    with open(zip_path, "rb") as zip_f:
                        files = {"file": ("dubbed_segments.zip", zip_f, "application/zip")}
                        upload_res = make_request(
                            "POST",
                            f"/v1/internal/jobs/{job_id}/output",
                            files=files
                        )
                        
                    if upload_res.status_code == 200:
                        log(f"Successfully uploaded job {job_id} dubbed segments output.")
                    else:
                        raise Exception(f"Failed to upload dubbed segments output: {upload_res.status_code} - {upload_res.text}")
                        
                    if os.path.exists(zip_path):
                        os.remove(zip_path)
                    continue

                # Handle ASR job type directly
                if job_type == "asr":
                    make_request(
                        "POST",
                        f"/v1/internal/jobs/{job_id}/status",
                        json={"status": "transcribing", "message": "Đang nhận dạng giọng nói...", "progress": 70}
                    )
                    
                    log(f"Transcribing audio {local_ref_path}...")
                    asr_res = model._asr_pipe(local_ref_path, return_timestamps="word")
                    transcribed_text = asr_res.get("text", "").strip()
                    chunks = asr_res.get("chunks", [])
                    
                    chunks_json = json.dumps(chunks)
                    
                    log(f"ASR complete. Text: {transcribed_text}")
                    
                    if local_ref_path and os.path.exists(local_ref_path):
                        os.remove(local_ref_path)
                        
                    upload_res = make_request(
                        "POST",
                        f"/v1/internal/jobs/{job_id}/asr",
                        json={"text": transcribed_text, "alignment": chunks_json}
                    )
                    if upload_res.status_code == 200:
                        log(f"Successfully uploaded job {job_id} ASR results.")
                    else:
                        raise Exception(f"Failed to upload ASR result to gateway: {upload_res.status_code} - {upload_res.text}")
                    continue

                make_request(
                    "POST",
                    f"/v1/internal/jobs/{job_id}/status",
                    json={"status": "generating_audio", "message": "Đang xử lý âm thanh...", "progress": 70}
                )

                # Build generate arguments
                job_text = job.get("text", "")
                if job.get("normalize_text", True) and job_text:
                    job_text = normalize_vietnamese_text(job_text)
                    job["text"] = job_text
                generate_args = {
                    "text": job_text
                }
                
                if job_type == "clone_voice":
                    generate_args["ref_audio"] = local_ref_path
                    if job.get("ref_text"):
                        generate_args["ref_text"] = job["ref_text"]
                elif job_type in ["voice_design_preview", "voice_design_tts"]:
                    generate_args["instruct"] = job.get("instruct")
                elif job_type != "auto_voice":
                    raise Exception(f"Unknown job type: {job_type}")
                
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

                log(f"Calling model.generate with arguments: {list(generate_args.keys())}")
                try:
                    audio_result = model.generate(**generate_args)
                except (TypeError, ImportError, Exception) as gen_err:
                    err_str = str(gen_err).lower()
                    if any(k in err_str for k in ["normalize_text", "wetextprocessing", "pynini", "no module named 'tn'", "module 'tn'"]):
                        log(f"Notice: normalize_text failed ({gen_err}). Retrying model.generate without text normalization...")
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
                    f"/v1/internal/jobs/{job_id}/status",
                    json={"status": "exporting_wav", "message": "Đang xuất WAV...", "progress": 90}
                )
                
                temp_out_fd, local_out_path = tempfile.mkstemp(suffix=".wav")
                os.close(temp_out_fd)
                
                sf.write(local_out_path, audio_result[0], 24000, format='WAV', subtype='PCM_16')
                log(f"Generated audio saved to {local_out_path}")

                # Generate word alignments
                alignment_str = None
                if job.get("with_alignment"):
                    log("Generating word alignment...")
                    alignment_list = None
                    try:
                        log("Attempting precise word alignment using faster-whisper...")
                        w_model = get_whisper_model()
                        log(f"Transcribing audio {local_out_path} with word timestamps...")
                        
                        # Dynamic language detection
                        job_text = job.get("text") or ""
                        vi_chars = set("áàảãạăắằẳẵặâấầẩẫậéèẻẽẹêếềểễệíìỉĩịóòỏõọôốồổỗộơớờởỡợúùủũụưứừửữựýỳỷỹỵđ")
                        is_vi = any(c in vi_chars for c in job_text.lower())
                        lang = "vi" if is_vi else None
                        log(f"Language detection: is_vi={is_vi}, using language_param={lang}")
                        
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
                        
                        log(f"Whisper transcribed {len(transcribed_words)} words.")
                        
                        original_words = job_text.split()
                        if original_words and transcribed_words:
                            alignment_list = align_words(original_words, transcribed_words, duration_sec)
                            if alignment_list:
                                log(f"Successfully aligned {len(alignment_list)} original words with Whisper timestamps.")
                    except Exception as whisper_err:
                        log(f"Warning: Precise alignment failed: {whisper_err}. Falling back to proportional spacing.")
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
                                clean_w = w.strip(".,!?\"'")
                                alignment_list.append(dict(
                                    word=clean_w,
                                    start=round(curr_time, 3),
                                    end=round(curr_time + word_dur, 3)
                                ))
                                curr_time += word_dur
                                
                    if alignment_list:
                        alignment_str = json.dumps(alignment_list)
                        log(f"Generated alignment data: {len(alignment_list)} words")

                # Upload output WAV along with any alignment data
                data_payload = {}
                if alignment_str:
                    data_payload["alignment"] = alignment_str

                with open(local_out_path, "rb") as out_file:
                    files = {"file": (f"{job_id}.wav", out_file, "audio/wav")}
                    upload_res = make_request(
                        "POST", 
                        f"/v1/internal/jobs/{job_id}/output", 
                        files=files,
                        data=data_payload
                    )
                    
                if upload_res.status_code == 200:
                    log(f"Successfully uploaded job {job_id} output audio.")
                else:
                    raise Exception(f"Failed to upload audio to gateway: {upload_res.status_code} - {upload_res.text}")

                if os.path.exists(local_out_path):
                    os.remove(local_out_path)

            except Exception as inner_e:
                err_str = str(inner_e)
                trace = traceback.format_exc()
                log(f"Error executing job {job_id}: {err_str}\n{trace}")
                
                make_request(
                    "POST",
                    f"/v1/internal/jobs/{job_id}/status",
                    json={
                        "status": "failed",
                        "message": "Lỗi xử lý âm thanh.",
                        "progress": 100,
                        "error_message": f"{err_str}\n{trace}"
                    }
                )

        except Exception as e:
            log(f"Network or loop error: {e}")
            time.sleep(POLL_INTERVAL)

    log("Worker execution finished.")

if __name__ == '__main__':
    main()
