import json
import os
import secrets
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


_WEAK_SECRET_KEYS = {
    "",
    "super_secret_omnivoice_gateway_key_9988",
    "changeme",
    "secret",
}

_WEAK_WORKER_TOKENS = {
    "",
    "default_secure_worker_token_12345",
    "changeme",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_ENV: str = "production"
    WORKER_MODE: str = "kaggle"  # "mock" or "kaggle"
    SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 24

    DATABASE_URL: str = "sqlite:///./storage/omnivoice_gateway.db"
    STORAGE_DIR: str = "./storage"
    ALLOW_REGISTRATION: bool = True

    PUBLIC_API_BASE_URL: str = ""
    API_BASE_URL: str = ""
    FRONTEND_ORIGINS: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "https://omnivoice-gateway.pages.dev,https://voice.oloka.net,"
        "https://phucsd-oloka-voice.hf.space,https://phucsd-oloka-voice-studio.hf.space"
    )

    WORKER_TOKEN: str = ""
    WORKER_IDLE_TIMEOUT_SECONDS: int = 300
    WORKER_POLL_INTERVAL_SECONDS: int = 1

    KAGGLE_USERNAME: str = ""
    KAGGLE_KEY: str = ""
    KAGGLE_KERNEL_REF: str = ""
    KAGGLE_KERNEL_SLUG: str = "omnivoice-worker"
    KAGGLE_KERNEL_TITLE: str = "OmniVoice Worker"
    KAGGLE_ACCELERATOR: str = "NvidiaTeslaT4"
    KAGGLE_TIMEOUT_SECONDS: int = 3600
    KAGGLE_WORKER_DIR: str = "../kaggle_worker"

    HF_TOKEN: str = ""
    ENABLE_HF_AUDIO_CDN: bool = False

    # LLM Settings
    LLM_PROVIDER: str = "gemini"  # "gemini" or "openai" or "none"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "gemini-2.5-flash"
    LLM_CUSTOM_ENDPOINT: str = ""
    LLM_THINKING_EFFORT: str = "none"  # "none", "low", "medium", "high"

    # SMTP Settings
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "OmniVoice <no-reply@omnivoice.local>"

    # OAuth Settings (must be supplied via environment / Secrets)
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GITHUB_CLIENT_ID: str = ""
    GITHUB_CLIENT_SECRET: str = ""

    # Tailscale Settings (must be supplied via environment / Secrets)
    TAILSCALE_AUTHKEY: str = ""

    LOG_LEVEL: str = "info"

    @property
    def cors_origins(self) -> List[str]:
        if not self.FRONTEND_ORIGINS:
            return []
        return [o.strip() for o in self.FRONTEND_ORIGINS.split(",") if o.strip()]

    @property
    def uploads_dir(self) -> str:
        return os.path.join(self.STORAGE_DIR, "uploads")

    @property
    def voice_samples_dir(self) -> str:
        return os.path.join(self.STORAGE_DIR, "voice_samples")

    @property
    def previews_dir(self) -> str:
        return os.path.join(self.STORAGE_DIR, "previews")

    @property
    def outputs_dir(self) -> str:
        return os.path.join(self.STORAGE_DIR, "outputs")

    @property
    def dubbing_dir(self) -> str:
        return os.path.join(self.STORAGE_DIR, "dubbing")


def _ensure_runtime_secrets(cfg: Settings) -> None:
    """Populate strong runtime secrets when unset or using legacy placeholders."""
    need_jwt = (cfg.SECRET_KEY or "").strip() in _WEAK_SECRET_KEYS
    need_worker = (cfg.WORKER_TOKEN or "").strip() in _WEAK_WORKER_TOKENS

    if not need_jwt and not need_worker:
        return

    if (cfg.APP_ENV or "").lower() == "testing":
        if need_jwt:
            cfg.SECRET_KEY = secrets.token_urlsafe(48)
        if need_worker:
            cfg.WORKER_TOKEN = f"ovw_{secrets.token_urlsafe(32)}"
        return

    os.makedirs(cfg.STORAGE_DIR, exist_ok=True)
    secrets_file = os.path.join(cfg.STORAGE_DIR, ".gateway_secrets.json")
    stored: dict = {}
    if os.path.exists(secrets_file):
        try:
            with open(secrets_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    stored = data
        except Exception:
            stored = {}

    changed = False
    if need_jwt:
        if not stored.get("SECRET_KEY"):
            stored["SECRET_KEY"] = secrets.token_urlsafe(48)
            changed = True
        cfg.SECRET_KEY = stored["SECRET_KEY"]

    if need_worker:
        if not stored.get("WORKER_TOKEN"):
            stored["WORKER_TOKEN"] = f"ovw_{secrets.token_urlsafe(32)}"
            changed = True
        cfg.WORKER_TOKEN = stored["WORKER_TOKEN"]

    if changed:
        try:
            with open(secrets_file, "w", encoding="utf-8") as f:
                json.dump(stored, f, indent=2)
            try:
                os.chmod(secrets_file, 0o600)
            except Exception:
                pass
        except Exception as e:
            print(f"[Config] Warning: failed to persist runtime secrets: {e}")


settings = Settings()

# Check for Hugging Face Spaces Persistent Storage (/data is mounted and writable)
if os.path.exists("/data") and os.access("/data", os.W_OK):
    print("[Config] Persistent storage detected at /data. Redirecting SQLite database and Storage directories.")
    try:
        os.makedirs("/data/db", exist_ok=True)
        os.makedirs("/data/storage", exist_ok=True)
    except Exception as e:
        print(f"[Config] Error creating persistent storage directories: {e}")
    settings.DATABASE_URL = "sqlite:////data/db/omnivoice_gateway.db"
    settings.STORAGE_DIR = "/data/storage"

_ensure_runtime_secrets(settings)
