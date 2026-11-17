"""Application configuration loaded from environment variables / ``.env``."""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """Raised when a required setting is missing at the moment it is needed."""


class Settings(BaseSettings):
    """Typed settings. Secrets use ``SecretStr`` so they never leak into logs."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    github_token: SecretStr = SecretStr("")
    github_api_url: str = "https://api.github.com"
    github_webhook_secret: SecretStr = SecretStr("")
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-3.8-flash"

    checkpoint_db_path: str = "data/checkpoints.sqlite"
    max_revisions: int = 5
    max_file_bytes: int = 200_000
    max_files_per_pr: int = 20
    http_timeout_seconds: float = 20.0

    def require_github_token(self) -> str:
        """Return the GitHub token or raise ``ConfigurationError``."""
        value = self.github_token.get_secret_value()
        if not value:
            raise ConfigurationError("GITHUB_TOKEN is not configured")
        return value

    def require_gemini_key(self) -> str:
        """Return the Gemini key or raise ``ConfigurationError``."""
        value = self.gemini_api_key.get_secret_value()
        if not value:
            raise ConfigurationError("GEMINI_API_KEY is not configured")
        return value

    def missing_required(self) -> list[str]:
        """Names of required variables that are currently unset."""
        missing = []
        if not self.github_token.get_secret_value():
            missing.append("GITHUB_TOKEN")
        if not self.gemini_api_key.get_secret_value():
            missing.append("GEMINI_API_KEY")
        return missing


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor."""
    return Settings()
