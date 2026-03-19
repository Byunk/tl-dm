import os
from pathlib import Path
from unittest.mock import patch

from tldm.config import Settings


class TestSettings:
    def test_defaults(self):
        settings = Settings()
        assert settings.transcription_model == "gemini/gemini-2.5-flash-lite"
        assert settings.summary_model == "gemini/gemini-2.5-flash-lite"
        assert settings.service_account_path is None

    def test_constructor_override(self):
        settings = Settings(transcription_model="openrouter/anthropic/claude-sonnet-4")
        assert settings.transcription_model == "openrouter/anthropic/claude-sonnet-4"

    def test_env_var_override(self):
        with patch.dict(os.environ, {"TLDM_TRANSCRIPTION_MODEL": "gemini/gemini-2.5-pro"}):
            settings = Settings()
            assert settings.transcription_model == "gemini/gemini-2.5-pro"

    def test_constructor_takes_precedence_over_env(self):
        with patch.dict(os.environ, {"TLDM_TRANSCRIPTION_MODEL": "gemini/gemini-2.5-pro"}):
            settings = Settings(transcription_model="openrouter/meta/llama-3")
            assert settings.transcription_model == "openrouter/meta/llama-3"

    def test_summary_model_follows_transcription_model_override(self):
        settings = Settings(transcription_model="openrouter/anthropic/claude-sonnet-4")
        assert settings.summary_model == "openrouter/anthropic/claude-sonnet-4"

    def test_summary_model_independent_override(self):
        settings = Settings(
            transcription_model="gemini/gemini-2.5-flash-lite",
            summary_model="openrouter/anthropic/claude-sonnet-4",
        )
        assert settings.transcription_model == "gemini/gemini-2.5-flash-lite"
        assert settings.summary_model == "openrouter/anthropic/claude-sonnet-4"

    def test_service_account_path(self):
        settings = Settings(service_account_path=Path("/tmp/sa.json"))
        assert settings.service_account_path == Path("/tmp/sa.json")
