"""Serve the local static test sites (backend/tests/sites) on 127.0.0.1 for tests and the H3 gate."""

from __future__ import annotations

import contextlib
import functools
import threading
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.config import BACKEND_DIR

SITES_DIR = BACKEND_DIR / "tests" / "sites"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return None


@contextlib.contextmanager
def serve_site(name: str = "example_site") -> Iterator[str]:
    """Yield the base URL of a local test site. Binds to 127.0.0.1 only."""
    root = Path(SITES_DIR / name)
    handler = functools.partial(_QuietHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()
