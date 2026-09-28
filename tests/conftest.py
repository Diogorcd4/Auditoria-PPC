from __future__ import annotations

import functools
import http.server
import os
import threading
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from auditor.browser import launch_browser


def _find_sandbox_chromium() -> str | None:
    """Pin the Chromium build already installed in this dev container.

    Only relevant here: on a contributor's own machine (no PLAYWRIGHT_BROWSERS_PATH override,
    or one whose browsers actually match the installed `playwright` package) this returns
    None and Playwright resolves its own browser as usual, exactly like `playwright install`
    documents.
    """
    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not browsers_path:
        return None
    base = Path(browsers_path)
    if not base.is_dir():
        return None
    for entry in sorted(base.glob("chromium-*")):
        candidate = entry / "chrome-linux" / "chrome"
        if candidate.exists():
            return str(candidate)
    return None


_SANDBOX_CHROMIUM = _find_sandbox_chromium()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - matches base class signature
        pass


@pytest.fixture
def make_site_server():
    """Factory fixture: make_site_server(directory) -> "http://127.0.0.1:<port>".

    Serves static files from `directory` over real HTTP on localhost only, so crawler code
    that fetches robots.txt/sitemap.xml with httpx (not through Playwright) still works
    without any real network access. Every server started is shut down after the test.
    """
    servers: list[http.server.ThreadingHTTPServer] = []

    def _make(directory: Path) -> str:
        handler_cls = functools.partial(_QuietHandler, directory=str(directory))
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        servers.append(httpd)
        port = httpd.server_address[1]
        return f"http://127.0.0.1:{port}"

    yield _make

    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
async def browser():
    async with async_playwright() as pw:
        kwargs = {"headless": True}
        if _SANDBOX_CHROMIUM:
            kwargs["executable_path"] = _SANDBOX_CHROMIUM
        b = await launch_browser(pw, **kwargs)
        yield b
        await b.close()


@pytest.fixture
async def page(browser):
    p = await browser.new_page()
    yield p
    await p.close()
