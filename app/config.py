"""Settings, read from environment variables (or a .env file)."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    transcriber: Literal["openai", "local"] = "openai"

    # TRANSCRIBER=openai: any OpenAI-compatible /audio/transcriptions endpoint
    openai_api_url: str = "https://api.openai.com/v1/audio/transcriptions"
    openai_api_key: str = ""
    openai_model: str = "whisper-1"
    openai_timeout_seconds: float = 120.0

    # TRANSCRIBER=local: faster-whisper running on this machine
    local_model: str = "base"
    local_device: str = "cpu"
    local_compute_type: str = "int8"

    # Optional bearer token clients must send. Empty = no auth.
    api_key: str = ""
    max_audio_mb: int = 25


@lru_cache
def get_settings() -> Settings:
    return Settings()
