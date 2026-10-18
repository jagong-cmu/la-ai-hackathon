"""Settings, thresholds and per-dependency run modes.

Every value can be set in `.env` (see `.env.example`). Nested values use `__`,
for example `CHECKOUT_CONTACT__ADDRESS1=...`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
FIXTURES_DIR = BACKEND_DIR / "fixtures"

Mode = Literal["live", "cached"]
DEPENDENCIES = ("jev", "llm", "creao", "browser", "phone")


class Thresholds(BaseModel):
    """Decision thresholds. Tune them on cached runs at H10 and record them in the spec (§6)."""

    match_min: float = 0.5  # P(same product) below this: not eligible
    match_act: float = 0.8  # below this: eligible, flagged low_match
    must_have_min: float = 0.5
    must_have_act: float = 0.8
    in_stock_min: float = 0.5
    overbuy_ratio: float = 0.25
    risk_penalty: float = 0.05  # ranking only: each J5 doubt flag adds this share of the line cost
    payment_stop: float = 0.2  # J6: P(payment step) at or above this stops the lane
    act_band: float = 0.8  # decide(): confidence at or above this acts without escalation
    confirm_band: float = 0.5  # decide(): between confirm and act, escalate to Step 5 Preview


class CheckoutContact(BaseModel):
    """Team-controlled guest checkout details. Never a third party's data."""

    name: str = "Demo Buyer"
    company: str = "Hearthloaf Bakeries (demo)"
    email: str = "demo-buyer@example.com"
    phone: str = "+10000000000"
    address1: str = "TEAM ADDRESS NOT SET"
    city: str = "Kalamazoo"
    state: str = "MI"
    zip: str = "49007"

    @model_validator(mode="before")
    @classmethod
    def _blank_uses_default(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if not (isinstance(v, str) and not v.strip())}
        return data


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_nested_delimiter="__",
        extra="ignore",
    )

    # "fixture" sets every dependency to cached and needs no keys. "live" uses the keys you set.
    run_mode: Literal["fixture", "live"] = "fixture"
    var_dir: Path = BACKEND_DIR / "var"
    qb_data_dir: Path | None = None  # path to the extracted `hackathon-100` folder
    injected_bills_path: Path = FIXTURES_DIR / "injected_supply_bills.yaml"
    business_id: str = "company-001"
    demo_zip: str = "49007"
    cors_origins: list[str] = ["http://localhost:3000"]

    # Job timing (spec §4.1, §10)
    job_deadline_s: float = 150
    lane_search_budget_s: float = 45
    lane_cart_budget_s: float = 45
    lane_subset_budget_s: float = 20
    lane_agent_budget_factor: float = 3.0  # LLM-agent lanes (unknown sites) get longer budgets
    # An LLM-agent lane searches only this many items, largest past spend first. The H3 gate (2026-10-17)
    # took 92 s to search 2 items and 93 s to cart them, which does not fit the job deadline.
    agent_lane_max_items: int = 1
    # call_first: call the distributor as soon as the first plan exists, then text a recap.
    # sms_first: text a quote request, wait distributor_reply_wait_s, then call if no full quote.
    distributor_flow: Literal["call_first", "sms_first"] = "call_first"
    # The call covers only the N items with the highest line cost in the best plan (short demo call).
    # The other items stay with the web suppliers. 0 = every item. 1 keeps the demo call short.
    distributor_call_max_items: int = 1
    distributor_reply_wait_s: float = 20
    distributor_call_latest_s: float = 60
    fixture_time_scale: float = 1.0  # 0 makes fixture lanes instant (tests)

    # Jev (TypeSafe)
    jev_api_key: SecretStr | None = None
    jev_base_url: str = "https://api.typesafe.ai"
    jev_model: str = "jev-latest"
    jev_concurrency: int = 6
    jev_timeout_s: float = 5
    jev_safety_timeout_s: float = 3

    # Step 5 Preview through OpenRouter
    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    step5_model: str = "stepfun/step-5-preview"
    llm_timeout_s: float = 30
    llm_concurrency: int = 4
    # Step 5 Preview reasons before it answers, and the reasoning counts against max_tokens.
    # A spec used about 1,150 tokens in a test (2026-10-17), so no call asks for less than this.
    llm_min_max_tokens: int = 2048

    # CREAO
    creao_api_key: SecretStr | None = None
    creao_base_url: str = "https://agent.creao.ai"
    creao_app_id: str | None = None
    creao_poll_s: float = 10
    creao_max_wait_s: float = 300

    # Security and observability
    admin_token: SecretStr = SecretStr("dev-admin")
    webhook_secret: SecretStr = SecretStr("dev-webhook")
    restock_token: SecretStr | None = None  # bearer token for POST /planner/check (CREAO scheduled run)
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = 1.0

    thresholds: Thresholds = Thresholds()
    checkout_contact: CheckoutContact = CheckoutContact()

    @field_validator(
        "jev_api_key", "openrouter_api_key", "creao_api_key", "creao_app_id", "sentry_dsn", "qb_data_dir",
        "restock_token",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def db_path(self) -> Path:
        return self.var_dir / "app.db"

    @property
    def evidence_dir(self) -> Path:
        return self.var_dir / "evidence"

    def initial_modes(self) -> dict[str, Mode]:
        if self.run_mode == "fixture":
            return {dep: "cached" for dep in DEPENDENCIES}
        return {
            "jev": "live" if self.jev_api_key else "cached",
            "llm": "live" if self.openrouter_api_key else "cached",
            "creao": "live" if self.creao_api_key and self.creao_app_id else "cached",
            "browser": "live",
            "phone": "live",
        }


class RuntimeModes:
    """Per-dependency modes that the admin drawer can change while the server runs."""

    def __init__(self, initial: dict[str, Mode]):
        self._modes: dict[str, Mode] = dict(initial)

    def get(self, dep: str) -> Mode:
        return self._modes[dep]

    def set(self, dep: str, mode: Mode) -> None:
        if dep not in DEPENDENCIES:
            raise KeyError(dep)
        self._modes[dep] = mode

    def as_dict(self) -> dict[str, Mode]:
        return dict(self._modes)


@lru_cache
def get_settings() -> Settings:
    return Settings()
