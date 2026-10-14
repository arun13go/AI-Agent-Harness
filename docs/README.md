# Documentation Guide

> **Audience:** everyone using or changing the repository  
> **Status:** current and authoritative navigation  
> **Purpose:** identify what to read, what is authoritative, and where each concern belongs

Start with the smallest reading path that matches your task. Most contributors do not need to read
the full pattern catalogue or historical blueprint.

## Ten-minute orientation

Read these in order:

1. [Root README](../README.md) - product purpose, current status, quick start, and repository map.
2. [Developer code tour](getting-started/code-tour.md) - current files and end-to-end flows.
3. [Reference architecture](architecture/overview.md) - runtime flow and mandatory boundaries.
4. [Contributor guide](../CONTRIBUTING.md) - setup and validation commands.

You should then know what the repository builds, what exists today, how to validate it, and what
Phase 1 delivers next.

## Developer path

Before changing implementation:

1. Complete the ten-minute orientation.
2. Read the [implementation plan](delivery/implementation-plan.md) for current status and sequence.
3. Use the [developer code tour](getting-started/code-tour.md) to trace the relevant runtime path.
4. Read the [adapter contracts](architecture/adapter-contracts.md) when adding or changing an
   adapter.
5. Inspect the relevant [configuration schema](../config/schema/v1/harness.schema.json), protocol,
   profile, and test rather than reading every document.

## Architecture path

Read this path only when changing scope, ownership, or cross-cutting semantics:

1. [Reference architecture](architecture/overview.md)
2. Relevant [architecture decision records](decisions/)
3. [Pattern catalogue](delivery/pattern-catalog.md) for detailed capability semantics
4. [Implementation plan](delivery/implementation-plan.md) for sequencing impact

The superseded review under [historical/](historical/) explains how the current decisions were
reached. It is not a current requirement.

## Authoritative source by subject

| Subject | Authority |
|---|---|
| Product summary and current status | [Root README](../README.md) |
| Local setup and contributor commands | [Contributor guide](../CONTRIBUTING.md) |
| Current source map and runtime walkthroughs | [Developer code tour](getting-started/code-tour.md) |
| Tool and dependency versions | [Toolchain policy](getting-started/toolchain.md) |
| Runtime architecture and mandatory controls | [Reference architecture](architecture/overview.md) |
| Adapter behavior | [Adapter contracts](architecture/adapter-contracts.md) |
| Delivery status and next work | [Implementation plan](delivery/implementation-plan.md) |
| Detailed capability semantics | [Pattern catalogue](delivery/pattern-catalog.md) |
| Configuration validity | [JSON Schema](../config/schema/v1/harness.schema.json) |
| Individual architecture choices | [ADRs](decisions/) |
| AI coding-agent behavior | [AGENTS.md](../AGENTS.md) |

Other documents should link to these sources instead of copying their content.

## Repository file map

```text
.
|-- README.md                         # Human landing page and quick start
|-- CONTRIBUTING.md                   # Contributor workflow
|-- AGENTS.md                         # AI coding-agent instructions
|-- pyproject.toml / uv.lock          # Python project and exact dependency graph
|-- config/
|   |-- profiles/                     # Safe example configurations
|   `-- schema/v1/                    # Machine-enforced configuration and event contracts
|-- src/agent_harness/core/           # Framework-neutral types and protocols
|-- src/agent_harness/adapters/       # Local tools and storage adapters
|-- src/agent_harness/app/            # Read/action runners and CLI
|-- tests/                            # Deterministic schema and contract checks
`-- docs/
    |-- README.md                     # This navigation guide
    |-- architecture/                 # Current design and adapter semantics
    |-- decisions/                    # Accepted ADRs
    |-- delivery/                     # Roadmap, status, and pattern catalogue
    |-- getting-started/              # Toolchain policy and developer code tour
    `-- historical/                   # Superseded design-review provenance
```

Evaluation and infrastructure folders are added only when their first working artifacts exist.

## Current status

- **Phase 0:** complete and validated.
- **Phase 1A and 1B:** complete and validated.
- **Local runtime:** governed read and approval-gated action paths are available.
- **Model runtime:** Phase 1C is in progress with deterministic MAF composition and a live Foundry
  client path; cloud validation, streaming, and Agent Inspector evidence remain.
- **Azure deployment:** not yet available.

Use the implementation plan status table as the source of truth for future capabilities.
