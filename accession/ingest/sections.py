"""Split a filing's blocks into Part/Item sections.

Two lessons from SecSignal's regex-over-plain-text approach:

1. The table of contents repeats every Item heading. Real headings are text
   blocks outside tables, while TOC entries live inside a `<table>`, so we
   only look for headings in text blocks (TOC tables are dropped upstream).
2. A 10-Q has Part I and Part II, and both contain an "Item 1" and an
   "Item 1A". Sections are keyed by (part, item), never by item alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from accession.ingest.html_blocks import Block

_PART = re.compile(r"^PART\s+(IV|III|II|I)\b\.?\s*(.*)$", re.IGNORECASE)
_ITEM = re.compile(r"^ITEM\s+(\d{1,2})\s*([A-C])?\s*[.:\-—–]?\s*(.*)$", re.IGNORECASE)
_MAX_HEADING_CHARS = 200

_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4}

# (part, item) -> a form-independent name, so agents can ask for "mda" or
# "risk_factors" without knowing that MD&A is Item 7 in a 10-K but Item 2 in a 10-Q.
_KINDS_10K = {
    (1, "1"): "business", (1, "1A"): "risk_factors", (1, "1B"): "unresolved_staff_comments",
    (1, "1C"): "cybersecurity", (1, "2"): "properties", (1, "3"): "legal_proceedings",
    (1, "4"): "mine_safety", (2, "5"): "market_for_equity", (2, "6"): "reserved",
    (2, "7"): "mda", (2, "7A"): "market_risk", (2, "8"): "financial_statements",
    (2, "9"): "accountant_changes", (2, "9A"): "controls", (2, "9B"): "other_information",
    (2, "9C"): "foreign_inspections", (3, "10"): "directors_governance",
    (3, "11"): "executive_compensation", (3, "12"): "security_ownership",
    (3, "13"): "related_transactions", (3, "14"): "accountant_fees", (4, "15"): "exhibits",
    (4, "16"): "form_summary",
}  # fmt: skip
_KINDS_10Q = {
    (1, "1"): "financial_statements", (1, "2"): "mda", (1, "3"): "market_risk",
    (1, "4"): "controls", (2, "1"): "legal_proceedings", (2, "1A"): "risk_factors",
    (2, "2"): "equity_sales", (2, "3"): "defaults", (2, "4"): "mine_safety",
    (2, "5"): "other_information", (2, "6"): "exhibits",
}  # fmt: skip
# 10-K items are unique across parts, so a missing PART heading can be inferred.
_PART_OF_10K_ITEM = {item: part for (part, item) in _KINDS_10K}


@dataclass
class Section:
    key: str  # "cover", "part1_item2", ...
    part: int | None
    item: str | None
    title: str
    kind: str
    order: int
    blocks: list[Block] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks)


def split_sections(blocks: list[Block], form_type: str) -> list[Section]:
    base_form = form_type.removesuffix("/A")
    kinds = _KINDS_10Q if base_form == "10-Q" else _KINDS_10K

    sections = [
        Section(key="cover", part=None, item=None, title="Cover page", kind="cover", order=0)
    ]
    part: int | None = None
    last_item_number = 0

    i = 0
    while i < len(blocks):
        block = blocks[i]
        heading = _heading(block)
        if heading is None:
            sections[-1].blocks.append(block)
            i += 1
            continue

        kind_of_heading, value, title = heading
        if kind_of_heading == "part":
            part = _ROMAN[value.upper()]
            last_item_number = 0
            i += 1
            continue

        item = value
        number = int(re.match(r"\d+", item).group())  # type: ignore[union-attr]
        if base_form == "10-Q" and part is not None and number < last_item_number and part == 1:
            part = 2  # Item numbering restarted without a PART II heading.
        if base_form != "10-Q" and (part is None or (part, item) not in kinds):
            part = _PART_OF_10K_ITEM.get(item, part)
        last_item_number = number

        # Some filers put the title in its own block right after "ITEM 7.".
        if not title and i + 1 < len(blocks) and _is_heading_like(blocks[i + 1]):
            title = blocks[i + 1].text
            i += 1

        key = f"part{part}_item{item.lower()}" if part else f"item{item.lower()}"
        sections.append(
            Section(
                key=key,
                part=part,
                item=item,
                title=title or f"Item {item}",
                kind=kinds.get((part or 0, item), "other"),
                order=len(sections),
            )
        )
        i += 1

    return [s for s in sections if s.blocks or s.key != "cover"]


def _heading(block: Block) -> tuple[str, str, str] | None:
    if not _is_heading_like(block):
        return None
    if match := _PART.match(block.text):
        return "part", match.group(1), match.group(2).strip(" .")
    if match := _ITEM.match(block.text):
        number, letter, title = match.groups()
        # Body text also starts with "Item 1A" ("Item 1A of our 10-K describes...");
        # real headings are bold or upper case.
        if not (block.is_bold or block.text[:4] == "ITEM"):
            return None
        return "item", number + (letter or "").upper(), title.strip(" .")
    return None


def _is_heading_like(block: Block) -> bool:
    return block.kind == "text" and len(block.text) <= _MAX_HEADING_CHARS
