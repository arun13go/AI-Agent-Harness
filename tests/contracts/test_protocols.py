"""Verify shared adapter contracts remain structural protocols."""

from typing import Protocol

from agent_harness.core import contracts


def test_adapter_contracts_are_protocols() -> None:
    contract_types = (
        contracts.ModelClient,
        contracts.RetrievalProvider,
        contracts.Chunker,
        contracts.ContextPolicy,
        contracts.MemoryStore,
        contracts.RunStateStore,
        contracts.ArtifactStore,
        contracts.ProposalStore,
        contracts.CheckpointStore,
        contracts.ApprovalProvider,
        contracts.IdempotencyLedger,
        contracts.ActionProposer,
        contracts.Tool,
        contracts.AuditSink,
        contracts.TelemetryExporter,
    )

    assert all(issubclass(contract_type, Protocol) for contract_type in contract_types)
