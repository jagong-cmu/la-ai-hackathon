"""Supplier discovery: CREAO agent (API Trigger, polled) -> prices filter -> J3 filter -> lane selection.

CREAO runs take minutes, so the result is cached per (category, zip) and the UI starts
discovery when screen 0 loads. Fallback order: live run -> cache -> saved real run
(RUN_MODE=live only, fixtures/creao/) -> fixture list.

CREAO contract:
- Auth: `Authorization: Bearer capi_...` (a key made on the agent page; it runs only that agent).
- Start: `POST /api/v1/apps/{appId}/runs` with `{"inputs": {...}}` -> 202
  `{"id", "status": "pending", "appId", "appName"}`.
- Poll: `GET /api/v1/apps/{appId}/runs/{runId}` until `status` is `completed`, `failed` or
  `cancelled` (other values: `pending`, `running`). The final text is in `result.text`
  (`result.chatText` when the chat reply differs); files are in `result.artifacts`.
  Live runs put the supplier JSON in `result.chatText`, so the parser tries each field.
  A failed run carries `errorCode`.
- Errors: JSON envelope `{"error": "ERROR_CODE"}`; 401 = bad or revoked key; 429 = rate
  limited (honor `Retry-After`).
- Webhooks cannot reach localhost, so this client polls.

The agent's final message must contain one JSON object (see agents/creao/README.md):
{"suppliers": [{"name", "url", "phone", "email", "notes", "pricesPublic"}]}.
`_parse_creao_output` also accepts a bare list and a ```json fenced block.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from typing import Any

import httpx

from app.config import FIXTURES_DIR, RuntimeModes, Settings
from app.contracts import DiscoveryResult, DistributorRef, SupplierCandidate, SupplierRef, utcnow
from app.db import Database, stable_hash
from app.decide import Decider
from app.judgments import j3_supplier

MAX_ADAPTER_LANES = 3
MAX_AGENT_LANES = 1

# Supplier domain -> adapter name. CREAO never sets an adapter, so discovery maps by domain.
# A mapping takes effect only once that adapter is registered (app/lanes/live_setup.py);
# until then the site stays an unknown site for the LLM agent lane. Register adapters
# under exactly these names.
ADAPTER_DOMAINS: dict[str, str] = {
    "webstaurantstore.com": "webstaurant",
    "katom.com": "katom",
    "uline.com": "uline",
    "restaurantware.com": "restaurantware",
    "wasserstrom.com": "wasserstrom",
}

# Real CREAO results saved per (category, zip): fixtures/creao/<category>-<zip>.json.
# Live runs use one when CREAO fails and the database has no cached result.
CREAO_SEED_DIR = FIXTURES_DIR / "creao"

TERMINAL_OK = "completed"
TERMINAL_FAIL = ("failed", "cancelled", "canceled")
REQUEST_TIMEOUT_S = 30.0
MAX_RETRY_AFTER_S = 60.0


class CreaoError(Exception):
    pass


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def _error_code(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}"
    if isinstance(body, dict):
        err = body.get("error") or body.get("errorCode")
        if isinstance(err, dict):
            err = err.get("code")
        if err:
            return f"HTTP {resp.status_code} {err}"
    return f"HTTP {resp.status_code}"


def _json_body(resp: httpx.Response) -> dict[str, Any]:
    try:
        body = resp.json()
    except ValueError as exc:
        raise CreaoError(f"non-JSON response: HTTP {resp.status_code}") from exc
    if not isinstance(body, dict):
        raise CreaoError("unexpected CREAO response shape")
    return body


def _retry_after(resp: httpx.Response, default: float) -> float:
    try:
        return min(float(resp.headers.get("Retry-After", default)), MAX_RETRY_AFTER_S)
    except ValueError:
        return default


def _find_json(text: str) -> Any:
    """Return the first JSON object or list in free text (fenced blocks first)."""
    candidates = [m.group(1) for m in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.S)]
    candidates.append(text)
    decoder = json.JSONDecoder()
    for chunk in candidates:
        for i, ch in enumerate(chunk):
            if ch not in "{[":
                continue
            try:
                value, _ = decoder.raw_decode(chunk, i)
            except ValueError:
                continue
            if (isinstance(value, dict) and "suppliers" in value) or isinstance(value, list):
                return value
    raise CreaoError("no supplier JSON in CREAO output")


def _run_outputs(run: dict[str, Any]) -> list[Any]:
    """Every place a polled run can hold the agent's output, in the order to try them.

    Live runs (checked 2026-10-17) put a short summary JSON in `result.text` and the
    supplier JSON in `result.chatText`, so one non-empty field is not enough."""
    result = run.get("result")
    outputs: list[Any] = []
    if isinstance(result, dict):
        outputs += [result[key] for key in ("text", "chatText", "output") if result.get(key)]
        if "suppliers" in result:
            outputs.append(result)
    elif result:
        outputs.append(result)
    if run.get("output"):
        outputs.append(run["output"])
    return outputs


def _parse_run(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse the first output field that holds a usable supplier list."""
    error = CreaoError("empty CREAO output")
    for output in _run_outputs(run):
        try:
            return _parse_creao_output(output)
        except CreaoError as exc:
            error = exc
    raise error


