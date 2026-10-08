# ADR 0008: Use GPT-5.4 Mini as the Initial Reference Model

- **Status:** Accepted
- **Date:** 8 October 2026

## Context

The walking skeleton needs a current Foundry model with chat completion and Responses support,
structured tool use, streaming, and a cost/latency profile suitable for repeated development and
evaluation. The model is a tested baseline, not part of the framework-neutral model-client
contract.

The live Microsoft Foundry catalog was checked on 8 October 2026. It lists `gpt-5.4-mini`, version
`2026-03-17`, with chat-completion and Responses inference tasks and Azure OpenAI deployment.

## Decision

Use `gpt-5.4-mini` model version `2026-03-17` as the initial v1.0 development and evaluation
baseline. Read the customer-defined deployment name from `AZURE_AI_MODEL_DEPLOYMENT_NAME`.

Before provisioning, verify regional availability, lifecycle status, quota, and required
capabilities. Do not commit endpoints, resource identifiers, or deployment names. Model upgrades
require evaluation comparison and a recorded baseline update.

## Consequences

- Tool behavior, latency, token usage, and evaluation thresholds have a reproducible baseline.
- Customers can implement another model client only after capability checks and conformance tests.
- The deployment can fail preflight in a region or subscription without capacity; deployment must
  provide actionable guidance rather than silently selecting another model.
