"""Institutional contact-data disambiguation (issuer detection + allowlist)."""
import fitz

from app.services.layout.models import PageZone, ZonedPage, ZoneType
from app.services.pii.institution_filter import (
    institution_name_spans,
    institutional_allowlist,
    letterhead_ranges,
    normalize_value,
)
from app.services.pii.models import PiiEntity


def _zone(zone_type, text, offset):
    return PageZone(
        zone_type=zone_type,
        rect=fitz.Rect(0, 0, 10, 10),
        text=text,
        page_text_offset=offset,
    )


def _zoned(zones):
    return ZonedPage(page_index=0, width=100, height=100, zones=zones)


# --- normalization -------------------------------------------------------

def test_normalize_phone_strips_separators():
    assert normalize_value("PHONE_NUMBER", "+34 946 266 565") == "34946266565"
    assert normalize_value("PHONE_NUMBER", "93 580 11 00") == "935801100"


def test_normalize_postal_and_address():
    assert normalize_value("POSTAL_CODE", "46410") == "46410"
    assert normalize_value("ADDRESS", "  Plaça Major, 1, 46410 Sueca. ") == \
        "plaça major, 1, 46410 sueca"


# --- issuer detection ----------------------------------------------------

def test_institution_name_spans_finds_public_and_company():
    text = "Ajuntament de Sueca y Consultores Ibéricos S.L. y Hospital General"
    spans = institution_name_spans(text)
    found = [text[s:e] for s, e in spans]
    assert any("Ajuntament" in f for f in found)
    assert any("Hospital" in f for f in found)
    assert any("S.L" in f or "S.L." in f for f in found)


def test_letterhead_ranges_cover_header_and_first_lines():
    page_text = "Ajuntament de Sueca\nPlaça Major, 1, 46410 Sueca\nRef.: X\nl4\nl5\nbody body"
    zoned = _zoned([_zone(ZoneType.HEADER, "Ajuntament de Sueca", 0)])
    ranges = letterhead_ranges(page_text, zoned)
    # header zone range present
    assert any(r0 == 0 for r0, _ in ranges)
    # first-lines range covers the institutional address line
    addr_pos = page_text.index("46410")
    assert any(r0 <= addr_pos < r1 for r0, r1 in ranges)


# --- allowlist -----------------------------------------------------------

def test_allowlist_collects_letterhead_values():
    page_text = (
        "Ayuntamiento de Bellaterra\n"
        "Plaza de la Vila, 1, 08193 Bellaterra Tel. 93 580 11 00\n"
        "cuerpo del documento\n"
    )
    header_text = page_text[: page_text.index("\ncuerpo")]
    zoned = _zoned([_zone(ZoneType.HEADER, header_text, 0)])

    cp_pos = page_text.index("08193")
    tel_pos = page_text.index("93 580 11 00")
    entities = [
        PiiEntity("POSTAL_CODE", cp_pos, cp_pos + 5, 0.9, text="08193"),
        PiiEntity("PHONE_NUMBER", tel_pos, tel_pos + 12, 0.9, text="93 580 11 00"),
    ]
    allow = institutional_allowlist(page_text, zoned, entities)
    assert "08193" in allow["POSTAL_CODE"]
    assert "935801100" in allow["PHONE_NUMBER"]


def test_allowlist_ignores_body_only_values():
    page_text = "Solicitante\ncuerpo\nl3\nl4\nl5\nTeléfono: 600 123 456 personal\n"
    zoned = _zoned([_zone(ZoneType.BODY, page_text, 0)])
    tel_pos = page_text.index("600 123 456")
    entities = [PiiEntity("PHONE_NUMBER", tel_pos, tel_pos + 11, 0.9, text="600 123 456")]
    allow = institutional_allowlist(page_text, zoned, entities)
    assert "600123456" not in allow["PHONE_NUMBER"]
