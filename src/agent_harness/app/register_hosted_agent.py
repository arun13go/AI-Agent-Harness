"""Run the passwordless hosted registration adapter as deployment tooling."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from azure.identity import ManagedIdentityCredential
from pydantic import ValidationError

from agent_harness.adapters.foundry.deployment import FoundryDeploymentTarget, HostedAgentRelease
from agent_harness.adapters.foundry.registration import (
    DeploymentError,
    DeploymentLimits,
    HostedAgentRegistrar,
    ManagedIdentityTransport,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--managed-identity-client-id", required=True)
    parser.add_argument("--exclusive-deployment-owner", action="store_true", required=True)
    parser.add_argument("--allow-initial-exposure", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args()
    try:
        target = FoundryDeploymentTarget.model_validate_json(
            args.target.read_text(encoding="utf-8")
        )
        release = HostedAgentRelease.model_validate_json(args.release.read_text(encoding="utf-8"))
        limits = DeploymentLimits(timeout_seconds=args.timeout_seconds)
        with ManagedIdentityCredential(
            client_id=args.managed_identity_client_id,
            connection_timeout=10,
            read_timeout=10,
            retry_total=0,
        ) as credential:
            receipt = HostedAgentRegistrar(
                target, ManagedIdentityTransport(credential), limits
            ).register(
                release,
                exclusive_deployment_owner=args.exclusive_deployment_owner,
                allow_initial_exposure=args.allow_initial_exposure,
            )
        print(json.dumps(asdict(receipt)))
    except (OSError, ValidationError):
        print("deployment_input_error: cannot read or validate deployment inputs", file=sys.stderr)
        return 1
    except DeploymentError as error:
        print(f"{error.code}: {error.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
