"""Register one bounded hosted candidate without changing an existing endpoint."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.client import HTTPException
from typing import Literal, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from azure.core.credentials import TokenCredential
from azure.core.exceptions import AzureError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_harness.adapters.foundry.deployment import FoundryDeploymentTarget, HostedAgentRelease
from agent_harness.core.errors import HarnessError
from agent_harness.core.types import JsonValue


class DeploymentError(HarnessError):
    code = "deployment_error"


class DeploymentUncertainError(DeploymentError):
    code = "deployment_outcome_unknown"


class DeploymentLimits(BaseModel):
    """Bound polling, individual socket waits, read retries, and version enumeration."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    timeout_seconds: int = Field(default=600, ge=1, le=1800)
    request_timeout_seconds: int = Field(default=30, ge=1, le=60)
    poll_seconds: int = Field(default=5, ge=1, le=30)
    max_pages: int = Field(default=20, ge=1, le=100)
    read_attempts: int = Field(default=4, ge=1, le=5)


class _Version(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    name: Literal["agent-harness"]
    version: str = Field(pattern=r"^[1-9][0-9]{0,9}$")
    status: Literal["creating", "active", "failed", "deleting", "deleted"]
    definition: dict[str, JsonValue]
    metadata: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True)
class DeploymentReceipt:
    project_endpoint: str
    agent_name: str
    candidate_version: str
    image: str
    serving_version: str | None
    reused: bool
    initial_exposure: bool
    status: Literal["active"] = "active"
    smoke_verified: Literal[False] = False
    promoted: Literal[False] = False


@dataclass(frozen=True)
class WireResponse:
    status: int
    body: bytes


class DeploymentTransport(Protocol):
    def request(
        self, method: str, url: str, body: bytes | None, timeout: float
    ) -> WireResponse: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


