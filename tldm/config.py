from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path.home() / ".config" / "tldm"

_DEFAULT_MODEL = "gemini/gemini-3.1-flash-lite-preview"


class Settings(BaseSettings):
    """Application settings loaded from env vars and constructor overrides."""

    model_config = SettingsConfigDict(env_prefix="TLDM_")

    transcription_model: str = _DEFAULT_MODEL
    summary_model: str = _DEFAULT_MODEL
    drive_credentials_path: Path = CONFIG_DIR / "credentials.json"
    service_account_path: Path | None = None

    @model_validator(mode="after")
    def _set_summary_model_default(self) -> "Settings":
        if (
            not self.summary_model or self.summary_model == _DEFAULT_MODEL
        ) and self.transcription_model != _DEFAULT_MODEL:
            self.summary_model = self.transcription_model
        return self
