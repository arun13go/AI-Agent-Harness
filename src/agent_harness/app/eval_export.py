"""Export evaluation rows in the shape Foundry agent evaluation takes as ``inputData``.

Each row holds exactly a ``query`` and an ``expected_behavior`` (ADR 0034). Row order follows the
source file, so a row number always points back to the same question; the dataset version belongs
in the release report's run labels.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agent_harness.app.retrieval_eval import EvaluationQuestion, load_evaluation_dataset
from agent_harness.core.errors import ConfigurationError

type Row = dict[str, str]

_REFUSAL = (
    "Does not answer, because the handbook has no such information; it declines or asks for more "
    "detail. Does not guess or use outside knowledge."
)


class AssistantRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    category: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")
    query: str = Field(min_length=1, max_length=2048)
    expected_behavior: str = Field(min_length=1, max_length=1024)


class AssistantRows(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    name: str = Field(min_length=1, max_length=64)
    rows: tuple[AssistantRow, ...] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _ids_are_unique(self) -> Self:
        ids = [row.id for row in self.rows]
        if len(set(ids)) != len(ids):
            raise ValueError("row ids must be unique")
        return self


def load_assistant_rows(path: Path) -> tuple[AssistantRow, ...]:
    """Load the assistant tool-call, scope and injection rows."""

    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ConfigurationError(f"Cannot read assistant rows '{path.name}'") from error
    try:
        return AssistantRows.model_validate(value).rows
    except ValidationError as error:
        raise ConfigurationError(f"Invalid assistant rows '{path.name}': {error}") from error


def _grounded_behavior(question: EvaluationQuestion) -> str:
    if question.expected_route != "answer":
        return _REFUSAL
    sources = ", ".join(question.expected_sources)
    return (
        f"Answers from the operations handbook using only retrieved evidence, and cites {sources}."
    )


def export_rows(target: str, *, grounded_dir: Path, assistant_file: Path) -> list[Row]:
    """Return the rows for ``grounded`` (the question set) or ``assistant``."""

    if target == "grounded":
        dataset = load_evaluation_dataset(grounded_dir)
        return [
            {"query": question.question, "expected_behavior": _grounded_behavior(question)}
            for question in dataset.questions
        ]
    if target == "assistant":
        return [
            {"query": row.query, "expected_behavior": row.expected_behavior}
            for row in load_assistant_rows(assistant_file)
        ]
    raise ConfigurationError(f"Unknown export target '{target}'")


def write_rows(path: Path, rows: list[Row]) -> None:
    """Write the row array as JSON."""

    try:
        path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    except OSError as error:
        raise ConfigurationError(f"Cannot write '{path.name}'") from error
