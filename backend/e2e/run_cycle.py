"""Run one soak cycle against a fresh fixture-mode server.

    uv run python -m e2e.run_cycle --out <dir> [--port 8100] [--only name,name] [--time-scale 0.1]

The server runs from this checkout on its own port with its own VAR_DIR, so it never touches the
live server on port 8000. Every dependency stays cached: the harness refuses to set a live mode.
The report (report.json) lists each scenario's failures plus any traceback in the server log.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

from e2e import scenarios
from e2e.scenarios import Ctx

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent


def _dotenv(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            name, sep, value = line.partition("=")
            if sep and not name.lstrip().startswith("#"):
                env[name.strip()] = value.strip().strip('"').strip("'")
    return env


def start_server(port: int, var_dir: Path, log_path: Path, extra_env: dict[str, str],
                 append: bool = False) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV",)}
    env.update(_dotenv(REPO_DIR / ".env"))
    env.update({
        "RUN_MODE": "fixture",  # every dependency cached; never live from this harness
        "VAR_DIR": str(var_dir),
        "CORS_ORIGINS": '["http://localhost:3000"]',
        **extra_env,
    })
    log = log_path.open("a" if append else "w")
    return subprocess.Popen(
        ["uv", "run", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=BACKEND_DIR, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )


def stop_server(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    os.killpg(proc.pid, signal.SIGTERM)  # our own process group only
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)


async def wait_healthy(base: str, timeout: float = 60) -> None:
    end = time.monotonic() + timeout
    async with httpx.AsyncClient(base_url=base, timeout=2) as c:
        while time.monotonic() < end:
            try:
                if (await c.get("/health")).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.3)
    raise RuntimeError("server did not become healthy")


async def run_all(ctx: Ctx, only: set[str] | None, timeout: float, results: list[dict]) -> None:
    """Append each scenario's result to `results` as it ends, so a killed run keeps what it did."""
    for sc in scenarios.SCENARIOS:
        if only and sc.name not in only:
            continue
        ctx.failures = []
        started = time.monotonic()
        try:
            await asyncio.wait_for(sc.fn(ctx), timeout)
        except AssertionError as exc:
            ctx.fail(f"assertion: {exc}")
        except TimeoutError:
            ctx.fail(f"scenario timed out after {timeout}s")
        except Exception as exc:  # noqa: BLE001 - a harness crash is a finding too
            ctx.fail(f"harness error: {type(exc).__name__}: {exc}")
        results.append({"name": sc.name, "area": sc.area, "seconds": round(time.monotonic() - started, 2),
                        "ok": not ctx.failures, "failures": ctx.failures})
        print(f"[{results[-1]['seconds']:7.1f}s] {'ok  ' if not ctx.failures else 'FAIL'} {sc.name}", flush=True)


def scan_log(log_path: Path) -> list[str]:
    text = log_path.read_text(errors="replace")
    hits: list[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("Traceback (most recent call last)"):
            # Report the traceback's last line (the exception) and the frame above it.
            j = i + 1
            while j < len(lines) and (lines[j].startswith(" ") or lines[j].startswith("Traceback")):
                j += 1
            hits.append("\n".join(lines[max(i, j - 3): j + 1]))
        elif re.match(r"^(ERROR|CRITICAL)[: ]", line):
            hits.append(line)
    return hits


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--only", default="")
    ap.add_argument("--scenario-timeout", type=float, default=180)
    ap.add_argument("--time-scale", default="0.1", help="FIXTURE_TIME_SCALE; 1.0 is real time (finds races)")
    args = ap.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 - CLI setup before the loop matters
    var_dir = out / "var"
    log_path = out / "server.log"
    env = _dotenv(REPO_DIR / ".env")
    server_env = {
        "FIXTURE_TIME_SCALE": args.time_scale,
        "SENTRY_RELEASE": f"soak-{out.name}",  # the SDK reads it; filter Sentry with release:soak-*
    }
    proc = start_server(args.port, var_dir, log_path, server_env)
    base = f"http://127.0.0.1:{args.port}"
    started = time.time()
    results: list[dict] = []
    try:
        await wait_healthy(base)
        async with httpx.AsyncClient(base_url=base, timeout=30) as client:
            async def restart() -> None:
                nonlocal proc
                stop_server(proc)
                with log_path.open("a") as log:
                    log.write("\n===== soak restart =====\n")
                proc = start_server(args.port, var_dir, log_path, server_env, append=True)
                await wait_healthy(base)

            ctx = Ctx(client=client, base=base, admin_token=env.get("ADMIN_TOKEN", "dev-admin"),
                      webhook_secret=env.get("WEBHOOK_SECRET", "dev-webhook"),
                      restock_token=env.get("RESTOCK_TOKEN"), port=args.port, restart=restart)
            only = {s for s in args.only.split(",") if s} or None
            await run_all(ctx, only, args.scenario_timeout, results)
    finally:
        stop_server(proc)
        write_report(out, started, results, log_path, proc)
    return 1 if any(not r["ok"] for r in results) or scan_log(log_path) else 0


def write_report(out: Path, started: float, results: list[dict], log_path: Path, proc: subprocess.Popen) -> None:
    report = {
        "started": started, "seconds": round(time.time() - started, 1),
        "scenarios": results,
        "failed": [r["name"] for r in results if not r["ok"]],
        "server_log_errors": scan_log(log_path),
        "server_exit": proc.returncode,
    }
    (out / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"failed": report["failed"], "log_errors": len(report["server_log_errors"]),
                      "scenarios": len(results)}, indent=2))
    for r in results:
        for f in r["failures"]:
            print(f"FAIL {r['name']}: {f}")
    for e in report["server_log_errors"][:20]:
        print("LOG", e.replace("\n", " | ")[:400])


def _interrupt(signum, frame):  # noqa: ARG001
    raise KeyboardInterrupt  # asyncio.run cancels main(), whose finally stops the server


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _interrupt)
    sys.exit(asyncio.run(main()))
