# ADR 0009: Add Agent Inspector with the HTTP Host

- **Status:** Accepted
- **Date:** 14 October 2026

## Decision

Phase 1C proves the governed Microsoft Agent Framework runtime against a live Microsoft Foundry
model deployment through the existing CLI. VS Code Agent Inspector integration moves to Phase 1D,
where the repository introduces its supported HTTP application boundary.

The Inspector path will use the released Microsoft Foundry Responses host rather than a custom
wire protocol. The host adapter must preserve identity scope, bounded execution, registered-tool
governance, source labels, audit events, error normalization, and trace correlation before it is
accepted.

## Rationale

Agent Inspector connects to an HTTP server that implements a supported hosted-agent protocol. The
Phase 1C application is intentionally CLI-only. Adding debug configuration without a real server
would create a broken F5 path, while wrapping a raw MAF agent directly would bypass the existing
`MafAgentExecutor` governance boundary.

The HTTP host also introduces new released dependencies and lifecycle concerns that belong with
the Azure Dev hosting work in Phase 1D. Live model validation does not require that boundary and
can proceed through the already implemented passwordless Foundry client.

## Consequences

- Phase 1C exits after a real Foundry invocation selects a registered tool with correlated model
  and tool evidence.
- Phase 1D owns the supported Responses HTTP host, VS Code launch/tasks configuration, and Agent
  Inspector evidence.
- Inspector integration cannot construct a parallel agent that bypasses `MafAgentExecutor`
  controls.
- The CLI remains the supported Phase 1C local entry point.

## References

- [Basic hosted Agent Framework sample](https://github.com/microsoft-foundry/foundry-samples/tree/main/samples/python/hosted-agents/agent-framework/responses/01-basic)
- [Microsoft Agent Framework Foundry hosting package](https://pypi.org/project/agent-framework-foundry-hosting/)