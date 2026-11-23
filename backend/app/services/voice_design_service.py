"""LLM-powered Voice Design Architect & OmniVoice Instruct Normalizer."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

import requests
from sqlalchemy.orm import Session

from app.config import settings
from app.models import LLMProfile, SystemSetting
from app.utils.slugify import slugify
from app.utils.url_security import validate_safe_external_url


# Canonical k2-fsa/OmniVoice Voice Design attributes (at most one per category)
OMNIVOICE_GENDERS = ("female", "male")
OMNIVOICE_AGES = ("child", "teenager", "young adult", "middle-aged", "elderly")
OMNIVOICE_PITCHES = (
    "very low pitch",
    "low pitch",
    "moderate pitch",
    "high pitch",
    "very high pitch",
)
OMNIVOICE_STYLES = ("whisper",)
OMNIVOICE_ENGLISH_ACCENTS = (
    "american accent",
    "british accent",
    "australian accent",
    "canadian accent",
    "indian accent",
    "chinese accent",
    "korean accent",
    "japanese accent",
    "portuguese accent",
    "russian accent",
)
OMNIVOICE_CHINESE_DIALECTS = (
    "河南话",
    "陕西话",
    "四川话",
    "贵州话",
    "云南话",
    "桂林话",
    "济南话",
    "石家庄话",
    "甘肃话",
    "宁夏话",
    "青岛话",
    "东北话",
)
OMNIVOICE_ALL_ACCENTS = OMNIVOICE_ENGLISH_ACCENTS + OMNIVOICE_CHINESE_DIALECTS

PARALINGUISTIC_TAGS = (
    "[laughter]",
    "[sigh]",
    "[surprise-ah]",
    "[surprise-oh]",
    "[surprise-wa]",
    "[surprise-yo]",
    "[question-ah]",
    "[question-oh]",
    "[question-en]",
    "[question-ei]",
    "[question-yi]",
    "[confirmation-en]",
    "[dissatisfaction-hnn]",
)

PRESET_LIBRARY_TAGS = (
    "Miền Bắc",
    "Miền Nam",
    "Miền Trung",
    "Trẻ",
    "Trung niên",
    "Cao tuổi",
    "Kể chuyện",
    "Quảng cáo",
    "Tin tức",
    "Podcast",
    "Audiobook",
)

_GENDER_SYNONYMS = {
    "female": "female",
    "woman": "female",
    "girl": "female",
    "feminine": "female",
    " nữ ": "female",
    "nữ": "female",
    "女": "female",
    "male": "male",
    "man": "male",
    "boy": "male",
    "masculine": "male",
    "nam": "male",
    "男": "male",
}

_AGE_SYNONYMS = {
    "child": "child",
    "kid": "child",
    "kids": "child",
    "children": "child",
    "儿童": "child",
    "teenager": "teenager",
    "teen": "teenager",
    "adolescent": "teenager",
    "youth": "teenager",
    "少年": "teenager",
    "young adult": "young adult",
    "young": "young adult",
    "adult": "young adult",
    "20s": "young adult",
    "30s": "young adult",
    "青年": "young adult",
    "middle-aged": "middle-aged",
    "middle aged": "middle-aged",
    "middle age": "middle-aged",
    "mature": "middle-aged",
    "40s": "middle-aged",
    "50s": "middle-aged",
    "中年": "middle-aged",
    "elderly": "elderly",
    "senior": "elderly",
    "old": "elderly",
    "aged": "elderly",
    "老年": "elderly",
}

_PITCH_SYNONYMS = {
    "very low pitch": "very low pitch",
    "very low": "very low pitch",
    "ultra low pitch": "very low pitch",
    "very deep": "very low pitch",
    "bass": "very low pitch",
    "极低音调": "very low pitch",
    "low pitch": "low pitch",
    "low": "low pitch",
    "deep": "low pitch",
    "deep voice": "low pitch",
    "baritone": "low pitch",
    "warm": "low pitch",
    "低音调": "low pitch",
    "moderate pitch": "moderate pitch",
    "medium pitch": "moderate pitch",
    "normal pitch": "moderate pitch",
    "neutral pitch": "moderate pitch",
    "balanced pitch": "moderate pitch",
    "moderate": "moderate pitch",
    "medium": "moderate pitch",
    "中音调": "moderate pitch",
    "high pitch": "high pitch",
    "high": "high pitch",
    "bright": "high pitch",
    "clear high": "high pitch",
    "soprano": "high pitch",
    "高音调": "high pitch",
    "very high pitch": "very high pitch",
    "very high": "very high pitch",
    "squeaky": "very high pitch",
    "极高音调": "very high pitch",
}

_STYLE_SYNONYMS = {
    "whisper": "whisper",
    "whispering": "whisper",
    "whispered": "whisper",
    "soft whisper": "whisper",
    "asmr": "whisper",
    "耳语": "whisper",
}

_ACCENT_SYNONYMS = {
    "american accent": "american accent",
    "american": "american accent",
    "us accent": "american accent",
    "general american": "american accent",
    "british accent": "british accent",
    "british": "british accent",
    "uk accent": "british accent",
    "english accent": "british accent",
    "rp accent": "british accent",
    "australian accent": "australian accent",
    "australian": "australian accent",
    "aussie accent": "australian accent",
    "canadian accent": "canadian accent",
    "canadian": "canadian accent",
    "indian accent": "indian accent",
    "indian": "indian accent",
    "chinese accent": "chinese accent",
    "korean accent": "korean accent",
    "japanese accent": "japanese accent",
    "portuguese accent": "portuguese accent",
    "russian accent": "russian accent",
    **{d: d for d in OMNIVOICE_CHINESE_DIALECTS},
}

_VIETNAMESE_DIACRITICS = set(
    "àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệđìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ"
)


def _clamp(val: Any, low: float, high: float, default: float, ndigits: int = 2) -> float:
    try:
        num = float(val)
    except (TypeError, ValueError):
        return default
    if num != num:  # NaN check
        return default
    return round(max(low, min(high, num)), ndigits)


def _clamp_int(val: Any, low: int, high: int, default: int) -> int:
    try:
        num = int(round(float(val)))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, num))


def _has_word(text: str, phrase: str) -> bool:
    """Matches Vietnamese/English whole word or multi-word phrase cleanly."""
    pattern = r"(?<!\w)" + re.escape(phrase.lower()) + r"(?!\w)"
    return bool(re.search(pattern, text))


class VoiceDesignService:
    """Domain service for OmniVoice Voice Design & LLM-assisted voice crafting."""

    @staticmethod
    def parse_instruct_to_attributes(instruct: Optional[str]) -> Dict[str, Optional[str]]:
        """Parses a comma-separated instruct string into canonical OmniVoice attribute slots."""
        attrs: Dict[str, Optional[str]] = {
            "gender": None,
            "age": None,
            "pitch": None,
            "style": None,
            "accent": None,
        }
        if not instruct:
            return attrs

        tokens = [t.strip() for t in re.split(r"[,;/|]+", instruct) if t.strip()]
        for raw_tok in tokens:
            tok = raw_tok.lower().strip()
            if attrs["gender"] is None and tok in _GENDER_SYNONYMS:
                attrs["gender"] = _GENDER_SYNONYMS[tok]
                continue
            if attrs["age"] is None and tok in _AGE_SYNONYMS:
                attrs["age"] = _AGE_SYNONYMS[tok]
                continue
            if attrs["pitch"] is None and tok in _PITCH_SYNONYMS:
                attrs["pitch"] = _PITCH_SYNONYMS[tok]
                continue
            if attrs["style"] is None and tok in _STYLE_SYNONYMS:
                attrs["style"] = _STYLE_SYNONYMS[tok]
                continue
            if attrs["accent"] is None:
                if raw_tok in _ACCENT_SYNONYMS:
                    attrs["accent"] = _ACCENT_SYNONYMS[raw_tok]
                    continue
                if tok in _ACCENT_SYNONYMS:
                    attrs["accent"] = _ACCENT_SYNONYMS[tok]
                    continue

        return attrs

    @staticmethod
    def normalize_attributes(raw_attrs: Optional[Dict[str, Any]]) -> Dict[str, Optional[str]]:
        """Sanitizes an attribute dict so every value strictly matches OmniVoice's vocabulary."""
        out: Dict[str, Optional[str]] = {
            "gender": None,
            "age": None,
            "pitch": None,
            "style": None,
            "accent": None,
        }
        if not isinstance(raw_attrs, dict):
            return out

        g = str(raw_attrs.get("gender") or "").strip().lower()
        if g in _GENDER_SYNONYMS:
            out["gender"] = _GENDER_SYNONYMS[g]

        a = str(raw_attrs.get("age") or "").strip().lower()
        if a in _AGE_SYNONYMS:
            out["age"] = _AGE_SYNONYMS[a]

        p = str(raw_attrs.get("pitch") or "").strip().lower()
        if p in _PITCH_SYNONYMS:
            out["pitch"] = _PITCH_SYNONYMS[p]

        s = str(raw_attrs.get("style") or "").strip().lower()
        if s in _STYLE_SYNONYMS:
            out["style"] = _STYLE_SYNONYMS[s]

        acc_raw = str(raw_attrs.get("accent") or "").strip()
        acc_low = acc_raw.lower()
        if acc_raw in _ACCENT_SYNONYMS:
            out["accent"] = _ACCENT_SYNONYMS[acc_raw]
        elif acc_low in _ACCENT_SYNONYMS:
            out["accent"] = _ACCENT_SYNONYMS[acc_low]

        return out

    @staticmethod
    def build_instruct_from_attributes(
        attrs: Dict[str, Optional[str]],
        fallback_default: bool = True,
    ) -> str:
        """Builds a canonical comma-separated OmniVoice instruct string from attributes."""
        norm = VoiceDesignService.normalize_attributes(attrs)
        ordered: List[str] = []
        for key in ("gender", "age", "pitch", "style", "accent"):
            val = norm.get(key)
            if val:
                ordered.append(val)
        if not ordered and fallback_default:
            return "female, young adult"
        return ", ".join(ordered)

    @staticmethod
    def validate_and_normalize_instruct(
        instruct: Optional[str],
        fallback_attrs: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, Dict[str, Optional[str]]]:
        """Normalizes any instruct string or attribute dict into valid OmniVoice tags."""
        parsed = VoiceDesignService.parse_instruct_to_attributes(instruct)
        if fallback_attrs:
            norm_fb = VoiceDesignService.normalize_attributes(fallback_attrs)
            for k, v in norm_fb.items():
                if parsed.get(k) is None and v is not None:
                    parsed[k] = v

        if not any(parsed.values()):
            parsed["gender"] = "female"
            parsed["age"] = "young adult"
        elif parsed["gender"] is None:
            parsed["gender"] = "female"

        built = VoiceDesignService.build_instruct_from_attributes(parsed, fallback_default=True)
        return built, parsed

    @staticmethod
    def is_canonical_english_instruct(text: str) -> bool:
        """Returns True if text is already a clean comma-separated list of valid OmniVoice tags."""
        raw = (text or "").strip()
        if not raw:
            return False
        if any(c in _VIETNAMESE_DIACRITICS for c in raw.lower()):
            return False
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if not parts or len(parts) > 5:
            return False
        valid_tokens = (
            set(_GENDER_SYNONYMS.keys())
            | set(_AGE_SYNONYMS.keys())
            | set(_PITCH_SYNONYMS.keys())
            | set(_STYLE_SYNONYMS.keys())
            | set(_ACCENT_SYNONYMS.keys())
        )
        return all(p in valid_tokens or p.lower() in valid_tokens for p in parts)

    @staticmethod
    def resolve_llm_config(db: Session, llm_profile_id: Optional[str] = None) -> Dict[str, Any]:
        """Resolves requested LLMProfile, active LLMProfile, or SystemSetting fallback."""
        selected_profile: Optional[LLMProfile] = None
        if llm_profile_id:
            selected_profile = db.query(LLMProfile).filter(LLMProfile.id == llm_profile_id).first()
        if not selected_profile:
            selected_profile = db.query(LLMProfile).filter(LLMProfile.is_active == True).first()

        def get_sys(key: str, default: str) -> str:
            entry = db.query(SystemSetting).filter(SystemSetting.key == key).first()
            if entry and entry.value and entry.value.strip():
                return entry.value.strip()
            return default

        if selected_profile:
            resolved_key = (selected_profile.api_key or "").strip()
            if not resolved_key:
                # Fallback to global system llm_api_key if profile left api_key blank
                resolved_key = get_sys("llm_api_key", settings.LLM_API_KEY)
            provider = (selected_profile.provider or "gemini").strip().lower()
            custom_ep = (selected_profile.custom_endpoint or "").strip()
            is_configured = provider != "none" and (
                bool(resolved_key) or (provider == "custom" and bool(custom_ep))
            )
            return {
                "profile_id": selected_profile.id,
                "profile_name": selected_profile.name,
                "provider": provider,
                "api_key": resolved_key,
                "model": (selected_profile.model or "gemini-2.5-flash").strip(),
                "custom_endpoint": custom_ep,
                "thinking_effort": (selected_profile.thinking_effort or "none").strip().lower(),
                "is_configured": is_configured,
            }

        provider = get_sys("llm_provider", settings.LLM_PROVIDER).strip().lower()
        api_key = get_sys("llm_api_key", settings.LLM_API_KEY).strip()
        model = get_sys("llm_model", settings.LLM_MODEL).strip() or "gemini-2.5-flash"
        custom_ep = get_sys("llm_custom_endpoint", settings.LLM_CUSTOM_ENDPOINT).strip()
        thinking_effort = get_sys("llm_thinking_effort", settings.LLM_THINKING_EFFORT).strip().lower()
        is_configured = provider != "none" and (
            bool(api_key) or (provider == "custom" and bool(custom_ep))
        )
        return {
            "profile_id": None,
            "profile_name": "Cấu hình hệ thống mặc định",
            "provider": provider,
            "api_key": api_key,
            "model": model,
            "custom_endpoint": custom_ep,
            "thinking_effort": thinking_effort or "none",
            "is_configured": is_configured,
        }

    @staticmethod
    def get_llm_status(db: Session) -> Dict[str, Any]:
        """Returns public LLM availability & profile list for Voice Design Studio."""
        cfg = VoiceDesignService.resolve_llm_config(db)
        profiles = (
            db.query(LLMProfile)
            .order_by(LLMProfile.is_active.desc(), LLMProfile.created_at.desc())
            .all()
        )
        return {
            "is_configured": bool(cfg["is_configured"]),
            "active_provider": cfg["provider"],
            "active_model": cfg["model"],
            "active_profile_id": cfg["profile_id"],
            "active_profile_name": cfg["profile_name"],
            "profiles": profiles,
        }

    @staticmethod
    def _call_llm_json_object(
        llm_config: Dict[str, Any],
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: int = 35,
    ) -> Dict[str, Any]:
        """Calls Gemini, OpenAI, or OpenAI-compatible Custom Endpoint and parses a JSON object."""
        provider = llm_config["provider"]
        api_key = llm_config["api_key"]
        model = llm_config["model"]
        custom_endpoint = llm_config["custom_endpoint"]
        thinking_effort = llm_config.get("thinking_effort", "none")

        req_timeout = (10, timeout_seconds)

        if provider == "gemini":
            if not api_key:
                raise ValueError("Chưa cấu hình Gemini API Key.")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            headers = {"Content-Type": "application/json"}
            gen_config: Dict[str, Any] = {
                "responseMimeType": "application/json",
                "temperature": 0.45,
            }
            if thinking_effort and thinking_effort != "none":
                # Keep interactive Voice Design latency snappy
                budget_map = {"low": 512, "medium": 1024, "high": 1536}
                gen_config["thinkingConfig"] = {
                    "thinkingBudget": budget_map.get(thinking_effort, 512)
                }

            payload = {
                "contents": [{"parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]}],
                "generationConfig": gen_config,
            }
            res = requests.post(url, headers=headers, json=payload, timeout=req_timeout)
            if not res.ok and res.status_code == 400 and (
                "thinkingConfig" in gen_config or "responseMimeType" in gen_config
            ):
                gen_config.pop("thinkingConfig", None)
                payload["generationConfig"] = gen_config
                res = requests.post(url, headers=headers, json=payload, timeout=req_timeout)
                if not res.ok and res.status_code == 400 and "responseMimeType" in gen_config:
                    gen_config.pop("responseMimeType", None)
                    payload["generationConfig"] = gen_config
                    res = requests.post(url, headers=headers, json=payload, timeout=req_timeout)

            if not res.ok:
                err_msg = res.text
                try:
                    err_json = res.json()
                    if "error" in err_json:
                        err_msg = err_json["error"].get("message", err_msg)
                except Exception:
                    pass
                raise RuntimeError(f"HTTP {res.status_code} (gemini): {err_msg[:220]}")

            res_data = res.json()
            parts = res_data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text_out = ""
            for part in parts:
                if part.get("thought"):
                    continue
                if "text" in part:
                    text_out += part["text"]
            if not text_out and parts:
                text_out = parts[-1].get("text", "")

        elif provider in ("openai", "custom"):
            if provider == "custom" and custom_endpoint:
                url = custom_endpoint.strip()
                if not url.endswith("/chat/completions"):
                    if url.endswith("/"):
                        url = url + "chat/completions"
                    elif "/v1" not in url:
                        url = url + "/v1/chat/completions"
                    else:
                        url = url + "/chat/completions"
                validate_safe_external_url(url)
            else:
                if not api_key:
                    raise ValueError("Chưa cấu hình OpenAI API Key.")
                url = "https://api.openai.com/v1/chat/completions"

            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.45,
            }
            if thinking_effort and thinking_effort != "none":
                payload["reasoning_effort"] = (
                    "low" if thinking_effort in ("low", "medium") else "medium"
                )

            res = requests.post(url, headers=headers, json=payload, timeout=req_timeout)
            if not res.ok and "reasoning_effort" in payload and res.status_code == 400:
                payload.pop("reasoning_effort", None)
                res = requests.post(url, headers=headers, json=payload, timeout=req_timeout)

            if not res.ok:
                err_msg = res.text
                try:
                    err_json = res.json()
                    if "error" in err_json:
                        err_msg = err_json["error"].get("message", err_msg)
                except Exception:
                    pass
                raise RuntimeError(f"HTTP {res.status_code} ({provider}): {err_msg[:220]}")

            res_data = res.json()
            msg_obj = res_data.get("choices", [{}])[0].get("message", {})
            text_out = msg_obj.get("content") or msg_obj.get("reasoning_content") or ""
        else:
            raise ValueError(f"Provider LLM không được hỗ trợ: {provider}")

        cleaned = (text_out or "").strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()

        try:
            parsed = json.loads(cleaned)
        except Exception:
            b_start = cleaned.find("{")
            b_end = cleaned.rfind("}")
            if b_start != -1 and b_end > b_start:
                parsed = json.loads(cleaned[b_start : b_end + 1])
            else:
                raise ValueError("LLM không trả về định dạng JSON hợp lệ.")

        if not isinstance(parsed, dict):
            raise ValueError("Phản hồi JSON từ LLM không phải đối tượng (object).")
        return parsed

    @staticmethod
    def detect_target_language(voice_request: str, explicit_language: Optional[str] = None) -> str:
        """Detects target synthesis language code ('vi', 'en', 'zh', 'ja', 'ko')."""
        if explicit_language and explicit_language.strip().lower() not in ("", "auto"):
            return explicit_language.strip().lower()

        low = (voice_request or "").lower()
        if any(k in low for k in ("tiếng anh", "english", "british", "american", "australian", "canadian", "giọng mỹ", "giọng anh")):
            if "tiếng việt" not in low:
                return "en"
        if any(k in low for k in ("tiếng trung", "chinese", "mandarin")) or any(d in voice_request for d in OMNIVOICE_CHINESE_DIALECTS):
            return "zh"
        if any(k in low for k in ("tiếng nhật", "japanese")):
            return "ja"
        if any(k in low for k in ("tiếng hàn", "korean")):
            return "ko"
        return "vi"

    @staticmethod
    def _build_default_preview_script(
        genre: str,
        language: str,
        include_laughter: bool = False,
        include_sigh: bool = False,
    ) -> str:
        """Returns a phonetically balanced ~6-8s script matched to genre and language."""
        if language == "en":
            scripts_en = {
                "news": "Good evening. Global technology markets surged today as next-generation voice synthesis reached a remarkable milestone in natural human expression.",
                "story": "Beneath the quiet lantern light, an old traveler opened the leather journal and began recounting the forgotten legend of the northern sea.",
                "ads": "Discover effortless clarity every single morning. Crafted with pure botanical extracts, our new formula keeps your skin hydrated all day long.",
                "podcast": "Welcome back to the late night podcast. Today we are exploring how small daily habits quietly shape the way we think and connect.",
                "whisper": "Close your eyes and take a slow, steady breath. Let all the noise of the day fade gently into the quiet night.",
                "general": "Hello there, welcome to OmniVoice Studio. Every nuance of tone, rhythm, and warmth is crafted to sound naturally human.",
            }
            txt = scripts_en.get(genre, scripts_en["general"])
            if include_laughter and "[laughter]" not in txt:
                txt = "Honestly, I did not expect that twist at all [laughter] it completely made my day brighter."
            elif include_sigh and "[sigh]" not in txt:
                txt = "After all these years of searching [sigh] it feels peaceful to finally return home."
            return txt

        if language == "zh":
            return "大家好，欢迎来到语音设计工作室，今天我们一起体验自然流畅、富有情感的智能语音合成技术。"
        if language == "ja":
            return "こんにちは。本日のプログラムでは、自然で温かみのある最新の音声合成技術について詳しくご紹介いたします。"
        if language == "ko":
            return "안녕하세요, 오늘 방송에서는 자연스럽고 따뜻한 감성을 담은 차세대 인공지능 음성 합성 기술을 소개해 드리겠습니다."

        scripts_vi = {
            "news": "Kính chào quý vị khán giả đang theo dõi bản tin tối nay. Thị trường công nghệ trong nước vừa ghi nhận nhiều bước tiến ấn tượng về trí tuệ nhân tạo.",
            "story": "Đêm dần buông xuống bên hiên nhà cổ, tiếng gió khẽ lùa qua tán lá như gợi lại câu chuyện xưa đầy kỷ niệm êm đềm một thời đã xa.",
            "ads": "Khởi đầu ngày mới tràn đầy năng lượng cùng hương vị cà phê rang mộc nguyên bản, đánh thức sự tỉnh táo và cảm hứng sáng tạo trong bạn.",
            "podcast": "Chào mừng bạn quay trở lại với góc nhỏ tâm sự cuối tuần, nơi chúng mình cùng lắng nghe và sẻ chia những câu chuyện bình dị trong cuộc sống.",
            "whisper": "Hãy khẽ nhắm mắt lại, thả lỏng đôi vai và hít một hơi thật sâu, để tâm trí bạn được nghỉ ngơi trọn vẹn trong khoảnh khắc tĩnh lặng này.",
            "general": "Xin chào, rất vui được đồng hành cùng bạn trên hệ thống OmniVoice với chất giọng tự nhiên, rõ nét và giàu cảm xúc chân thực.",
        }
        txt = scripts_vi.get(genre, scripts_vi["general"])
        if include_laughter:
            txt = "Thật sự bất ngờ quá mọi người ơi [laughter] không ngờ câu chuyện hôm nay lại thú vị và đáng yêu đến vậy."
        elif include_sigh:
            txt = "Sau một ngày dài bận rộn ngoài kia [sigh] được trở về căn phòng quen thuộc thật sự bình yên biết bao."
        return txt

    @staticmethod
    def _heuristic_assist(
        voice_request: str,
        target_language: str = "auto",
        current_preview_text: Optional[str] = None,
        preserve_preview_text: bool = False,
        fallback_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Deterministic semantic analyzer when LLM is unconfigured or unreachable."""
        raw = (voice_request or "").strip()
        req_low = raw.lower()
        lang = VoiceDesignService.detect_target_language(raw, target_language)

        # If already canonical tags, start from them
        if VoiceDesignService.is_canonical_english_instruct(raw):
            instruct, attrs = VoiceDesignService.validate_and_normalize_instruct(raw)
        else:
            attrs: Dict[str, Optional[str]] = {
                "gender": None,
                "age": None,
                "pitch": None,
                "style": None,
                "accent": None,
            }
            # Gender
            if any(_has_word(req_low, w) for w in ("nữ", "cô", "chị", "bà", "bé gái", "female", "woman", "girl")):
                attrs["gender"] = "female"
            elif any(_has_word(req_low, w) for w in ("nam", "anh", "chú", "ông", "bác", "bé trai", "male", "man", "boy")):
                attrs["gender"] = "male"

            # Age
            if any(_has_word(req_low, w) for w in ("trẻ em", "con nít", "thiếu nhi", "em bé", "bé gái", "bé trai", "child", "kid")):
                attrs["age"] = "child"
            elif any(_has_word(req_low, w) for w in ("thiếu niên", "học sinh", "teen", "teenager")):
                attrs["age"] = "teenager"
            elif any(_has_word(req_low, w) for w in ("cao tuổi", "lớn tuổi", "ông lão", "bà lão", "cụ", "già", "lão", "elderly", "senior")):
                attrs["age"] = "elderly"
            elif any(_has_word(req_low, w) for w in ("trung niên", "chững chạc", "đĩnh đạc", "chuyên gia", "middle-aged", "mature")):
                attrs["age"] = "middle-aged"
            elif any(_has_word(req_low, w) for w in ("trẻ", "thanh niên", "sinh viên", "năng động", "young", "young adult")):
                attrs["age"] = "young adult"

            # Pitch
            if any(p in req_low for p in ("rất trầm", "cực trầm", "trầm sâu", "very low", "very deep", "bass")):
                attrs["pitch"] = "very low pitch"
            elif any(p in req_low for p in ("rất cao", "cao vút", "lảnh lót", "very high")):
                attrs["pitch"] = "very high pitch"
            elif any(_has_word(req_low, w) for w in ("trầm", "ấm", "trầm ấm", "thấp", "deep", "low pitch", "warm")):
                attrs["pitch"] = "low pitch"
            elif any(_has_word(req_low, w) for w in ("cao", "sáng", "trong trẻo", "tươi sáng", "high pitch", "bright")):
                attrs["pitch"] = "high pitch"
            elif any(_has_word(req_low, w) for w in ("cân bằng", "vừa", "chuẩn mực", "moderate")):
                attrs["pitch"] = "moderate pitch"

            # Style (Whisper strictly when hushed/ASMR/whisper is meant)
            if any(p in req_low for p in ("thì thầm", "nói nhỏ", "thầm thì", "asmr", "whisper")):
                attrs["style"] = "whisper"

            # Accents (when English/foreign accent requested)
            accent_pairs = [
                (("giọng mỹ", "tiếng mỹ", "accent mỹ", "american"), "american accent"),
                (("giọng anh", "anh quốc", "accent anh", "british", "uk accent"), "british accent"),
                (("giọng úc", "australian"), "australian accent"),
                (("canada", "canadian"), "canadian accent"),
                (("giọng ấn", "indian"), "indian accent"),
                (("accent nhật", "japanese accent"), "japanese accent"),
                (("accent hàn", "korean accent"), "korean accent"),
                (("accent trung", "chinese accent"), "chinese accent"),
                (("portuguese",), "portuguese accent"),
                (("russian", "giọng nga"), "russian accent"),
            ]
            for keys, acc_val in accent_pairs:
                if any(k in req_low for k in keys):
                    attrs["accent"] = acc_val
                    break

            # Role-informed defaults when unspecified
            if attrs["gender"] is None:
                if any(k in req_low for k in ("thời sự", "tài chính", "trinh thám", "phim tài liệu")):
                    attrs["gender"] = "male"
                else:
                    attrs["gender"] = "female"
            if attrs["age"] is None:
                if any(k in req_low for k in ("thời sự", "bản tin", "tài chính", "phim tài liệu", "audiobook")):
                    attrs["age"] = "middle-aged"
                else:
                    attrs["age"] = "young adult"
            if attrs["pitch"] is None:
                if any(k in req_low for k in ("kể chuyện", "đêm khuya", "podcast", "tâm sự")):
                    attrs["pitch"] = "low pitch"
                elif any(k in req_low for k in ("quảng cáo", "review", "tiktok", "sôi nổi", "năng lượng")):
                    attrs["pitch"] = "high pitch"
                else:
                    attrs["pitch"] = "moderate pitch"

            instruct = VoiceDesignService.build_instruct_from_attributes(attrs)

        # Genre & prosody tuning
        genre = "general"
        speed = 1.0
        guidance_scale = 2.1
        position_temperature = 5.0
        class_temperature = 0.05
        t_shift = 0.10
        num_step = 32
        tags: List[str] = []

        if attrs.get("style") == "whisper":
            genre = "whisper"
            speed = 0.88
            guidance_scale = 2.4
            position_temperature = 4.0
            class_temperature = 0.0
            num_step = 36
            tags.append("Kể chuyện")
        elif any(k in req_low for k in ("quảng cáo", "review", "tiktok", "livestream", "sôi nổi", "năng lượng", "commercial", "promo")):
            genre = "ads"
            speed = 1.10
            guidance_scale = 2.5
            position_temperature = 5.8
            class_temperature = 0.15
            t_shift = 0.13
            tags.append("Quảng cáo")
        elif any(k in req_low for k in ("tin tức", "thời sự", "bản tin", "phát thanh viên", "news", "broadcast")):
            genre = "news"
            speed = 1.03
            guidance_scale = 2.8
            position_temperature = 4.5
            class_temperature = 0.0
            num_step = 36
            tags.append("Tin tức")
        elif any(k in req_low for k in ("podcast", "tâm sự", "chữa lành", "radio", "talkshow")):
            genre = "podcast"
            speed = 0.94
            guidance_scale = 2.0
            position_temperature = 4.5
            class_temperature = 0.10
            tags.append("Podcast")
        elif any(k in req_low for k in ("kể chuyện", "cổ tích", "đêm khuya", "audiobook", "truyện", "story", "narrator")):
            genre = "story"
            speed = 0.90
            guidance_scale = 2.2
            position_temperature = 4.2
            class_temperature = 0.08
            num_step = 38
            tags.extend(["Kể chuyện", "Audiobook"])

        # Region & Age tags
        if any(k in req_low for k in ("miền bắc", "hà nội", "chuẩn bắc")):
            tags.insert(0, "Miền Bắc")
        elif any(k in req_low for k in ("miền nam", "sài gòn", "nam bộ")):
            tags.insert(0, "Miền Nam")
        elif any(k in req_low for k in ("miền trung", "huế", "đà nẵng")):
            tags.insert(0, "Miền Trung")

        age_tag_map = {
            "child": "Trẻ",
            "teenager": "Trẻ",
            "young adult": "Trẻ",
            "middle-aged": "Trung niên",
            "elderly": "Cao tuổi",
        }
        mapped_age_tag = age_tag_map.get(attrs.get("age") or "")
        if mapped_age_tag and mapped_age_tag not in tags:
            tags.append(mapped_age_tag)
        if not tags:
            tags.append("Trẻ")

        has_laughter = any(k in req_low for k in ("cười", "khúc khích", "laughter", "giggle"))
        has_sigh = any(k in req_low for k in ("thở dài", "sigh"))

        if preserve_preview_text and current_preview_text and current_preview_text.strip():
            preview_text = current_preview_text.strip()
        else:
            preview_text = VoiceDesignService._build_default_preview_script(
                genre=genre,
                language=lang,
                include_laughter=has_laughter,
                include_sigh=has_sigh,
            )

        gender_vi = "Nữ" if attrs.get("gender") == "female" else "Nam"
        age_vi_map = {
            "child": "Thiếu Nhi",
            "teenager": "Thiếu Niên",
            "young adult": "Trẻ",
            "middle-aged": "Trung Niên",
            "elderly": "Cao Tuổi",
        }
        genre_vi_map = {
            "news": "Bản Tin",
            "story": "Kể Chuyện",
            "ads": "Quảng Cáo",
            "podcast": "Podcast",
            "whisper": "Thì Thầm",
            "general": "Tự Nhiên",
        }
        suggested_name = f"Giọng {gender_vi} {age_vi_map.get(attrs.get('age') or 'young adult', 'Trẻ')} - {genre_vi_map.get(genre, 'Tự Nhiên')}"
        suggested_slug = slugify(suggested_name)[:45] or "giong_thiet_ke"

        alt_pitch = "low pitch" if attrs.get("pitch") != "low pitch" else "moderate pitch"
        alt_attrs_2 = {**attrs, "pitch": alt_pitch, "style": None}
        alt_instruct_2 = VoiceDesignService.build_instruct_from_attributes(alt_attrs_2)

        alt_age = "middle-aged" if attrs.get("age") != "middle-aged" else "young adult"
        alt_attrs_3 = {**attrs, "age": alt_age, "pitch": "low pitch"}
        alt_instruct_3 = VoiceDesignService.build_instruct_from_attributes(alt_attrs_3)

        variants = [
            {
                "label": "Chuẩn mô tả (Khuyên dùng)",
                "summary": f"Khớp trực tiếp yêu cầu với nhịp độ {speed:.2f}x",
                "instruct": instruct,
                "attributes": attrs,
                "speed": speed,
                "guidance_scale": guidance_scale,
                "position_temperature": position_temperature,
                "class_temperature": class_temperature,
                "preview_text": preview_text,
            },
            {
                "label": "Tự nhiên & Gần gũi",
                "summary": "Âm sắc cân bằng, nhịp nói đời thường dễ nghe",
                "instruct": alt_instruct_2,
                "attributes": alt_attrs_2,
                "speed": 1.0,
                "guidance_scale": 2.0,
                "position_temperature": 5.0,
                "class_temperature": 0.1,
                "preview_text": VoiceDesignService._build_default_preview_script("podcast", lang),
            },
            {
                "label": "Trầm ấm & Chững chạc",
                "summary": "Giọng dày, độ ổn định cao cho thuyết minh dài",
                "instruct": alt_instruct_3,
                "attributes": alt_attrs_3,
                "speed": 0.94,
                "guidance_scale": 2.5,
                "position_temperature": 4.2,
                "class_temperature": 0.0,
                "preview_text": VoiceDesignService._build_default_preview_script("story", lang),
            },
        ]

        explanation = (
            f"Đã phân tích đặc trưng thành tổ hợp chuẩn OmniVoice ({instruct}) "
            f"kết hợp tốc độ {speed:.2f}x và Guidance {guidance_scale:.1f} phù hợp phong cách {genre_vi_map.get(genre, 'tự nhiên').lower()}."
        )
        if fallback_reason:
            explanation += f" (Chế độ phân tích cục bộ: {fallback_reason})"

        return {
            "source": "heuristic_fallback",
            "llm_model": None,
            "profile_name": None,
            "instruct": instruct,
            "attributes": attrs,
            "recommended_params": {
                "speed": speed,
                "guidance_scale": guidance_scale,
                "position_temperature": position_temperature,
                "class_temperature": class_temperature,
                "t_shift": t_shift,
                "num_step": num_step,
                "denoise": True,
                "language": lang,
            },
            "preview_text": preview_text,
            "suggested_name": suggested_name,
            "suggested_slug": suggested_slug,
            "suggested_tags": tags[:5],
            "explanation": explanation,
            "variants": variants,
        }

    @staticmethod
    def assist_voice_design(
        db: Session,
        voice_request: str,
        target_language: str = "auto",
        current_preview_text: Optional[str] = None,
        preserve_preview_text: bool = False,
        llm_profile_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Uses the configured LLM to design an OmniVoice persona:
        - Maps free-form Vietnamese/English prompt into strict OmniVoice instruct attributes
        - Tunes acoustic/prosody parameters (speed, guidance_scale, temperatures, t_shift, num_step)
        - Crafts an optimal 6-8s preview_text for Voice Clone reference creation
        - Suggests library name, slug, tags, and 3 auditionable persona variants
        Falls back cleanly to _heuristic_assist if LLM is unconfigured or errors.
        """
        clean_req = (voice_request or "").strip()
        if not clean_req:
            raise ValueError("Vui lòng nhập mô tả ý tưởng giọng nói.")

        llm_cfg = VoiceDesignService.resolve_llm_config(db, llm_profile_id=llm_profile_id)
        detected_lang = VoiceDesignService.detect_target_language(clean_req, target_language)

        if not llm_cfg["is_configured"]:
            return VoiceDesignService._heuristic_assist(
                clean_req,
                target_language=detected_lang,
                current_preview_text=current_preview_text,
                preserve_preview_text=preserve_preview_text,
                fallback_reason="Chưa cấu hình khóa API cho LLM",
            )

        system_prompt = (
            "You are an expert Speech Synthesis & Voice Casting Director for the k2-fsa/OmniVoice engine.\n"
            "Your job is to translate a user's natural-language voice description (often in Vietnamese or English) "
            "into STRICT, valid OmniVoice speaker attributes, calibrated acoustic synthesis parameters, "
            "an expressive 6-to-8 second audition script (ideal for 8s Voice Cloning reference audio), "
            "metadata for the Voice Library, and 3 distinct creative persona variants.\n\n"
            "STRICT OMNIVOICE ATTRIBUTE RULES (select AT MOST ONE value per category; null if not applicable):\n"
            "1. gender: 'female' | 'male'\n"
            "2. age: 'child' | 'teenager' | 'young adult' | 'middle-aged' | 'elderly'\n"
            "3. pitch: 'very low pitch' | 'low pitch' | 'moderate pitch' | 'high pitch' | 'very high pitch'\n"
            "4. style: 'whisper' | null  (CRITICAL: ONLY set 'whisper' when user explicitly wants whispering, hushed voice, or ASMR. "
            "Do NOT set 'whisper' for merely gentle/calm/warm/soft voices—use low/moderate pitch and slower speed instead!)\n"
            "5. accent: null OR (ONLY if target language is 'en': 'american accent' | 'british accent' | 'australian accent' | "
            "'canadian accent' | 'indian accent' | 'chinese accent' | 'korean accent' | 'japanese accent' | 'portuguese accent' | 'russian accent'; "
            "ONLY if target language is 'zh': '河南话' | '陕西话' | '四川话' | '贵州话' | '云南话' | '桂林话' | '济南话' | '石家庄话' | '甘肃话' | '宁夏话' | '青岛话' | '东北话'). "
            "For Vietnamese ('vi'), accent MUST be null.\n\n"
            "ACOUSTIC PARAMETER CALIBRATION GUIDELINES:\n"
            "- speed (0.75 to 1.35): 0.88-0.94 for bedtime/storytelling/calm podcast; 1.00-1.04 for news/documentary; 1.08-1.16 for energetic ads/TikTok.\n"
            "- guidance_scale (1.5 to 3.5): 1.9-2.2 for relaxed conversational flow; 2.4-2.9 for crisp broadcast articulation & strong attribute adherence.\n"
            "- position_temperature (3.0 to 7.0): 3.8-4.5 for steady controlled cadence; 5.2-6.2 for lively expressive rhythm.\n"
            "- class_temperature (0.0 to 0.4): 0.0 for clean studio stability; 0.1-0.25 for emotional warmth/acting.\n"
            "- t_shift (0.08 to 0.18): 0.10 standard; 0.12-0.15 for punchier commercial presence.\n"
            "- num_step (28 to 44): 32-40 for studio-grade reference sample generation.\n\n"
            "PREVIEW SCRIPT GUIDELINES:\n"
            "- Must be written in the target synthesis language ('vi' = natural, idiomatic Vietnamese; 'en' = English; etc.).\n"
            "- Length MUST be ~22 to 34 Vietnamese words (or ~18 to 28 English words) so spoken audio is ~6-8 seconds—the exact sweet spot for OmniVoice VoiceClonePrompt.\n"
            "- Content must vividly match the persona/scenario (e.g., news anchor reads realistic news; storyteller reads atmospheric narrative; reviewer speaks naturally).\n"
            "- Optional inline paralinguistic tag (AT MOST ONE, ONLY if user explicitly asked for laughter/sigh/surprise): "
            "[laughter], [sigh], [surprise-ah], [surprise-oh], [question-ah], [confirmation-en].\n\n"
            "OUTPUT STRICT JSON OBJECT ONLY matching this exact schema:\n"
            "{\n"
            '  "attributes": {"gender": "...", "age": "...", "pitch": "...", "style": null, "accent": null},\n'
            '  "recommended_params": {\n'
            '    "speed": 1.0, "guidance_scale": 2.2, "position_temperature": 5.0,\n'
            '    "class_temperature": 0.1, "t_shift": 0.1, "num_step": 32, "denoise": true, "language": "vi"\n'
            "  },\n"
            '  "preview_text": "...",\n'
            '  "suggested_name": "Short Vietnamese title (<=45 chars)",\n'
            '  "suggested_tags": ["Tag1", "Tag2", "Tag3"],\n'
            '  "explanation": "Concise 1-2 sentence Vietnamese audio-director rationale explaining attribute & parameter choices.",\n'
            '  "variants": [\n'
            "    {\n"
            '      "label": "Short Vietnamese variant name (e.g. Trầm ấm kể chuyện)",\n'
            '      "summary": "Short Vietnamese 6-12 word description",\n'
            '      "attributes": {"gender": "...", "age": "...", "pitch": "...", "style": null, "accent": null},\n'
            '      "speed": 0.92, "guidance_scale": 2.2, "position_temperature": 4.4, "class_temperature": 0.08,\n'
            '      "preview_text": "..."\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Include exactly 3 items in 'variants' (Variant 1 = best match to prompt; Variants 2 & 3 = distinct nuanced takes). "
            "Prefer preset Vietnamese tags where relevant: Miền Bắc, Miền Nam, Miền Trung, Trẻ, Trung niên, Cao tuổi, Kể chuyện, Quảng cáo, Tin tức, Podcast, Audiobook."
        )

        user_prompt = (
            f"User voice request: {json.dumps(clean_req, ensure_ascii=False)}\n"
            f"Target synthesis language: {detected_lang}\n"
            f"Preserve existing preview text: {preserve_preview_text}\n"
            f"Current preview text: {json.dumps(current_preview_text or '', ensure_ascii=False)}"
        )

        try:
            raw_json = VoiceDesignService._call_llm_json_object(
                llm_cfg,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                timeout_seconds=35,
            )

            instruct, norm_attrs = VoiceDesignService.validate_and_normalize_instruct(
                raw_json.get("instruct"),
                fallback_attrs=raw_json.get("attributes"),
            )
            # Enforce OmniVoice language rule: English accents don't apply to pure Vietnamese speech
            raw_params = raw_json.get("recommended_params") or {}
            out_lang = str(raw_params.get("language") or detected_lang or "vi").strip().lower()
            if out_lang not in ("vi", "en", "zh", "ja", "ko", "fr", "de", "es"):
                out_lang = detected_lang
            if out_lang == "vi" and norm_attrs.get("accent") in OMNIVOICE_ENGLISH_ACCENTS:
                norm_attrs["accent"] = None
                instruct = VoiceDesignService.build_instruct_from_attributes(norm_attrs)

            rec_params = {
                "speed": _clamp(raw_params.get("speed"), 0.5, 2.0, 1.0, 2),
                "guidance_scale": _clamp(raw_params.get("guidance_scale"), 0.5, 5.0, 2.2, 1),
                "position_temperature": _clamp(raw_params.get("position_temperature"), 0.0, 10.0, 5.0, 1),
                "class_temperature": _clamp(raw_params.get("class_temperature"), 0.0, 5.0, 0.0, 2),
                "t_shift": _clamp(raw_params.get("t_shift"), 0.01, 0.50, 0.10, 2),
                "num_step": _clamp_int(raw_params.get("num_step"), 10, 64, 32),
                "denoise": bool(raw_params.get("denoise", True)),
                "language": out_lang,
            }

            if preserve_preview_text and current_preview_text and current_preview_text.strip():
                final_preview_text = current_preview_text.strip()
            else:
                llm_text = str(raw_json.get("preview_text") or "").strip()
                final_preview_text = llm_text or VoiceDesignService._build_default_preview_script(
                    "general", out_lang
                )

            suggested_name = str(raw_json.get("suggested_name") or "").strip()[:80]
            if not suggested_name:
                suggested_name = f"Giọng AI - {clean_req[:35]}"
            suggested_slug = slugify(suggested_name)[:45] or "giong_thiet_ke_ai"

            raw_tags = raw_json.get("suggested_tags")
            clean_tags: List[str] = []
            if isinstance(raw_tags, list):
                for t in raw_tags:
                    ts = str(t or "").strip()
                    if ts and ts not in clean_tags and len(ts) <= 30:
                        clean_tags.append(ts)
            if not clean_tags:
                clean_tags = ["Trẻ"]

            explanation = str(raw_json.get("explanation") or "").strip() or (
                f"Đã tối ưu tổ hợp thuộc tính OmniVoice ({instruct}) bằng mô hình {llm_cfg['model']}."
            )

            variants_out: List[Dict[str, Any]] = []
            raw_variants = raw_json.get("variants")
            if isinstance(raw_variants, list):
                for idx, v in enumerate(raw_variants[:4]):
                    if not isinstance(v, dict):
                        continue
                    v_inst, v_attrs = VoiceDesignService.validate_and_normalize_instruct(
                        v.get("instruct"),
                        fallback_attrs=v.get("attributes") or norm_attrs,
                    )
                    if out_lang == "vi" and v_attrs.get("accent") in OMNIVOICE_ENGLISH_ACCENTS:
                        v_attrs["accent"] = None
                        v_inst = VoiceDesignService.build_instruct_from_attributes(v_attrs)
                    variants_out.append(
                        {
                            "label": str(v.get("label") or f"Phương án {idx + 1}").strip()[:50],
                            "summary": str(v.get("summary") or v_inst).strip()[:120],
                            "instruct": v_inst,
                            "attributes": v_attrs,
                            "speed": _clamp(v.get("speed"), 0.5, 2.0, rec_params["speed"], 2),
                            "guidance_scale": _clamp(
                                v.get("guidance_scale"), 0.5, 5.0, rec_params["guidance_scale"], 1
                            ),
                            "position_temperature": _clamp(
                                v.get("position_temperature"),
                                0.0,
                                10.0,
                                rec_params["position_temperature"],
                                1,
                            ),
                            "class_temperature": _clamp(
                                v.get("class_temperature"),
                                0.0,
                                5.0,
                                rec_params["class_temperature"],
                                2,
                            ),
                            "preview_text": str(v.get("preview_text") or final_preview_text).strip(),
                        }
                    )

            if not variants_out:
                variants_out.append(
                    {
                        "label": "Phương án chính",
                        "summary": explanation[:90],
                        "instruct": instruct,
                        "attributes": norm_attrs,
                        "speed": rec_params["speed"],
                        "guidance_scale": rec_params["guidance_scale"],
                        "position_temperature": rec_params["position_temperature"],
                        "class_temperature": rec_params["class_temperature"],
                        "preview_text": final_preview_text,
                    }
                )

            return {
                "source": "llm",
                "llm_model": llm_cfg["model"],
                "profile_name": llm_cfg["profile_name"],
                "instruct": instruct,
                "attributes": norm_attrs,
                "recommended_params": rec_params,
                "preview_text": final_preview_text,
                "suggested_name": suggested_name,
                "suggested_slug": suggested_slug,
                "suggested_tags": clean_tags[:6],
                "explanation": explanation,
                "variants": variants_out,
            }
        except Exception as exc:
            print(f"[VoiceDesignService] LLM assist fallback ({llm_cfg['profile_name']}): {exc}")
            return VoiceDesignService._heuristic_assist(
                clean_req,
                target_language=detected_lang,
                current_preview_text=current_preview_text,
                preserve_preview_text=preserve_preview_text,
                fallback_reason=str(exc)[:120],
            )

    @staticmethod
    def generate_preview_script(
        db: Session,
        voice_request: str = "",
        instruct: str = "",
        style_or_topic: str = "Kể chuyện tự nhiên",
        language: str = "vi",
        include_paralinguistic: bool = False,
        llm_profile_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generates a fresh ~6-8s audition script tailored to persona, topic, and language."""
        lang = VoiceDesignService.detect_target_language(
            f"{voice_request} {style_or_topic}",
            explicit_language=language,
        )
        llm_cfg = VoiceDesignService.resolve_llm_config(db, llm_profile_id=llm_profile_id)

        topic_low = (style_or_topic or "").lower()
        genre = "general"
        if any(k in topic_low for k in ("tin tức", "thời sự", "tài chính", "news")):
            genre = "news"
        elif any(k in topic_low for k in ("quảng cáo", "review", "sale", "ads")):
            genre = "ads"
        elif any(k in topic_low for k in ("podcast", "tâm sự", "radio")):
            genre = "podcast"
        elif any(k in topic_low for k in ("thì thầm", "asmr", "whisper")):
            genre = "whisper"
        elif any(k in topic_low for k in ("kể chuyện", "truyện", "cổ tích", "audiobook", "story")):
            genre = "story"

        if not llm_cfg["is_configured"]:
            script = VoiceDesignService._build_default_preview_script(
                genre=genre,
                language=lang,
                include_laughter=include_paralinguistic and "cười" in topic_low,
                include_sigh=include_paralinguistic and "thở dài" in topic_low,
            )
            return {
                "source": "heuristic_fallback",
                "preview_text": script,
                "language": lang,
                "estimated_duration": VoiceDesignService.estimate_spoken_duration(script, lang),
                "llm_model": None,
            }

        para_rule = (
            "Include ONE natural inline paralinguistic cue such as [laughter], [sigh], [surprise-ah], or [question-ah] where emotionally fitting."
            if include_paralinguistic
            else "Do NOT include bracketed tags unless explicitly requested in the topic."
        )
        lang_label = {
            "vi": "Vietnamese (natural, idiomatic, rich tonal flow, no robotic phrasing)",
            "en": "English",
            "zh": "Simplified Chinese",
            "ja": "Japanese",
            "ko": "Korean",
        }.get(lang, "Vietnamese")

        system_prompt = (
            "You write studio voice-audition scripts for OmniVoice TTS & Voice Cloning.\n"
            f"Target language: {lang_label}.\n"
            "Write a single cohesive 1-2 sentence audition script that takes ~6 to 8 seconds to speak aloud "
            "(22-34 words for Vietnamese, 18-28 words for English).\n"
            f"{para_rule}\n"
            'Return STRICT JSON ONLY: {"preview_text": "..."}'
        )
        user_prompt = (
            f"Voice persona: {voice_request or instruct or 'Natural expressive speaker'}\n"
            f"OmniVoice instruct tags: {instruct or 'auto'}\n"
            f"Topic / Style: {style_or_topic}"
        )

        try:
            res = VoiceDesignService._call_llm_json_object(
                llm_cfg,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                timeout_seconds=25,
            )
            txt = str(res.get("preview_text") or "").strip().strip('"')
            if not txt:
                raise ValueError("Empty script from LLM")
            return {
                "source": "llm",
                "preview_text": txt,
                "language": lang,
                "estimated_duration": VoiceDesignService.estimate_spoken_duration(txt, lang),
                "llm_model": llm_cfg["model"],
            }
        except Exception as exc:
            print(f"[VoiceDesignService] Script generation fallback: {exc}")
            script = VoiceDesignService._build_default_preview_script(
                genre=genre,
                language=lang,
                include_laughter=include_paralinguistic,
            )
            return {
                "source": "heuristic_fallback",
                "preview_text": script,
                "language": lang,
                "estimated_duration": VoiceDesignService.estimate_spoken_duration(script, lang),
                "llm_model": None,
            }

    @staticmethod
    def resolve_instruct_fast(
        db: Session,
        voice_request: str,
        explicit_instruct: Optional[str] = None,
        use_llm: bool = True,
        llm_profile_id: Optional[str] = None,
        fallback_mapper=None,
    ) -> str:
        """
        Resolves the final OmniVoice instruct string when creating a preview or TTS job.
        1. If explicit_instruct is given, normalizes and validates it directly.
        2. If voice_request is already canonical OmniVoice tags, normalizes it directly.
        3. If use_llm=True, APP_ENV != 'testing', and LLM is configured, asks LLM for canonical attributes (fast timeout).
        4. Otherwise falls back to deterministic Vietnamese/English mapper.
        """
        if explicit_instruct and explicit_instruct.strip():
            if VoiceDesignService.is_canonical_english_instruct(explicit_instruct):
                inst, _ = VoiceDesignService.validate_and_normalize_instruct(explicit_instruct)
                return inst
            # If user typed natural Vietnamese into explicit_instruct, continue resolving it
            voice_request = explicit_instruct.strip()

        clean_req = (voice_request or "").strip()
        if not clean_req:
            return "female, young adult"

        if VoiceDesignService.is_canonical_english_instruct(clean_req):
            inst, _ = VoiceDesignService.validate_and_normalize_instruct(clean_req)
            return inst

        if use_llm and (settings.APP_ENV or "").lower() != "testing":
            llm_cfg = VoiceDesignService.resolve_llm_config(db, llm_profile_id=llm_profile_id)
            if llm_cfg["is_configured"]:
                lang = VoiceDesignService.detect_target_language(clean_req)
                sys_prompt = (
                    "Map the user's voice description to k2-fsa/OmniVoice speaker attributes.\n"
                    "Allowed values (at most one per key, or null):\n"
                    "- gender: 'female' | 'male'\n"
                    "- age: 'child' | 'teenager' | 'young adult' | 'middle-aged' | 'elderly'\n"
                    "- pitch: 'very low pitch' | 'low pitch' | 'moderate pitch' | 'high pitch' | 'very high pitch'\n"
                    "- style: 'whisper' | null (ONLY for whispering/hushed/ASMR, NOT merely gentle/calm)\n"
                    "- accent: null (or valid OmniVoice English accent ONLY if target language is English)\n"
                    'Return JSON ONLY: {"attributes": {"gender": "...", "age": "...", "pitch": "...", "style": null, "accent": null}}'
                )
                try:
                    data = VoiceDesignService._call_llm_json_object(
                        llm_cfg,
                        system_prompt=sys_prompt,
                        user_prompt=f"Description: {clean_req}\nTarget language: {lang}",
                        timeout_seconds=12,
                    )
                    inst, attrs = VoiceDesignService.validate_and_normalize_instruct(
                        data.get("instruct"),
                        fallback_attrs=data.get("attributes"),
                    )
                    if lang == "vi" and attrs.get("accent") in OMNIVOICE_ENGLISH_ACCENTS:
                        attrs["accent"] = None
                        inst = VoiceDesignService.build_instruct_from_attributes(attrs)
                    return inst
                except Exception as exc:
                    print(f"[VoiceDesignService] Fast instruct LLM fallback: {exc}")

        if callable(fallback_mapper):
            mapped = fallback_mapper(clean_req)
            inst, _ = VoiceDesignService.validate_and_normalize_instruct(mapped)
            return inst

        heur = VoiceDesignService._heuristic_assist(clean_req)
        return heur["instruct"]

    @staticmethod
    def estimate_spoken_duration(text: str, language: str = "vi", speed: float = 1.0) -> float:
        """Estimates spoken audio duration in seconds for UI & script calibration."""
        cleaned = re.sub(r"\[[^\[\]]{1,40}\]", " ", text or "").strip()
        if not cleaned:
            return 0.0
        eff_speed = max(0.5, min(2.0, float(speed or 1.0)))
        if language in ("zh", "ja", "ko"):
            chars = len(re.sub(r"\s+", "", cleaned))
            return round((chars / 4.2) / eff_speed, 1)
        words = len(cleaned.split())
        rate = 3.9 if language == "vi" else 2.7
        return round((words / rate) / eff_speed, 1)
