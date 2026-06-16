"""Institutional contact-data disambiguation (issuer detection + allowlist)."""
import fitz

from app.services.layout.models import PageZone, ZonedPage, ZoneType
from app.services.pii.institution_filter import (
    filter_institutional,
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


# --- suppression decision (filter_institutional) -------------------------

def _ent(page_text, entity_type, value):
    pos = page_text.index(value)
    return PiiEntity(entity_type, pos, pos + len(value), 0.9, text=value)


def test_suppress_phone_in_header():  # case 1
    page_text = "Ajuntament de Sueca Tel. 96 170 00 00\ncuerpo\nl3\nl4\nl5\n"
    zoned = _zoned([_zone(ZoneType.HEADER, "Ajuntament de Sueca Tel. 96 170 00 00", 0)])
    ents = [_ent(page_text, "PHONE_NUMBER", "96 170 00 00")]
    assert filter_institutional(page_text, zoned, ents) == {0}


def test_suppress_postal_in_header():  # case 2
    page_text = "Ayuntamiento\nPlaza Mayor 1, 46410 Sueca\ncuerpo\nl4\nl5\n"
    header = page_text[: page_text.index("\ncuerpo")]
    zoned = _zoned([_zone(ZoneType.HEADER, header, 0)])
    ents = [_ent(page_text, "POSTAL_CODE", "46410")]
    assert filter_institutional(page_text, zoned, ents) == {0}


def test_suppress_body_phone_matching_letterhead():  # case 3
    page_text = (
        "Hospital General Tel. 96 197 20 00\n"
        "cuerpo del documento\nl3\nl4\nl5\n"
        "Para citas llame al 96 197 20 00 en horario de mañana.\n"
    )
    header = page_text[: page_text.index("\ncuerpo")]
    zoned = _zoned([_zone(ZoneType.HEADER, header, 0)])
    first = page_text.index("96 197 20 00")
    second = page_text.index("96 197 20 00", first + 1)
    ents = [
        PiiEntity("PHONE_NUMBER", first, first + 12, 0.9, text="96 197 20 00"),
        PiiEntity("PHONE_NUMBER", second, second + 12, 0.9, text="96 197 20 00"),
    ]
    # both the letterhead occurrence and its body recurrence are institutional
    assert filter_institutional(page_text, zoned, ents) == {0, 1}


def test_suppress_address_after_institution_no_person():  # case 4
    page_text = (
        "Solicitud\nl2\nl3\nl4\nl5\n"
        "La Conselleria de Sanitat, con domicilio en Carrer Misser Mascó 31, "
        "atenderá la consulta.\n"
    )
    zoned = _zoned([_zone(ZoneType.BODY, page_text, 0)])
    ents = [_ent(page_text, "ADDRESS", "Carrer Misser Mascó 31")]
    assert filter_institutional(page_text, zoned, ents) == {0}


def test_keep_phone_after_person():  # case 5
    page_text = (
        "Datos\nl2\nl3\nl4\nl5\n"
        "El solicitante Juan Pérez García, con teléfono 600 123 456.\n"
    )
    zoned = _zoned([_zone(ZoneType.BODY, page_text, 0)])
    ents = [
        _ent(page_text, "PERSON", "Juan Pérez García"),
        _ent(page_text, "PHONE_NUMBER", "600 123 456"),
    ]
    assert filter_institutional(page_text, zoned, ents) == set()


def test_keep_phone_when_person_between_institution_and_datum():  # case 6
    page_text = (
        "Acta\nl2\nl3\nl4\nl5\n"
        "El Ajuntament notifica a Cristina Delgado Navarro, teléfono 696 555 444.\n"
    )
    zoned = _zoned([_zone(ZoneType.BODY, page_text, 0)])
    ents = [
        _ent(page_text, "PERSON", "Cristina Delgado Navarro"),
        _ent(page_text, "PHONE_NUMBER", "696 555 444"),
    ]
    assert filter_institutional(page_text, zoned, ents) == set()


def test_keep_non_suppressible_type_in_header():  # case 7
    page_text = "Ajuntament de Sueca NIF P4623400J\ncuerpo\nl3\nl4\nl5\n"
    header = page_text[: page_text.index("\ncuerpo")]
    zoned = _zoned([_zone(ZoneType.HEADER, header, 0)])
    ents = [_ent(page_text, "ES_NIF", "P4623400J")]
    assert filter_institutional(page_text, zoned, ents) == set()


def test_keep_personal_phone_far_from_institution():  # case 8
    page_text = (
        "Solicitud\nl2\nl3\nl4\nl5\n"
        "Mi número de contacto particular es 655 987 321 para cualquier aviso.\n"
    )
    zoned = _zoned([_zone(ZoneType.BODY, page_text, 0)])
    ents = [_ent(page_text, "PHONE_NUMBER", "655 987 321")]
    assert filter_institutional(page_text, zoned, ents) == set()


def test_keep_datum_in_form_zone():  # case 9
    page_text = "Teléfono: 91 123 45 67\n"
    zoned = _zoned([_zone(ZoneType.FORM, page_text, 0)])
    ents = [_ent(page_text, "PHONE_NUMBER", "91 123 45 67")]
    assert filter_institutional(page_text, zoned, ents) == set()


def test_keep_when_institution_beyond_window():  # case 10
    filler = "x" * 200
    page_text = (
        "Ajuntament de Sueca\nl2\nl3\nl4\nl5\n"
        f"{filler} teléfono 962 000 111.\n"
    )
    zoned = _zoned([_zone(ZoneType.BODY, page_text, 0)])
    ents = [_ent(page_text, "PHONE_NUMBER", "962 000 111")]
    assert filter_institutional(page_text, zoned, ents) == set()


def test_suppress_address_in_first_lines_without_header_zone():  # case 11
    page_text = (
        "Universitat de València\n"
        "Av. de Blasco Ibáñez 13, 46010 València\n"
        "Ref.: 2026/001\nl4\nl5\nbody\n"
    )
    zoned = _zoned([_zone(ZoneType.BODY, page_text, 0)])
    ents = [_ent(page_text, "ADDRESS", "Av. de Blasco Ibáñez 13")]
    assert filter_institutional(page_text, zoned, ents) == {0}
