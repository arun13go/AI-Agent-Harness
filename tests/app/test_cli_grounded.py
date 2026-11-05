"""Prove the grounded-run command answers only from the local corpus and fails before any model."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml

from agent_harness.adapters.retrieval.in_memory import InMemoryRetrievalProvider
from agent_harness.app import cli
from agent_harness.app.cli import main
from tests.conformance.fixtures import make_evidence
from tests.conformance.test_context_policy import RecordingClient

ROOT = Path(__file__).parents[2]
LOCAL_PROFILE = ROOT / "config" / "profiles" / "grounded-assistant-local.yaml"
HANDBOOK = """# Operations

## Restart procedure

To restart the API service, drain traffic and then restart the process. CORPUS-MARKER-RESTART

# Billing

Invoices are issued monthly and are payable within thirty days. CORPUS-MARKER-BILLING
"""


class FakeFoundryClient(RecordingClient):
    """Stand in for the Foundry client: scripted answers and an observable close."""

    def __init__(self) -> None:
        super().__init__()
        self.closed = False
        self.project_client = SimpleNamespace(close=self._close)

    async def _close(self) -> None:
        self.closed = True


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "handbook.md").write_text(HANDBOOK, encoding="utf-8", newline="\n")
    return root


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> FakeFoundryClient:
    fake = FakeFoundryClient()

    def create(config: object) -> FakeFoundryClient:
        del config
        return fake

    monkeypatch.setattr(cli, "create_foundry_chat_client", create)
    return fake


def run(
    capsys: pytest.CaptureFixture[str],
    corpus: Path,
    question: str,
    *extra: str,
    profile: Path = LOCAL_PROFILE,
) -> tuple[int, dict[str, Any], dict[str, Any]]:
    exit_code = main(
        [
            "grounded-run",
            "--profile",
            str(profile),
            "--config-schema",
            str(ROOT / "config" / "schema" / "v1" / "harness.schema.json"),
            "--event-schema",
            str(ROOT / "config" / "schema" / "v1" / "run-event.schema.json"),
            "--tenant",
            "tenant-0001",
            "--subject",
            "subject-0001",
            "--corpus",
            str(corpus),
            "--question",
            question,
            *extra,
        ]
    )
    captured = capsys.readouterr()
    out: dict[str, Any] = json.loads(captured.out) if captured.out else {}
    err: dict[str, Any] = json.loads(captured.err) if captured.err else {}
    return exit_code, out, err


def test_on_topic_question_is_answered_with_a_citation_to_the_corpus_file(
    capsys: pytest.CaptureFixture[str], corpus: Path, client: FakeFoundryClient
) -> None:
    code, out, _ = run(capsys, corpus, "How do I restart the API service?")

    assert code == 0
    assert out["route"] == "answer" and out["reasons"] == ["sufficient"]
    assert out["answer"] == "Done."
    citations = cast(list[dict[str, str]], out["citations"])
    assert len(citations) == 1
    assert citations[0]["sourceId"] == "handbook.md"
    assert citations[0]["path"].startswith("/handbook.md#")
    assert out["includedEvidenceCount"] == 1 and out["omittedEvidenceCount"] == 0
    assert out["modelId"] == "fake-model" and out["auditEventCount"] == 5
    sent = " ".join(message.text for message in client.requests[0])
    assert "CORPUS-MARKER-RESTART" in sent
    assert "CORPUS-MARKER-BILLING" not in sent  # irrelevant sections are never sent
    assert client.closed
    assert "CORPUS-MARKER" not in json.dumps(out)


def test_off_topic_question_abstains_without_calling_the_model(
    capsys: pytest.CaptureFixture[str], corpus: Path, client: FakeFoundryClient
) -> None:
    code, out, _ = run(capsys, corpus, "What is the capital of France?")

    assert code == 0
    assert out["route"] == "abstain" and out["reasons"] == ["missing"]
    assert out["answer"] == "I can't answer this from the evidence available to me."
    assert out["citations"] == [] and out["modelId"] is None
    assert out["retrievalAttempts"] == 2 and out["auditEventCount"] == 3
    assert client.requests == [] and client.closed


def test_a_question_only_partly_covered_by_the_corpus_is_weak_and_abstains(
    capsys: pytest.CaptureFixture[str], corpus: Path, client: FakeFoundryClient
) -> None:
    code, out, _ = run(capsys, corpus, "restart the billing database cluster")

    assert code == 0
    assert out["route"] == "abstain" and out["reasons"] == ["weak"]
    assert client.requests == []


def test_lowering_the_minimum_score_lets_a_partly_covered_question_through(
    capsys: pytest.CaptureFixture[str], corpus: Path, client: FakeFoundryClient
) -> None:
    code, out, _ = run(
        capsys, corpus, "restart the billing database cluster", "--minimum-score", "0.1"
    )

    assert code == 0 and out["route"] == "answer"
    assert len(client.requests) == 1


def test_a_caller_without_the_corpus_label_gets_nothing_and_no_model_call(
    capsys: pytest.CaptureFixture[str], corpus: Path, client: FakeFoundryClient
) -> None:
    code, out, _ = run(
        capsys,
        corpus,
        "How do I restart the API service?",
        "--corpus-label",
        "group:ops",
    )

    assert code == 0 and out["route"] == "abstain"
    assert client.requests == []

    code, out, _ = run(
        capsys,
        corpus,
        "How do I restart the API service?",
        "--corpus-label",
        "group:ops",
        "--caller-label",
        "group:ops",
    )

    assert code == 0 and out["route"] == "answer"
    assert len(client.requests) == 1


@pytest.mark.parametrize(
    ("profile", "message"),
    [
        (ROOT / "config" / "profiles" / "assistant.yaml", "requires the 'grounded-assistant'"),
    ],
    ids=["wrong-profile"],
)
def test_unsupported_profiles_fail_before_a_model_client_is_created(
    capsys: pytest.CaptureFixture[str],
    corpus: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile: Path,
    message: str,
) -> None:
    def forbidden(config: object) -> None:
        raise AssertionError("A model client must not be created for an unsupported profile")

    monkeypatch.setattr(cli, "create_foundry_chat_client", forbidden)

    code, out, err = run(capsys, corpus, "How do I restart the API service?", profile=profile)

    assert code == 2 and out == {}
    assert err["error"] == "configuration_error" and message in err["message"]


AZURE_PROFILE = ROOT / "config" / "profiles" / "grounded-assistant.yaml"


def azure_profile(tmp_path: Path, **retrieval: Any) -> Path:
    document = yaml.safe_load(AZURE_PROFILE.read_text(encoding="utf-8"))
    document["retrieval"].update(retrieval)
    path = tmp_path / "azure-profile.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def run_azure(
    capsys: pytest.CaptureFixture[str], profile: Path, *extra: str
) -> tuple[int, dict[str, Any], dict[str, Any]]:
    exit_code = main(
        [
            "grounded-run",
            "--profile",
            str(profile),
            "--config-schema",
            str(ROOT / "config" / "schema" / "v1" / "harness.schema.json"),
            "--event-schema",
            str(ROOT / "config" / "schema" / "v1" / "run-event.schema.json"),
            "--tenant",
            "tenant-0001",
            "--subject",
            "subject-0001",
            "--question",
            "How do I restart the API service?",
            *extra,
        ]
    )
    captured = capsys.readouterr()
    out: dict[str, Any] = json.loads(captured.out) if captured.out else {}
    err: dict[str, Any] = json.loads(captured.err) if captured.err else {}
    return exit_code, out, err


def forbid_model_client(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(config: object) -> None:
        raise AssertionError("A model client must not be created before configuration is valid")

    monkeypatch.setattr(cli, "create_foundry_chat_client", forbidden)


@pytest.mark.parametrize("strategy", ["vector", "hybrid"])
def test_unimplemented_azure_strategies_fail_before_a_model_client_is_created(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    strategy: str,
) -> None:
    forbid_model_client(monkeypatch)

    code, out, err = run_azure(capsys, azure_profile(tmp_path, strategy=strategy))

    assert code == 2 and out == {}
    assert err["error"] == "configuration_error"
    assert f"strategy '{strategy}' is not implemented" in err["message"]


def test_azure_profile_rejects_a_local_corpus_before_a_model_client_is_created(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, corpus: Path
) -> None:
    forbid_model_client(monkeypatch)

    code, out, err = run_azure(capsys, AZURE_PROFILE, "--corpus", str(corpus))

    assert code == 2 and out == {}
    assert err["error"] == "configuration_error" and "--corpus" in err["message"]


def test_azure_profile_without_its_endpoint_variable_fails_before_a_model_client_is_created(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    forbid_model_client(monkeypatch)
    monkeypatch.delenv("AZURE_SEARCH_ENDPOINT", raising=False)

    code, out, err = run_azure(capsys, AZURE_PROFILE)

    assert code == 2 and out == {}
    assert "'AZURE_SEARCH_ENDPOINT' is not set" in err["message"]


def test_in_memory_profile_requires_a_corpus(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    forbid_model_client(monkeypatch)

    code, out, err = run_azure(capsys, LOCAL_PROFILE)

    assert code == 2 and out == {}
    assert "--corpus is required" in err["message"]


@pytest.fixture
def strong_but_not_stronger_than_the_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve one item scored 0.6: below the shipped profile's 0.625, above the old 0.5 default."""

    provider = InMemoryRetrievalProvider([make_evidence("chunk-a", score=0.6)])

    def serve(config: object, *, environ: object) -> InMemoryRetrievalProvider:
        del config, environ
        return provider

    monkeypatch.setattr(cli, "create_azure_search_provider", serve)


