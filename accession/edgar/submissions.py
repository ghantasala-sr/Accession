"""Ticker → CIK resolution and the per-company filing history.

The submissions endpoint stores filings column-wise: `filings.recent` holds
parallel arrays (`accessionNumber[i]`, `form[i]`, `filingDate[i]`, ...) for
roughly the latest 1,000 filings, and `filings.files` points at older pages
with the same column layout.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from accession.edgar.client import EdgarClient

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{document}"

PERIODIC_FORMS = frozenset({"10-K", "10-Q", "10-K/A", "10-Q/A"})


def pad_cik(cik: int | str) -> str:
    return f"{int(cik):010d}"


@dataclass(frozen=True)
class Company:
    cik: str  # zero-padded to 10 digits
    ticker: str
    name: str
    sic_code: str | None = None


@dataclass(frozen=True)
class Filing:
    accession_number: str
    cik: str
    form_type: str
    filed_date: date
    period_of_report: date | None
    primary_document: str
    is_amendment: bool
    is_xbrl: bool
    is_inline_xbrl: bool

    @property
    def document_url(self) -> str:
        return ARCHIVE_URL.format(
            cik=int(self.cik),
            accession_no_dashes=self.accession_number.replace("-", ""),
            document=self.primary_document,
        )

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["filed_date"] = self.filed_date.isoformat()
        record["period_of_report"] = (
            self.period_of_report.isoformat() if self.period_of_report else None
        )
        record["document_url"] = self.document_url
        return record


def resolve_tickers(
    client: EdgarClient, tickers: list[str], refresh: bool = False
) -> list[Company]:
    """Map tickers to CIKs using SEC's published ticker file."""
    data = client.get_json(TICKERS_URL, "raw/edgar/company_tickers.json", refresh=refresh)
    by_ticker = {row["ticker"].upper(): row for row in data.values()}

    companies: list[Company] = []
    missing: list[str] = []
    for ticker in tickers:
        row = by_ticker.get(ticker.upper())
        if row is None:
            missing.append(ticker)
            continue
        companies.append(
            Company(cik=pad_cik(row["cik_str"]), ticker=row["ticker"], name=row["title"])
        )
    if missing:
        raise ValueError(f"unknown tickers: {', '.join(missing)}")
    return companies


def fetch_submissions(
    client: EdgarClient, cik: str, refresh: bool = False, include_history: bool = True
) -> tuple[dict[str, Any], list[Filing]]:
    """Return the raw submissions document and every periodic filing in it."""
    name = f"CIK{pad_cik(cik)}.json"
    doc = client.get_json(
        SUBMISSIONS_URL.format(name=name), f"raw/edgar/submissions/{name}", refresh=refresh
    )
    filings = parse_filing_columns(doc["filings"]["recent"], cik)

    if include_history:
        for page in doc["filings"].get("files", []):
            # Older pages only grow when a company files ~1,000 more documents,
            # so they are effectively immutable once written.
            older = client.get_json(
                SUBMISSIONS_URL.format(name=page["name"]),
                f"raw/edgar/submissions/{page['name']}",
            )
            filings.extend(parse_filing_columns(older, cik))

    filings.sort(key=lambda f: (f.filed_date, f.accession_number), reverse=True)
    return doc, filings


def parse_filing_columns(columns: dict[str, list[Any]], cik: str) -> list[Filing]:
    filings: list[Filing] = []
    for i, form in enumerate(columns["form"]):
        if form not in PERIODIC_FORMS:
            continue
        report_date = columns["reportDate"][i]
        filings.append(
            Filing(
                accession_number=columns["accessionNumber"][i],
                cik=pad_cik(cik),
                form_type=form,
                filed_date=date.fromisoformat(columns["filingDate"][i]),
                period_of_report=date.fromisoformat(report_date) if report_date else None,
                primary_document=columns["primaryDocument"][i],
                is_amendment=form.endswith("/A"),
                is_xbrl=bool(columns["isXBRL"][i]),
                is_inline_xbrl=bool(columns["isInlineXBRL"][i]),
            )
        )
    return filings
