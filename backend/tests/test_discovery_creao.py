"""CREAO API Trigger client: polled runs, output parsing and the live -> cache -> fixture fallback.

All HTTP goes through httpx.MockTransport. No real CREAO run starts.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

import app.discovery as discovery
import app.lanes.registry as registry
from app.config import RuntimeModes, Settings
from app.db import Database
from app.decide import Decider
from app.discovery import CreaoError, Discovery, _parse_creao_output, adapter_for_url

APP_ID = "00000000-0000-4000-8000-000000000000"
RUN_ID = "11111111-1111-4111-8111-111111111111"
RUN_PATH = f"/api/v1/apps/{APP_ID}/runs"
KEY = "capi_test_only_not_a_real_key"

SUPPLIERS = {
    "suppliers": [
        {"name": "Westside Restaurant Supply", "url": "https://westside.example", "phone": "+1 310 555 0100",
         "email": "sales@westside.example", "notes": "Packaging and disposables.", "pricesPublic": True},
        {"name": "Harbor Paper Co", "url": "https://harbor.example", "phone": None, "email": None,
         "notes": "Wholesale bags and boxes.", "pricesPublic": False},
    ]
}
FINAL_TEXT = "I found 2 suppliers.\n\n```json\n" + json.dumps(SUPPLIERS) + "\n```\n"

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def live_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={
        "creao_api_key": SecretStr(KEY),
        "creao_app_id": APP_ID,
        "creao_poll_s": 0.0,
        "creao_max_wait_s": 5.0,
    })


@pytest.fixture(autouse=True)
def llm_agent_registered(monkeypatch):
    """Live browsers can open any unknown site through the LLM agent lane."""
    monkeypatch.setattr(registry, "_LLM_AGENT", lambda supplier: None)


def make_discovery(live_settings: Settings, db: Database, decider: Decider, handler: Handler,
                   browser: str = "live") -> Discovery:
    modes = RuntimeModes({**live_settings.initial_modes(), "creao": "live", "browser": browser})
    return Discovery(live_settings, db, modes, decider, transport=httpx.MockTransport(handler))


def scripted(polls: list[httpx.Response], seen: list[httpx.Request] | None = None) -> Handler:
    """Start returns 202; each poll returns the next scripted response (the last one repeats)."""
    queue = list(polls)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.method == "POST" and request.url.path == RUN_PATH:
            return httpx.Response(202, json={"id": RUN_ID, "status": "pending", "appId": APP_ID,
                                             "appName": "Supplier Discovery"})
        if request.method == "GET" and request.url.path == f"{RUN_PATH}/{RUN_ID}":
            return queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(404, json={"error": "RUN_NOT_FOUND"})

    return handler


def run_state(status: str, **extra: object) -> httpx.Response:
    return httpx.Response(200, json={"id": RUN_ID, "status": status, **extra})


# ---------------------------------------------------------------- client


async def test_success_polls_until_completed(live_settings, db, decider):
    seen: list[httpx.Request] = []
    handler = scripted([run_state("pending"), run_state("running"),
                        run_state("completed", result={"text": FINAL_TEXT, "artifacts": []})], seen)
    disc = make_discovery(live_settings, db, decider, handler)

    run_id, rows = await disc._run_creao("packaging_disposables", "90024", ["to-go box", "kraft bag"])

    assert run_id == RUN_ID
    assert [r["name"] for r in rows] == ["Westside Restaurant Supply", "Harbor Paper Co"]
    assert rows[0]["prices_public"] is True and rows[1]["prices_public"] is False
    start = seen[0]
    assert start.headers["Authorization"] == f"Bearer {KEY}"
    body = json.loads(start.content)
    assert body["inputs"] == {"category": "packaging_disposables", "zipCode": "90024",
                              "items": "to-go box, kraft bag"}
    assert 8 <= len(body["idempotencyKey"]) <= 128
    assert "appId" not in body  # app id lives in the URL on the app-scoped route
    assert sum(r.method == "GET" for r in seen) == 3


async def test_success_reads_chat_text_when_text_is_a_summary(live_settings, db, decider):
    # The shape of a live run on 2026-10-17: a summary in text, the suppliers in chatText.
    summary = '{"category":"packaging_disposables","zipCode":90024,"supplier_count":2}'
    handler = scripted([run_state("completed", result={"text": summary, "chatText": FINAL_TEXT})])
    disc = make_discovery(live_settings, db, decider, handler)

    _, rows = await disc._run_creao("packaging_disposables", "90024")

    assert [r["name"] for r in rows] == ["Westside Restaurant Supply", "Harbor Paper Co"]


async def test_success_discover_uses_creao_and_fills_cache(live_settings, db, decider):
    handler = scripted([run_state("completed", result={"text": FINAL_TEXT, "artifacts": []})])
    disc = make_discovery(live_settings, db, decider, handler)

    result = await disc.discover("packaging_disposables", "90024", None)

    assert result.source == "creao"
    assert {c.url for c in result.candidates} == {"https://westside.example", "https://harbor.example"}
    assert all(c.creao_run_id == RUN_ID for c in result.candidates)
    assert db.cache_get("creao:packaging_disposables:90024")[0]["name"] == "Westside Restaurant Supply"


async def test_failed_run_raises_with_error_code(live_settings, db, decider):
    handler = scripted([run_state("running"), run_state("failed", errorCode="INSUFFICIENT_CREDITS")])
    disc = make_discovery(live_settings, db, decider, handler)

    with pytest.raises(CreaoError, match="failed: INSUFFICIENT_CREDITS"):
        await disc._run_creao("packaging_disposables", "90024")


async def test_failed_run_falls_back_to_fixture(live_settings, db, decider):
    handler = scripted([run_state("cancelled")])
    disc = make_discovery(live_settings, db, decider, handler)

    result = await disc.discover("packaging_disposables", "90024", None)

    assert result.source == "fallback"
    assert any("(fixture)" in c.name for c in result.candidates)
    assert db.cache_get("creao:packaging_disposables:90024") is None


async def test_timeout_falls_back_to_cache(live_settings, db, decider):
    cached = [{"name": "Cached Supply", "url": "https://cached.example", "notes": "Packaging."}]
    db.cache_set("creao:packaging_disposables:90024", cached)
    slow = live_settings.model_copy(update={"creao_poll_s": 0.01, "creao_max_wait_s": 0.05})
    disc = make_discovery(slow, db, decider, scripted([run_state("running")]))

    with pytest.raises(CreaoError, match="timed out"):
        await disc._run_creao("packaging_disposables", "90024")
    result = await disc.discover("packaging_disposables", "90024", None)

    assert result.source == "fallback"
    assert [c.url for c in result.candidates] == ["https://cached.example"]
    assert result.age_s is not None
    assert db.cache_get("creao:packaging_disposables:90024") == cached  # cache not overwritten


async def test_timeout_without_cache_falls_back_to_fixture(live_settings, db, decider):
    slow = live_settings.model_copy(update={"creao_poll_s": 0.01, "creao_max_wait_s": 0.03})
    disc = make_discovery(slow, db, decider, scripted([run_state("pending")]))

    result = await disc.discover("packaging_disposables", "90024", None)

    assert result.source == "fallback"
    assert any("(fixture)" in c.name for c in result.candidates)


async def test_malformed_output_raises_and_falls_back(live_settings, db, decider):
    handler = scripted([run_state("completed", result={"text": "Sorry, I could not find any suppliers."})])
    disc = make_discovery(live_settings, db, decider, handler)

    with pytest.raises(CreaoError):
        await disc._run_creao("packaging_disposables", "90024")
    result = await disc.discover("packaging_disposables", "90024", None)
    assert result.source == "fallback"


async def test_start_auth_error_is_creao_error(live_settings, db, decider):
    disc = make_discovery(live_settings, db, decider,
                          lambda r: httpx.Response(401, json={"error": "AUTHENTICATION_REQUIRED"}))

    with pytest.raises(CreaoError, match="401 AUTHENTICATION_REQUIRED"):
        await disc._run_creao("packaging_disposables", "90024")


async def test_start_retries_without_idempotency_key_on_400(live_settings, db, decider):
    bodies: list[dict] = []
    done = scripted([run_state("completed", result={"text": json.dumps(SUPPLIERS)})])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            bodies.append(json.loads(request.content))
            if "idempotencyKey" in bodies[-1]:
                return httpx.Response(400, json={"error": "INVALID_INPUT"})
        return done(request)

    disc = make_discovery(live_settings, db, decider, handler)
    run_id, rows = await disc._run_creao("packaging_disposables", "90024")

    assert run_id == RUN_ID and len(rows) == 2
    assert "idempotencyKey" in bodies[0] and "idempotencyKey" not in bodies[1]


async def test_poll_rate_limit_and_server_error_keep_polling(live_settings, db, decider):
    handler = scripted([
        httpx.Response(429, json={"error": "RATE_LIMITED"}, headers={"Retry-After": "0"}),
        httpx.Response(503, text="upstream down"),
        run_state("completed", result={"text": json.dumps(SUPPLIERS)}),
    ])
    disc = make_discovery(live_settings, db, decider, handler)

    run_id, rows = await disc._run_creao("packaging_disposables", "90024")
    assert run_id == RUN_ID and len(rows) == 2


async def test_not_configured_uses_fixture(settings, db, decider):
    modes = RuntimeModes({**settings.initial_modes(), "creao": "live"})
    disc = Discovery(settings, db, modes, decider, transport=httpx.MockTransport(
        lambda r: pytest.fail("no HTTP call expected")))

    result = await disc.discover("packaging_disposables", "90024", None)
    assert result.source == "fallback"


# ---------------------------------------------------------------- parser


def test_parse_accepts_bare_list_and_prose():
    text = 'Here you go: [{"name": "A", "url": "https://a.example"}] Thanks!'
    assert _parse_creao_output(text) == [{"name": "A", "url": "https://a.example"}]


def test_parse_accepts_dict_output_and_drops_bad_rows():
    rows = _parse_creao_output({"suppliers": [
        {"name": "Good", "url": "https://good.example", "pricesPublic": "yes"},
        {"name": "No URL"},
        {"name": "Bad URL", "url": "call us"},
        "not a dict",
    ]})
    assert [r["name"] for r in rows] == ["Good"]
    assert rows[0]["prices_public"] is True


def test_parse_skips_unrelated_json_before_the_supplier_block():
    text = 'Inputs: {"zipCode": "90024"}\n```json\n' + json.dumps(SUPPLIERS) + "\n```"
    assert len(_parse_creao_output(text)) == 2


@pytest.mark.parametrize("bad", [None, "", "no json here", '{"suppliers": []}', '{"suppliers": "x"}', 42])
def test_parse_rejects_malformed(bad):
    with pytest.raises(CreaoError):
        _parse_creao_output(bad)


# ---------------------------------------------------------------- adapters, prices filter, saved run

KATOM = {"name": "KaTom Restaurant Supply", "url": "https://www.katom.com", "phone": None, "email": None,
         "notes": "Restaurant supply with public prices.", "pricesPublic": True}


def completed_with(*rows: dict) -> Handler:
    text = "```json\n" + json.dumps({"suppliers": list(rows)}) + "\n```"
    return scripted([run_state("completed", result={"text": text})])


@pytest.mark.parametrize("url, adapter", [
    ("https://www.webstaurantstore.com/", "webstaurant"),
    ("https://katom.com/cat/gloves", "katom"),
    ("https://shop.uline.com", "uline"),
    ("https://notkatom.com", None),
    ("https://katom.com.evil.example", None),
    ("https://westside.example", None),
])
def test_adapter_for_url(url, adapter):
    assert adapter_for_url(url) == adapter


async def test_domain_maps_only_to_a_registered_adapter(live_settings, db, decider, monkeypatch):
    # Not registered yet: KaTom is an unknown site for the LLM agent lane.
    disc = make_discovery(live_settings, db, decider, completed_with(KATOM))
    katom = (await disc.discover("packaging_disposables", "90024", None)).candidates[0]
    assert (katom.adapter, katom.selected, katom.reason) == (None, True, "unknown site: LLM agent lane")

    # Registered: the same CREAO row maps to the adapter lane by domain.
    monkeypatch.setitem(registry._ADAPTERS, "katom", lambda supplier: None)
    disc = make_discovery(live_settings, db, decider, completed_with(KATOM))
    katom = (await disc.discover("packaging_disposables", "90024", None)).candidates[0]
    assert (katom.adapter, katom.selected, katom.reason) == ("katom", True, "known adapter")


async def test_hidden_prices_are_filtered_before_j3(live_settings, db, decider, monkeypatch):
    judged: list[str] = []
    real_j3 = discovery.j3_supplier

    async def spy(decider_, cand, category, notes):
        judged.append(cand.name)
        return await real_j3(decider_, cand, category, notes)

    monkeypatch.setattr(discovery, "j3_supplier", spy)
    handler = scripted([run_state("completed", result={"text": FINAL_TEXT})])  # Harbor: pricesPublic false
    result = await make_discovery(live_settings, db, decider, handler).discover("packaging_disposables", "90024", None)

    by_name = {c.name: c for c in result.candidates}
    assert by_name["Harbor Paper Co"].reason == "filtered: prices not public"
    assert not by_name["Harbor Paper Co"].selected
    assert by_name["Westside Restaurant Supply"].selected
    assert judged == ["Westside Restaurant Supply"]  # no Jev call for the filtered supplier


def test_missing_prices_flag_is_not_hidden():
    assert not discovery._prices_hidden({"url": "https://a.example"})
    assert discovery._prices_hidden({"url": "https://a.example", "prices_public": False})


def test_saved_real_run_parses():
    run_id, rows, age = discovery._load_seed("packaging_disposables", "49007")
    assert run_id.startswith("13698a75") and age is not None and age > 0
    assert len(rows) == 10 and all(r["url"].startswith("https://") for r in rows)
    assert discovery._load_seed("packaging_disposables", "90024") is None


async def test_live_mode_falls_back_to_saved_real_run(live_settings, db, decider):
    live = live_settings.model_copy(update={"run_mode": "live"})
    disc = make_discovery(live, db, decider, scripted([run_state("failed", errorCode="INSUFFICIENT_CREDITS")]))

    result = await disc.discover("packaging_disposables", "49007", None)

    assert result.source == "fallback" and result.age_s is not None
    assert len(result.candidates) == 10
    assert all(c.creao_run_id and c.creao_run_id.startswith("13698a75") for c in result.candidates)
    assert not any("(fixture)" in c.name for c in result.candidates)
    hidden = {c.name for c in result.candidates if c.reason == "filtered: prices not public"}
    assert {"KSS Enterprises", "Highland Supply"} <= hidden
    # Wees Express maps to its adapter by domain once that adapter is registered; the rest
    # of the saved run has no adapter and stays an unknown site for the LLM agent lane.
    known = registry.registered()["adapters"]
    by_name = {c.name: c for c in result.candidates}
    assert by_name["Wees Express"].adapter == ("wees_express" if "wees_express" in known else None)
    assert all(c.adapter is None for c in result.candidates if c.name != "Wees Express")


async def test_fixture_mode_keeps_the_fake_list(settings, db, decider):
    modes = RuntimeModes(settings.initial_modes())
    result = await Discovery(settings, db, modes, decider).discover("packaging_disposables", "49007", None)

    assert result.source == "fixture"
    assert {c.id for c in result.candidates} == {"alpha", "bravo", "charlie", "delta", "echo"}


# ---------------------------------------------------------------- lane checks and approved sites


async def test_cached_browsers_skip_creao_sites_and_add_the_fixture_suppliers(live_settings, db, decider):
    # Cached browsers can only replay fixtures, so a real CREAO site gets no lane and the
    # fixture suppliers fill the adapter slots. The job still gets prices.
    disc = make_discovery(live_settings, db, decider, completed_with(KATOM), browser="cached")
    result = await disc.discover("packaging_disposables", "90024", None)

    katom = result.candidates[0]
    assert (katom.selected, katom.reason) == (False, "no lane in cached browser mode")
    added = [c for c in result.candidates if c.reason == discovery.APPROVED_REASON]
    assert [c.id for c in added] == ["alpha", "bravo", "charlie"]
    assert all(c.selected for c in added)
    refs = discovery.selected_suppliers(result)
    assert [(r.id, r.origin) for r in refs] == [("alpha", "fixture"), ("bravo", "fixture"), ("charlie", "fixture")]


async def test_approved_site_fills_a_free_slot_once_its_adapter_is_registered(live_settings, db, decider,
                                                                               monkeypatch):
    monkeypatch.setitem(registry._ADAPTERS, "uline", lambda supplier: None)
    disc = make_discovery(live_settings, db, decider, completed_with(KATOM))
    result = await disc.discover("packaging_disposables", "90024", None)

    by_id = {c.id: c for c in result.candidates}
    assert by_id["katom-restaurant-supply"].reason == "unknown site: LLM agent lane"
    assert (by_id["uline"].selected, by_id["uline"].reason) == (True, discovery.APPROVED_REASON)
    assert "webstaurant" not in by_id  # not registered: never added
    refs = {r.id: r.origin for r in discovery.selected_suppliers(result)}
    assert refs == {"katom-restaurant-supply": "creao", "uline": "manual"}


async def test_approved_site_is_not_added_twice_when_creao_returns_it(live_settings, db, decider, monkeypatch):
    monkeypatch.setitem(registry._ADAPTERS, "katom", lambda supplier: None)
    disc = make_discovery(live_settings, db, decider, completed_with(KATOM))
    result = await disc.discover("packaging_disposables", "90024", None)

    katoms = [c for c in result.candidates if c.adapter == "katom"]
    assert [(c.id, c.reason) for c in katoms] == [("katom-restaurant-supply", "known adapter")]


async def test_eight_lanes_and_the_agent_slot_is_kept(live_settings, db, decider, monkeypatch):
    # Every approved site registered: approved sites fill the slots up to MAX_LANES, and the
    # unknown site keeps its agent lane though it comes after the adapter sites.
    for site in json.loads(discovery.APPROVED_SITES.read_text())["sites"]:
        monkeypatch.setitem(registry._ADAPTERS, site["adapter"], lambda supplier: None)
    unknown = {**KATOM, "name": "Mystery Supply", "url": "https://mystery-supply.example"}
    disc = make_discovery(live_settings, db, decider, completed_with(KATOM, unknown))
    result = await disc.discover("packaging_disposables", "90024", None)

    selected = [c for c in result.candidates if c.selected]
    assert len(selected) == discovery.MAX_LANES == 8
    assert [c.adapter for c in selected].count(None) == 1
    assert next(c for c in selected if c.adapter is None).name == "Mystery Supply"


async def test_fixture_mode_selection_is_unchanged(settings, db, decider):
    disc = Discovery(settings, db, RuntimeModes(settings.initial_modes()), decider)
    result = await disc.discover("packaging_disposables", "49007", None)

    assert [c.id for c in result.candidates if c.selected] == ["alpha", "bravo", "charlie", "delta"]
    assert not any(c.reason == discovery.APPROVED_REASON for c in result.candidates)


async def test_model_output_with_odd_field_types_does_not_break_discovery(live_settings, db, decider):
    """Soak S-015: CREAO rows come from model output. An int id or phone raised a pydantic
    ValidationError after the rows were cached, so every later draft failed until the cache expired."""
    odd = {"suppliers": [
        {"id": 7, "name": "Seven Supply", "url": "https://seven.example", "phone": 3105550100, "pricesPublic": True},
        {"name": ["not", "a", "name"], "url": "https://list.example", "email": {"x": 1}},
        {"name": "Fine Co", "url": "https://fine.example", "adapter": ["webstaurant"]},
    ]}
    text = "```json\n" + json.dumps(odd) + "\n```"
    disc = make_discovery(live_settings, db, decider,
                          scripted([run_state("completed", result={"text": text, "artifacts": []})]))
    result = await disc.discover("packaging_disposables", "90024", None)
    by_url = {c.url: c for c in result.candidates}
    assert set(by_url) == {"https://seven.example", "https://list.example", "https://fine.example"}
    assert by_url["https://seven.example"].id == "7" and by_url["https://seven.example"].phone == "3105550100"
    assert all(isinstance(c.name, str) and c.name for c in result.candidates)
    again = await disc.discover("packaging_disposables", "90024", None)  # the cached rows work too
    assert len(again.candidates) == 3


def test_seed_path_stays_in_the_seed_folder(tmp_path, monkeypatch):
    """Soak S-027 (security): GET /suppliers?category=../..&zip=... picked the seed file by joining the
    raw query values, so live mode read any '*-<zip>.json' file and returned its rows."""
    secret = tmp_path / "outside-1.json"
    secret.write_text(json.dumps({"suppliers": [{"name": "Leak", "url": "https://leak.example"}]}))
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    monkeypatch.setattr(discovery, "CREAO_SEED_DIR", seeds)
    assert discovery._load_seed("../outside", "1") is None
    assert discovery._load_seed("x" * 5000, "1") is None  # no OSError for a long name
    (seeds / "packaging_disposables-49007.json").write_text(json.dumps(
        {"suppliers": [{"name": "Real", "url": "https://real.example"}]}))
    assert discovery._load_seed("packaging_disposables", "49007")[1][0]["name"] == "Real"
