"""One browser per lane, for the whole job (spec §5, review C3).

Both modes give a CDP URL. Playwright (adapter steps, snapshots, screenshots) and the
Browser Use agent (unknown sites) attach to the same browser through it, so the guest
cart lives in one browser from search to the payment stop.

- local: this module starts Playwright's Chromium with a remote-debugging port.
- cloud: this module creates a Browser Use cloud browser with the v2 REST API itself,
  because `Browser(use_cloud=True)` only logs the live URL and does not keep it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import socket
import tempfile
from typing import TYPE_CHECKING, Any

import httpx
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from app.contracts import PageInput, PageSnapshot
from app.lanes.live.config import LiveLaneSettings

if TYPE_CHECKING:
    from app.lanes.base import LaneContext

log = logging.getLogger(__name__)

# Runs in the page. Returns visible text and every form field with the attributes the guard reads.
SNAPSHOT_JS = r"""
() => {
  const visible = (el) => {
    const s = window.getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  };
  const labelOf = (el) => {
    if (el.labels && el.labels.length) return Array.from(el.labels).map(l => l.innerText).join(' ').trim();
    const aria = el.getAttribute('aria-label');
    if (aria) return aria;
    const by = el.getAttribute('aria-labelledby');
    if (by) { const t = document.getElementById(by); if (t) return t.innerText.trim(); }
    return el.getAttribute('placeholder') || null;
  };
  const inputs = [];
  for (const el of document.querySelectorAll('input, select, textarea')) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (type === 'hidden') continue;
    inputs.push({
      name: el.getAttribute('name'), id: el.id || null, type,
      autocomplete: el.getAttribute('autocomplete'), label: labelOf(el), visible: visible(el),
    });
  }
  const frames = [];
  for (const f of document.querySelectorAll('iframe')) {
    frames.push({name: f.getAttribute('name'), title: f.getAttribute('title'), src: f.getAttribute('src') || ''});
  }
  return {url: location.href, text: (document.body ? document.body.innerText : ''), inputs, frames};
}
"""

# Payment fields often live in a third-party iframe (Stripe, Braintree, Adyen...).
PAYMENT_FRAME_HINTS = ("card", "payment", "stripe", "braintree", "adyen", "checkout.com", "paypal", "cvv", "cvc")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _clean_text(text: str, limit: int) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    out = "\n".join(line for line in lines if line)
    return out[:limit]


def _inputs_from(raw: list[dict[str, Any]]) -> list[PageInput]:
    # Keep invisible fields too: a card field that is hidden now can appear on the next click.
    return [
        PageInput(name=i.get("name"), id=i.get("id"), type=i.get("type"),
                  autocomplete=i.get("autocomplete"), label=i.get("label"))
        for i in raw
    ]


def _frame_inputs(frames: list[dict[str, Any]]) -> list[PageInput]:
    out: list[PageInput] = []
    for f in frames:
        blob = " ".join(str(f.get(k) or "") for k in ("name", "title", "src")).lower()
        if any(h in blob for h in PAYMENT_FRAME_HINTS):
            # The label matches the guard's card-field pattern on purpose: fail closed.
            out.append(PageInput(name=f.get("name"), type="iframe",
                                 label=f"card number frame: {f.get('title') or f.get('src', '')[:80]}"))
    return out


class LaneBrowser:
    """A browser session that one lane keeps from open() to close()."""

    def __init__(self, cfg: LiveLaneSettings, label: str = "lane"):
        self.cfg = cfg
        self.label = label
        self.cdp_url: str | None = None
        self.live_url: str | None = None
        self.session_id: str | None = None
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._user_dir: str | None = None
        self._cloud_id: str | None = None
        self._page: Page | None = None

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if self._browser is not None:
            return
        self._pw = await async_playwright().start()
        if self.cfg.browser_mode == "cloud":
            await self._start_cloud()
        else:
            await self._start_local()
        assert self.cdp_url
        self._browser = await self._pw.chromium.connect_over_cdp(self.cdp_url)
        self._context = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()

    async def _start_local(self) -> None:
        assert self._pw is not None
        exe = self.cfg.browser_executable or self._pw.chromium.executable_path
        port = _free_port()
        self._user_dir = tempfile.mkdtemp(prefix=f"lane-{self.label}-")
        # --no-proxy-server: a macOS system proxy can block 127.0.0.1 test sites (seen in dev).
        args = [
            f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={self._user_dir}", "--no-first-run", "--no-default-browser-check",
            "--disable-features=Translate", "--window-size=1280,900", "--no-proxy-server",
        ]
        if self.cfg.browser_headless:
            args.append("--headless=new")
        self._proc = await asyncio.create_subprocess_exec(
            exe, *args, "about:blank",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        self.cdp_url = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(timeout=1.0) as client:
            for _ in range(100):
                try:
                    r = await client.get(f"{self.cdp_url}/json/version")
                    if r.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if self._proc.returncode is not None:
                    raise RuntimeError(f"local browser exited with code {self._proc.returncode}")
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("local browser did not open its debugging port")
        self.session_id = f"local-{self.label}-{port}"
        self.live_url = None  # a local headless browser has no shareable live view

    async def _start_cloud(self) -> None:
        if self.cfg.browser_use_api_key is None:
            raise RuntimeError("BROWSER_MODE=cloud needs BROWSER_USE_API_KEY")
        body: dict[str, Any] = {"timeout": self.cfg.cloud_timeout_min}
        if self.cfg.cloud_proxy_country_code:
            body["proxy_country_code"] = self.cfg.cloud_proxy_country_code
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(f"{self.cfg.browser_use_api_base}/api/v2/browsers",
                                  headers=self._cloud_headers(), json=body)
        if r.status_code in (401, 403):
            raise RuntimeError(f"browser use cloud refused the key or plan (HTTP {r.status_code})")
        r.raise_for_status()
        data = r.json()
        self._cloud_id = data["id"]
        self.session_id = data["id"]
        self.cdp_url = data["cdpUrl"]
        self.live_url = data.get("liveUrl") or None

    def _cloud_headers(self) -> dict[str, str]:
        assert self.cfg.browser_use_api_key is not None
        return {"X-Browser-Use-API-Key": self.cfg.browser_use_api_key.get_secret_value(),
                "Content-Type": "application/json"}

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            if self._browser is not None:
                await self._browser.close()  # for connect_over_cdp this only disconnects
        with contextlib.suppress(Exception):
            if self._pw is not None:
                await self._pw.stop()
        if self._cloud_id:
            with contextlib.suppress(Exception):
                async with httpx.AsyncClient(timeout=15.0) as client:
                    await client.patch(f"{self.cfg.browser_use_api_base}/api/v2/browsers/{self._cloud_id}",
                                       headers=self._cloud_headers(), json={"action": "stop"})
        if self._proc is not None and self._proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    self._proc.kill()
        if self._user_dir:
            shutil.rmtree(self._user_dir, ignore_errors=True)
        self._browser = self._pw = self._context = self._page = None
        self._proc = None

    # ------------------------------------------------------------ pages

    @property
    def context(self) -> BrowserContext:
        assert self._context is not None, "LaneBrowser.start() was not called"
        return self._context

    async def page(self, prefer_url: str | None = None) -> Page:
        """The page the lane works on. With prefer_url, the open tab that shows that URL."""
        pages = [p for p in self.context.pages if not p.is_closed()]
        if prefer_url:
            for p in reversed(pages):
                if p.url == prefer_url:
                    self._page = p
                    return p
        if self._page is not None and not self._page.is_closed():
            return self._page
        self._page = pages[-1] if pages else await self.context.new_page()
        return self._page

    async def goto(self, url: str) -> Page:
        page = await self.page()
        await page.goto(url, wait_until="domcontentloaded")
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=3000)
        return page

    async def snapshot(self, page: Page | None = None) -> PageSnapshot:
        page = page or await self.page()
        data = await page.evaluate(SNAPSHOT_JS)
        inputs = _inputs_from(data.get("inputs", []))
        inputs += _frame_inputs(data.get("frames", []))
        # Same-origin and cross-origin child frames: read their fields too (CDP allows it).
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            with contextlib.suppress(Exception):
                sub = await frame.evaluate(SNAPSHOT_JS)
                inputs += _inputs_from(sub.get("inputs", []))
        return PageSnapshot(url=data.get("url") or page.url,
                            visible_text=_clean_text(data.get("text", ""), self.cfg.snapshot_text_chars),
                            inputs=inputs)

    async def text(self, selector: str, page: Page | None = None) -> str:
        page = page or await self.page()
        loc = page.locator(selector)
        if await loc.count() == 0:
            return ""
        return _clean_text(await loc.first.inner_text(), self.cfg.snapshot_text_chars)

    async def screenshot(self, ctx: LaneContext, name: str, page: Page | None = None) -> str | None:
        """Write a PNG to the lane's evidence folder and emit it. Returns the public URL."""
        page = page or await self.page()
        path, rel = ctx.evidence(name)
        try:
            await page.screenshot(path=str(path), full_page=False, timeout=10000)
        except Exception as exc:  # evidence must never break the lane
            log.warning("screenshot failed: %s", exc)
            return None
        await ctx.emit("screenshot", {"url": rel})
        return rel

    def write_evidence(self, ctx: LaneContext, name: str, payload: Any) -> str:
        path, rel = ctx.evidence(name)
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload, indent=2, default=str))
        return rel

    # ------------------------------------------------------------ Browser Use

    def browser_use_session(self) -> Any:
        """A new Browser Use session object attached to this browser.

        keep_alive=True: the agent never closes the browser; the lane does that in close().
        A new object per agent run is safe because the cart lives in the browser, not here.
        """
        from browser_use import Browser as BUBrowser

        assert self.cdp_url
        return BUBrowser(cdp_url=self.cdp_url, keep_alive=True)
