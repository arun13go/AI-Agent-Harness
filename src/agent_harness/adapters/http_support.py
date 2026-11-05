"""Share the small, security-relevant pieces of the REST clients: tokens and redirects.

Both Azure service clients send an Entra bearer token, so both must reuse one token until shortly
before it expires (some credentials start a process on every call) and must refuse redirects, which
would otherwise forward the credential to whatever host the service names.
"""

from __future__ import annotations

import threading
import time
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol

_REFRESH_SKEW_SECONDS = 300


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
