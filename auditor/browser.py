from __future__ import annotations

import os
from typing import Optional

from playwright.async_api import Browser, Playwright


def cloud_proxy_settings() -> Optional[dict]:
    """Proxy Chromium needs to reach the outside world from the Claude Code cloud sandbox.

    On the end user's own machine CLAUDE_CODE_REMOTE is never set, so this is a no-op there
    and Chromium launches with its normal, direct network access.
    """
    if os.environ.get("CLAUDE_CODE_REMOTE") != "true":
        return None
    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if not proxy_url:
        return None
    return {"server": proxy_url, "bypass": "localhost,127.0.0.1,::1"}


async def launch_browser(playwright: Playwright, **kwargs) -> Browser:
    proxy = cloud_proxy_settings()
    if proxy is not None:
        kwargs.setdefault("proxy", proxy)
    return await playwright.chromium.launch(**kwargs)
