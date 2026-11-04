from __future__ import annotations

from datetime import date
from typing import Any

from accession.edgar.filing_docs import is_instance, select_files
from accession.edgar.instance import instance_key, parse_instance

# Shapes copied from TSLA's Q3 2026 10-Q instance (tsla-20260930_htm.xml).
INSTANCE = b"""<?xml version="1.0" encoding="utf-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
  xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
  xmlns:us-gaap="http://fasb.org/us-gaap/2025"
  xmlns:srt="http://fasb.org/srt/2025"
  xmlns:dei="http://xbrl.sec.gov/dei/2025"
  xmlns:tsla="http://www.tesla.com/20260930">
  <xbrli:context id="c-1">
    <xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0001318605</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period>
  </xbrli:context>
  <xbrli:context id="c-q3">
    <xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0001318605</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:startDate>2026-07-01</xbrli:startDate><xbrli:endDate>2026-09-30</xbrli:endDate></xbrli:period>
  </xbrli:context>
  <xbrli:context id="c-q3-energy">
    <xbrli:entity>
      <xbrli:identifier scheme="http://www.sec.gov/CIK">0001318605</xbrli:identifier>
      <xbrli:segment>
        <xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">tsla:EnergyGenerationAndStorageMember</xbrldi:explicitMember>
      </xbrli:segment>
    </xbrli:entity>
    <xbrli:period><xbrli:startDate>2026-07-01</xbrli:startDate><xbrli:endDate>2026-09-30</xbrli:endDate></xbrli:period>
  </xbrli:context>
  <xbrli:context id="c-energy-assets">
    <xbrli:entity>
      <xbrli:identifier scheme="http://www.sec.gov/CIK">0001318605</xbrli:identifier>
      <xbrli:segment>
        <xbrldi:explicitMember dimension="us-gaap:PropertySubjectToOrAvailableForOperatingLeaseByMajorPropertyClassAxis">tsla:EnergyGenerationAndStorageSystemsMember</xbrldi:explicitMember>
      </xbrli:segment>
    </xbrli:entity>
    <xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period>
  </xbrli:context>
  <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
  <xbrli:unit id="usdPerShare">
    <xbrli:divide>
      <xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator>
      <xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator>
    </xbrli:divide>
  </xbrli:unit>
  <dei:DocumentType contextRef="c-q3">10-Q</dei:DocumentType>
  <us-gaap:Assets contextRef="c-1" unitRef="usd" decimals="-6">157517000000</us-gaap:Assets>
  <us-gaap:Assets contextRef="c-1" unitRef="usd" decimals="-6">157517000000</us-gaap:Assets>
  <us-gaap:Revenues contextRef="c-q3" unitRef="usd" decimals="-6">28046000000</us-gaap:Revenues>
  <us-gaap:Revenues contextRef="c-q3-energy" unitRef="usd" decimals="-6">2881000000</us-gaap:Revenues>
  <tsla:LeasedAssetsNet contextRef="c-energy-assets" unitRef="usd" decimals="-6">4453000000</tsla:LeasedAssetsNet>
  <us-gaap:EarningsPerShareBasic contextRef="c-q3" unitRef="usdPerShare" decimals="2">0.43</us-gaap:EarningsPerShareBasic>
  <us-gaap:GoodwillImpairmentLoss contextRef="c-q3" unitRef="usd" xsi:nil="true"/>
  <us-gaap:RevenueFromContractWithCustomerTextBlock contextRef="c-q3">&lt;div&gt;Revenue note&lt;/div&gt;</us-gaap:RevenueFromContractWithCustomerTextBlock>
</xbrli:xbrl>
"""


def facts() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = parse_instance(INSTANCE).to_pylist()
    return rows


def by_concept(concept: str) -> list[dict[str, Any]]:
    return [r for r in facts() if r["concept"] == concept]


def test_identical_repeats_are_collapsed() -> None:
    assert len(by_concept("us-gaap:Assets")) == 1
    assert len(facts()) == 8


def test_consolidated_fact_has_no_dimensions() -> None:
    (assets,) = by_concept("us-gaap:Assets")
    assert assets["dimensions"] == []
    assert assets["period_type"] == "instant"
    assert assets["value"] == 157_517_000_000
    assert (assets["unit"], assets["decimals"]) == ("USD", "-6")


def test_dimensional_fact_keeps_axis_and_member() -> None:
    total, energy = sorted(by_concept("us-gaap:Revenues"), key=lambda r: len(r["dimensions"]))
    assert total["dimensions"] == []
    assert energy["dimensions"] == [
        {
            "axis": "srt:ProductOrServiceAxis",
            "member": "tsla:EnergyGenerationAndStorageMember",
            "typed_value": None,
        }
    ]
    assert energy["period_start"] == date(2026, 7, 1)


def test_extension_concepts_are_kept() -> None:
    (leased,) = by_concept("tsla:LeasedAssetsNet")
    assert leased["taxonomy"] == "tsla"
    assert leased["value"] == 4_453_000_000


def test_divide_units_render_like_companyfacts() -> None:
    (eps,) = by_concept("us-gaap:EarningsPerShareBasic")
    assert (eps["unit"], eps["value"]) == ("USD/shares", 0.43)


def test_nil_text_blocks_and_text_facts() -> None:
    (nil,) = by_concept("us-gaap:GoodwillImpairmentLoss")
    assert nil["is_nil"] and nil["value"] is None

    (block,) = by_concept("us-gaap:RevenueFromContractWithCustomerTextBlock")
    assert block["is_text_block"] and block["value_text"] is None

    (doc_type,) = by_concept("dei:DocumentType")
    assert doc_type["value_text"] == "10-Q" and doc_type["unit"] is None


def test_select_files_keeps_document_instance_taxonomy_and_images() -> None:
    names = [
        "0001628280-26-066775.txt", "FilingSummary.xml", "MetaLinks.json", "R1.htm",
        "tsla-20260930.htm", "tsla-20260930.xsd", "tsla-20260930_cal.xml",
        "tsla-20260930_htm.xml", "tsla-20260930_lab.xml", "chart_g1.jpg",
        "tsla-2026x09x30xex311.htm",
    ]  # fmt: skip
    assert select_files(names, "tsla-20260930.htm") == [
        "FilingSummary.xml", "tsla-20260930.htm", "tsla-20260930.xsd",
        "tsla-20260930_cal.xml", "tsla-20260930_htm.xml", "tsla-20260930_lab.xml",
        "chart_g1.jpg",
    ]  # fmt: skip


def test_instance_detection_covers_legacy_standalone_instances() -> None:
    assert is_instance("tsla-20260930_htm.xml")
    assert is_instance("tsla-20181231.xml")
    assert not is_instance("tsla-20181231_pre.xml")
    assert not is_instance("FilingSummary.xml")


def test_instance_key_partitions_by_company_and_filing() -> None:
    assert instance_key("1318605", "0001628280-26-066775") == (
        "facts/xbrl/instance/cik=0001318605/accession=0001628280-26-066775/facts.parquet"
    )
