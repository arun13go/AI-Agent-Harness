"""Prove the live isolation check's expectations match the provider's rules, without a service."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from agent_harness.app.search_live import CANARY_ITEMS, check_isolation
from tests.conformance.test_azure_search_retrieval import FakeSearchBackend, LeakyBackend


class FakeIndex(FakeSearchBackend):
    def __init__(self) -> None:
        super().__init__([])

    async def create_index(self) -> None:
        return None

    async def upload(self, documents: Sequence[Mapping[str, Any]]) -> int:
        self.documents = list(documents)
        return len(documents)


class LeakyIndex(LeakyBackend):
    def __init__(self) -> None:
        super().__init__([])

    async def create_index(self) -> None:
        return None

    async def upload(self, documents: Sequence[Mapping[str, Any]]) -> int:
        self.documents = [{**document, "@search.score": 5.0} for document in documents]
        return len(documents)


@pytest.mark.asyncio
async def test_expectations_hold_for_a_correct_backend() -> None:
    report = await check_isolation(FakeIndex(), settle_seconds=0.0)

    assert report["passed"] is True
    cases: Any = report["cases"]
    assert len(cases) == 8
    assert all(case["error"] is None for case in cases)
    assert {item.tenant_scope for item in CANARY_ITEMS} == {
        "tenant-canary-a01",
        "tenant-canary-b01",
    }


@pytest.mark.asyncio
async def test_a_backend_that_ignores_the_filter_fails_the_check_without_leaking_content() -> None:
    report = await check_isolation(LeakyIndex(), settle_seconds=0.0)

    assert report["passed"] is False
    cases: Any = report["cases"]
    assert any(case["error"] == "AuthorizationError" for case in cases)
    assert "zebrafish" not in str(report)
