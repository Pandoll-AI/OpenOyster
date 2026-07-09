from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="OPENOYSTER_", env_file=".env", extra="ignore", case_sensitive=False
    )
    db_url: str = Field(default="sqlite:///./openoyster.db")
    workspace: Path = Field(default=Path("./workspace"))
    inbox_dir: Path | None = Field(default=None)
    archive_dir: Path | None = Field(default=None)
    log_level: str = Field(default="INFO")
    log_json: bool = Field(default=False)
    llm_provider: Literal["codex", "openai-compatible", "stub"] = Field(default="codex")
    llm_api_key: str | None = Field(default=None)
    llm_base_url: str = Field(default="https://api.openai.com/v1")
    llm_model: str = Field(default="gpt-4.1-mini")
    llm_timeout_seconds: float = Field(default=45.0, ge=1.0, le=300.0)
    llm_max_retries: int = Field(default=2, ge=0, le=10)
    codex_binary: str = Field(default="codex")
    codex_batch_size: int = Field(default=5, ge=1, le=20)
    codex_timeout_seconds: float = Field(default=300.0, ge=10.0, le=1800.0)
    codex_config_dir: Path = Field(default=Path(".codex-llm"))
    max_events_per_loop: int = Field(default=100, ge=1, le=5000)
    event_scan_multiplier: int = Field(default=20, ge=1, le=100)
    loop_lease_seconds: int = Field(default=300, ge=10, le=3600)
    continue_on_loop_error: bool = Field(default=True)
    scheduler_tick_seconds: float = Field(default=30.0, ge=0.1, le=86400)
    max_file_bytes: int = Field(default=25 * 1024 * 1024, ge=1024, le=500 * 1024 * 1024)
    archive_processed_files: bool = Field(default=False)
    extraction_max_attempts: int = Field(default=3, ge=1, le=20)
    api_key: str | None = Field(default=None)
    api_key_header: str = Field(default="X-OpenOyster-Key")
    api_allow_unsafe_no_key: bool = Field(default=False)
    api_max_page_size: int = Field(default=200, ge=10, le=1000)
    default_policy_version: str = Field(default="default-0.4.0")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        value = value.upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if value not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return value

    @field_validator("llm_base_url")
    @classmethod
    def strip_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def derive_workspace_paths(self) -> Settings:
        if self.inbox_dir is None:
            self.inbox_dir = self.workspace / "inbox"
        if self.archive_dir is None:
            self.archive_dir = self.workspace / "archive"
        return self

    def ensure_workspace(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        assert self.inbox_dir is not None and self.archive_dir is not None
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_workspace()
    return settings


def clear_settings_cache() -> None:
    get_settings.cache_clear()
