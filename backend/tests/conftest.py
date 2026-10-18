# ruff: noqa: E402  (the env setup below must run before any app import)
from __future__ import annotations

import os
from pathlib import Path

import dotenv

# browser-use calls load_dotenv() on import, which copies the repo's .env (keys, RESTOCK_TOKEN)
# into os.environ. Tests must not depend on local keys, so turn it off before any app import.
dotenv.load_dotenv = lambda *args, **kwargs: False

# Tests never send to the real Sentry projects, even when .env has a DSN (a blank DSN means off).
os.environ["SENTRY_DSN"] = ""

import pytest

from app.config import RuntimeModes, Settings
from app.db import Database
from app.decide import Decider
from app.jev import JevClient
from app.llm import Step5Client


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
