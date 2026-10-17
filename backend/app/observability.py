"""Sentry setup and a span helper that does nothing when Sentry is off."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import sentry_sdk

from app.config import Settings

_enabled = False


def init_sentry(settings: Settings) -> None:
    global _enabled
    if not settings.sentry_dsn:
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
    )
    _enabled = True


@contextlib.contextmanager
def span(op: str, name: str, **data: Any) -> Iterator[None]:
    if not _enabled:
        yield
        return
    with sentry_sdk.start_span(op=op, name=name) as s:
        for key, value in data.items():
            s.set_data(key, value)
        yield


def capture(exc: BaseException) -> None:
    if _enabled:
        sentry_sdk.capture_exception(exc)
