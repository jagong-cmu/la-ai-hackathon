# ruff: noqa: E402  (the env setup below must run before any app import)
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import dotenv

# browser-use calls load_dotenv() on import, which copies the repo's .env (keys, RESTOCK_TOKEN)
# into os.environ. Tests must not depend on local keys, so turn it off before any app import.
dotenv.load_dotenv = lambda *args, **kwargs: False

# Tests never send to the real Sentry projects, even when .env has a DSN (a blank DSN means off).
os.environ["SENTRY_DSN"] = ""
# browser_use writes a config file on import; keep it out of the developer's home directory.
os.environ.setdefault("BROWSER_USE_CONFIG_DIR", tempfile.mkdtemp(prefix="browseruse-test-"))

import pytest

from app.config import BACKEND_DIR, REPO_DIR, RuntimeModes, Settings
from app.db import Database
from app.decide import Decider
from app.jev import JevClient
from app.llm import Step5Client


def _dotenv_keys() -> set[str]:
    keys: set[str] = set()
    for path in (REPO_DIR / ".env", BACKEND_DIR / ".env"):
        if path.exists():
            for line in path.read_text().splitlines():
                name, sep, _ = line.partition("=")
                if sep and not name.lstrip().startswith("#"):
                    keys.add(name.strip())
    return keys - {"SENTRY_DSN"}


_DOTENV_KEYS = _dotenv_keys()


@pytest.fixture(autouse=True)
def _no_local_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """browser_use calls load_dotenv() on import, which copies a developer's .env into
    os.environ. Settings(_env_file=None) would still read those values, so remove them."""
    for key in _DOTENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _clean_lane_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """app.main imports app.lanes.live_setup, which registers the real-site adapters for the
    whole process. Discovery adds approved sites only for registered adapters, so each test
    starts with no lanes registered and registers the ones it needs."""
    from app.lanes import registry

    monkeypatch.setattr(registry, "_ADAPTERS", {})
    monkeypatch.setattr(registry, "_LLM_AGENT", None)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        run_mode="fixture",
        var_dir=tmp_path / "var",
        qb_data_dir=None,
        fixture_time_scale=0.01,
        jev_api_key=None,
        openrouter_api_key=None,
        creao_api_key=None,
        sentry_dsn=None,
    )


@pytest.fixture
def db(settings: Settings) -> Database:
    return Database(settings.db_path)


@pytest.fixture
def modes(settings: Settings) -> RuntimeModes:
    return RuntimeModes(settings.initial_modes())


@pytest.fixture
def decider(settings: Settings, db: Database, modes: RuntimeModes) -> Decider:
    return Decider(settings, db, JevClient(settings, db, modes), Step5Client(settings, db, modes))
