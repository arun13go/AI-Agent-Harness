"""Exercise registration outcomes without Azure credentials or network access."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict
from urllib.parse import parse_qs, urlsplit

import pytest

from agent_harness.adapters.foundry.deployment import FoundryDeploymentTarget, HostedAgentRelease
from agent_harness.adapters.foundry.registration import (
    DeploymentError,
    DeploymentLimits,
    DeploymentUncertainError,
    HostedAgentRegistrar,
    WireResponse,
)
from agent_harness.core.types import JsonValue

IMAGE = "example.azurecr.io/harness@sha256:" + "a" * 64
RELEASE = HostedAgentRelease(image=IMAGE, model_deployment_name="example-model")
TARGET = FoundryDeploymentTarget(
    project_resource_id=(
        "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/example-rg"
        "/providers/Microsoft.CognitiveServices/accounts/example/projects/example-project"
    ),
    project_endpoint="https://example.services.ai.azure.com/api/projects/example-project",
)


def version(number: str, *, matching: bool = False, status: str = "active") -> dict[str, JsonValue]:
    definition = RELEASE.rest_definition()
    if not matching:
        definition["container_configuration"] = {"image": IMAGE.replace("a" * 64, "b" * 64)}
    return {
        "name": "agent-harness",
        "version": number,
        "definition": definition,
        "status": status,
        "metadata": {},
    }


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class Service:
    """Model the documented REST shapes and inject individual failure modes."""

    def __init__(self) -> None:
        self.endpoint: dict[str, JsonValue] = {
            "version_selector": {
                "version_selection_rules": [
                    {"type": "FixedRatio", "traffic_percentage": 100, "agent_version": "1"}
                ]
            },
            "protocol_configuration": {"responses": {}},
        }
        self.exists = True
        self.versions = {"1": version("1")}
        self.calls: list[tuple[str, str, float]] = []
        self.posts = 0
        self.read_failures: list[int] = []
        self.post_failure: str | None = None
        self.candidate_status = "active"
        self.polls_to_active = 0
        self.page_size = 100
        self.repeat_cursor = False
        self.mutate_candidate = False
        self.compete_after_poll = False
        self.agent_reads = 0
        self.change_before_create = False
        self.activate_on_poll = False

    @staticmethod
    def response(status: int, value: object) -> WireResponse:
        return WireResponse(status, json.dumps(value).encode())

    def request(self, method: str, url: str, body: bytes | None, timeout: float) -> WireResponse:
        self.calls.append((method, url, timeout))
        assert url.startswith(TARGET.project_endpoint + "/agents/agent-harness")
        path = urlsplit(url).path
        if method == "GET" and self.read_failures:
            return self.response(self.read_failures.pop(0), {"error": "secret-provider-content"})
        if method == "POST":
            self.posts += 1
            assert body is not None
            payload: dict[str, JsonValue] = json.loads(body)
            if self.post_failure == "denied":
                return self.response(403, {"secret": "not-for-logs"})
            if self.post_failure == "conflict":
                return self.response(409, {})
            if self.post_failure == "throttled":
                return self.response(429, {})
            if self.post_failure == "lost-before-write":
                raise DeploymentUncertainError("simulated lost request")
            candidate = version("2", matching=True, status=self.candidate_status)
            candidate["metadata"] = payload["metadata"]
            self.versions["2"] = candidate
            self.exists = True
            if self.post_failure == "competing-writer":
                self.versions["3"] = version("3")
                raise DeploymentUncertainError("simulated lost response")
            if self.post_failure == "endpoint-changed":
                self.endpoint["protocol_configuration"] = {"invocations": {}}
            if self.post_failure == "foreign-metadata":
                candidate["metadata"] = {}
            if self.post_failure == "lost-after-write":
                raise DeploymentUncertainError("simulated lost response")
            if self.post_failure == "server-after-write":
                return self.response(503, {})
            if self.post_failure == "malformed-after-write":
                return WireResponse(201, b"not json")
            if self.post_failure == "accepted-after-write":
                return self.response(202, {})
            return self.response(201, candidate)
        assert method == "GET"
        if path.endswith("/agent-harness"):
            self.agent_reads += 1
            if self.change_before_create and self.agent_reads == 2:
                self.endpoint["protocol_configuration"] = {"invocations": {}}
            if not self.exists:
                return self.response(404, {})
            return self.response(200, {"name": "agent-harness", "agent_endpoint": self.endpoint})
        if path.endswith("/versions"):
            params = parse_qs(urlsplit(url).query)
            keys = sorted(self.versions, reverse=True)
            after = params.get("after", [None])[0]
            start = keys.index(after) + 1 if after is not None else 0
            selected = keys[start : start + self.page_size]
            more = start + self.page_size < len(keys)
            return self.response(
                200,
                {
                    "data": [self.versions[key] for key in selected],
                    "has_more": more or self.repeat_cursor,
                    "last_id": selected[-1] if selected else after,
                },
            )
        number = path.rsplit("/", 1)[1]
        if number == "2" and self.mutate_candidate:
            self.versions[number]["definition"] = {"kind": "prompt"}
        if number == "2" and self.compete_after_poll:
            self.versions["3"] = version("3")
        if number == "2" and self.polls_to_active:
            self.polls_to_active -= 1
            result = deepcopy(self.versions[number])
            result["status"] = "creating"
            return self.response(200, result)
        if number == "2" and self.activate_on_poll:
            self.versions[number]["status"] = "active"
        return self.response(200, self.versions[number])


def registrar(service: Service, **limits: int) -> HostedAgentRegistrar:
    clock = Clock()
    return HostedAgentRegistrar(
        TARGET, service, DeploymentLimits(**limits), clock=clock.time, sleep=clock.sleep
    )


def test_creates_exact_candidate_and_preserves_endpoint() -> None:
    service = Service()
    original = deepcopy(service.endpoint)
    service.polls_to_active = 2
    receipt = registrar(service).register(RELEASE, exclusive_deployment_owner=True)
    assert service.posts == 1
    assert service.endpoint == original
    assert asdict(receipt) == {
        "project_endpoint": TARGET.project_endpoint,
        "agent_name": "agent-harness",
        "candidate_version": "2",
        "image": IMAGE,
        "serving_version": "1",
        "reused": False,
        "initial_exposure": False,
        "status": "active",
        "smoke_verified": False,
        "promoted": False,
    }
    assert all(method in {"GET", "POST"} for method, _, _ in service.calls)
    assert all(0 < timeout <= 30 for _, _, timeout in service.calls)


def test_reuses_unique_active_match_without_writing() -> None:
    service = Service()
    service.versions["2"] = version("2", matching=True)
    receipt = registrar(service).register(RELEASE, exclusive_deployment_owner=True)
    assert receipt.reused
    assert receipt.candidate_version == "2"
    assert receipt.serving_version == "1"
    assert service.posts == 0


@pytest.mark.parametrize(
    "failure",
    ["lost-after-write", "server-after-write", "malformed-after-write", "accepted-after-write"],
)
def test_reconciles_one_attributed_create_without_reposting(failure: str) -> None:
    service = Service()
    service.post_failure = failure
    receipt = registrar(service).register(RELEASE, exclusive_deployment_owner=True)
    assert receipt.candidate_version == "2"
    assert service.posts == 1


@pytest.mark.parametrize("failure", ["lost-before-write", "competing-writer", "foreign-metadata"])
def test_unknown_or_foreign_outcome_fails_without_reposting(failure: str) -> None:
    service = Service()
    service.post_failure = failure
    with pytest.raises(DeploymentUncertainError):
        registrar(service).register(RELEASE, exclusive_deployment_owner=True)
    assert service.posts == 1


@pytest.mark.parametrize("failure", ["denied", "conflict", "throttled"])
def test_creation_http_failures_are_explicit_and_not_retried(failure: str) -> None:
    service = Service()
    service.post_failure = failure
    with pytest.raises(DeploymentError) as error:
        registrar(service).register(RELEASE, exclusive_deployment_owner=True)
    assert service.posts == 1
    assert "not-for-logs" not in str(error.value)


def test_read_throttling_and_server_errors_are_bounded() -> None:
    service = Service()
    service.read_failures = [429, 503, 502]
    assert registrar(service).register(RELEASE, exclusive_deployment_owner=True).status == "active"
    exhausted = Service()
    exhausted.read_failures = [429] * 4
    with pytest.raises(DeploymentError, match="retries exhausted"):
        registrar(exhausted).register(RELEASE, exclusive_deployment_owner=True)
    assert exhausted.posts == 0
    assert len(exhausted.calls) == 4


@pytest.mark.parametrize("status", [401, 403, 302])
def test_read_auth_and_redirect_errors_are_not_retried(status: int) -> None:
    service = Service()
    service.read_failures = [status]
    with pytest.raises(DeploymentError, match=f"HTTP {status}"):
        registrar(service).register(RELEASE, exclusive_deployment_owner=True)
    assert len(service.calls) == 1


def test_requires_exclusive_deployment_acknowledgment_before_network() -> None:
    service = Service()
    with pytest.raises(DeploymentError, match="Exclusive"):
        registrar(service).register(RELEASE, exclusive_deployment_owner=False)
    assert service.calls == []


def test_requires_existing_concrete_pin_without_mutating_it() -> None:
    service = Service()
    service.endpoint["version_selector"] = {}
    with pytest.raises(DeploymentError, match="must be pinned"):
        registrar(service).register(RELEASE, exclusive_deployment_owner=True)
    assert service.posts == 0


def test_initial_deployment_requires_exposure_acknowledgment() -> None:
    service = Service()
    service.exists = False
    service.versions = {}
    with pytest.raises(DeploymentError, match="initial-exposure"):
        registrar(service).register(RELEASE, exclusive_deployment_owner=True)
    assert service.posts == 0
    receipt = registrar(service).register(
        RELEASE, exclusive_deployment_owner=True, allow_initial_exposure=True
    )
    assert receipt.serving_version is None
    assert receipt.initial_exposure
    assert not receipt.promoted
    assert service.posts == 1


@pytest.mark.parametrize("status", ["failed", "deleting", "deleted"])
def test_terminal_candidate_status_fails_explicitly(status: str) -> None:
    service = Service()
    service.candidate_status = status
    with pytest.raises(DeploymentError, match="terminal status"):
        registrar(service).register(RELEASE, exclusive_deployment_owner=True)
    assert service.posts == 1


def test_polling_stops_at_deadline() -> None:
    service = Service()
    service.polls_to_active = 1000
    with pytest.raises(DeploymentError, match="deadline"):
        registrar(service, timeout_seconds=10).register(RELEASE, exclusive_deployment_owner=True)
    assert service.posts == 1
    assert len(service.calls) < 20


def test_endpoint_change_is_reported_without_repair_or_promotion() -> None:
    service = Service()
    service.post_failure = "endpoint-changed"
    with pytest.raises(DeploymentUncertainError, match="endpoint changed"):
        registrar(service).register(RELEASE, exclusive_deployment_owner=True)
    assert service.posts == 1
    assert not any(method in {"PATCH", "DELETE"} for method, _, _ in service.calls)


def test_duplicate_matching_versions_fail_without_writing() -> None:
    service = Service()
    service.versions["2"] = version("2", matching=True)
    service.versions["3"] = version("3", matching=True)
    with pytest.raises(DeploymentUncertainError, match="Multiple versions"):
        registrar(service).register(RELEASE, exclusive_deployment_owner=True)
    assert service.posts == 0


def test_pagination_is_complete_and_bounded() -> None:
    service = Service()
    service.page_size = 1
    service.versions["2"] = version("2", matching=True)
    assert registrar(service).register(RELEASE, exclusive_deployment_owner=True).reused
    with pytest.raises(DeploymentError, match="page limit"):
        registrar(service, max_pages=1).register(RELEASE, exclusive_deployment_owner=True)


def test_repeated_pagination_cursor_fails_closed() -> None:
    service = Service()
    service.repeat_cursor = True
    with pytest.raises(DeploymentError, match="pagination cursor"):
        registrar(service).register(RELEASE, exclusive_deployment_owner=True)
    assert service.posts == 0


def test_version_payload_validation_does_not_disclose_response_values() -> None:
    service = Service()
    service.versions["1"]["version"] = "secret-invalid-version"
    with pytest.raises(DeploymentError) as error:
        registrar(service).register(RELEASE, exclusive_deployment_owner=True)
    assert "secret-invalid-version" not in str(error.value)


def test_candidate_mutation_fails_closed() -> None:
    service = Service()
    service.versions["2"] = version("2", matching=True)
    service.mutate_candidate = True
    with pytest.raises(DeploymentUncertainError, match="definition changed"):
        registrar(service).register(RELEASE, exclusive_deployment_owner=True)
    assert service.posts == 0


def test_existing_creating_match_can_resume_to_active_without_a_new_write() -> None:
    service = Service()
    service.versions["2"] = version("2", matching=True, status="creating")
    service.activate_on_poll = True
    service.polls_to_active = 1
    assert registrar(service).register(RELEASE, exclusive_deployment_owner=True).reused
    assert service.posts == 0


def test_competing_writer_after_a_successful_create_is_detected() -> None:
    service = Service()
    service.compete_after_poll = True
    with pytest.raises(DeploymentUncertainError, match="Concurrent"):
        registrar(service).register(RELEASE, exclusive_deployment_owner=True)
    assert service.posts == 1


def test_changed_agent_before_create_stops_without_writing() -> None:
    service = Service()
    service.change_before_create = True
    with pytest.raises(DeploymentUncertainError, match="changed before creation"):
        registrar(service).register(RELEASE, exclusive_deployment_owner=True)
    assert service.posts == 0
