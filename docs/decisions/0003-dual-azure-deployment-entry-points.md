# ADR 0003: Support Two Azure Deployment Entry Points

- **Status:** Accepted
- **Date:** 17 September 2026

## Decision

Version 1.0 supports both `azd up` and a one-click **Deploy to Azure** button.

The canonical source is modular Bicep. `azd up` consumes the Bicep directly; the portal button consumes an ARM artifact generated from it. CI rejects template drift and verifies equivalent parameters, RBAC, outputs and smoke tests.

## Consequences

- Developers can use `azd up`; portal users can use one-click deployment.
- Both paths create the same Azure Dev topology.
- The generated ARM artifact must not be edited independently.
- Authentication, quota and model-capacity failures must be surfaced before or during deployment with actionable guidance.