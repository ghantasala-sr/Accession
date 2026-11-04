"""Parse a filing's XBRL instance into one row per fact.

Unlike companyfacts, the instance keeps everything the filing tagged:
company extension concepts (`tsla:LeasedAssetsNet`) and dimensional facts
(revenue by `srt:ProductOrServiceAxis`, segment, geography). A fact with an
empty `dimensions` list is the consolidated, face-of-statement value.

Text blocks (whole notes tagged as HTML) are flagged but their content is
dropped; the primary document is the source for narrative text.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from typing import Any

import pyarrow as pa

from accession.edgar.submissions import pad_cik

_XBRLI = "{http://www.xbrl.org/2003/instance}"
_XBRLDI = "{http://xbrl.org/2006/xbrldi}"
_XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"

_DIMENSION = pa.struct(
    [("axis", pa.string()), ("member", pa.string()), ("typed_value", pa.string())]
)

# No `cik` / `accession_number` columns: both are Hive partition keys in the path.
INSTANCE_SCHEMA = pa.schema(
    [
        ("concept", pa.string()),  # prefix:LocalName, e.g. us-gaap:Revenues
        ("taxonomy", pa.string()),
        ("context_id", pa.string()),
        ("period_type", pa.string()),
        ("period_start", pa.date32()),
        ("period_end", pa.date32()),
        ("dimensions", pa.list_(_DIMENSION)),
        ("unit", pa.string()),  # USD, shares, USD/shares; null for non-numeric
        ("decimals", pa.string()),  # "-6", "2", "INF"; null for non-numeric
        ("value", pa.float64()),  # raw units, already scaled
        ("value_text", pa.string()),  # non-numeric facts, except text blocks
        ("is_text_block", pa.bool_()),
        ("is_nil", pa.bool_()),
    ]
)


@dataclass(frozen=True)
class _Context:
    start: date | None
    end: date
    dimensions: list[dict[str, str | None]]


def instance_key(cik: str, accession_number: str) -> str:
    return f"facts/xbrl/instance/cik={pad_cik(cik)}/accession={accession_number}/facts.parquet"


def parse_instance(xml: bytes) -> pa.Table:
    prefixes = _namespace_prefixes(xml)
    root = ET.fromstring(xml)
    contexts = {c.get("id", ""): _parse_context(c) for c in root.iter(_XBRLI + "context")}
    units = {u.get("id", ""): _parse_unit(u) for u in root.iter(_XBRLI + "unit")}

    columns: dict[str, list[Any]] = {name: [] for name in INSTANCE_SCHEMA.names}
    seen: set[tuple[str, str, str | None, str | None]] = set()

    for element in root:
        context_id = element.get("contextRef")
        if context_id is None:
            continue
        concept = _qualify(element.tag, prefixes)
        unit_id = element.get("unitRef")
        text = element.text.strip() if element.text else None

        # Filings repeat a fact wherever it is displayed; identical repeats carry no information.
        identity = (concept, context_id, unit_id, text)
        if identity in seen:
            continue
        seen.add(identity)

        ctx = contexts[context_id]
        is_nil = element.get(_XSI_NIL) == "true"
        is_numeric = unit_id is not None
        is_text_block = concept.endswith("TextBlock")
        row = {
            "concept": concept,
            "taxonomy": concept.split(":", 1)[0],
            "context_id": context_id,
            "period_type": "duration" if ctx.start else "instant",
            "period_start": ctx.start,
            "period_end": ctx.end,
            "dimensions": ctx.dimensions,
            "unit": units.get(unit_id) if unit_id else None,
            "decimals": element.get("decimals"),
            "value": float(text) if is_numeric and text and not is_nil else None,
            "value_text": None if is_numeric or is_text_block else text,
            "is_text_block": is_text_block,
            "is_nil": is_nil,
        }
        for name, value in row.items():
            columns[name].append(value)

    return pa.table(columns, schema=INSTANCE_SCHEMA)


def _namespace_prefixes(xml: bytes) -> dict[str, str]:
    prefixes: dict[str, str] = {}
    for _, (prefix, uri) in ET.iterparse(io.BytesIO(xml), events=["start-ns"]):
        prefixes.setdefault(uri, prefix)
    return prefixes


def _qualify(name: str, prefixes: dict[str, str]) -> str:
    """`{http://fasb.org/us-gaap/2025}Revenues` -> `us-gaap:Revenues`."""
    if name.startswith("{"):
        uri, local = name[1:].split("}", 1)
        return f"{prefixes.get(uri, uri)}:{local}"
    return name


def _parse_context(element: ET.Element) -> _Context:
    period = element.find(_XBRLI + "period")
    assert period is not None, f"context {element.get('id')} has no period"
    instant = period.findtext(_XBRLI + "instant")
    if instant:
        start, end = None, date.fromisoformat(instant.strip())
    else:
        start = date.fromisoformat(period.findtext(_XBRLI + "startDate", "").strip())
        end = date.fromisoformat(period.findtext(_XBRLI + "endDate", "").strip())

    # Axis and member QNames are kept in the filer's own prefixes (us-gaap:, srt:, tsla:).
    dimensions: list[dict[str, str | None]] = []
    for member in element.iter(_XBRLDI + "explicitMember"):
        dimensions.append(
            {
                "axis": member.get("dimension", "").strip(),
                "member": (member.text or "").strip(),
                "typed_value": None,
            }
        )
    for member in element.iter(_XBRLDI + "typedMember"):
        child = next(iter(member), None)
        dimensions.append(
            {
                "axis": member.get("dimension", "").strip(),
                "member": None,
                "typed_value": (child.text or "").strip() if child is not None else None,
            }
        )
    return _Context(start=start, end=end, dimensions=dimensions)


def _measure(element: ET.Element | None) -> str:
    text = (element.findtext(_XBRLI + "measure") or "") if element is not None else ""
    return text.strip().split(":")[-1]  # iso4217:USD -> USD, xbrli:shares -> shares


def _parse_unit(element: ET.Element) -> str:
    divide = element.find(_XBRLI + "divide")
    if divide is not None:
        numerator = _measure(divide.find(_XBRLI + "unitNumerator"))
        denominator = _measure(divide.find(_XBRLI + "unitDenominator"))
        return f"{numerator}/{denominator}"
    return _measure(element)
