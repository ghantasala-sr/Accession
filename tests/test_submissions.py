from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import respx

from accession.edgar.client import EdgarClient
from accession.edgar.submissions import (
    TICKERS_URL,
    fetch_submissions,
    parse_filing_columns,
    resolve_tickers,
)
from accession.storage import LocalStore

CIK = "0001318605"


def columns(*rows: tuple[str, str, str, str, str]) -> dict[str, list[object]]:
    """Build the column-wise layout EDGAR uses: one list per field."""
    return {
        "accessionNumber": [r[0] for r in rows],
        "form": [r[1] for r in rows],
        "filingDate": [r[2] for r in rows],
        "reportDate": [r[3] for r in rows],
        "primaryDocument": [r[4] for r in rows],
        "isXBRL": [1 for _ in rows],
        "isInlineXBRL": [1 for _ in rows],
    }


RECENT = columns(
    ("0001628280-26-000001", "10-Q", "2026-10-23", "2026-09-30", "tsla-20260930.htm"),
    ("0001628280-26-000002", "8-K", "2026-10-22", "2026-10-22", "tsla-8k.htm"),
    ("0001628280-26-000003", "10-K/A", "2026-04-30", "2025-12-31", "tsla-10ka.htm"),
    ("0000950170-26-000004", "10-K", "2026-01-29", "2025-12-31", "tsla-20251231.htm"),
)
RECENT_ROW_10Q = ("0001628280-26-000001", "10-Q", "2026-10-23", "2026-09-30", "tsla-20260930.htm")


def client(tmp_path: Path) -> EdgarClient:
    return EdgarClient("T t@example.com", LocalStore(tmp_path), sleep=lambda _: None)


def test_parse_keeps_only_periodic_forms_and_flags_amendments() -> None:
    filings = parse_filing_columns(RECENT, "1318605")
    assert [f.form_type for f in filings] == ["10-Q", "10-K/A", "10-K"]
    assert [f.is_amendment for f in filings] == [False, True, False]
    assert filings[0].cik == CIK
    assert filings[0].period_of_report == date(2026, 9, 30)


def test_document_url_drops_cik_padding_and_accession_dashes() -> None:
    (filing,) = parse_filing_columns(columns(RECENT_ROW_10Q), CIK)
    assert filing.document_url == (
        "https://www.sec.gov/Archives/edgar/data/1318605/000162828026000001/tsla-20260930.htm"
    )


def test_blank_report_date_becomes_none() -> None:
    (filing,) = parse_filing_columns(
        columns(("0000000000-26-000009", "10-Q", "2026-05-01", "", "x.htm")), CIK
    )
    assert filing.period_of_report is None


@respx.mock
def test_fetch_follows_history_pages_and_sorts_newest_first(tmp_path: Path) -> None:
    respx.get(f"https://data.sec.gov/submissions/CIK{CIK}.json").respond(
        200,
        json={
            "name": "Tesla, Inc.",
            "filings": {
                "recent": RECENT,
                "files": [{"name": f"CIK{CIK}-submissions-001.json"}],
            },
        },
    )
    respx.get(f"https://data.sec.gov/submissions/CIK{CIK}-submissions-001.json").respond(
        200,
        json=columns(("0001564590-15-000005", "10-K", "2015-02-26", "2014-12-31", "old.htm")),
    )

    _, filings = fetch_submissions(client(tmp_path), CIK)

    assert [f.filed_date.year for f in filings] == [2026, 2026, 2026, 2015]
    assert (tmp_path / f"raw/edgar/submissions/CIK{CIK}-submissions-001.json").is_file()


@respx.mock
def test_resolve_tickers_is_case_insensitive_and_reports_unknowns(tmp_path: Path) -> None:
    respx.get(TICKERS_URL).respond(
        200, json={"0": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla, Inc."}}
    )
    c = client(tmp_path)
    (company,) = resolve_tickers(c, ["tsla"])
    assert company.cik == CIK

    with pytest.raises(ValueError, match="ZZZZ"):
        resolve_tickers(c, ["TSLA", "ZZZZ"])
