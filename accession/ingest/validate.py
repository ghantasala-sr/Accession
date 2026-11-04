"""Checks that run on every processed filing.

Errors mean the output should not be trusted (missing MD&A, chunks pointing
at XBRL facts that don't exist). Warnings flag things worth a look that can
be legitimate (a section kind we don't recognise).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

import pyarrow as pa

# Sections every original filing of a form must have. Amendments are exempt:
# a 10-K/A often carries only Part III.
_REQUIRED_KINDS = {
    "10-Q": {"financial_statements", "mda"},
    "10-K": {"business", "risk_factors", "mda", "financial_statements"},
}
# Assets and Liabilities + Equity are both rounded to the filing's precision.
_BALANCE_TOLERANCE = 1e6


@dataclass(frozen=True)
class Issue:
    severity: str  # "error" | "warning"
    check: str
    message: str


def validate_filing(
    form_type: str,
    sections: pa.Table,
    chunks: pa.Table,
    instance: pa.Table | None,
) -> list[Issue]:
    issues: list[Issue] = []
    section_rows = sections.to_pylist()
    chunk_rows = chunks.to_pylist()

    if not chunk_rows:
        return [Issue("error", "chunks", "no chunks produced")]

    empty = sum(1 for c in chunk_rows if not c["text"].strip())
    if empty:
        issues.append(Issue("error", "empty_chunks", f"{empty} chunks have no text"))

    kinds = {s["section_kind"] for s in section_rows}
    missing = _REQUIRED_KINDS.get(form_type, set()) - kinds
    if missing:
        issues.append(Issue("error", "required_sections", f"missing {sorted(missing)}"))

    other = [s["section_key"] for s in section_rows if s["section_kind"] == "other"]
    if other:
        issues.append(Issue("warning", "unknown_sections", f"unrecognised items {other}"))

    repeated = [key for key, n in Counter(s["section_key"] for s in section_rows).items() if n > 1]
    if repeated:
        issues.append(Issue("warning", "repeated_sections", f"headings seen twice: {repeated}"))

    if instance is not None:
        issues.extend(_check_fact_refs(chunk_rows, instance))
        issues.extend(_check_balance(instance))
    return issues


def _check_fact_refs(chunk_rows: list[dict[str, Any]], instance: pa.Table) -> list[Issue]:
    known = set(
        zip(instance["concept"].to_pylist(), instance["context_id"].to_pylist(), strict=True)
    )
    refs = {(r["concept"], r["context_id"]) for c in chunk_rows for r in c["fact_refs"]}
    unresolved = refs - known
    if unresolved:
        sample = sorted(unresolved)[:3]
        return [
            Issue(
                "error",
                "fact_refs",
                f"{len(unresolved)}/{len(refs)} fact refs not in the XBRL instance, e.g. {sample}",
            )
        ]
    return []


def _check_balance(instance: pa.Table) -> list[Issue]:
    """Assets must equal Liabilities and Stockholders' Equity on every balance sheet date."""
    totals: dict[str, dict[Any, float]] = {"Assets": {}, "LiabilitiesAndStockholdersEquity": {}}
    for row in instance.to_pylist():
        name = row["concept"].removeprefix("us-gaap:")
        if name in totals and not row["dimensions"] and row["value"] is not None:
            totals[name][row["period_end"]] = row["value"]

    issues: list[Issue] = []
    for period, assets in totals["Assets"].items():
        other_side = totals["LiabilitiesAndStockholdersEquity"].get(period)
        if other_side is not None and abs(assets - other_side) > _BALANCE_TOLERANCE:
            issues.append(
                Issue(
                    "error",
                    "balance_sheet",
                    f"{period}: assets {assets:,.0f} != liabilities + equity {other_side:,.0f}",
                )
            )
    return issues


def status_of(issues: list[Issue]) -> str:
    if any(i.severity == "error" for i in issues):
        return "failed"
    return "warning" if issues else "ok"
