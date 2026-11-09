import httpx
import pytest
import respx

from edgebot.ingest.http import FetchError, JsonClient
from edgebot.ratelimit import TokenBucket


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def test_token_bucket_spaces_requests():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_s=2.0, burst=1, clock=clock.now, sleep=clock.sleep)
    for _ in range(5):
        bucket.acquire()
    # first call is free, the next four wait 0.5s each
    assert clock.sleeps == pytest.approx([0.5] * 4)


def test_token_bucket_refills_after_idle():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_s=1.0, burst=2, clock=clock.now, sleep=clock.sleep)
    bucket.acquire()
    bucket.acquire()
    clock.t += 10  # idle long enough to refill, but never above burst
    bucket.acquire()
    bucket.acquire()
    assert clock.sleeps == []
    bucket.acquire()
    assert clock.sleeps == pytest.approx([1.0])


def test_bucket_rejects_bad_config():
    with pytest.raises(ValueError):
        TokenBucket(0)


def _client(sleeps):
    return JsonClient("https://example.test", rate_per_s=1000, max_retries=2, sleep=sleeps.append)


@respx.mock
def test_retries_429_then_succeeds():
    route = respx.get("https://example.test/x").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "3"}),
            httpx.Response(503),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    sleeps = []
    assert _client(sleeps).get("/x") == {"ok": True}
    assert route.call_count == 3
    assert 3.0 in sleeps and 2.0 in sleeps  # Retry-After honored, then backoff


@respx.mock
def test_gives_up_after_max_retries():
    respx.get("https://example.test/x").mock(return_value=httpx.Response(429))
    with pytest.raises(FetchError, match="429"):
        _client([]).get("/x")


@respx.mock
def test_non_retryable_status_and_non_json_raise():
    respx.get("https://example.test/404").mock(return_value=httpx.Response(404))
    respx.get("https://example.test/html").mock(return_value=httpx.Response(200, text="<html>"))
    c = _client([])
    with pytest.raises(FetchError, match="404"):
        c.get("/404")
    with pytest.raises(FetchError, match="non-JSON"):
        c.get("/html")
