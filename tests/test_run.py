from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa

from accession.edgar.instance import INSTANCE_SCHEMA
from accession.edgar.submissions import Filing
from accession.ingest.pipeline import CHUNKS_SCHEMA, PARSER_VERSION, SECTIONS_SCHEMA, chunks_key
from accession.ingest.validate import status_of, validate_filing
from accession.run import is_done, select_filings
from accession.storage import LocalStore

CIK = "0001318605"
ACCN = "0001628280-26-066775"


def sections(*kinds: str) -> pa.Table:
    rows = [{"section_key": f"s{i}", "section_kind": k} for i, k in enumerate(kinds)]
    return pa.Table.from_pylist(rows, schema=SECTIONS_SCHEMA)


def chunks(*texts: str, refs: list[dict[str, str]] | None = None) -> pa.Table:
    rows = [{"chunk_id": str(i), "text": t, "fact_refs": refs or []} for i, t in enumerate(texts)]
    return pa.Table.from_pylist(rows, schema=CHUNKS_SCHEMA)


def instance(*facts: dict[str, Any]) -> pa.Table:
    base = {"dimensions": [], "period_end": date(2026, 9, 30), "is_text_block": False}
    return pa.Table.from_pylist([{**base, **f} for f in facts], schema=INSTANCE_SCHEMA)


BALANCED = instance(
    {"concept": "us-gaap:Assets", "context_id": "c-3", "value": 157_517e6},
    {
        "concept": "us-gaap:LiabilitiesAndStockholdersEquity",
        "context_id": "c-3",
        "value": 157_517e6,
    },
)


def checks(issues: list[Any]) -> set[str]:
    return {i.check for i in issues}


def test_clean_10q_passes() -> None:
    issues = validate_filing(
        "10-Q",
        sections("cover", "financial_statements", "mda"),
        chunks(
            "Cash was $17,853 million.", refs=[{"concept": "us-gaap:Assets", "context_id": "c-3"}]
        ),
        BALANCED,
    )
    assert issues == []
    assert status_of(issues) == "ok"


def test_missing_mda_is_an_error_but_not_for_amendments() -> None:
    assert "required_sections" in checks(
        validate_filing("10-Q", sections("financial_statements"), chunks("x"), None)
    )
    # A Part III-only 10-K/A legitimately has no MD&A.
    assert validate_filing("10-K/A", sections("executive_compensation"), chunks("x"), None) == []


def test_unresolved_fact_refs_fail() -> None:
    issues = validate_filing(
        "10-Q",
        sections("financial_statements", "mda"),
        chunks("x", refs=[{"concept": "us-gaap:Revenues", "context_id": "c-999"}]),
        BALANCED,
    )
    assert "fact_refs" in checks(issues)
    assert status_of(issues) == "failed"


def test_unbalanced_balance_sheet_fails() -> None:
    lopsided = instance(
        {"concept": "us-gaap:Assets", "context_id": "c-3", "value": 157_517e6},
        {
            "concept": "us-gaap:LiabilitiesAndStockholdersEquity",
            "context_id": "c-3",
            "value": 150e9,
        },
    )
    issues = validate_filing("10-Q", sections("financial_statements", "mda"), chunks("x"), lopsided)
    assert "balance_sheet" in checks(issues)


def test_unknown_and_repeated_sections_are_warnings() -> None:
    issues = validate_filing(
        "10-Q", sections("financial_statements", "mda", "mda", "other"), chunks("x"), None
    )
    assert checks(issues) == {"unknown_sections"}  # keys differ, so no repeat
    assert status_of(issues) == "warning"


def test_empty_output_fails() -> None:
    assert status_of(validate_filing("10-Q", sections(), chunks(), None)) == "failed"
    assert "empty_chunks" in checks(
        validate_filing("10-Q", sections("financial_statements", "mda"), chunks(" "), None)
    )


def filing(form: str, filed: date) -> Filing:
    return Filing(
        accession_number=ACCN, cik=CIK, form_type=form, filed_date=filed,
        period_of_report=None, primary_document="x.htm", is_amendment=form.endswith("/A"),
        is_xbrl=True, is_inline_xbrl=True,
    )  # fmt: skip


def test_select_filings_filters_by_date_and_form_oldest_first() -> None:
    filings = [
        filing("10-Q", date(2026, 10, 22)),
        filing("10-K/A", date(2026, 4, 30)),
        filing("10-Q", date(2022, 10, 24)),
        filing("10-K", date(2026, 1, 29)),
    ]
    chosen = select_filings(filings, date(2023, 1, 1), {"10-K", "10-Q"})
    assert [(f.form_type, f.filed_date.year) for f in chosen] == [("10-K", 2026), ("10-Q", 2026)]


def test_is_done_requires_success_current_version_and_output(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    ok = {"status": "ok", "parser_version": PARSER_VERSION}
    assert not is_done(ok, store, CIK, ACCN)  # no chunks written yet

    store.put(chunks_key(CIK, ACCN), b"parquet")
    assert is_done(ok, store, CIK, ACCN)
    assert is_done({**ok, "status": "warning"}, store, CIK, ACCN)
    assert not is_done({**ok, "status": "failed"}, store, CIK, ACCN)
    assert not is_done({**ok, "parser_version": "0"}, store, CIK, ACCN)
    assert not is_done(None, store, CIK, ACCN)
