"""HTTP client for the CommonTrace backend API.

BackendClient wraps httpx.AsyncClient to provide a clean interface for
making authenticated POST and GET requests to the FastAPI backend, with
circuit breaker protection and per-operation SLA timeouts.
"""

import asyncio
import time
from collections import defaultdict

import httpx

from app.config import settings


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open and requests are blocked."""
    pass


class BackendUnavailableError(Exception):
    """Raised when a backend call fails (timeout, connection error, 5xx HTTP error)."""
    pass


class RateLimitError(Exception):
    """M14: Raised when a client exceeds the MCP-layer rate limit."""
    pass


class CircuitBreaker:
    """Async circuit breaker with three states: closed, open, half-open.

    - closed: requests flow normally, failures are counted
    - open: requests are immediately rejected with CircuitOpenError
    - half-open: one probe request is allowed; success -> closed, failure -> open
    """

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.state = "closed"

    async def call(self, coro_factory, timeout: float):
        """Execute coroutine factory with circuit breaker protection and timeout."""
        if self.state == "open":
            if time.monotonic() - self.last_failure_time > self.recovery_timeout:
                self.state = "half-open"
            else:
                raise CircuitOpenError(
                    "CommonTrace backend is temporarily unavailable. "
                    "Please try again in a few seconds."
                )

        try:
            result = await asyncio.wait_for(coro_factory(), timeout=timeout)
            self._on_success()
            return result
        except (httpx.HTTPError, asyncio.TimeoutError, ConnectionError, OSError) as exc:
            self._on_failure()
            raise BackendUnavailableError(str(exc)) from exc

    def _on_success(self):
        self.failure_count = 0
        self.state = "closed"

    def _on_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.failure_count >= self.failure_threshold:
            self.state = "open"


class PerKeyCircuitBreakerPool:
    """M11: Per-API-key circuit breakers — one bad actor can't block everyone."""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0, max_keys: int = 1000):
        self._breakers: dict[str, CircuitBreaker] = {}
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._max_keys = max_keys

    def get(self, api_key: str) -> CircuitBreaker:
        if api_key not in self._breakers:
            # Evict oldest if at capacity
            if len(self._breakers) >= self._max_keys:
                oldest_key = next(iter(self._breakers))
                del self._breakers[oldest_key]
            self._breakers[api_key] = CircuitBreaker(
                self._failure_threshold, self._recovery_timeout
            )
        return self._breakers[api_key]


class TokenBucketRateLimiter:
    """M14: Simple in-memory token bucket rate limiter per API key."""

    def __init__(self, max_tokens: int = 30, refill_per_second: float = 0.5):
        self._max_tokens = max_tokens
        self._refill_rate = refill_per_second
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_refill)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        tokens, last_refill = self._buckets.get(key, (self._max_tokens, now))

        elapsed = now - last_refill
        tokens = min(self._max_tokens, tokens + elapsed * self._refill_rate)

        if tokens >= 1:
            self._buckets[key] = (tokens - 1, now)
            return True
        self._buckets[key] = (tokens, now)
        return False


class BackendClient:
    """Thin wrapper around httpx.AsyncClient for backend API calls.

    Manages a persistent async HTTP client with connection pooling,
    circuit breaker protection, and per-request SLA timeouts.
    API key authentication is forwarded from MCP client headers.
    """

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            base_url=settings.api_base_url,
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        # M11: Per-API-key circuit breakers
        self.breaker_pool = PerKeyCircuitBreakerPool(
            failure_threshold=settings.circuit_failure_threshold,
            recovery_timeout=settings.circuit_recovery_timeout,
        )
        # M14: MCP-layer rate limiting (30 requests/min per key)
        self.rate_limiter = TokenBucketRateLimiter(max_tokens=30, refill_per_second=0.5)

    async def post(
        self,
        path: str,
        json: dict,
        api_key: str,
        timeout: float = 2.0,
    ) -> dict:
        """POST to backend with circuit breaker protection.

        Args:
            path: URL path (e.g. "/api/v1/traces")
            json: Request body as a dict (serialized to JSON)
            api_key: API key forwarded from MCP client headers
            timeout: Per-request SLA timeout in seconds

        Returns:
            Parsed JSON response body as dict

        Raises:
            CircuitOpenError: When circuit breaker is open
            BackendUnavailableError: On timeout, connection error, or 5xx response
            httpx.HTTPStatusError: On 4xx responses (client errors, do NOT trip circuit)
        """
        if not self.rate_limiter.allow(api_key):
            raise RateLimitError("MCP rate limit exceeded. Please slow down.")

        breaker = self.breaker_pool.get(api_key)

        async def _request():
            resp = await self.client.post(
                path,
                json=json,
                headers={"X-API-Key": api_key},
            )
            return resp

        resp = await breaker.call(_request, timeout=timeout)
        if resp.status_code >= 500:
            breaker._on_failure()
            raise BackendUnavailableError(f"Backend returned {resp.status_code}")
        resp.raise_for_status()
        return resp.json()

    async def get(
        self,
        path: str,
        api_key: str,
        timeout: float = 0.5,
    ) -> dict:
        """GET from backend with circuit breaker protection."""
        if not self.rate_limiter.allow(api_key):
            raise RateLimitError("MCP rate limit exceeded. Please slow down.")

        breaker = self.breaker_pool.get(api_key)

        async def _request():
            resp = await self.client.get(
                path,
                headers={"X-API-Key": api_key},
            )
            return resp

        resp = await breaker.call(_request, timeout=timeout)
        if resp.status_code >= 500:
            breaker._on_failure()
            raise BackendUnavailableError(f"Backend returned {resp.status_code}")
        resp.raise_for_status()
        return resp.json()

    async def close(self) -> None:
        """Close the underlying httpx client and release connections."""
        await self.client.aclose()


# Module-level singleton — shared across all tool invocations
backend = BackendClient()
