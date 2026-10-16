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

## Code documentation

- Start hand-maintained Python files with a short purpose and key-dependency docstring.
- Add one-line docstrings to public orchestration classes and major functions.
- Start YAML/TOML configuration with purpose and consumer comments.
- Use JSON Schema `$comment` and `description`; do not edit generated files such as `uv.lock`.
- Keep the source-file count low: extend a cohesive module before adding another file.
- Add a file only for a clear responsibility or architecture boundary; avoid both tiny wrappers and
  oversized modules with unrelated concerns.
- Name modules `<domain>_<responsibility>.py`; avoid ambiguous `runtime`, `runner`, `manager`, or
  `helper` names.
- Update the [developer code tour](docs/getting-started/code-tour.md) when adding a major runtime
  path, adapter boundary, or application entry point.

## Dependency updates

Dependabot proposes grouped Python dependency updates. Accept an update only after deterministic
tests, type checking, linting, and the applicable integration/evaluation checks pass. Preview
dependencies require an explicit feature flag, a released fallback, and an architecture decision.
