"""XBRL company facts: one row per reported value, written to Parquet.

The companyfacts document nests facts as
`facts[taxonomy][tag].units[unit] -> [fact, ...]`. This module flattens that
tree without interpreting it. Things to know when reading the output:

- `filing_fiscal_year` / `filing_fiscal_period` describe the filing a fact
  came from, not the period the fact measures. A Q3 2026 10-Q also reports
  2025 comparatives, all labelled 2026/Q3. Use `period_start`/`period_end`.
- Instants (balance sheet) have no `period_start`; durations (income and
  cash flow statements) do. A 10-Q reports both the quarter and year-to-date.
- The same value is re-reported by every later filing that shows it as a
  comparative. `frame` is set on exactly one of those rows (SEC's pick for
  that calendar period); the rest are kept because they carry filing dates,
  which restatement-aware answers need.
- Company extension tags (e.g. `tsla:`) and dimensional facts (segments)
  are not in companyfacts. They need the filing's own XBRL instance.
- Values are in raw units (dollars, not millions).
"""

from __future__ import annotations

import io
from datetime import date
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from accession.edgar.client import EdgarClient
from accession.edgar.submissions import pad_cik

COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# No `cik` column: it is the Hive partition key in the object path (see
# `facts_key`), and Athena rejects a partition key that is also a data column.
FACTS_SCHEMA = pa.schema(
    [
        ("taxonomy", pa.string()),
        ("tag", pa.string()),
        ("label", pa.string()),
        ("unit", pa.string()),
        # float64 is exact for integers below 2**53 (~9e15), well above any reported value.
        ("value", pa.float64()),
        ("period_type", pa.string()),  # "instant" | "duration"
        ("period_start", pa.date32()),
        ("period_end", pa.date32()),
        ("duration_days", pa.int32()),
        ("accession_number", pa.string()),
        ("form_type", pa.string()),
        ("filed_date", pa.date32()),
        ("filing_fiscal_year", pa.int32()),
        ("filing_fiscal_period", pa.string()),
        ("frame", pa.string()),
    ]
)


def fetch_company_facts(client: EdgarClient, cik: str, refresh: bool = False) -> dict[str, Any]:
    padded = pad_cik(cik)
    doc: dict[str, Any] = client.get_json(
        COMPANY_FACTS_URL.format(cik=padded),
        f"raw/edgar/companyfacts/CIK{padded}.json",
        refresh=refresh,
    )
    return doc


def flatten_company_facts(doc: dict[str, Any]) -> pa.Table:
    columns: dict[str, list[Any]] = {name: [] for name in FACTS_SCHEMA.names}

    for taxonomy, concepts in doc["facts"].items():
        for tag, concept in concepts.items():
            for unit, facts in concept["units"].items():
                for fact in facts:
                    start = date.fromisoformat(fact["start"]) if "start" in fact else None
                    end = date.fromisoformat(fact["end"])
                    row = {
                        "taxonomy": taxonomy,
                        "tag": tag,
                        "label": concept.get("label"),
                        "unit": unit,
                        "value": float(fact["val"]),
                        "period_type": "duration" if start else "instant",
                        "period_start": start,
                        "period_end": end,
                        # Inclusive day count: a calendar quarter is ~91 days, a year ~365.
                        "duration_days": (end - start).days + 1 if start else None,
                        "accession_number": fact["accn"],
                        "form_type": fact["form"],
                        "filed_date": date.fromisoformat(fact["filed"]),
                        "filing_fiscal_year": fact.get("fy"),
                        "filing_fiscal_period": fact.get("fp"),
                        "frame": fact.get("frame"),
                    }
                    for name, value in row.items():
                        columns[name].append(value)

    return pa.table(columns, schema=FACTS_SCHEMA)


def to_parquet_bytes(table: pa.Table) -> bytes:
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd")
    return buffer.getvalue()


def facts_key(cik: str) -> str:
    # Partitioned by company only for now; a fiscal-year partition needs the
    # fiscal calendar mapping, which comes with the dbt models.
    return f"facts/xbrl/companyfacts/cik={pad_cik(cik)}/facts.parquet"
