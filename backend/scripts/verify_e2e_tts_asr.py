import os
import sys
import json
import base64
import requests

# Fix Windows console UTF-8 output
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.utils.vietnamese_normalizer import normalize_vietnamese_text
import torch
import soundfile as sf
from omnivoice import OmniVoice

# Read Gemini API Key from config.json
config_path = r"C:\Users\admin\.auto-video-workflow\config.json"
gemini_key = None
if os.path.exists(config_path):
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
        gemini_key = cfg.get("gemini_api_key")

if not gemini_key:
    gemini_key = os.environ.get("GEMINI_API_KEY")

print("=== VERIFYING END-TO-END TTS & GEMINI ASR ===")
test_raw_text = "Phiên bản 1.6 với 1.600 người và tỷ lệ 1,3456%"
normalized_text = normalize_vietnamese_text(test_raw_text)

print(f"Raw Input:        {test_raw_text}")
print(f"Normalized Input: {normalized_text}")

# 1. Generate audio
print("\nLoading OmniVoice model onto GPU...")
model = OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map="cuda:0", dtype=torch.float16)

out_wav_path = "test_number_results/final_verified_e2e.wav"
print(f"Synthesizing normalized text to {out_wav_path}...")
res = model.generate(text=normalized_text, num_step=32)
sf.write(out_wav_path, res[0], 24000, format='WAV', subtype='PCM_16')
print(f"Audio synthesized successfully! Duration: {len(res[0])/24000:.2f}s")

# 2. Transcribe using Gemini 2.5 Flash ASR
if gemini_key:
    print("\nSending audio to Gemini 2.5 Flash for verbatim Speech-to-Text transcription...")
    with open(out_wav_path, "rb") as af:
        audio_b64 = base64.b64encode(af.read()).decode("utf-8")

    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": "audio/wav",
                            "data": audio_b64
                        }
                    },
                    {
                        "text": "Transcribe the spoken Vietnamese audio verbatim exactly as heard. Write down the phonetic words spoken. Do not paraphrase."
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.0
        }
    }

    resp = requests.post(gemini_url, json=payload, headers={"Content-Type": "application/json"}, timeout=60)
    if resp.status_code == 200:
        data = resp.json()
        transcription = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        print(f"\n[GEMINI ASR RESULT]:")
        print(f'"{transcription}"')
        
        # Verify keywords
        trans_lower = transcription.lower()
        has_cham = "chấm" in trans_lower
        has_nghin = "nghìn" in trans_lower or "ngàn" in trans_lower
        has_phay = "phẩy" in trans_lower
        has_phantram = "phần trăm" in trans_lower or "%" in trans_lower

        print("\n--- Verification Checklist ---")
        print(f"✓ Reads '1.6' with 'chấm':       {'YES' if has_cham else 'NO'}")
        print(f"✓ Reads '1.600' with 'nghìn':     {'YES' if has_nghin else 'NO'}")
        print(f"✓ Reads '1,3456' with 'phẩy':     {'YES' if has_phay else 'NO'}")
        print(f"✓ Reads '%' with 'phần trăm':   {'YES' if has_phantram else 'NO'}")
    else:
        print(f"Gemini API Error: {resp.status_code} {resp.text}")
else:
    print("Gemini API key not found; skipping online ASR.")
