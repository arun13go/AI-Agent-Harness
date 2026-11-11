"""Combine test, retrieval, quality, safety and operational results into one release verdict.

Every input is a plain file, so the report runs offline and calls no model or service. A rule
whose metric is missing fails, and the thresholds must cover every required scorecard, so a
verdict cannot pass by leaving evidence out (ADR 0034).
"""

from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, Self, cast

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, model_validator

from agent_harness.core.errors import ConfigurationError

SCORECARDS = ("policy", "retrieval", "quality", "safety", "latency", "cost", "telemetry")
_REQUIRED = ("policy", "quality", "safety", "latency", "cost", "telemetry")
_QUALITY = ("policy", "retrieval", "quality", "safety")
_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
_LABEL = re.compile(r"[A-Za-z0-9._:/@+-]{1,128}")
_UNSAFE_XML = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_MAX_FILE_BYTES = 5 * 1024 * 1024
_MAX_LABELS = 20
_EPSILON = 1e-9

type Metrics = dict[str, dict[str, float]]


def _reject_bool(value: object) -> object:
    if isinstance(value, bool):
        raise ValueError("a number is required, not a boolean")
    return value


Number = Annotated[float, BeforeValidator(_reject_bool), Field(allow_inf_nan=False)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Rule(_Strict):
    scorecard: Literal["policy", "retrieval", "quality", "safety", "latency", "cost", "telemetry"]
    metric: str = Field(pattern=f"^{_NAME.pattern}$")
    minimum: Number | None = None
    maximum: Number | None = None

    @model_validator(mode="after")
    def _exactly_one_bound(self) -> Self:
        if (self.minimum is None) == (self.maximum is None):
            raise ValueError("a rule needs exactly one of minimum or maximum")
        return self


class Prices(_Strict):
    input_per_million_tokens: Number = Field(alias="inputPerMillionTokens", ge=0)
    output_per_million_tokens: Number = Field(alias="outputPerMillionTokens", ge=0)


class Thresholds(_Strict):
    version: Literal[1]
    regression_tolerance: Number = Field(default=0.05, alias="regressionTolerance", ge=0, le=1)
    prices: Prices | None = None
    rules: tuple[Rule, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _cover_every_required_scorecard_once(self) -> Self:
        seen = [(rule.scorecard, rule.metric) for rule in self.rules]
        if len(set(seen)) != len(seen):
            raise ValueError("a metric has more than one rule")
        for card in _REQUIRED:
            if all(rule.scorecard != card for rule in self.rules):
                raise ValueError(f"no rule for the '{card}' scorecard")
        return self


@dataclass(frozen=True, slots=True)
class Finding:
    scorecard: str
    metric: str
    value: float | None
    rule: str
    passed: bool
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ReleaseReport:
    labels: dict[str, str]
    findings: tuple[Finding, ...]
    metrics: Metrics

    def _healthy(self, cards: Iterable[str]) -> bool:
        names = set(cards)
        return all(f.passed for f in self.findings if f.scorecard in names)

    @property
    def quality_health(self) -> bool:
        return self._healthy(_QUALITY)

    @property
    def operational_health(self) -> bool:
        return self._healthy(set(SCORECARDS) - set(_QUALITY))

    @property
    def passed(self) -> bool:
        return self.quality_health and self.operational_health

    @property
    def failures(self) -> tuple[str, ...]:
        return tuple(f"{f.scorecard}.{f.metric} {f.detail}" for f in self.findings if not f.passed)

    def to_json(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "qualityHealth": self.quality_health,
            "operationalHealth": self.operational_health,
            "labels": self.labels,
            "failures": list(self.failures),
            "scorecards": {
                card: {
                    "passed": all(f.passed for f in self.findings if f.scorecard == card),
                    "findings": [
                        {"metric": f.metric, "value": f.value, "rule": f.rule, "passed": f.passed}
                        for f in self.findings
                        if f.scorecard == card
                    ],
                }
                for card in SCORECARDS
                if any(f.scorecard == card for f in self.findings)
            },
            "metrics": self.metrics,
        }


def _read(path: Path, what: str) -> bytes:
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            raise ConfigurationError(f"{what} is larger than {_MAX_FILE_BYTES} bytes")
        return path.read_bytes()
    except OSError as error:
        raise ConfigurationError(f"Cannot read {what} '{path.name}'") from error


def _json(path: Path, what: str) -> object:
    try:
        return json.loads(_read(path, what))
    except ValueError as error:
        raise ConfigurationError(f"{what} '{path.name}' is not valid JSON") from error


def load_thresholds(path: Path) -> Thresholds:
    """Load the release thresholds, refusing anything malformed or incomplete."""

    try:
        value = yaml.safe_load(_read(path, "thresholds file"))
    except yaml.YAMLError as error:
        raise ConfigurationError(f"Thresholds file '{path.name}' is not valid YAML") from error
    try:
        return Thresholds.model_validate(value)
    except ValidationError as error:
        raise ConfigurationError(f"Invalid thresholds file '{path.name}': {error}") from error


def _number(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ConfigurationError(f"{where} must be a finite number")
    return float(value)


def _object(value: object, what: str) -> dict[str, object]:
    """Return a JSON object (its keys are always strings), or reject it."""

    if not isinstance(value, dict):
        raise ConfigurationError(f"{what} must be a JSON object")
    return cast("dict[str, object]", value)


def _scorecard(name: str, values: object, where: str) -> dict[str, float]:
    if name not in SCORECARDS:
        raise ConfigurationError(f"{where} names an unknown scorecard")
    result: dict[str, float] = {}
    for metric, value in _object(values, f"{where} {name}").items():
        if not _NAME.fullmatch(metric):
            raise ConfigurationError(f"{where} has an invalid metric name")
        result[metric] = _number(value, f"{where} {name}.{metric}")
    return result


def load_metrics(path: Path) -> Metrics:
    """Load ``{scorecard: {metric: number}}`` written by a tool or a previous report."""

    where = f"Metrics file '{path.name}'"
    cards = _object(_json(path, "metrics file"), where)
    return {name: _scorecard(name, values, where) for name, values in cards.items()}


def junit_metrics(path: Path) -> Metrics:
    """Count tests, failures (including errors) and skips from a pytest junit XML file."""

    data = _read(path, "junit file")
    if _UNSAFE_XML.search(data):
        raise ConfigurationError(f"Junit file '{path.name}' must not declare a DOCTYPE or entity")
    try:
        root = ET.fromstring(data)  # input is size-capped and has no DOCTYPE or entity
    except ET.ParseError as error:
        raise ConfigurationError(f"Junit file '{path.name}' is not valid XML") from error
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    if root.tag not in ("testsuite", "testsuites") or not suites:
        raise ConfigurationError(f"Junit file '{path.name}' has no test suite")
    totals = dict.fromkeys(("tests", "failures", "errors", "skipped"), 0)
    for suite in suites:
        for key in totals:
            raw = suite.get(key, "")
            if not raw.isascii() or not raw.isdecimal():
                raise ConfigurationError(f"Junit file '{path.name}' has an invalid '{key}' count")
            totals[key] += int(raw)
    return {
        "policy": {
            "tests": float(totals["tests"]),
            "failures": float(totals["failures"] + totals["errors"]),
            "skipped": float(totals["skipped"]),
        }
    }


def retrieval_metrics(path: Path) -> Metrics:
    """Read the overall metrics from an ``eval-retrieval`` result."""

    where = f"Retrieval result '{path.name}'"
    try:
        metrics = _object(_object(_json(path, "retrieval result"), where)["metrics"], where)
        overall = _object(metrics["all"], where)
    except KeyError:
        raise ConfigurationError(f"{where} has no overall metrics") from None
    present = {key: value for key, value in overall.items() if value is not None}
    return {"retrieval": _scorecard("retrieval", present, where)}


def _list(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        raise ConfigurationError(f"{what} is malformed")
    return cast("list[object]", value)


def telemetry_metrics(path: Path) -> Metrics:
    """Read the one-row result of ``evals/release/telemetry.kql``.

    Columns are named ``<scorecard>__<metric>``. A null value means the window held no traffic,
    which must fail loudly rather than read as a healthy zero.
    """

    where = f"Telemetry result '{path.name}'"
    tables = _list(_object(_json(path, "telemetry result"), where).get("tables"), where)
    if len(tables) != 1:
        raise ConfigurationError(f"{where} must hold exactly one table")
    table = _object(tables[0], where)
    columns, rows = _list(table.get("columns"), where), _list(table.get("rows"), where)
    if len(rows) != 1:
        raise ConfigurationError(f"{where} must hold exactly one row")
    row = _list(rows[0], where)
    if len(row) != len(columns):
        raise ConfigurationError(f"{where} has a row that does not match its columns")
    cards: dict[str, dict[str, object]] = {}
    for index, column in enumerate(columns):
        name = _object(column, where).get("name")
        card, _, metric = name.partition("__") if isinstance(name, str) else ("", "", "")
        if row[index] is None:
            raise ConfigurationError(f"{where} column '{name}' is null; there may be no traffic")
        if metric in cards.setdefault(card, {}):
            raise ConfigurationError(f"{where} repeats column '{name}'")
        cards[card][metric] = row[index]
    return {card: _scorecard(card, values, where) for card, values in cards.items()}


def merge_metrics(sources: Sequence[Mapping[str, Mapping[str, float]]]) -> Metrics:
    """Combine sources; two sources reporting one metric is an error, not a silent overwrite."""

    merged: Metrics = {}
    for source in sources:
        for card, values in source.items():
            target = merged.setdefault(card, {})
            for metric, value in values.items():
                if metric in target:
                    raise ConfigurationError(f"Metric {card}.{metric} is reported twice")
                target[metric] = value
    return merged


def parse_labels(items: Sequence[str]) -> dict[str, str]:
    """Parse ``KEY=VALUE`` run labels such as the model, dataset and prompt versions."""

    labels: dict[str, str] = {}
    for item in items:
        key, separator, value = item.partition("=")
        if not separator or key in labels:
            raise ConfigurationError("Labels must be unique KEY=VALUE pairs")
        labels[key] = value
    return labels


def _fmt(value: float) -> str:
    return f"{value:g}"


def _judge(rule: Rule, value: float | None) -> tuple[str, bool, str | None]:
    text = (
        f"<= {_fmt(rule.maximum)}" if rule.maximum is not None else f">= {_fmt(rule.minimum or 0)}"
    )
    if value is None:
        return text, False, "is missing"
    if rule.maximum is not None and value > rule.maximum:
        return text, False, f"= {_fmt(value)} is above the maximum {_fmt(rule.maximum)}"
    if rule.minimum is not None and value < rule.minimum:
        return text, False, f"= {_fmt(value)} is below the minimum {_fmt(rule.minimum)}"
    return text, True, None


def _regression(rule: Rule, value: float, before: float, tolerance: float) -> str | None:
    slack = tolerance * abs(before)
    if rule.maximum is not None and value - (before + slack) > _EPSILON:
        return f"regressed from {_fmt(before)} to {_fmt(value)} (tolerance {_fmt(tolerance)})"
    if rule.minimum is not None and (before - slack) - value > _EPSILON:
        return f"regressed from {_fmt(before)} to {_fmt(value)} (tolerance {_fmt(tolerance)})"
    return None


def build_report(
    thresholds: Thresholds,
    metrics: Mapping[str, Mapping[str, float]],
    *,
    baseline: Mapping[str, Mapping[str, float]] | None = None,
    labels: Mapping[str, str],
) -> ReleaseReport:
    """Apply every rule, plus the regression check against a baseline when one is given."""

    if len(labels) > _MAX_LABELS or not all(
        _LABEL.fullmatch(k) and _LABEL.fullmatch(v) for k, v in labels.items()
    ):
        raise ConfigurationError("Labels must be at most 20 short, plain KEY=VALUE pairs")
    values: Metrics = {card: dict(items) for card, items in metrics.items()}
    cost, telemetry, prices = (
        values.setdefault("cost", {}),
        values.get("telemetry", {}),
        thresholds.prices,
    )
    if (
        prices
        and "estimated_per_run" not in cost
        and {"avg_input_tokens", "avg_output_tokens"} <= telemetry.keys()
    ):
        cost["estimated_per_run"] = (
            telemetry["avg_input_tokens"] * prices.input_per_million_tokens
            + telemetry["avg_output_tokens"] * prices.output_per_million_tokens
        ) / 1_000_000
    findings: list[Finding] = []
    for rule in thresholds.rules:
        value = values.get(rule.scorecard, {}).get(rule.metric)
        text, passed, detail = _judge(rule, value)
        before = (baseline or {}).get(rule.scorecard, {}).get(rule.metric)
        if passed and value is not None and before is not None:
            detail = _regression(rule, value, before, thresholds.regression_tolerance)
            passed = detail is None
        findings.append(Finding(rule.scorecard, rule.metric, value, text, passed, detail))
    return ReleaseReport(dict(labels), tuple(findings), values)


def render_markdown(report: ReleaseReport) -> str:
    """Render the report; metric names and labels are validated, so no text can alter the layout."""

    def verdict(passed: bool) -> str:
        return "**PASS**" if passed else "**FAIL**"

    lines = [
        f"# Release report: {verdict(report.passed)}",
        "",
        f"- Quality health: {verdict(report.quality_health)}",
        f"- Operational health: {verdict(report.operational_health)}",
    ]
    lines += [f"- {key}: {value}" for key, value in report.labels.items()]
    for title, cards in (
        ("Quality health", _QUALITY),
        ("Operational health", ("latency", "cost", "telemetry")),
    ):
        rows = [f for f in report.findings if f.scorecard in cards]
        lines += [
            "",
            f"## {title}",
            "",
            "| Scorecard | Metric | Value | Rule | Result |",
            "|---|---|---|---|---|",
        ]
        for f in rows:
            value = "missing" if f.value is None else _fmt(f.value)
            outcome = "PASS" if f.passed else "FAIL"
            lines.append(f"| {f.scorecard} | {f.metric} | {value} | {f.rule} | {outcome} |")
    if report.failures:
        lines += ["", "## Failures", ""] + [f"- {failure}" for failure in report.failures]
    return "\n".join(lines) + "\n"


def write_text(path: Path, text: str) -> None:
    """Write an output file, reporting failure as a configuration error."""

    try:
        path.write_text(text, encoding="utf-8")
    except OSError as error:
        raise ConfigurationError(f"Cannot write '{path.name}'") from error
