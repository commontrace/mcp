"""HTTP client for the CommonTrace backend API.

BackendClient wraps httpx.AsyncClient to provide a clean interface for
making authenticated POST and GET requests to the FastAPI backend, with
circuit breaker protection and per-operation SLA timeouts.
"""

import asyncio
import time

import httpx

from app.config import settings


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open and requests are blocked."""
    pass


class BackendUnavailableError(Exception):
    """Raised when a backend call fails (timeout, connection error, 5xx HTTP error)."""
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
        """Execute coroutine factory with circuit breaker protection and timeout.

        Args:
            coro_factory: A zero-argument callable that returns a coroutine.
            timeout: Per-request SLA timeout in seconds.
        """
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


class BackendClient:
    """Thin wrapper around httpx.AsyncClient for backend API calls.

    Manages a persistent async HTTP client with connection pooling,
    circuit breaker protection, and per-request SLA timeouts.
    API key authentication is forwarded from MCP client headers.
    """

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            base_url=settings.api_base_url,
            timeout=httpx.Timeout(5.0, connect=2.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        self.breaker = CircuitBreaker(
            failure_threshold=settings.circuit_failure_threshold,
            recovery_timeout=settings.circuit_recovery_timeout,
        )

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
        async def _request():
            resp = await self.client.post(
                path,
                json=json,
                headers={"X-API-Key": api_key},
            )
            return resp  # Return raw response, NOT raise_for_status()

        resp = await self.breaker.call(_request, timeout=timeout)
        # 5xx are server errors — manually count as circuit breaker failure
        if resp.status_code >= 500:
            self.breaker._on_failure()
            raise BackendUnavailableError(f"Backend returned {resp.status_code}")
        resp.raise_for_status()  # Raises httpx.HTTPStatusError for 4xx (does NOT trip circuit)
        return resp.json()

    async def get(
        self,
        path: str,
        api_key: str,
        timeout: float = 0.5,
    ) -> dict:
        """GET from backend with circuit breaker protection.

        Args:
            path: URL path (e.g. "/api/v1/tags")
            api_key: API key forwarded from MCP client headers
            timeout: Per-request SLA timeout in seconds

        Returns:
            Parsed JSON response body as dict

        Raises:
            CircuitOpenError: When circuit breaker is open
            BackendUnavailableError: On timeout, connection error, or 5xx response
            httpx.HTTPStatusError: On 4xx responses (client errors, do NOT trip circuit)
        """
        async def _request():
            resp = await self.client.get(
                path,
                headers={"X-API-Key": api_key},
            )
            return resp  # Return raw response, NOT raise_for_status()

        resp = await self.breaker.call(_request, timeout=timeout)
        # 5xx are server errors — manually count as circuit breaker failure
        if resp.status_code >= 500:
            self.breaker._on_failure()
            raise BackendUnavailableError(f"Backend returned {resp.status_code}")
        resp.raise_for_status()  # Raises httpx.HTTPStatusError for 4xx (does NOT trip circuit)
        return resp.json()

    async def close(self) -> None:
        """Close the underlying httpx client and release connections."""
        await self.client.aclose()


# Module-level singleton — shared across all tool invocations
backend = BackendClient()
