"""Site preflight (spec gate H0): can a lane search this supplier site as a guest?

For each site: open the home page, search with the site's own search box, and report
the HTTP status, a bot or login wall, how many prices the results show, and the search URL
the site used (adapters build that URL directly). A site passes when the search left the
home page, the results mention the query and show at least 3 prices, and no wall appeared.
It saves the results page HTML and a screenshot under var/preflight/<site>/ for writing
adapter selectors.

    PYTHONPATH=. uv run python scripts/site_preflight.py                  # default candidates
    PYTHONPATH=. uv run python scripts/site_preflight.py --query "kraft paper bag" katom.com
    PYTHONPATH=. uv run python scripts/site_preflight.py "https://www.uline.com/Search?keywords=nitrile+gloves"

A full URL is loaded as the search page itself (no home page, no search box).
Read-only: it never adds to a cart, signs in, or submits anything but a site search.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from dataclasses import asdict, dataclass, field

import httpx
from playwright.async_api import Locator, Page

from app.config import BACKEND_DIR
from app.lanes.live.config import get_live_settings
from app.lanes.live.session import LaneBrowser

CANDIDATES = [
    "webstaurantstore.com", "katom.com", "restaurantware.com", "wasserstrom.com", "uline.com",
    "gfsstore.com", "mrtakeoutbags.com", "togopackaging.com", "ecoqualityinc.com",
    "goodstartpackaging.com", "wees-express.com",
]
SEARCH_INPUTS = [
    "input[type=search]", "input[name=q]", "input[name=query]", "input[name=searchval]",
    "input[name=search]", "input[name=keyword]", "input[name=keywords]", "input[name=s]",
    "input[id*=search i]", "input[placeholder*=search i]", "input[aria-label*=search i]",
]
BOT_WALL = re.compile(
    r"captcha|are you a (human|robot)|verify (that )?you are (a )?human|access denied|just a moment|"
    r"pardon our interruption|request unsuccessful|unusual traffic|blocked|press (&|and) hold|cf-chl",
    re.I,
)
LOGIN_WALL = re.compile(r"(sign|log) ?in to (see|view) (the )?price|login for pric|price(s)? (shown|available) after", re.I)
PRICE = re.compile(r"\$\s?\d{1,4}(?:,\d{3})*\.\d{2}")
PRODUCT_HREF = re.compile(r"/(p|product|products|item)/|\.html$|-\d{4,}")
PARALLEL = 3  # more browsers at once slow every page into timeouts
SITE_TIMEOUT_S = 90  # one slow site must not hold up the run
OUT = BACKEND_DIR / "var" / "preflight"


@dataclass
class SiteReport:
    site: str
    ok: bool = False
    home_status: int | None = None
    search_url: str | None = None
    title: str = ""
    prices: int = 0
    query_mentions: int = 0
    product_links: int = 0
    wall: str | None = None
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)


async def check(target: str, query: str) -> SiteReport:
    rep = SiteReport(site=target)
    start = time.monotonic()
    browser = LaneBrowser(get_live_settings(), label="preflight")
    try:
        await asyncio.wait_for(_check(browser, target, query, rep), SITE_TIMEOUT_S)
    except TimeoutError:
        rep.notes.append(f"timed out after {SITE_TIMEOUT_S} s")
    except Exception as exc:  # report every failure as a row, never stop the run
        rep.notes.append(f"error: {type(exc).__name__}: {str(exc).splitlines()[0][:120]}")
    finally:
        rep.seconds = round(time.monotonic() - start, 1)
        await browser.close()
    return rep


async def _check(browser: LaneBrowser, target: str, query: str, rep: SiteReport) -> None:
    direct = target.startswith("http")
    site = (httpx.URL(target).host or "").removeprefix("www.") if direct else target
    start_url = target if direct else f"https://www.{site}/"
    out = OUT / site
    out.mkdir(parents=True, exist_ok=True)
    await browser.start()
    page = browser._page
    assert page is not None
    resp = await page.goto(start_url, wait_until="domcontentloaded", timeout=45000)
    rep.home_status = resp.status if resp else None
    await page.wait_for_timeout(2500)
    if not direct:
        if wall := _wall(await _text(page)):
            rep.wall = f"home: {wall}"
            return
        box = await _search_box(page, rep)
        if box is None:
            rep.notes.append("no visible search box")
            return
        await page.keyboard.press("Escape")  # close a newsletter or cookie popup if one is open
        await box.fill(query, force=True, timeout=10000)  # fill needs no click, so overlays do not block it
        await box.press("Enter")
        await page.wait_for_load_state("domcontentloaded", timeout=30000)
    text = await _wait_for_prices(page)
    rep.search_url = page.url
    rep.title = (await page.title())[:80]
    rep.prices = len(PRICE.findall(text))
    word = query.split()[-1].rstrip("s")
    rep.query_mentions = len(re.findall(re.escape(word), text, re.I))
    hrefs = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    rep.product_links = len({h for h in hrefs if PRODUCT_HREF.search(h)})
    searched = direct or page.url.rstrip("/") != start_url.rstrip("/")
    if not searched:
        rep.notes.append("search did not leave the home page")
    if wall := _wall(text):
        rep.wall = f"search: {wall}"
    elif LOGIN_WALL.search(text):
        rep.wall = "search: login for prices"
    (out / "search.html").write_text(await page.content())
    await page.screenshot(path=str(out / "search.png"))
    rep.ok = rep.wall is None and searched and rep.prices >= 3 and rep.query_mentions >= 3


async def _search_box(page: Page, rep: SiteReport) -> Locator | None:
    for sel in SEARCH_INPUTS:
        loc = page.locator(sel)
        for i in range(await loc.count()):
            if await loc.nth(i).is_visible():
                rep.notes.append(f"search box: {sel}")
                return loc.nth(i)
    return None


async def _wait_for_prices(page: Page) -> str:
    text = ""
    for _ in range(15):  # results often render late with JavaScript
        await page.wait_for_timeout(1000)
        text = await _text(page)
        if len(PRICE.findall(text)) >= 3:
            break
    return text


async def _text(page: Page) -> str:
    try:
        return await page.inner_text("body", timeout=5000)
    except Exception:
        return ""


def _wall(text: str) -> str | None:
    m = BOT_WALL.search(text[:5000])  # walls are short pages; long pages mention "blocked" in prose
    return m.group(0) if m and len(text) < 5000 else None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sites", nargs="*", default=CANDIDATES)
    ap.add_argument("--query", default="nitrile gloves")
    args = ap.parse_args()
    sem = asyncio.Semaphore(PARALLEL)

    async def one(site: str) -> SiteReport:
        async with sem:
            return await check(site, args.query)

    reports = await asyncio.gather(*(one(s) for s in args.sites))
    for r in sorted(reports, key=lambda r: (not r.ok, -r.prices)):
        mark = "PASS" if r.ok else "FAIL"
        print(f"{mark} {r.site[:40]:40} status={r.home_status} prices={r.prices:3} mentions={r.query_mentions:3} "
              f"{r.seconds:5}s wall={r.wall or '-'} | {r.search_url or ''} | {'; '.join(r.notes)}", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps([asdict(r) for r in reports], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