class ManagedIdentityTransport:
    """Use an explicitly supplied credential, with no redirects or automatic retries."""

    def __init__(self, credential: TokenCredential) -> None:
        self._credential = credential
        self._opener = build_opener(_NoRedirect())

    def request(self, method: str, url: str, body: bytes | None, timeout: float) -> WireResponse:
        started = time.monotonic()
        try:
            token = self._credential.get_token("https://ai.azure.com/.default")
        except AzureError:
            raise DeploymentError("Deployment credential acquisition failed") from None
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise DeploymentError("Deployment credential exceeded the request time budget")
        request = Request(
            url,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {token.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with self._opener.open(request, timeout=remaining) as response:
                content = response.read(1_048_577)
                if len(content) > 1_048_576:
                    raise DeploymentUncertainError(
                        "Deployment API response exceeded the size limit"
                    )
                return WireResponse(response.status, content)
        except HTTPError as error:
            status = error.code
            error.close()
            return WireResponse(status, b"")
        except (URLError, TimeoutError, OSError, HTTPException):
            raise DeploymentUncertainError("Deployment request transport failed") from None


class HostedAgentRegistrar:
    """Require exclusive ownership and a pre-pinned endpoint before one create attempt."""

    def __init__(
        self,
        target: FoundryDeploymentTarget,
        transport: DeploymentTransport,
        limits: DeploymentLimits | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._target = target
        self._transport = transport
        self._limits = limits or DeploymentLimits()
        self._clock = clock
        self._sleep = sleep
        self._deadline = 0.0

    def _remaining(self) -> float:
        remaining = self._deadline - self._clock()
        if remaining <= 0:
            raise DeploymentError("Deployment deadline exceeded; reconcile before retrying")
        return remaining

    def _wait(self) -> None:
        self._sleep(min(self._limits.poll_seconds, self._remaining()))
        self._remaining()

    def _request(
        self, method: str, path: str, body: dict[str, JsonValue] | None = None
    ) -> WireResponse:
        url = f"{self._target.project_endpoint}/agents/agent-harness{path}"
        payload = json.dumps(body).encode() if body is not None else None
        attempts = self._limits.read_attempts if method == "GET" else 1
        for attempt in range(attempts):
            timeout = min(self._limits.request_timeout_seconds, self._remaining())
            try:
                response = self._transport.request(method, url, payload, timeout)
            except DeploymentUncertainError:
                if method != "GET" or attempt + 1 == attempts:
                    raise
            else:
                if method != "GET" or response.status not in {429, 500, 502, 503, 504}:
                    if method == "POST" and self._clock() >= self._deadline:
                        raise DeploymentUncertainError(
                            "Create response exceeded the deployment deadline"
                        )
                    self._remaining()
                    return response
                if attempt + 1 == attempts:
                    raise DeploymentError("Deployment API transient read retries exhausted")
            self._wait()
        raise DeploymentError("Deployment request did not complete")

    @staticmethod
    def _object(response: WireResponse) -> dict[str, JsonValue]:
        if response.status not in {200, 201}:
            raise DeploymentError(f"Deployment API returned HTTP {response.status}")
        try:
            value = json.loads(response.body)
        except (ValueError, UnicodeError):
            raise DeploymentError("Deployment API returned invalid JSON") from None
        if not isinstance(value, dict):
            raise DeploymentError("Deployment API returned an invalid object")
        return cast(dict[str, JsonValue], value)

    @staticmethod
    def _version(value: object) -> _Version:
        try:
            return _Version.model_validate(value)
        except ValidationError:
            raise DeploymentError("Deployment API returned invalid version details") from None

    def _agent(self) -> dict[str, JsonValue] | None:
        response = self._request("GET", "?api-version=v1")
        if response.status == 404:
            return None
        agent = self._object(response)
        if agent.get("name") != "agent-harness":
            raise DeploymentError("Deployment API returned a different agent")
        return agent

    @staticmethod
    def _pinned_version(agent: dict[str, JsonValue]) -> str:
        endpoint = agent.get("agent_endpoint")
        if isinstance(endpoint, dict):
            selector = endpoint.get("version_selector")
            if isinstance(selector, dict):
                rules = selector.get("version_selection_rules")
                if isinstance(rules, list) and len(rules) == 1:
                    rule = rules[0]
                    if isinstance(rule, dict):
                        version = rule.get("agent_version")
                        if (
                            rule.get("type") == "FixedRatio"
                            and type(rule.get("traffic_percentage")) is int
                            and rule.get("traffic_percentage") == 100
                            and isinstance(version, str)
                            and re.fullmatch(r"[1-9][0-9]{0,9}", version)
                        ):
                            return version
        raise DeploymentError("Existing endpoint must be pinned to one concrete version at 100%")

    def _versions(self) -> dict[str, _Version]:
        versions: dict[str, _Version] = {}
        after: str | None = None
        for _ in range(self._limits.max_pages):
            params = {"api-version": "v1", "limit": "100", "order": "desc"}
            if after is not None:
                params["after"] = after
            page = self._object(self._request("GET", "/versions?" + urlencode(params)))
            data = page.get("data")
            if not isinstance(data, list) or type(page.get("has_more")) is not bool:
                raise DeploymentError("Deployment API returned invalid version pagination")
            for value in data:
                version = self._version(value)
                if version.version in versions:
                    raise DeploymentError(
                        "Version enumeration changed or repeated during deployment"
                    )
                versions[version.version] = version
            if page["has_more"] is False:
                return versions
            cursor = page.get("last_id")
            if not isinstance(cursor, str) or not cursor or cursor == after or not data:
                raise DeploymentError("Deployment API returned an invalid pagination cursor")
            after = cursor
        raise DeploymentError("Version enumeration exceeded the page limit")

    def _get_version(self, version: str) -> _Version:
        value = self._object(self._request("GET", f"/versions/{version}?api-version=v1"))
        result = self._version(value)
        if result.version != version:
            raise DeploymentError("Deployment API returned a different version")
        return result

    def register(
        self,
        release: HostedAgentRelease,
        *,
        exclusive_deployment_owner: bool,
        allow_initial_exposure: bool = False,
    ) -> DeploymentReceipt:
        if not exclusive_deployment_owner:
            raise DeploymentError("Exclusive deployment ownership must be acknowledged")
        self._deadline = self._clock() + self._limits.timeout_seconds
        agent = self._agent()
        serving = self._pinned_version(agent) if agent is not None else None
        if agent is None and not allow_initial_exposure:
            raise DeploymentError("First deployment requires explicit initial-exposure approval")
        before = self._versions() if agent is not None else {}
        if serving is not None and self._get_version(serving).status != "active":
            raise DeploymentError("Pinned serving version is not active")
        definition = release.rest_definition()
        matches = [version for version in before.values() if version.definition == definition]
        if len(matches) > 1:
            raise DeploymentUncertainError(
                "Multiple versions match this release; reconcile manually"
            )
        reused = bool(matches)
        if matches:
            candidate = matches[0]
        else:
            attempt_id = uuid4().hex
            fingerprint = hashlib.sha256(
                json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            body: dict[str, JsonValue] = {
                "definition": definition,
                "metadata": {
                    "harness_deployment_id": attempt_id,
                    "harness_release_sha256": fingerprint,
                },
            }
            # Recheck before the single non-idempotent write; this is not an atomic lock.
            if self._agent() != agent or self._versions_if_present(agent) != before:
                raise DeploymentUncertainError("Agent changed before creation; reconcile manually")
            candidate = self._create_or_reconcile(body, before, attempt_id, definition)
        if candidate.definition != definition:
            raise DeploymentUncertainError("Candidate definition differs from the intended release")
        expected_metadata = candidate.metadata
        while True:
            candidate = self._get_version(candidate.version)
            if candidate.definition != definition or candidate.metadata != expected_metadata:
                raise DeploymentUncertainError("Candidate definition changed during deployment")
            if candidate.status == "active":
                break
            if candidate.status != "creating":
                raise DeploymentError(f"Candidate reached terminal status '{candidate.status}'")
            self._wait()
        current = self._agent()
        if agent is not None:
            if current is None or current.get("agent_endpoint") != agent.get("agent_endpoint"):
                raise DeploymentUncertainError("Serving endpoint changed; no promotion performed")
        else:
            if current is None:
                raise DeploymentUncertainError("Created agent cannot be verified")
            # New agents can follow latest. Report no concrete serving pin rather than guess.
            serving = None
        after = self._versions()
        expected_ids = set(before) | {candidate.version}
        if (
            set(after) != expected_ids
            or after[candidate.version] != candidate
            or any(after[key] != value for key, value in before.items() if key != candidate.version)
        ):
            raise DeploymentUncertainError(
                "Concurrent version changes detected; reconcile manually"
            )
        return DeploymentReceipt(
            project_endpoint=self._target.project_endpoint,
            agent_name=release.name,
            candidate_version=candidate.version,
            image=release.image,
            serving_version=serving,
            reused=reused,
            initial_exposure=agent is None,
        )

    def _versions_if_present(self, agent: dict[str, JsonValue] | None) -> dict[str, _Version]:
        return self._versions() if agent is not None else {}

    def _create_or_reconcile(
        self,
        body: dict[str, JsonValue],
        before: dict[str, _Version],
        attempt_id: str,
        definition: dict[str, JsonValue],
    ) -> _Version:
        try:
            response = self._request("POST", "/versions?api-version=v1", body)
        except DeploymentUncertainError:
            response = None
        if response is not None and response.status in {200, 201}:
            try:
                candidate = self._version(self._object(response))
            except DeploymentError:
                candidate = None
            if candidate is not None:
                if (
                    candidate.version in before
                    or candidate.metadata.get("harness_deployment_id") != attempt_id
                ):
                    raise DeploymentUncertainError(
                        "Create response cannot be attributed to this write"
                    )
                return candidate
        elif response is not None and 300 <= response.status < 500 and response.status not in {408}:
            raise DeploymentError(f"Version creation rejected with HTTP {response.status}")
        try:
            versions = self._versions()
        except DeploymentError:
            raise DeploymentUncertainError(
                "Create outcome unknown; reconciliation failed; do not blindly retry"
            ) from None
        added = [value for key, value in versions.items() if key not in before]
        if (
            len(added) == 1
            and added[0].metadata.get("harness_deployment_id") == attempt_id
            and added[0].definition == definition
        ):
            return added[0]
        raise DeploymentUncertainError(
            "Create outcome unknown; no unique attributed version found; do not blindly retry"
        )
