from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "functions"))

import keep_warm  # noqa: E402 - needs the path above


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _clock(*ticks: float):
    values = iter(ticks)
    return lambda: next(values)


def test_health_url_defaults_to_the_deployed_console_and_can_be_overridden() -> None:
    assert keep_warm.health_url({}) == "https://siteops-console-coxh2t.azurewebsites.net/api/health"
    assert keep_warm.health_url({"CONSOLE_URL": "https://example.test/"}) == "https://example.test/api/health"


def test_ping_reports_status_and_duration_with_a_generous_timeout() -> None:
    seen: dict = {}

    def opener(url, timeout):
        seen.update(url=url, timeout=timeout)
        return _Response(200)

    status, seconds = keep_warm.ping_console({}, opener=opener, clock=_clock(10.0, 10.4))
    assert (status, seconds) == (200, pytest.approx(0.4))
    assert seen == {"url": "https://siteops-console-coxh2t.azurewebsites.net/api/health", "timeout": 90}


def test_ping_returns_the_http_error_code() -> None:
    def opener(url, timeout):
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", None, None)

    status, _ = keep_warm.ping_console({}, opener=opener, clock=_clock(0.0, 1.0))
    assert status == 503


def test_ping_returns_zero_when_nothing_answers() -> None:
    def opener(url, timeout):
        raise urllib.error.URLError("timed out")

    assert keep_warm.ping_console({}, opener=opener, clock=_clock(0.0, 90.0)) == (0, 90.0)
