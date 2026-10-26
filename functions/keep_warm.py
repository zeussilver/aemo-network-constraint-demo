"""Keep the console's App Service F1 app loaded.

F1 has no Always On: the platform unloads an app after about 20 idle minutes, and the
next request then waits up to a minute while it starts. A request to /api/health every
10 minutes (the timer in function_app.py) keeps it loaded. /api/health does not touch
the database, so the ping costs the web app almost nothing.
"""
from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from typing import Callable, Mapping

# The deployed console. CONSOLE_URL overrides it without a code change.
DEFAULT_CONSOLE_URL = "https://siteops-console-coxh2t.azurewebsites.net"
TIMEOUT_SECONDS = 90  # a cold start has taken about 60 s


def health_url(env: Mapping[str, str] = os.environ) -> str:
    return env.get("CONSOLE_URL", DEFAULT_CONSOLE_URL).rstrip("/") + "/api/health"


def ping_console(
    env: Mapping[str, str] = os.environ,
    opener: Callable = urllib.request.urlopen,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[int, float]:
    """Request /api/health once. Returns (HTTP status, seconds); status 0 if no response."""
    start = clock()
    try:
        with opener(health_url(env), timeout=TIMEOUT_SECONDS) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
    except (urllib.error.URLError, TimeoutError):
        status = 0
    return status, clock() - start
