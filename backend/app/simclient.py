"""Resilient HTTP client for the simulator: timeouts, jittered retries, circuit breaker, stale flag."""
import asyncio
import random
import time
from typing import Any

import httpx

from . import metrics
from .metrics import Rolling


class SimError(Exception):
    """kind: fault | http | timeout | conn | circuit_open | invalid"""

    def __init__(self, kind: str, status: int | None = None, code: str | None = None, message: str = ""):
        super().__init__(f"{kind} {status or ''} {code or ''} {message}".strip())
        self.kind, self.status, self.code, self.message = kind, status, code, message


def parse_error(resp: httpx.Response) -> tuple[str | None, str]:
    """Faults use {"error": {...}}; domain errors and stream faults use {"detail": {...}}."""
    try:
        body = resp.json()
    except ValueError:
        return None, resp.text[:200]
    for key in ("error", "detail"):
        val = body.get(key) if isinstance(body, dict) else None
        if isinstance(val, dict):
            return val.get("code"), val.get("message", "")
        if isinstance(val, list):
            return "VALIDATION_ERROR", str(val)[:200]
    return None, str(body)[:200]


class CircuitBreaker:
    CLOSED, HALF_OPEN, OPEN = "CLOSED", "HALF_OPEN", "OPEN"

    def __init__(self, threshold: int = 5, reset_seconds: float = 5.0):
        self.threshold, self.reset_seconds = threshold, reset_seconds
        self.state, self.failures, self.opened_at = self.CLOSED, 0, 0.0

    def allow(self) -> bool:
        if self.state == self.OPEN and time.monotonic() - self.opened_at >= self.reset_seconds:
            self.state = self.HALF_OPEN
        return self.state != self.OPEN

    def success(self) -> None:
        self.state, self.failures = self.CLOSED, 0

    def failure(self) -> None:
        self.failures += 1
        if self.state == self.HALF_OPEN or self.failures >= self.threshold:
            self.state, self.opened_at = self.OPEN, time.monotonic()

    @property
    def gauge(self) -> int:
        return {self.CLOSED: 0, self.HALF_OPEN: 1, self.OPEN: 2}[self.state]


def _endpoint_label(path: str) -> str:
    parts = path.split("?")[0].strip("/").split("/")
    return "/" + "/".join(p if not p.isdigit() else "{id}" for p in parts[:3])


class SimClient:
    def __init__(self, base_url: str, use_breaker: bool = True, retries: int = 2):
        self.http = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(2.0, connect=1.0),
                                      limits=httpx.Limits(max_connections=20, max_keepalive_connections=10))
        self.breaker = CircuitBreaker() if use_breaker else None
        self.retries = retries
        self.rolling = Rolling()
        self.last_success = 0.0
        self.stale_seen = 0.0

    @property
    def stale(self) -> bool:
        return time.time() - self.stale_seen < 3.0

    async def close(self) -> None:
        await self.http.aclose()

    async def request(self, method: str, path: str, json: Any = None, timeout: float | None = None) -> Any:
        label = _endpoint_label(path)
        if self.breaker and not self.breaker.allow():
            metrics.SIM_REQUESTS.labels(label, "circuit_open").inc()
            raise SimError("circuit_open", message="circuit breaker open")
        err: SimError | None = None
        # Only retry what is safe to repeat: reads, and allocation POSTs (idempotency key in the body; a second
        # cancel just returns CANNOT_CANCEL). Event, fault, step and reset POSTs are never retried.
        retries = self.retries if (method == "GET" or path.startswith("/v1/allocations")) else 0
        for attempt in range(retries + 1):
            t0 = time.perf_counter()
            try:
                resp = await self.http.request(method, path, json=json,
                                               timeout=timeout or (3.0 if method == "POST" else 2.0))
            except httpx.TimeoutException:
                err, outcome = SimError("timeout", message=path), "timeout"
            except httpx.TransportError as exc:
                err, outcome = SimError("conn", message=str(exc)[:120]), "http_error"
            else:
                ms = (time.perf_counter() - t0) * 1000
                metrics.SIM_LATENCY.labels(label).observe(ms / 1000)
                if resp.status_code < 400:
                    self.rolling.add(ms, True)
                    if self.breaker:
                        self.breaker.success()
                    self.last_success = time.time()
                    if resp.headers.get("X-Simulator-Stale", "").lower() == "true":
                        self.stale_seen = time.time()
                    metrics.SIM_REQUESTS.labels(label, "ok").inc()
                    return resp.json() if resp.content else None
                code, message = parse_error(resp)
                if resp.status_code == 503 or resp.status_code >= 500:
                    err, outcome = SimError("fault", resp.status_code, code, message), "fault"
                else:
                    # A domain error (404/409/422) means the simulator is reachable: never retry, never trip.
                    self.rolling.add(ms, True)
                    if self.breaker:
                        self.breaker.success()
                    self.last_success = time.time()
                    metrics.SIM_REQUESTS.labels(label, "http_error").inc()
                    raise SimError("http", resp.status_code, code, message)
            self.rolling.add((time.perf_counter() - t0) * 1000, False)
            metrics.SIM_REQUESTS.labels(label, outcome).inc()
            if attempt < retries:
                await asyncio.sleep(0.1 * 2 ** attempt + random.uniform(0, 0.1))
        if self.breaker:
            self.breaker.failure()
        raise err  # type: ignore[misc]
