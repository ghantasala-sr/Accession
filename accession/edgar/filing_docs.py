"""Download the parts of a filing folder that the pipeline uses.

A filing folder holds ~90 files. We keep the primary document, its images,
the XBRL instance, and the company's extension taxonomy (schema plus
calculation, definition, label, and presentation linkbases). SEC's rendered
R pages, exhibits, and the zip/txt bundles are skipped.
"""

from __future__ import annotations

from dataclasses import dataclass

from accession.edgar.client import EdgarClient
from accession.edgar.submissions import Filing

_LINKBASE_SUFFIXES = ("_cal.xml", "_def.xml", "_lab.xml", "_pre.xml")
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif")


@dataclass(frozen=True)
class FilingFiles:
    filing: Filing
    keys: dict[str, str]  # file name -> object store key

    @property
    def instance_key(self) -> str | None:
        names = [n for n in self.keys if is_instance(n)]
        return self.keys[names[0]] if names else None


def filing_base_url(filing: Filing) -> str:
    return filing.document_url.rsplit("/", 1)[0] + "/"


def filing_prefix(filing: Filing) -> str:
    return f"raw/edgar/filings/{filing.cik}/{filing.accession_number}/"


def is_instance(name: str) -> bool:
    # Inline XBRL filings ship SEC's extracted instance as `<doc>_htm.xml`;
    # pre-2019 filings have a standalone `<stem>.xml` instance instead.
    lower = name.lower()
    return lower.endswith(".xml") and not (
        lower.endswith(_LINKBASE_SUFFIXES) or lower == "filingsummary.xml"
    )


def select_files(names: list[str], primary_document: str) -> list[str]:
    keep: list[str] = []
    for name in names:
        lower = name.lower()
        if (
            name == primary_document
            or name == "FilingSummary.xml"
            or lower.endswith(".xsd")
            or lower.endswith(_LINKBASE_SUFFIXES)
            or lower.endswith(_IMAGE_SUFFIXES)
            or is_instance(name)
        ):
            keep.append(name)
    return keep


def download_filing(client: EdgarClient, filing: Filing) -> FilingFiles:
    base, prefix = filing_base_url(filing), filing_prefix(filing)
    # Archive folders are immutable once accepted, so the index is cached for good.
    index = client.get_json(base + "index.json", prefix + "index.json")
    names = [item["name"] for item in index["directory"]["item"]]

    keys: dict[str, str] = {}
    for name in select_files(names, filing.primary_document):
        client.get_bytes(base + name, prefix + name)
        keys[name] = prefix + name
    return FilingFiles(filing=filing, keys=keys)
