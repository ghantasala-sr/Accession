from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from accession.edgar.client import EdgarClient, EdgarError, RateLimiter
from accession.storage import LocalStore

URL = "https://data.sec.gov/submissions/CIK0001318605.json"
UA = "Test User test@example.com"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(tmp_path: Path, sleeps: list[float] | None = None) -> EdgarClient:
    record = sleeps if sleeps is not None else []
    return EdgarClient(UA, LocalStore(tmp_path), max_requests_per_second=10, sleep=record.append)


def test_rate_limiter_spaces_back_to_back_calls() -> None:
    clock = FakeClock()
    limiter = RateLimiter(10, clock=clock, sleep=clock.sleep)
    for _ in range(5):
        limiter.acquire()
    assert clock.sleeps == pytest.approx([0.1, 0.1, 0.1, 0.1])


def test_rate_limiter_does_not_sleep_after_idle_gap() -> None:
    clock = FakeClock()
    limiter = RateLimiter(10, clock=clock, sleep=clock.sleep)
    limiter.acquire()
    clock.now += 5
    limiter.acquire()
    assert clock.sleeps == []


@respx.mock
def test_sends_declared_user_agent(tmp_path: Path) -> None:
    route = respx.get(URL).respond(200, json={"ok": True})
    make_client(tmp_path).get_json(URL, "raw/x.json")
    assert route.calls.last.request.headers["User-Agent"] == UA


@respx.mock
def test_cache_hit_skips_network(tmp_path: Path) -> None:
    route = respx.get(URL).respond(200, json={"n": 1})
    client = make_client(tmp_path)
    assert client.get_json(URL, "raw/x.json") == {"n": 1}
    assert client.get_json(URL, "raw/x.json") == {"n": 1}
    assert route.call_count == 1
    assert (tmp_path / "raw/x.json").is_file()


@respx.mock
def test_refresh_refetches(tmp_path: Path) -> None:
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(200, json={"n": 1}), httpx.Response(200, json={"n": 2})]
    )
    client = make_client(tmp_path)
    client.get_json(URL, "raw/x.json")
    assert client.get_json(URL, "raw/x.json", refresh=True) == {"n": 2}
    assert route.call_count == 2


@respx.mock
def test_retries_429_honouring_retry_after(tmp_path: Path) -> None:
    respx.get(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(503),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    sleeps: list[float] = []
    assert make_client(tmp_path, sleeps).get_json(URL, "raw/x.json") == {"ok": True}
    backoffs = [s for s in sleeps if s >= 1]
    assert backoffs == [7.0, 2.0]


@respx.mock
def test_non_retryable_status_raises_and_caches_nothing(tmp_path: Path) -> None:
    respx.get(URL).respond(404)
    with pytest.raises(EdgarError, match="404"):
        make_client(tmp_path).get_json(URL, "raw/x.json")
    assert not (tmp_path / "raw/x.json").exists()


@respx.mock
def test_gives_up_after_max_retries(tmp_path: Path) -> None:
    route = respx.get(URL).respond(503)
    with pytest.raises(EdgarError, match="503"):
        make_client(tmp_path).get_json(URL, "raw/x.json")
    assert route.call_count == 5


def test_store_rejects_keys_outside_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        LocalStore(tmp_path).put("../escape.json", b"{}")
