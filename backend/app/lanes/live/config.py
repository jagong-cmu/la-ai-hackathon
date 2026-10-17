"""Live lane settings. Module-local on purpose: app/config.py belongs to the core team.

Reads the same `.env` files as app.config (repo root first, then backend/).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import BACKEND_DIR, REPO_DIR


class LiveLaneSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        extra="ignore",
    )

    # local: a Chromium on this machine (dev, tests). cloud: a Browser Use cloud browser (demo).
    browser_mode: Literal["local", "cloud"] = "local"
    browser_headless: bool = True
    browser_executable: str | None = None  # default: Playwright's Chromium

    # Browser Use cloud browser (POST /api/v2/browsers). Free plan: timeout <= 15 min; paid <= 240.
    browser_use_api_key: SecretStr | None = None
    browser_use_api_base: str = "https://api.browser-use.com"
    cloud_proxy_country_code: str | None = "us"
    cloud_timeout_min: int = 15

    # LLM for the Browser Use agent lane (unknown sites): Step 5 Preview through OpenRouter.
    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    step5_model: str = "stepfun/step-5-preview"
    browser_llm_fallback: str | None = None  # an OpenRouter model id, used if Step 5 fails
    browser_use_vision: bool = False  # Step 5 gets DOM text; turn on only for a vision model
    agent_max_steps: int = 40

    # Text limits for page snapshots sent to Jev.
    snapshot_text_chars: int = 6000

    @field_validator("browser_use_api_key", "openrouter_api_key", "browser_llm_fallback", "browser_executable",
                     mode="before")
    @classmethod
    def _blank_is_unset(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value


@lru_cache
def get_live_settings() -> LiveLaneSettings:
    return LiveLaneSettings()
