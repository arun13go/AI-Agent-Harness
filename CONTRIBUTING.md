# Contributing

## Prerequisites

Install the supported tools in
[docs/getting-started/toolchain.md](docs/getting-started/toolchain.md). Azure access is not required
for deterministic schema, contract, policy, lint, or type-check validation.

## Set up a clean clone

```powershell
uv sync --frozen
```

Do not activate the virtual environment manually. Prefix repository commands with `uv run`.

## Validate changes

Run the smallest check that covers the change:

```powershell
uv run pytest tests\schema tests\contracts
uv run pyright
uv run ruff check .
uv lock --check
```

Cloud integration and deployment checks will be added with the Phase 1 end-to-end foundation.
Never commit `.azure/`, credentials, tenant identifiers, subscription identifiers, connection
strings, or generated credentials.

## Dependency updates

Dependabot proposes grouped Python dependency updates. Accept an update only after deterministic
tests, type checking, linting, and the applicable integration/evaluation checks pass. Preview
dependencies require an explicit feature flag, a released fallback, and an architecture decision.
