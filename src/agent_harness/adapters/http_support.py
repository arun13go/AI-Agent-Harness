"""Share the small, security-relevant pieces of the REST clients: tokens and redirects.

Both Azure service clients send an Entra bearer token, so both must reuse one token until shortly
before it expires (some credentials start a process on every call) and must refuse redirects, which
would otherwise forward the credential to whatever host the service names.
"""

from __future__ import annotations

import asyncio
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

_REFRESH_SKEW_SECONDS = 300
_THROTTLED = frozenset({429, 503})
_MAX_ATTEMPTS = 3
_MAX_PAUSE_SECONDS = 5.0
_RETRY_BUDGET_SECONDS = 30.0


class AccessToken(Protocol):
    @property
    def token(self) -> str: ...

    @property
    def expires_on(self) -> int: ...


class TokenCredential(Protocol):
    """A synchronous Entra credential, such as ``DefaultAzureCredential``."""

    def get_token(self, *scopes: str) -> AccessToken: ...


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface a redirect as an HTTP error instead of following it with the credential."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class BearerTokenCache:
    """Hand out one token until shortly before it expires, safely across threads."""

    def __init__(
        self,
        credential: TokenCredential,
        scope: str,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._credential = credential
        self._scope = scope
        self._clock = clock
        self._lock = threading.Lock()
        self._token: AccessToken | None = None

    def token(self) -> str:
        with self._lock:
            token = self._token
            if token is None or token.expires_on - self._clock() < _REFRESH_SKEW_SECONDS:
                token = self._credential.get_token(self._scope)
                self._token = token
            return token.token

    def invalidate(self) -> None:
        """Forget the cached token so the next call asks the credential for a fresh one."""

        with self._lock:
            self._token = None


class Transient(Exception):
    """A failure that may succeed on retry: a rejected token, throttling or a network error."""

    def __init__(
        self,
        message: str,
        *,
        kind: Literal["auth", "throttled", "network"],
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after


def retry_after_seconds(headers: Any) -> float | None:
    """Read a whole-number delay (``retry-after`` seconds or ``x-ms-retry-after-ms``).

    An absent, dated or malformed value means the caller should use its own backoff.
    """

    if headers is None:
        return None
    seconds, millis = headers.get("retry-after"), headers.get("x-ms-retry-after-ms")
    if isinstance(seconds, str) and seconds.isdecimal():
        return float(seconds)
    if isinstance(millis, str) and millis.isdecimal():
        return float(millis) / 1000.0
    return None


def transient_for(error: urllib.error.HTTPError, message: str) -> Transient | None:
    """Classify an HTTP error: 401 retries once with a fresh token; 429 and 503 are throttling."""

    if error.code == 401:
        return Transient(message, kind="auth")
    if error.code in _THROTTLED:
        return Transient(message, kind="throttled", retry_after=retry_after_seconds(error.headers))
    return None


async def with_retries[T](
    attempt: Callable[[], Awaitable[T]],
    *,
    tokens: BearerTokenCache,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> T:
    """Run an idempotent call, retrying ``Transient`` failures a few times in a small budget.

    A 401 is retried once with a fresh token, because a cached token can be rejected shortly before
    it expires. Other failures back off, honoring a short ``Retry-After``. The last ``Transient`` is
    re-raised for the caller to turn into its own error.
    """

    started = monotonic()
    refreshed = False
    for number in range(1, _MAX_ATTEMPTS + 1):
        try:
            return await attempt()
        except Transient as failure:
            last = number == _MAX_ATTEMPTS
            if failure.kind == "auth":
                if refreshed or last:
                    raise
                refreshed = True
                tokens.invalidate()
                continue
            pause = failure.retry_after if failure.retry_after is not None else 0.5 * number
            if (
                last
                or pause > _MAX_PAUSE_SECONDS
                or monotonic() - started + pause > _RETRY_BUDGET_SECONDS
            ):
                raise
            await sleep(pause)
    raise AssertionError("unreachable")  # pragma: no cover