def test_azure_profile_applies_its_own_minimum_score_and_reports_no_local_corpus(
    capsys: pytest.CaptureFixture[str],
    client: FakeFoundryClient,
    strong_but_not_stronger_than_the_profile: None,
) -> None:
    code, out, _ = run_azure(
        capsys, AZURE_PROFILE, "--caller-label", "group:readers", "--caller-label", "group:ops"
    )

    assert code == 0
    assert out["route"] == "clarify"
    assert out["chunkCount"] is None
    assert client.requests == []


def test_an_explicit_minimum_score_overrides_the_profile(
    capsys: pytest.CaptureFixture[str],
    client: FakeFoundryClient,
    strong_but_not_stronger_than_the_profile: None,
) -> None:
    code, out, _ = run_azure(
        capsys,
        AZURE_PROFILE,
        "--minimum-score",
        "0.5",
        "--caller-label",
        "group:readers",
        "--caller-label",
        "group:ops",
    )

    assert code == 0 and out["route"] == "answer"
    assert out["chunkCount"] is None
    assert len(client.requests) == 1


@pytest.mark.parametrize(
    ("corpus_arg", "extra", "error"),
    [
        ("absent", (), "input_validation_error"),
        ("empty", (), "input_validation_error"),
        ("ok", ("--minimum-score", "1.5"), "configuration_error"),
        ("ok", ("--fresh-days", "0"), "input_validation_error"),
        ("ok", ("--caller-label", "bad label"), "input_validation_error"),
    ],
)
def test_invalid_inputs_are_reported_as_typed_errors_without_output(
    capsys: pytest.CaptureFixture[str],
    corpus: Path,
    client: FakeFoundryClient,
    tmp_path: Path,
    corpus_arg: str,
    extra: tuple[str, ...],
    error: str,
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    chosen = {"absent": tmp_path / "absent", "empty": empty, "ok": corpus}[corpus_arg]

    code, out, err = run(capsys, chosen, "How do I restart the API service?", *extra)

    assert code == 2 and out == {} and err["error"] == error
    assert client.requests == []


def test_missing_foundry_settings_are_a_configuration_error(
    capsys: pytest.CaptureFixture[str], corpus: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", raising=False)

    code, out, err = run(capsys, corpus, "How do I restart the API service?")

    assert code == 2 and out == {} and err["error"] == "configuration_error"
