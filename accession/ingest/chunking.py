"""Table-aware chunking with lineage.

Rules:
- Chunks never cross a section boundary or a subheading.
- Text chunks are built from whole paragraphs up to `max_text_chars`; only a
  single paragraph longer than that is split, at sentence boundaries.
- A table is its own chunk, together with its caption (the short lines just
  above it, e.g. "Consolidated Balance Sheets / (in millions) / (unaudited)").
  A table too long for one chunk is split by rows, and every piece repeats
  the caption and column headers, so no number is separated from its header.
- Every chunk carries its filing, section, fiscal period, units, and the
  XBRL facts it displays.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from accession.ingest.html_blocks import Block, DocumentInfo, FactRef, to_markdown
from accession.ingest.sections import Section

_MAX_CAPTION_LINES = 6
_MAX_CAPTION_CHARS = 200
_UNITS_IN_TEXT = re.compile(
    r"\(\s*(?:\w+\s+)?in\s+(thousands|millions|billions)[^)]*\)", re.IGNORECASE
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"“])")


@dataclass(frozen=True)
class FilingContext:
    cik: str
    ticker: str
    accession_number: str
    form_type: str
    filed_date: str
    period_of_report: str | None
    info: DocumentInfo


@dataclass
class Chunk:
    chunk_id: str
    chunk_type: str  # "text" | "table"
    section: Section
    ordinal: int
    subheading: str | None
    text: str
    table_caption: str | None = None
    table_header: str | None = None
    units: str | None = None
    fact_refs: list[FactRef] = field(default_factory=list)


def chunk_sections(
    sections: list[Section],
    filing: FilingContext,
    max_text_chars: int = 2000,
    max_table_chars: int = 6000,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in sections:
        chunks.extend(_chunk_section(section, filing, max_text_chars, max_table_chars))
    return chunks


def _chunk_section(
    section: Section, filing: FilingContext, max_text: int, max_table: int
) -> list[Chunk]:
    out: list[Chunk] = []
    buffer: list[Block] = []
    subheading: str | None = None

    def emit(chunk_type: str, text: str, **extra: object) -> None:
        out.append(
            Chunk(
                chunk_id=f"{filing.accession_number}:{section.key}:{len(out):04d}",
                chunk_type=chunk_type,
                section=section,
                ordinal=len(out),
                subheading=subheading,
                text=text,
                **extra,  # type: ignore[arg-type]
            )
        )

    def flush() -> None:
        if not buffer:
            return
        for text, refs in _pack_paragraphs(buffer, max_text):
            emit("text", text, fact_refs=refs)
        buffer.clear()

    for block in section.blocks:
        if block.kind == "table":
            caption_blocks = _take_caption(buffer)
            flush()
            caption = " ".join(b.text for b in caption_blocks) or None
            for piece in _split_table(block, caption, max_table):
                emit(
                    "table",
                    piece,
                    table_caption=caption,
                    table_header=_header_text(block),
                    units=_units(caption, block),
                    fact_refs=block.fact_refs,
                )
            continue

        if block.is_bold and len(block.text) <= _MAX_CAPTION_CHARS:
            # A new subheading closes the current chunk. Consecutive bold lines
            # ("Tesla, Inc." / "Consolidated Balance Sheets" / "(unaudited)") are
            # one heading. It stays in the buffer in case it captions a table.
            if buffer and all(_is_heading_block(b) for b in buffer):
                subheading = f"{subheading} {block.text}"
            else:
                flush()
                subheading = block.text
        buffer.append(block)

    flush()
    return out


def _take_caption(buffer: list[Block]) -> list[Block]:
    """Pop the short trailing lines that introduce the table."""
    caption: list[Block] = []
    while (
        buffer
        and len(caption) < _MAX_CAPTION_LINES
        # Bold headings qualify even when they end in a period ("Tesla, Inc.").
        and (_is_heading_block(buffer[-1]) or _is_caption_line(buffer[-1].text))
    ):
        caption.insert(0, buffer.pop())
    return caption


def _is_heading_block(block: Block) -> bool:
    return block.is_bold and len(block.text) <= _MAX_CAPTION_CHARS


def _is_caption_line(text: str) -> bool:
    # Titles and unit notes are short and don't read as sentences; a lead-in
    # sentence ("The following table summarizes...:") belongs to the prose.
    if len(text) > _MAX_CAPTION_CHARS:
        return False
    return _UNITS_IN_TEXT.search(text) is not None or not text.endswith((".", ":"))


def _pack_paragraphs(blocks: list[Block], max_chars: int) -> list[tuple[str, list[FactRef]]]:
    pieces: list[tuple[str, list[FactRef]]] = []
    current: list[str] = []
    refs: list[FactRef] = []
    size = 0

    for block in blocks:
        for paragraph in _split_long(block.text, max_chars):
            if current and size + len(paragraph) + 2 > max_chars:
                pieces.append(("\n\n".join(current), refs))
                current, refs, size = [], [], 0
            current.append(paragraph)
            size += len(paragraph) + 2
        refs.extend(block.fact_refs)

    if current:
        pieces.append(("\n\n".join(current), refs))
    return pieces


def _split_long(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        if current and len(current) + len(sentence) + 1 > max_chars:
            parts.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        parts.append(current)
    return parts


def _split_table(block: Block, caption: str | None, max_chars: int) -> list[str]:
    prefix = f"{caption}\n\n" if caption else ""
    full = prefix + block.text
    if len(full) <= max_chars:
        return [full]

    header, body = block.rows[: block.header_row_count], block.rows[block.header_row_count :]
    pieces: list[str] = []
    rows: list[list[str]] = []
    for row in body:
        candidate = prefix + to_markdown(header + rows + [row], len(header))
        if rows and len(candidate) > max_chars:
            pieces.append(prefix + to_markdown(header + rows, len(header)))
            rows = []
        rows.append(row)
    if rows:
        pieces.append(prefix + to_markdown(header + rows, len(header)))
    return pieces


def _header_text(block: Block) -> str | None:
    header = block.rows[: block.header_row_count]
    if not header:
        return None
    return " / ".join(" ".join(cell for cell in row if cell) for row in header)


def _units(caption: str | None, block: Block) -> str | None:
    # The caption says what the reader sees ("in millions, except per share data");
    # the XBRL scale attribute is the fallback when there is no caption.
    if caption and (match := _UNITS_IN_TEXT.search(caption)):
        return match.group(1).lower()
    return block.scale_units


def embed_text(chunk: Chunk, filing: FilingContext) -> str:
    """Text to embed: the chunk plus the context a retriever needs to rank it."""
    period = filing.info.period_end or filing.period_of_report or ""
    parts = [
        f"{filing.info.registrant or filing.ticker} ({filing.ticker}) {filing.form_type}"
        f" for the period ended {period}",
        chunk.section.title,
    ]
    if chunk.subheading and chunk.subheading != chunk.section.title:
        parts.append(chunk.subheading)
    return " | ".join(parts) + "\n\n" + chunk.text
