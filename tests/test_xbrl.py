from __future__ import annotations

import io
from datetime import date
from typing import Any

import pyarrow.parquet as pq

from accession.edgar.xbrl import facts_key, flatten_company_facts, to_parquet_bytes

Q3_10Q = "0001628280-26-066775"
FY25_10K = "0001628280-26-003952"

# Trimmed from TSLA's real companyfacts document.
DOC: dict[str, Any] = {
    "cik": 1318605,
    "entityName": "Tesla, Inc.",
    "facts": {
        "us-gaap": {
            "CashAndCashEquivalentsAtCarryingValue": {
                "label": "Cash and Cash Equivalents, at Carrying Value",
                "units": {
                    "USD": [
                        {
                            "end": "2025-12-31",
                            "val": 16513000000,
                            "accn": FY25_10K,
                            "fy": 2025,
                            "fp": "FY",
                            "form": "10-K",
                            "filed": "2026-01-29",
                        },
                        {
                            "end": "2025-12-31",
                            "val": 16513000000,
                            "accn": Q3_10Q,
                            "fy": 2026,
                            "fp": "Q3",
                            "form": "10-Q",
                            "filed": "2026-10-22",
                            "frame": "CY2025Q4I",
                        },
                    ]
                },
            },
            "Revenues": {
                "label": "Revenues",
                "units": {
                    "USD": [
                        {
                            "start": "2026-07-01",
                            "end": "2026-09-30",
                            "val": 28046000000,
                            "accn": Q3_10Q,
                            "fy": 2026,
                            "fp": "Q3",
                            "form": "10-Q",
                            "filed": "2026-10-22",
                            "frame": "CY2026Q3",
                        },
                        {
                            "start": "2026-01-01",
                            "end": "2026-09-30",
                            "val": 78669000000,
                            "accn": Q3_10Q,
                            "fy": 2026,
                            "fp": "Q3",
                            "form": "10-Q",
                            "filed": "2026-10-22",
                        },
                    ]
                },
            },
        },
        "dei": {
            "EntityCommonStockSharesOutstanding": {
                "label": "Entity Common Stock, Shares Outstanding",
                "units": {
                    "shares": [
                        {
                            "end": "2026-04-24",
                            "val": 3216000000,
                            "accn": "0001104659-26-053166",
                            "fy": 2025,
                            "fp": "FY",
                            "form": "10-K/A",
                            "filed": "2026-04-30",
                        }
                    ]
                },
            }
        },
    },
}


def rows() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = flatten_company_facts(DOC).to_pylist()
    return result


def test_one_row_per_fact_across_taxonomies_and_units() -> None:
    out = rows()
    assert len(out) == 5
    assert {(r["taxonomy"], r["unit"]) for r in out} == {("us-gaap", "USD"), ("dei", "shares")}
    assert "cik" not in out[0]  # lives in the partition path instead


def test_instant_has_no_start_and_keeps_raw_dollars() -> None:
    cash = next(
        r for r in rows() if r["accession_number"] == Q3_10Q and r["tag"].startswith("Cash")
    )
    assert cash["period_type"] == "instant"
    assert cash["period_start"] is None
    assert cash["duration_days"] is None
    assert cash["value"] == 16_513_000_000


def test_comparative_keeps_filing_labels_not_period_labels() -> None:
    # The Dec-2025 balance reported in the Q3 2026 10-Q is labelled fy=2026/Q3.
    cash = next(
        r for r in rows() if r["accession_number"] == Q3_10Q and r["tag"].startswith("Cash")
    )
    assert cash["period_end"] == date(2025, 12, 31)
    assert (cash["filing_fiscal_year"], cash["filing_fiscal_period"]) == (2026, "Q3")


def test_duplicates_are_kept_with_frame_on_one() -> None:
    dupes = [r for r in rows() if r["tag"].startswith("Cash")]
    assert len(dupes) == 2
    assert [r["frame"] for r in dupes].count(None) == 1


def test_quarter_and_ytd_durations() -> None:
    revenue = {r["duration_days"]: r["value"] for r in rows() if r["tag"] == "Revenues"}
    assert revenue == {92: 28_046_000_000, 273: 78_669_000_000}
    assert all(r["period_type"] == "duration" for r in rows() if r["tag"] == "Revenues")


def test_parquet_round_trip_and_key() -> None:
    table = flatten_company_facts(DOC)
    back = pq.read_table(io.BytesIO(to_parquet_bytes(table)))
    assert back.schema.equals(table.schema)
    assert back.num_rows == 5
    assert facts_key("1318605") == "facts/xbrl/companyfacts/cik=0001318605/facts.parquet"
