from __future__ import annotations

from accession.ingest.chunking import FilingContext, chunk_sections
from accession.ingest.html_blocks import Block, parse_html, to_markdown
from accession.ingest.pipeline import process_filing
from accession.ingest.sections import Section, split_sections

B = 'style="font-weight:700"'

# A miniature 10-Q in the shape Workiva generates for TSLA: TOC inside a table,
# real headings as bold divs, page footers, and a balance sheet whose numbers
# are inline XBRL with `$` and parentheses in their own cells.
HTML = f"""<html><body>
<div style="display:none"><ix:header>
  <ix:nonnumeric name="dei:DocumentType" contextref="c-1">10-Q</ix:nonnumeric>
</ix:header></div>
<div><span {B}>Tesla, Inc.</span></div>
<div><ix:nonnumeric name="dei:EntityRegistrantName" contextref="c-1">Tesla, Inc.</ix:nonnumeric></div>
<div><ix:nonnumeric name="dei:DocumentFiscalYearFocus" contextref="c-1">2026</ix:nonnumeric>
 <ix:nonnumeric name="dei:DocumentFiscalPeriodFocus" contextref="c-1">Q3</ix:nonnumeric></div>
<table>
  <tr><td>PART I. FINANCIAL INFORMATION</td></tr>
  <tr><td>Item 1.</td><td>Financial Statements</td><td>4</td></tr>
  <tr><td>Item 2.</td><td>Management's Discussion</td><td>20</td></tr>
  <tr><td>Item 1A.</td><td>Risk Factors</td><td>40</td></tr>
</table>
<div>6</div><hr/><div>Table of Contents</div>
<div><span {B}>PART I. FINANCIAL INFORMATION</span></div>
<div><span {B}>ITEM 1. FINANCIAL STATEMENTS</span></div>
<div><span {B}>Consolidated Balance Sheets</span></div>
<div><span {B}>(in millions, except per share data)</span></div>
<table>
  <tr><td></td><td colspan="3">September 30, 2026</td><td colspan="3">December 31, 2025</td></tr>
  <tr><td>Assets</td><td></td><td></td></tr>
  <tr><td>Cash and cash equivalents</td><td>$</td>
    <td><ix:nonfraction name="us-gaap:CashAndCashEquivalentsAtCarryingValue" contextref="c-3"
      unitref="usd" scale="6" decimals="-6">17,853</ix:nonfraction></td><td></td>
    <td>$</td><td><ix:nonfraction name="us-gaap:CashAndCashEquivalentsAtCarryingValue"
      contextref="c-4" unitref="usd" scale="6" decimals="-6">16,513</ix:nonfraction></td><td></td></tr>
  <tr><td>Other (expense) income, net</td><td>(</td><td>117</td><td>)</td><td>(</td><td>28</td><td>)</td></tr>
</table>
<div><span {B}>ITEM 2. MANAGEMENT'S DISCUSSION AND ANALYSIS</span></div>
<div>Item 1A of our Annual Report describes our risks. Revenue grew in the quarter.</div>
<div><span {B}>PART II. OTHER INFORMATION</span></div>
<div><span {B}>ITEM 1. LEGAL PROCEEDINGS</span></div>
<div>See Note 10.</div>
<div><span {B}>ITEM 1A. RISK FACTORS</span></div>
<div>No material changes.</div>
</body></html>""".encode()


def context() -> FilingContext:
    info, _ = parse_html(HTML)
    return FilingContext(
        cik="0001318605", ticker="TSLA", accession_number="0001628280-26-066775",
        form_type="10-Q", filed_date="2026-10-22", period_of_report="2026-09-30", info=info,
    )  # fmt: skip


def sections() -> dict[str, object]:
    _, blocks = parse_html(HTML)
    return {s.key: s for s in split_sections(blocks, "10-Q")}


def test_document_info_comes_from_inline_dei_tags() -> None:
    info, _ = parse_html(HTML)
    assert (info.registrant, info.document_type) == ("Tesla, Inc.", "10-Q")
    assert (info.fiscal_year_focus, info.fiscal_period_focus) == ("2026", "Q3")


def test_toc_and_page_artifacts_are_dropped() -> None:
    _, blocks = parse_html(HTML)
    texts = [b.text for b in blocks]
    assert "Table of Contents" not in texts and "6" not in texts
    assert not any("Item 2." in t for t in texts)  # TOC rows gone


