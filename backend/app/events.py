"""Event bus: append to the event log, wake SSE readers, and replay a recorded job.

Readers never miss an event: they read the log and start to wait while they hold the
same condition that `emit` notifies under.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from datetime import datetime

from app.contracts import Event, utcnow
from app.db import Database

KEEPALIVE_S = 15


class EventBus:
    def __init__(self, db: Database):
        self.db = db
        self._conds: dict[str, asyncio.Condition] = {}

    def _cond(self, job_id: str) -> asyncio.Condition:
        cond = self._conds.get(job_id)
        if cond is None:
            cond = self._conds[job_id] = asyncio.Condition()
        return cond

    async def emit(
        self, job_id: str, type_: str, data: dict | None = None, lane_id: str | None = None
    ) -> Event:
        ts = utcnow()
        seq = self.db.append_event(job_id, type_, ts.isoformat(), lane_id, data or {})
        cond = self._cond(job_id)
        async with cond:
            cond.notify_all()
        return Event(seq=seq, job_id=job_id, type=type_, ts=ts, lane_id=lane_id, data=data or {})

    async def stream(self, job_id: str, after_seq: int = 0) -> AsyncIterator[Event | None]:
        """Yield events after `after_seq` forever. Yields None as a keepalive tick."""
        last = after_seq
        cond = self._cond(job_id)
        while True:
            async with cond:
                events = self.db.events_after(job_id, last)
                if not events:
                    try:
                        await asyncio.wait_for(cond.wait(), timeout=KEEPALIVE_S)
                    except TimeoutError:
                        events = None
                    else:
                        events = self.db.events_after(job_id, last)
            if events is None:
                yield None
                continue
            for event in events:
                last = event.seq
                yield event

    async def replay(self, source_job_id: str, target_job_id: str, speed: float = 1.0,
                     before_emit: Callable[[Event], None] | None = None) -> None:
        """Re-emit a recorded job's events under a new job id with the original timing.

        `before_emit` sees each source event before it is re-emitted (to update the job view)."""
        events = self.db.events_after(source_job_id, 0, limit=100_000)
        await self.emit(target_job_id, "replay_started", {"sourceJobId": source_job_id})
        prev: datetime | None = None
        for event in events:
            if prev is not None and speed > 0:
                gap = (event.ts - prev).total_seconds() / speed
                with contextlib.suppress(Exception):
                    await asyncio.sleep(max(0.0, min(gap, 30.0)))
            prev = event.ts
            if before_emit is not None:
                before_emit(event)
            await self.emit(target_job_id, event.type, event.data, event.lane_id)
