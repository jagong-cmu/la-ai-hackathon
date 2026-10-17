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

    # LLM for the Browser Use agent lane (unknown sites). Any OpenAI-compatible endpoint: the team
    # uses StepFun directly (OPENROUTER_BASE_URL=https://api.stepfun.ai/v1) with the same key names.
    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    step5_model: str = "stepfun/step-5-preview"
    # The H3 gate (2026-10-17) found step-5-preview too slow to drive the lane (about 100 s a step),
    # so the lane has its own model. Unset: the lane uses step5_model.
    browser_llm_model: str | None = None
    browser_llm_fallback: str | None = None  # a model id at the same endpoint, used if the primary fails
    # Forced JSON (response_format) made step-5-preview pad replies with blank space until the token
    # limit. False: the schema goes in the system prompt and Browser Use parses the reply text.
    browser_llm_force_json: bool = True
    browser_use_vision: bool = False  # Step 5 gets DOM text; turn on only for a vision model
    agent_max_steps: int = 40

    # Text limits for page snapshots sent to Jev.
    snapshot_text_chars: int = 6000

    @field_validator("browser_use_api_key", "openrouter_api_key", "browser_llm_model", "browser_llm_fallback",
                     "browser_executable", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value


@lru_cache
def get_live_settings() -> LiveLaneSettings:
    return LiveLaneSettings()
