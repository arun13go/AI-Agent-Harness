set -euo pipefail
python3 - <<'PY'
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from zipfile import ZipFile

if sys.version_info[:2] != (3, 12):
    raise SystemExit("Registration bootstrap requires Python 3.12; no API request sent.")

bundle = json.loads(os.environ["HARNESS_RUNNER_BUNDLE"])
payload = base64.b64decode(bundle["base64"], validate=True)
if hashlib.sha256(payload).hexdigest() != bundle["sha256"]:
    raise SystemExit("Registration bundle integrity check failed; no API request sent.")

with tempfile.TemporaryDirectory(prefix="harness-registration-") as directory:
    work = Path(directory)
    archive = work / "runner.zip"
    archive.write_bytes(payload)
    with ZipFile(io.BytesIO(payload)) as source:
        requirements = source.read("requirements.txt")
    (work / "requirements.txt").write_bytes(requirements)
    (work / "target.json").write_text(os.environ["HARNESS_REGISTRATION_TARGET"], encoding="utf-8")
    (work / "release.json").write_text(os.environ["HARNESS_REGISTRATION_RELEASE"], encoding="utf-8")
    if os.environ["HARNESS_EXCLUSIVE_OWNER"] != "true":
        raise SystemExit("Exclusive deployment ownership must be acknowledged; no API request sent.")
    subprocess.run(
        [sys.executable, "-I", "-m", "venv", str(work / "installer")],
        check=True,
        timeout=60,
    )
    installer = work / "installer" / "bin" / "python"
    subprocess.run(
        [
            str(installer), "-I", "-m", "pip", "--isolated", "install",
            "--index-url", "https://pypi.org/simple",
            "--require-hashes", "--only-binary=:all:", "--ignore-installed",
            "--disable-pip-version-check", "--no-compile", "--retries", "2", "--timeout", "30",
            "--target", str(work / "dependencies"), "-r", str(work / "requirements.txt"),
        ],
        check=True,
        timeout=300,
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join([str(work / "dependencies"), str(archive)])
    command = [
        sys.executable, "-S", "-m", "agent_harness.app.register_hosted_agent",
        "--target", str(work / "target.json"), "--release", str(work / "release.json"),
        "--managed-identity-client-id", os.environ["HARNESS_REGISTRATION_CLIENT_ID"],
        "--exclusive-deployment-owner", "--timeout-seconds", "600",
    ]
    if os.environ["HARNESS_ALLOW_INITIAL_EXPOSURE"] == "true":
        command.append("--allow-initial-exposure")
    # A hard process bound supplements the runner's cooperative deadline.
    try:
        result = subprocess.run(
            command, env=environment, cwd=work, check=False, capture_output=True,
            text=True, timeout=660,
        )
    except subprocess.TimeoutExpired:
        raise SystemExit("Registration process deadline exceeded; outcome unknown. Do not blindly retry.")
    if result.returncode:
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    receipt = json.loads(result.stdout)
    output = Path(os.environ["AZ_SCRIPTS_OUTPUT_PATH"])
    output.write_text(json.dumps(receipt), encoding="utf-8")
    print("Registration runner completed; inspect deployment outputs for candidate status.")
PY
