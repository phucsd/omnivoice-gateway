import os
from typing import Optional
import requests

from app.config import settings


class S3StorageService:
    _hf_api = None
    _dataset_repo_id = "phucsd/omnivoice-audio-storage"

    @classmethod
    def get_hf_api(cls):
        if cls._hf_api is None:
            from huggingface_hub import HfApi

            token = os.environ.get("HF_TOKEN") or getattr(settings, "HF_TOKEN", None)
            cls._hf_api = HfApi(token=token) if token else HfApi()
            try:
                cls._hf_api.create_repo(
                    repo_id=cls._dataset_repo_id,
                    repo_type="dataset",
                    exist_ok=True,
                    private=True,
                )
            except Exception as e:
                print(f"[S3StorageService] Repo init warning: {e}")
        return cls._hf_api

    @classmethod
    def upload_audio_to_s3_cdn(cls, local_path: str, filename: str) -> Optional[str]:
        """
        Optionally uploads audio file to a private Hugging Face Dataset when
        ENABLE_HF_AUDIO_CDN is explicitly enabled and HF_TOKEN is configured.
        Disabled by default to keep user-generated audio private inside gateway auth.
        """
        enabled = getattr(settings, "ENABLE_HF_AUDIO_CDN", False) or (
            os.environ.get("ENABLE_HF_AUDIO_CDN", "").strip().lower() == "true"
        )
        hf_token = (os.environ.get("HF_TOKEN") or getattr(settings, "HF_TOKEN", "") or "").strip()
        if not enabled or not hf_token:
            return None

        if not local_path or not os.path.exists(local_path):
            return None

        try:
            api = cls.get_hf_api()
            repo_path = f"audio/{filename}"

            api.upload_file(
                path_or_fileobj=local_path,
                path_in_repo=repo_path,
                repo_id=cls._dataset_repo_id,
                repo_type="dataset",
            )

            dataset_url = f"https://huggingface.co/datasets/{cls._dataset_repo_id}/resolve/main/{repo_path}"
            try:
                res = requests.get(dataset_url, allow_redirects=False, timeout=5)
                if res.status_code in [301, 302] and res.headers.get("Location"):
                    return res.headers.get("Location")
            except Exception:
                pass

            return dataset_url
        except Exception as e:
            print(f"[S3StorageService ERROR] Failed to upload {filename} to S3 CDN: {e}")
            return None


s3_storage_service = S3StorageService()