def _parse_creao_output(output: Any) -> list[dict[str, Any]]:
    if output is None or output == "":
        raise CreaoError("empty CREAO output")
    if isinstance(output, str):
        output = _find_json(output)
    if isinstance(output, dict):
        output = output.get("suppliers") or output.get("result") or output.get("output") or []
    if not isinstance(output, list):
        raise CreaoError("CREAO output is not a supplier list")
    suppliers: list[dict[str, Any]] = []
    for s in output:
        if not isinstance(s, dict):
            continue
        url = str(s.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        row = dict(s)
        row["url"] = url
        if "pricesPublic" in row and "prices_public" not in row:
            row["prices_public"] = bool(row["pricesPublic"])
        suppliers.append(row)
    if not suppliers:
        raise CreaoError("CREAO output has no usable suppliers")
    return suppliers


def adapter_for_url(url: str) -> str | None:
    """The adapter name for a supplier URL by domain (subdomains match), or None."""
    host = (httpx.URL(url).host or "").lower().removeprefix("www.")
    for domain, adapter in ADAPTER_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return adapter
    return None


def _resolve_adapter(row: dict[str, Any], known: set[str]) -> str | None:
    """A row's own adapter, else its domain's; None (LLM agent lane) unless it is registered."""
    for adapter in (row.get("adapter"), adapter_for_url(row["url"])):
        if adapter and adapter in known:
            return adapter
    return None


def _prices_hidden(row: dict[str, Any]) -> bool:
    """True only when the source says prices need an account. A missing flag is not a no."""
    flag = row.get("pricesPublic", row.get("prices_public"))
    return flag is False


def _load_seed(category: str, zip_code: str) -> tuple[str | None, list[dict[str, Any]], float | None] | None:
    """A saved real CREAO result: (run id, rows, age in seconds), or None if there is none."""
    path = CREAO_SEED_DIR / f"{category}-{zip_code}.json"
    if not path.exists():
        return None
    seed = json.loads(path.read_text())
    age = None
    if seed.get("fetchedAt"):
        fetched = datetime.fromisoformat(seed["fetchedAt"].replace("Z", "+00:00"))
        age = (utcnow() - fetched).total_seconds()
    return seed.get("creaoRunId"), _parse_creao_output(seed), age


class Discovery:
    def __init__(self, settings: Settings, db: Database, modes: RuntimeModes, decider: Decider,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.db = db
        self.modes = modes
        self.decider = decider
        self.transport = transport  # tests inject httpx.MockTransport

    @property
    def known_adapters(self) -> set[str]:
        from app.lanes.registry import registered

        return {"fixture", *registered()["adapters"]}  # type: ignore[misc]

    async def discover(
        self, category: str, zip_code: str, distributor: DistributorRef | None,
        items: list[str] | None = None,
    ) -> DiscoveryResult:
        key = f"creao:{category}:{zip_code}"
        raw: list[dict[str, Any]] | None = None
        run_id: str | None = None
        source = "cache"
        if self.modes.get("creao") == "live":
            try:
                run_id, raw = await self._run_creao(category, zip_code, items or [])
                self.db.cache_set(key, raw)
                source = "creao"
            except (CreaoError, httpx.HTTPError, TimeoutError, ValueError):
                raw = None
                source = "fallback"
        age: float | None = None
        if raw is None:
            raw = self.db.cache_get(key)
            if raw is not None:
                age = self.db.cache_age_s(key)
        if raw is None and self.settings.run_mode == "live":
            # Fixture mode keeps the fake list: its lanes replay fixtures/suppliers/<id>.json.
            seed = _load_seed(category, zip_code)
            if seed is not None:
                run_id, raw, age = seed
        if raw is None:
            raw = json.loads((FIXTURES_DIR / "discovery.json").read_text())["candidates"]
            source = "fixture" if source == "cache" else "fallback"

        known = self.known_adapters
        candidates = [
            SupplierCandidate(
                id=c.get("id") or _slug(c.get("name") or c["url"]),
                name=c.get("name") or c["url"],
                url=c["url"],
                phone=c.get("phone") or None,
                email=c.get("email") or None,
                adapter=_resolve_adapter(c, known),
                creao_run_id=run_id,
                cached_at=utcnow() if source == "creao" else None,
            )
            for c in raw
        ]
        notes = {c.get("id") or _slug(c.get("name") or c["url"]): c.get("notes") or "" for c in raw}
        # Code filter before J3: a supplier without public prices cannot be priced by a lane.
        hidden = {cand.id for cand, c in zip(candidates, raw, strict=True) if _prices_hidden(c)}
        judged = [c for c in candidates if c.id not in hidden]
        decisions = dict(zip(
            (c.id for c in judged),
            await asyncio.gather(*(j3_supplier(self.decider, c, category, notes.get(c.id, "")) for c in judged)),
            strict=True,
        ))
        adapter_lanes = agent_lanes = 0
        for cand in candidates:
            if cand.id in hidden:
                cand.reason = "filtered: prices not public"
                continue
            cand.jev = {k: float(v.get("noul", 0.0)) for k, v in decisions[cand.id].answers.items()}
            failed = [k for k, v in cand.jev.items() if v < 0.5]
            if failed:
                cand.reason = "filtered: " + ", ".join(failed)
                continue
            if cand.adapter is not None and adapter_lanes < MAX_ADAPTER_LANES:  # registered (resolved above)
                cand.selected, cand.reason = True, "known adapter"
                adapter_lanes += 1
            elif cand.adapter is None and agent_lanes < MAX_AGENT_LANES:
                cand.selected, cand.reason = True, "unknown site: LLM agent lane"
                agent_lanes += 1
            else:
                cand.reason = "not needed (lane limit)"
        return DiscoveryResult(category=category, zip_code=zip_code, candidates=candidates,
                               distributor=distributor, source=source, age_s=age)

    async def _run_creao(
        self, category: str, zip_code: str, items: list[str] | None = None
    ) -> tuple[str, list[dict[str, Any]]]:
        """Start one CREAO run and poll it. Raises CreaoError on any failure or timeout."""
        if not (self.settings.creao_api_key and self.settings.creao_app_id):
            raise CreaoError("CREAO is not configured")
        app_id = self.settings.creao_app_id
        headers = {"Authorization": f"Bearer {self.settings.creao_api_key.get_secret_value()}"}
        # Agent input forms are text fields, so the item list goes in as one line of text.
        inputs = {"category": category, "zipCode": zip_code, "items": ", ".join(items or [])}
        idem = "disc-" + stable_hash(category, zip_code, time.strftime("%Y-%m-%d"))[:40]
        deadline = time.monotonic() + self.settings.creao_max_wait_s
        async with httpx.AsyncClient(base_url=self.settings.creao_base_url, timeout=REQUEST_TIMEOUT_S,
                                     transport=self.transport) as http:
            body: dict[str, Any] = {"inputs": inputs, "idempotencyKey": idem}
            resp = await http.post(f"/api/v1/apps/{app_id}/runs", json=body, headers=headers)
            if resp.status_code in (400, 422):
                # idempotencyKey is not in the current public docs; retry once without it.
                resp = await http.post(f"/api/v1/apps/{app_id}/runs", json={"inputs": inputs},
                                       headers=headers)
            if resp.status_code not in (200, 201, 202):
                raise CreaoError(f"start failed: {_error_code(resp)}")
            run = _json_body(resp)
            run_id = str(run.get("id") or run.get("runId") or "")
            if not run_id:
                raise CreaoError("start response has no run id")
            poll_s = max(self.settings.creao_poll_s, 0.0)
            while True:
                if time.monotonic() >= deadline:
                    raise CreaoError(f"run {run_id} timed out")
                await asyncio.sleep(poll_s)
                resp = await http.get(f"/api/v1/apps/{app_id}/runs/{run_id}", headers=headers)
                if resp.status_code == 429:
                    await asyncio.sleep(_retry_after(resp, poll_s))
                    continue
                if resp.status_code >= 500:
                    continue  # transient: keep polling until the deadline
                if resp.status_code != 200:
                    raise CreaoError(f"poll failed: {_error_code(resp)}")
                status = _json_body(resp)
                state = str(status.get("status", "")).lower()
                if state == TERMINAL_OK:
                    return run_id, _parse_run(status)
                if state in TERMINAL_FAIL:
                    err = status.get("error")
                    code = status.get("errorCode") or (err.get("code") if isinstance(err, dict) else err)
                    raise CreaoError(f"run {run_id} {state}" + (f": {code}" if code else ""))


def selected_suppliers(result: DiscoveryResult) -> list[SupplierRef]:
    return [
        SupplierRef(id=c.id, name=c.name, url=c.url, adapter=c.adapter,
                    origin="creao" if result.source == "creao" else
                    ("fixture" if result.source == "fixture" else "cache"))
        for c in result.candidates if c.selected
    ]
