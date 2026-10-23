"""Verify registration HTTP behavior and safe command-line failures on loopback."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import ClassVar

import pytest
from azure.core.credentials import AccessToken
from azure.core.exceptions import ClientAuthenticationError

from agent_harness.adapters.foundry.registration import (
    DeploymentError,
    DeploymentUncertainError,
    ManagedIdentityTransport,
)


class Credential:
    def __init__(self, *, fail: bool = False) -> None:
        self.scopes: tuple[str, ...] = ()
        self.fail = fail

    def get_token(self, *scopes: str, **kwargs: object) -> AccessToken:
        self.scopes = scopes
        if self.fail:
            raise ClientAuthenticationError("sensitive-credential-detail")
        return AccessToken("fake-loopback-only-token", 9999999999)


class Handler(BaseHTTPRequestHandler):
    paths: ClassVar[list[str]]
    authorization: ClassVar[str | None]

    def do_GET(self) -> None:
        self.paths.append(self.path)
        type(self).authorization = self.headers.get("Authorization")
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/must-not-follow")
            self.end_headers()
        elif self.path == "/failure":
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"sensitive-provider-response")
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"x" * 1_048_577 if self.path == "/oversize" else b'{"ok":true}')

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def server() -> Iterator[str]:
    Handler.paths = []
    Handler.authorization = None
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_credential_audience_and_http_response(server: str) -> None:
    credential = Credential()
    response = ManagedIdentityTransport(credential).request("GET", server + "/ok", None, 2)
    assert credential.scopes == ("https://ai.azure.com/.default",)
    assert Handler.authorization == "Bearer fake-loopback-only-token"
    assert response.status == 200
    assert json.loads(response.body) == {"ok": True}


def test_redirect_is_not_followed(server: str) -> None:
    response = ManagedIdentityTransport(Credential()).request("GET", server + "/redirect", None, 2)
    assert response.status == 302
    assert response.body == b""
    assert Handler.paths == ["/redirect"]


def test_provider_error_body_is_not_returned_or_retried(server: str) -> None:
    response = ManagedIdentityTransport(Credential()).request("GET", server + "/failure", None, 2)
    assert response.status == 503
    assert response.body == b""
    assert Handler.paths == ["/failure"]


def test_response_size_is_bounded(server: str) -> None:
    with pytest.raises(DeploymentUncertainError, match="size limit"):
        ManagedIdentityTransport(Credential()).request("GET", server + "/oversize", None, 2)


def test_credential_failure_is_sanitized_before_sending(server: str) -> None:
    with pytest.raises(DeploymentError) as error:
        ManagedIdentityTransport(Credential(fail=True)).request("GET", server + "/ok", None, 2)
    assert "sensitive-credential-detail" not in str(error.value)
    assert Handler.paths == []


def test_command_help_is_available_without_identity() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "agent_harness.app.register_hosted_agent", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    assert "--exclusive-deployment-owner" in result.stdout
    assert "--managed-identity-client-id" in result.stdout


def test_command_rejects_invalid_inputs_without_disclosing_them(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text('{"secret":"sensitive-input-value"}', encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_harness.app.register_hosted_agent",
            "--target",
            str(target),
            "--release",
            str(tmp_path / "missing-release.json"),
            "--managed-identity-client-id",
            "11111111-1111-4111-8111-111111111111",
            "--exclusive-deployment-owner",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("deployment_input_error:")
    assert "sensitive-input-value" not in result.stderr
    assert "Traceback" not in result.stderr