def test_table_cells_are_collapsed_and_aligned() -> None:
    _, blocks = parse_html(HTML)
    (table,) = [b for b in blocks if b.kind == "table"]
    assert table.rows[0] == ["", "September 30, 2026", "December 31, 2025"]
    assert table.rows[2] == ["Cash and cash equivalents", "$17,853", "$16,513"]
    assert table.rows[3] == ["Other (expense) income, net", "(117)", "(28)"]
    assert table.header_row_count == 1  # "Assets" is a group label, not a header
    assert table.scale_units == "millions"
    assert [r.context_id for r in table.fact_refs] == ["c-3", "c-4"]


def test_part_one_and_part_two_items_stay_separate() -> None:
    found = sections()
    assert {"part1_item1", "part1_item2", "part2_item1", "part2_item1a"} <= found.keys()
    assert found["part1_item2"].kind == "mda"  # type: ignore[attr-defined]
    assert found["part2_item1"].kind == "legal_proceedings"  # type: ignore[attr-defined]


def test_body_text_starting_with_item_is_not_a_heading() -> None:
    mda = sections()["part1_item2"]
    assert "Item 1A of our Annual Report" in mda.text  # type: ignore[attr-defined]


def test_infers_part_two_when_item_numbers_restart() -> None:
    blocks = [
        Block(kind="text", text="PART I. FINANCIAL INFORMATION", is_bold=True),
        Block(kind="text", text="ITEM 4. CONTROLS AND PROCEDURES", is_bold=True),
        Block(kind="text", text="ITEM 1A. RISK FACTORS", is_bold=True),
        Block(kind="text", text="No material changes."),
    ]
    assert [s.key for s in split_sections(blocks, "10-Q")] == ["part1_item4", "part2_item1a"]


def test_items_without_any_part_heading_are_not_guessed() -> None:
    blocks = [
        Block(kind="text", text="ITEM 1. FINANCIAL STATEMENTS", is_bold=True),
        Block(kind="text", text="ITEM 2. MD&A", is_bold=True),
        Block(kind="text", text="ITEM 1. LEGAL PROCEEDINGS", is_bold=True),
        Block(kind="text", text="None."),
    ]
    keys = [s.key for s in split_sections(blocks, "10-Q")]
    assert keys == ["item1", "item2", "item1"]


def test_ten_k_part_is_inferred_from_item_number() -> None:
    blocks = [Block(kind="text", text="ITEM 7. MANAGEMENT'S DISCUSSION", is_bold=True)]
    (mda,) = split_sections(blocks, "10-K")
    assert (mda.key, mda.kind) == ("part2_item7", "mda")


def test_table_chunk_carries_caption_units_and_facts() -> None:
    _, blocks = parse_html(HTML)
    chunks = chunk_sections(split_sections(blocks, "10-Q"), context())
    (table,) = [c for c in chunks if c.chunk_type == "table"]
    assert table.table_caption == "Consolidated Balance Sheets (in millions, except per share data)"
    assert table.units == "millions"
    assert table.table_header == "September 30, 2026 December 31, 2025"
    assert table.text.startswith("Consolidated Balance Sheets")
    assert "| Cash and cash equivalents | $17,853 | $16,513 |" in table.text
    # Caption lines move into the table chunk instead of a stray text chunk.
    assert not any(c.text == "Consolidated Balance Sheets" for c in chunks)


def test_long_tables_split_by_rows_and_repeat_headers() -> None:
    rows = [["", "2026", "2025"]] + [[f"Line item {i}", f"{i},000", f"{i},100"] for i in range(60)]
    table = Block(kind="table", text=to_markdown(rows, 1), rows=rows, header_row_count=1)
    caption = Block(kind="text", text="Debt summary (in millions)", is_bold=True)
    section = Section(key="part1_item1", part=1, item="1", title="FS", kind="x", order=1)
    section.blocks = [caption, table]
    pieces = chunk_sections([section], context(), max_table_chars=600)
    assert len(pieces) > 1
    for piece in pieces:
        lines = piece.text.splitlines()
        assert lines[0] == "Debt summary (in millions)"
        assert lines[2] == "|  | 2026 | 2025 |"
        assert len(piece.text) <= 600


def test_process_filing_rows_carry_lineage() -> None:
    sections_table, chunks_table = process_filing(HTML, context())
    chunk = chunks_table.to_pylist()[0]
    assert chunk["accession_number"] == "0001628280-26-066775"
    assert (chunk["fiscal_year_focus"], chunk["fiscal_period_focus"]) == ("2026", "Q3")
    assert chunk["embed_text"].startswith("Tesla, Inc. (TSLA) 10-Q for the period ended")
    assert sections_table.num_rows == len(sections())
