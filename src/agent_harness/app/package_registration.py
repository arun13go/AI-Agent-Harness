"""Build a deterministic, allowlisted registration source bundle for Bicep embedding."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

SOURCES = (
    "agent_harness/adapters/foundry/deployment.py",
    "agent_harness/adapters/foundry/registration.py",
    "agent_harness/app/register_hosted_agent.py",
    "agent_harness/core/errors.py",
    "agent_harness/core/types.py",
)
PACKAGES = (
    "agent_harness",
    "agent_harness/adapters",
    "agent_harness/adapters/foundry",
    "agent_harness/app",
    "agent_harness/core",
)


def build_bundle(root: Path) -> str:
    files = {
        name: (root / "src" / name).read_text(encoding="utf-8").replace("\r\n", "\n")
        for name in SOURCES
    }
    files.update({f"{name}/__init__.py": "" for name in PACKAGES})
    files["requirements.txt"] = (
        (root / "infra" / "registration-requirements.txt")
        .read_text(encoding="utf-8")
        .replace("\r\n", "\n")
    )
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(files.items()):
            entry = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.compress_type = ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, content.encode("utf-8"), compresslevel=9)
    payload = stream.getvalue()
    return (
        json.dumps(
            {
                "sha256": hashlib.sha256(payload).hexdigest(),
                "base64": base64.b64encode(payload).decode("ascii"),
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = args.root / "infra" / "registration-bundle.json"
    expected = build_bundle(args.root)
    if args.check:
        if output.read_text(encoding="utf-8") != expected:
            parser.exit(1, "Registration bundle drift; regenerate before compiling Bicep.\n")
    else:
        output.write_text(expected, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
