# Contributing

## Prerequisites

Install the supported tools in [docs/toolchain.md](docs/toolchain.md). Azure access is not
required for deterministic schema, contract, policy, lint, or type-check validation.

## Set up a clean clone

```powershell
uv sync --frozen
```

If your network uses the Microsoft package feed proxy configured for this environment:

```powershell
uv sync --frozen --index "https://pypi.org/simple/"
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

Cloud integration and deployment checks will be added with the Phase 1 walking skeleton.
Never commit `.azure/`, credentials, tenant identifiers, subscription identifiers, connection
strings, or generated credentials.

## Dependency updates

Dependabot proposes grouped Python dependency updates. Accept an update only after deterministic
tests, type checking, linting, and the applicable integration/evaluation checks pass. Preview
dependencies require an explicit feature flag, a released fallback, and an architecture decision.
