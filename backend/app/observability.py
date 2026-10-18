"""Sentry setup and span helpers that do nothing when Sentry is off.

A job runs in background tasks after the request that started it has ended. A span with no
open transaction is dropped, so each background run gets its own transaction (`in_transaction`),
in the same trace as the request that started it.

AI calls use the `gen_ai.*` span ops and attributes, so Sentry's AI Agents view shows them.
"""

from __future__ import annotations

import contextlib
from collections.abc import Coroutine, Iterator
from typing import Any, TypeVar

import sentry_sdk

from app.config import Settings

T = TypeVar("T")
_enabled = False


def init_sentry(settings: Settings) -> None:
    global _enabled
    if not settings.sentry_dsn:
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.run_mode,  # "fixture" and "live" runs stay apart
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        enable_logs=True,
    )
    _enabled = True


@contextlib.contextmanager
def span(op: str, name: str, **data: Any) -> Iterator[Any]:
    """A child span of the current transaction. Yields the span, or None when Sentry is off."""
    if not _enabled:
        yield None
        return
    with sentry_sdk.start_span(op=op, name=name) as s:
        set_data(s, **data)
        yield s


def set_data(s: Any, **data: Any) -> None:
    """Set span attributes. Safe with s=None (Sentry off) and skips None values."""
    if s is None:
        return
    for key, value in data.items():
        if value is not None:
            s.set_data(key, value)


def ai_span(system: str, model: str, purpose: str, **data: Any) -> contextlib.AbstractContextManager[Any]:
    """A `gen_ai.chat` span for one model call. Add the token counts with `set_data` after the call."""
    return span("gen_ai.chat", f"chat {model}", **{
        "gen_ai.system": system, "gen_ai.operation.name": "chat", "gen_ai.request.model": model,
        "gen_ai.function_id": purpose, **data,
    })


def in_transaction(op: str, name: str, coro: Coroutine[Any, Any, T], **tags: str) -> Coroutine[Any, Any, T]:
    """Run a background coroutine in its own transaction, in the trace of the current request.

    Call it where the task is created (inside the request), not inside the task."""
    if not _enabled:
        return coro
    headers = {"sentry-trace": sentry_sdk.get_traceparent() or "", "baggage": sentry_sdk.get_baggage() or ""}

    async def run() -> T:
        # The task shares the request's scope objects, so fork them before this task changes them.
        with sentry_sdk.isolation_scope(), sentry_sdk.new_scope():
            for key, value in tags.items():
                sentry_sdk.set_tag(key, value)
            transaction = sentry_sdk.continue_trace(headers, op=op, name=name)
            with sentry_sdk.start_transaction(transaction):
                return await coro

    return run()


def forked(coro: Coroutine[Any, Any, T]) -> Coroutine[Any, Any, T]:
    """Give a concurrent task its own scope, so its spans are children of the span that started it.

    Without it, parallel tasks share one scope object and each new span becomes the parent of the next."""
    if not _enabled:
        return coro

    async def run() -> T:
        with sentry_sdk.new_scope():
            return await coro

    return run()


def capture(exc: BaseException) -> None:
    if _enabled:
        sentry_sdk.capture_exception(exc)
