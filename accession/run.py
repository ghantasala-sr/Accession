"""One command from ticker to validated chunks.

    uv run accession TSLA AAPL
    uv run accession NVDA --since 2024-01-01 --forms 10-K
    uv run accession TSLA --force          # reprocess even if already done

For each ticker:
  1. resolve the ticker to a CIK
  2. refresh the filing history and XBRL company facts (2 requests)
  3. for each 10-K/10-Q (and amendments) filed since `--since`:
     download -> parse XBRL instance -> sections -> chunks -> validate
  4. record the outcome in a per-company manifest

Filings already processed by the current parser version are skipped, and
archive documents are cached, so re-running only does new work. One bad
filing is recorded as failed and the run moves on; the exit code is 1 if
anything failed.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from collections import Counter
from datetime import UTC, date, datetime
from typing import Any

import pyarrow as pa
import structlog

from accession.config import get_settings
from accession.edgar.client import EdgarClient
from accession.edgar.filing_docs import download_filing
from accession.edgar.instance import instance_key, parse_instance
from accession.edgar.submissions import (
    PERIODIC_FORMS,
    Company,
    Filing,
    fetch_submissions,
    resolve_tickers,
)
from accession.edgar.xbrl import (
    facts_key,
    fetch_company_facts,
    flatten_company_facts,
    to_parquet_bytes,
)
from accession.ingest.chunking import FilingContext
from accession.ingest.pipeline import PARSER_VERSION, chunks_key, process_filing, sections_key
from accession.ingest.validate import Issue, status_of, validate_filing
from accession.storage import LocalStore, ObjectStore

DEFAULT_SINCE = date(2023, 1, 1)


def manifest_key(cik: str) -> str:
    return f"processed/manifests/CIK{cik}.json"


def filings_key(cik: str) -> str:
    return f"processed/edgar/filings/CIK{cik}.jsonl"


def load_manifest(store: ObjectStore, cik: str) -> dict[str, Any]:
    key = manifest_key(cik)
    if store.exists(key):
        manifest: dict[str, Any] = json.loads(store.get(key))
        return manifest
    return {"cik": cik, "filings": {}}


def is_done(entry: dict[str, Any] | None, store: ObjectStore, cik: str, accession: str) -> bool:
    return (
        entry is not None
        and entry.get("status") in ("ok", "warning")
        and entry.get("parser_version") == PARSER_VERSION
        and store.exists(chunks_key(cik, accession))
    )


def select_filings(filings: list[Filing], since: date, forms: set[str]) -> list[Filing]:
    chosen = [f for f in filings if f.filed_date >= since and f.form_type in forms]
    return sorted(chosen, key=lambda f: f.filed_date)


def process_one(
    client: EdgarClient, store: ObjectStore, company: Company, filing: Filing
) -> dict[str, Any]:
    files = download_filing(client, filing)

    instance: pa.Table | None = None
    if files.instance_key is not None:
        instance = parse_instance(store.get(files.instance_key))
        store.put(instance_key(company.cik, filing.accession_number), to_parquet_bytes(instance))

    html = store.get(files.keys[filing.primary_document])
    context = FilingContext(
        cik=company.cik,
        ticker=company.ticker,
        accession_number=filing.accession_number,
        form_type=filing.form_type,
        filed_date=filing.filed_date.isoformat(),
        period_of_report=filing.period_of_report.isoformat() if filing.period_of_report else None,
        info=None,  # type: ignore[arg-type]  # filled in from the document
    )
    sections, chunks = process_filing(html, context)
    store.put(sections_key(company.cik, filing.accession_number), to_parquet_bytes(sections))
    store.put(chunks_key(company.cik, filing.accession_number), to_parquet_bytes(chunks))

    issues = validate_filing(filing.form_type, sections, chunks, instance)
    if filing.is_xbrl and instance is None:
        issues.append(Issue("warning", "instance", "filing is XBRL but no instance was found"))

    return {
        "status": status_of(issues),
        "sections": sections.num_rows,
        "chunks": chunks.num_rows,
        "instance_facts": instance.num_rows if instance is not None else 0,
        "issues": [issue.__dict__ for issue in issues],
    }


def run_company(
    client: EdgarClient,
    store: ObjectStore,
    company: Company,
    since: date,
    forms: set[str],
    force: bool,
    refresh: bool,
) -> Counter[str]:
    doc, filings = fetch_submissions(client, company.cik, refresh=refresh)
    store.put(
        filings_key(company.cik),
        "".join(json.dumps(f.to_record()) + "\n" for f in filings).encode(),
    )
    facts = flatten_company_facts(fetch_company_facts(client, company.cik, refresh=refresh))
    store.put(facts_key(company.cik), to_parquet_bytes(facts))

    selected = select_filings(filings, since, forms)
    print(
        f"\n{company.ticker}  {doc['name']}  CIK {company.cik}"
        f"  |  {facts.num_rows:,} company facts  |  {len(selected)} filings since {since}"
    )

    manifest = load_manifest(store, company.cik)
    manifest["ticker"] = company.ticker
    tally: Counter[str] = Counter()

    for filing in selected:
        accession = filing.accession_number
        entry = manifest["filings"].get(accession)
        label = f"  {filing.filed_date}  {filing.form_type:<6} {accession}"

        if not force and is_done(entry, store, company.cik, accession):
            tally["skipped"] += 1
            continue

        try:
            result = process_one(client, store, company, filing)
        except Exception as exc:  # one bad filing must not stop the batch
            result = {
                "status": "failed",
                "issues": [{"severity": "error", "check": "exception", "message": repr(exc)}],
                "traceback": traceback.format_exc(limit=5),
            }

        manifest["filings"][accession] = {
            "form_type": filing.form_type,
            "filed_date": filing.filed_date.isoformat(),
            "period_of_report": (
                filing.period_of_report.isoformat() if filing.period_of_report else None
            ),
            "parser_version": PARSER_VERSION,
            "processed_at": datetime.now(UTC).isoformat(timespec="seconds"),
            **result,
        }
        tally[result["status"]] += 1

        counts = (
            f"{result['sections']:>2} sections {result['chunks']:>4} chunks"
            f" {result['instance_facts']:>5} facts"
            if "chunks" in result
            else ""
        )
        print(f"{label}  {result['status']:<7} {counts}")
        for issue in result["issues"]:
            print(f"      {issue['severity']}: {issue['check']}: {issue['message']}")

        # Save after every filing so an interrupted run keeps its progress.
        store.put(manifest_key(company.cik), json.dumps(manifest, indent=2).encode())

    store.put(manifest_key(company.cik), json.dumps(manifest, indent=2).encode())
    if tally["skipped"]:
        print(f"  ({tally['skipped']} already processed, skipped)")
    return tally


def resolve(client: EdgarClient, tickers: list[str]) -> list[Company]:
    try:
        return resolve_tickers(client, tickers)
    except ValueError:
        # A ticker missing from the cached map may be newly listed; refresh once.
        return resolve_tickers(client, tickers, refresh=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="accession", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("tickers", nargs="+", help="e.g. TSLA AAPL")
    parser.add_argument(
        "--since",
        type=date.fromisoformat,
        default=DEFAULT_SINCE,
        help=f"earliest filing date (default {DEFAULT_SINCE})",
    )
    parser.add_argument(
        "--forms",
        nargs="+",
        default=sorted(PERIODIC_FORMS),
        choices=sorted(PERIODIC_FORMS),
        help="form types to process (default: 10-K, 10-Q and their amendments)",
    )
    parser.add_argument("--force", action="store_true", help="reprocess finished filings")
    parser.add_argument(
        "--no-refresh",
        dest="refresh",
        action="store_false",
        help="use cached filing history and company facts (offline)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log every SEC request")
    args = parser.parse_args(argv)

    # Request logs go to stderr so the per-filing report on stdout stays readable.
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if args.verbose else logging.WARNING
        ),
        logger_factory=structlog.PrintLoggerFactory(sys.stderr),
    )

    settings = get_settings()
    store = LocalStore(settings.data_dir)
    totals: Counter[str] = Counter()

    with EdgarClient(
        settings.sec_user_agent, store, settings.sec_max_requests_per_second
    ) as client:
        try:
            companies = resolve(client, args.tickers)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        for company in companies:
            totals += run_company(
                client, store, company, args.since, set(args.forms), args.force, args.refresh
            )

    summary = ", ".join(f"{totals[k]} {k}" for k in ("ok", "warning", "failed", "skipped"))
    print(f"\nDone: {summary}")
    return 1 if totals["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
