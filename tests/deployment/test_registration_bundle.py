"""Verify the embedded runner and disabled-by-default ARM bridge without Azure."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import pytest

from agent_harness.app.package_registration import PACKAGES, SOURCES, build_bundle

ROOT = Path(__file__).parents[2]
BOOTSTRAP = (ROOT / "infra" / "scripts" / "register-agent.sh").read_text()
PYTHON_BOOTSTRAP = BOOTSTRAP.split("python3 - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


def test_bundle_is_reproducible_allowlisted_and_current() -> None:
    expected = build_bundle(ROOT)
    assert expected == build_bundle(ROOT)
    assert expected == (ROOT / "infra" / "registration-bundle.json").read_text()
    assert len(expected.encode()) < 32000
    bundle = json.loads(expected)
    payload = base64.b64decode(bundle["base64"], validate=True)
    assert hashlib.sha256(payload).hexdigest() == bundle["sha256"]
    with ZipFile(io.BytesIO(payload)) as archive:
        assert set(archive.namelist()) == (
            set(SOURCES) | {f"{name}/__init__.py" for name in PACKAGES} | {"requirements.txt"}
        )
        for name in SOURCES:
            assert archive.read(name).decode() == (ROOT / "src" / name).read_text()
        requirements = archive.read("requirements.txt").decode()
        assert "azure-identity==1.26.0" in requirements
        assert "pydantic==2.12.5" in requirements
        assert "--hash=sha256:" in requirements
        assert "agent-framework" not in requirements
        assert "agentserver" not in requirements
        assert "http://" not in requirements
        assert "https://" not in requirements


def test_archive_entry_point_runs_away_from_source_checkout(tmp_path: Path) -> None:
    bundle = json.loads(build_bundle(ROOT))
    archive = tmp_path / "runner.zip"
    archive.write_bytes(base64.b64decode(bundle["base64"]))
    # Explicit dependency path, not the installed harness or current directory.
    dependencies = next(
        path for path in sys.path if (Path(path) / "pydantic" / "__init__.py").is_file()
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join([str(archive), dependencies])
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-m",
            "agent_harness.app.register_hosted_agent",
            "--help",
        ],
        env=environment,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "--managed-identity-client-id" in result.stdout


def bootstrap_environment(tmp_path: Path) -> dict[str, str]:
    return {
        "HARNESS_RUNNER_BUNDLE": build_bundle(ROOT),
        "HARNESS_REGISTRATION_TARGET": '{"project_endpoint":"invalid"}',
        "HARNESS_REGISTRATION_RELEASE": '{"image":"invalid"}',
        "HARNESS_REGISTRATION_CLIENT_ID": "test-client",
        "HARNESS_EXCLUSIVE_OWNER": "true",
        "HARNESS_ALLOW_INITIAL_EXPOSURE": "false",
        "AZ_SCRIPTS_OUTPUT_PATH": str(tmp_path / "outputs.json"),
    }


def test_bootstrap_uses_hash_checked_binary_dependencies_and_same_cli(tmp_path: Path) -> None:
    receipt = {"candidate_version": "5", "smoke_verified": False, "promoted": False}
    with (
        patch.dict(os.environ, bootstrap_environment(tmp_path)),
        patch(
            "subprocess.run",
            side_effect=[
                subprocess.CompletedProcess([], 0),
                subprocess.CompletedProcess([], 0),
                subprocess.CompletedProcess([], 0, json.dumps(receipt), ""),
            ],
        ) as run,
    ):
        exec(compile(PYTHON_BOOTSTRAP, "<bootstrap>", "exec"), {})
        venv_command = run.call_args_list[0].args[0]
        assert venv_command[:4] == [sys.executable, "-I", "-m", "venv"]
        assert run.call_args_list[0].kwargs["timeout"] == 60
        pip_command = run.call_args_list[1].args[0]
        assert pip_command[0] == str(Path(venv_command[4]) / "bin" / "python")
        assert pip_command[1:4] == ["-I", "-m", "pip"]
        assert "--require-hashes" in pip_command
        assert "--only-binary=:all:" in pip_command
        assert "--isolated" in pip_command
        assert "--target" in pip_command
        assert pip_command[pip_command.index("--index-url") + 1] == "https://pypi.org/simple"
        assert run.call_args_list[1].kwargs["timeout"] == 300
        cli = run.call_args_list[2]
        assert cli.args[0][1:4] == ["-S", "-m", "agent_harness.app.register_hosted_agent"]
        assert "--allow-initial-exposure" not in cli.args[0]
        assert cli.kwargs["timeout"] == 660
    assert json.loads((tmp_path / "outputs.json").read_text()) == receipt


@pytest.mark.parametrize("problem", ["integrity", "ownership", "python"])
def test_bootstrap_rejects_invalid_preconditions_before_install(
    tmp_path: Path, problem: str
) -> None:
    environment = bootstrap_environment(tmp_path)
    if problem == "integrity":
        bundle = json.loads(environment["HARNESS_RUNNER_BUNDLE"])
        bundle["sha256"] = "0" * 64
        environment["HARNESS_RUNNER_BUNDLE"] = json.dumps(bundle)
    if problem == "ownership":
        environment["HARNESS_EXCLUSIVE_OWNER"] = "false"
    with patch.dict(os.environ, environment), patch("subprocess.run") as run:
        with patch("sys.version_info", (3, 11) if problem == "python" else (3, 12)):
            with pytest.raises(SystemExit):
                exec(compile(PYTHON_BOOTSTRAP, "<bootstrap>", "exec"), {})
        run.assert_not_called()
    assert not (tmp_path / "outputs.json").exists()


@pytest.mark.parametrize("problem", ["venv", "install", "runner", "timeout"])
def test_bootstrap_never_publishes_success_on_failure(tmp_path: Path, problem: str) -> None:
    failure = subprocess.CalledProcessError(1, ["pip"])
    effects: list[object]
    if problem == "venv":
        effects = [failure]
    elif problem == "install":
        effects = [subprocess.CompletedProcess([], 0), failure]
    elif problem == "runner":
        effects = [
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 1, "", "deployment_error: safe failure\n"),
        ]
    else:
        effects = [
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 0),
            subprocess.TimeoutExpired(["runner"], 660),
        ]
    with (
        patch.dict(os.environ, bootstrap_environment(tmp_path)),
        patch("subprocess.run", side_effect=effects) as run,
    ):
        with pytest.raises((SystemExit, subprocess.CalledProcessError)):
            exec(compile(PYTHON_BOOTSTRAP, "<bootstrap>", "exec"), {})
        assert run.call_count == {"venv": 1, "install": 2, "runner": 3, "timeout": 3}[problem]
    assert not (tmp_path / "outputs.json").exists()


def test_arm_bridge_uses_existing_identity_without_role_grants_and_bounds_script() -> None:
    template = json.loads((ROOT / "infra" / "main.json").read_text())
    bridge = template["resources"]["registration"]
    assert bridge["condition"] == "[not(equals(parameters('registrationConfig'), null()))]"
    resources = bridge["properties"]["template"]["resources"]
    assert set(resources) == {"identity", "script"}
    assert resources["identity"]["existing"] is True
    assert resources["identity"]["type"] == "Microsoft.ManagedIdentity/userAssignedIdentities"
    parameters = {
        key: value["value"]
        for key, value in resources["script"]["properties"]["parameters"].items()
    }
    assert parameters["kind"] == "AzureCLI"
    assert parameters["azCliVersion"] == "2.89.1"
    assert parameters["timeout"] == "PT20M"
    assert parameters["cleanupPreference"] == "Always"
    assert parameters["retentionInterval"] == "PT24H"
    assert parameters["enableTelemetry"] is False
    assert parameters["runOnce"] is False
    assert "uniqueString(" in parameters["baseTime"]
    assert "utcNow" not in parameters["baseTime"]
    assert "roleAssignments" not in parameters
    assert "primaryScriptUri" not in parameters
    script_variable = parameters["scriptContent"].removeprefix("[variables('").removesuffix("')]")
    assert bridge["properties"]["template"]["variables"][script_variable] == BOOTSTRAP
    module_parameters = resources["script"]["properties"]["template"]["parameters"]
    assert module_parameters["roleAssignments"]["nullable"] is True
    assert parameters["managedIdentities"]["userAssignedResourceIds"] == [
        "[resourceId('Microsoft.ManagedIdentity/userAssignedIdentities', "
        "parameters('identityName'))]"
    ]
    assert template["outputs"]["HARNESS_REGISTRATION_RECEIPT"]["value"].endswith(
        ".outputs.receipt.value, createObject())]"
    )
