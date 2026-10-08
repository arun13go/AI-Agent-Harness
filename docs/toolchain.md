# Toolchain Policy

## Supported baseline

| Tool | Supported version | Enforcement |
|---|---:|---|
| Python | 3.12.x | `.python-version` and `requires-python` |
| uv | 0.11.15 | `tool.uv.required-version` |
| Git | 2.55.0 | environment preflight |
| Azure CLI | 2.81.0 | environment preflight |
| Bicep CLI | 0.40.2 | environment preflight |
| Microsoft Agent Framework Core | 1.19.0 | exact direct dependency |
| Microsoft Agent Framework Foundry adapter | 1.13.1 | exact direct dependency |
| Azure Developer CLI | 1.34.2 | environment preflight |
| `microsoft.foundry` azd extension | 1.0.0-beta.2 until a stable release exists | environment preflight; deployment tooling only |
| Ruff | 0.16.9 | development dependency |
| Pyright | 1.1.414 | development dependency |
| pytest | 9.1.1 | development dependency |

Python runtime and development packages are declared in `pyproject.toml` and fully resolved in
`uv.lock`. Direct dependencies use exact versions. Transitive versions come only from the lock.

## Stability policy

- Runtime code uses released MAF packages and APIs on the v1.0 path.
- Preview integrations are not direct runtime dependencies unless an ADR records the need.
- An experimental capability must be disabled by default, have a released fallback, and be covered
  by tests for both paths.
- `uv lock --check` rejects a stale lock file.
- Dependabot proposes dependency updates; updates do not merge without validation.

The `microsoft.foundry` azd extension is currently beta and is tooling rather than imported runtime
code. Phase 1 deployment must pin and preflight the exact compatible extension version until a
stable release is available.

Tool upgrades are reviewed changes to this table and the Phase 1 environment preflight. Dependency
and tool updates require the deterministic checks below plus applicable cloud smoke and evaluation
checks after those suites exist.

## Foundry model baseline

The reference model is `gpt-5.4-mini`, model version `2026-03-17`, selected in ADR 0008. A customer
supplies a deployment name through the environment variable named by configuration. No endpoint,
tenant, subscription, resource group, or deployment identifier is committed.

Deployment preflight must verify regional availability, model lifecycle, quota, and the required
chat completion, Responses, streaming, structured-output, and tool-calling capabilities.
