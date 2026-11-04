"""Fetch filing histories, XBRL company facts, and filing documents.

uv run python -m accession.edgar.fetch --tickers AAPL TSLA --documents 5
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

from accession.config import get_settings
from accession.edgar.client import EdgarClient
from accession.edgar.filing_docs import download_filing
from accession.edgar.instance import instance_key, parse_instance
from accession.edgar.submissions import fetch_submissions, resolve_tickers
from accession.edgar.xbrl import (
    facts_key,
    fetch_company_facts,
    flatten_company_facts,
    to_parquet_bytes,
)
from accession.storage import LocalStore


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", nargs="+", required=True)
    parser.add_argument("--refresh", action="store_true", help="re-fetch index endpoints")
    parser.add_argument(
        "--documents",
        type=int,
        default=0,
        metavar="N",
        help="also download and parse the N most recent periodic filings per ticker",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    store = LocalStore(settings.data_dir)

    with EdgarClient(
        settings.sec_user_agent, store, settings.sec_max_requests_per_second
    ) as client:
        for company in resolve_tickers(client, args.tickers, refresh=args.refresh):
            doc, filings = fetch_submissions(client, company.cik, refresh=args.refresh)

            lines = "".join(json.dumps(f.to_record()) + "\n" for f in filings)
            store.put(f"processed/edgar/filings/CIK{company.cik}.jsonl", lines.encode())

            forms = Counter(f.form_type for f in filings)
            print(f"\n{company.ticker}  CIK {company.cik}  {doc['name']}  SIC {doc.get('sic')}")
            print("  " + "  ".join(f"{form}: {n}" for form, n in sorted(forms.items())))
            for filing in filings[:5]:
                print(
                    f"  {filing.filed_date}  {filing.form_type:<6}  {filing.accession_number}"
                    f"  period {filing.period_of_report}  {filing.primary_document}"
                )

            facts = flatten_company_facts(fetch_company_facts(client, company.cik, args.refresh))
            store.put(facts_key(company.cik), to_parquet_bytes(facts))
            framed = facts.column("frame").drop_null()
            print(
                f"  XBRL facts: {facts.num_rows:,} rows, {len(framed):,} with a frame"
                f" -> {facts_key(company.cik)}"
            )

            for filing in filings[: args.documents]:
                files = download_filing(client, filing)
                if files.instance_key is None:
                    print(f"  {filing.accession_number}: {len(files.keys)} files, no XBRL instance")
                    continue
                table = parse_instance(store.get(files.instance_key))
                key = instance_key(company.cik, filing.accession_number)
                store.put(key, to_parquet_bytes(table))
                print(
                    f"  {filing.accession_number} {filing.form_type:<6}"
                    f" {len(files.keys)} files, {table.num_rows:,} instance facts -> {key}"
                )


if __name__ == "__main__":
    main()
