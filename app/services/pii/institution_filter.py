"""Personal-vs-institutional disambiguation for contact data.

A document's contact data (phone, postal address, postal code) belongs either to
a private individual (PII → redact) or to the **issuing institution** — the
ayuntamiento, university, hospital or company that emits the document (not PII;
redacting it over-redacts and tanks precision).

The two are structurally identical (a switchboard number looks like a personal
landline; the town-hall address looks like a citizen's address).  The only
signal is *whose* datum it is, which this module derives from document structure:

  * the **letterhead** (HEADER/FOOTER zones, or the first lines of the page)
    carries the issuer's own contact data;
  * contact data sitting right after an **institution name** with no person in
    between belongs to that institution
    (e.g. "Ajuntament de Sueca, amb domicili a Plaça Major, 1, 46410 Sueca").

Only PHONE_NUMBER, ADDRESS and POSTAL_CODE are ever considered for suppression.
"""
from __future__ import annotations

import re

from app.services.layout.models import PageZone, ZonedPage, ZoneType
from app.services.pii.models import PiiEntity

# Entity types subject to institutional suppression.
SUPPRESSIBLE_TYPES = {"PHONE_NUMBER", "ADDRESS", "POSTAL_CODE"}

# Public-institution name roots.  Centralised here and imported by analyzer.py
# (NER false-positive filtering).  Any span containing one of these roots names a
# document-issuing institution, not personal data under RGPD Art. 4.
INSTITUTION_NAME_RE = re.compile(
    r"(?i)\b(?:"
    r"universi(?:tat|dad|dade)"
    r"|ajuntament|ayuntamiento"
    r"|institut(?:o|ució|ución)?"
    r"|escola\b|escuela"
    r"|departament\b|departamento"
    r"|generalitat|govern\b|gobierno"
    r"|diputaci[oó]|consell(?:eria)?|consej(?:ería|eria)|conselleria"
    r"|patronat|patronato"
    r"|fundaci[oó]"
    r"|hospital|cl[ií]nica"
    r"|mancomunitat|mancomunidad|consorci|c[aà]mara"
    r"|ag[èe]ncia|tresoreria|tesorer[ií]a"
    r"|delegaci[oó]|ministeri[oo]?|direcci[oó]n\s+general"
    r"|col[·.]?legi|colegio"
    r"|enginyeria|ingenier[ií]a"
    r"|t[eè]cnica?\b"
    r")\b"
)

# Company legal forms (S.L., S.A., S.L.U., S.Coop) — used ONLY for the
# institution-proximity rule, never for the analyzer's NER filtering, to avoid
# clipping person names.
COMPANY_FORM_RE = re.compile(
    r"(?i)\bS\.?\s?(?:L\.?(?:U\.?)?|A\.?|Coop\.?|L\.?L\.?)\b"
)

# How many characters before a contact datum to look for an institution name.
PROXIMITY_WINDOW = 120

# Letterhead is also approximated by the first N text lines of the page, to
# catch issuer contact data that fell below the HEADER Y-band.
_LETTERHEAD_LINES = 5


# ---------------------------------------------------------------------------
# Value normalisation (for the institutional allowlist match)
# ---------------------------------------------------------------------------

def normalize_value(entity_type: str, value: str) -> str:
    """Normalise a contact value so header and body occurrences compare equal."""
    if entity_type == "PHONE_NUMBER":
        return re.sub(r"\D", "", value)
    if entity_type == "POSTAL_CODE":
        digits = re.sub(r"\D", "", value)
        return digits
    # ADDRESS and fallback: lowercase, collapse whitespace, strip edge punctuation.
    text = value.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .,;:·-")


# ---------------------------------------------------------------------------
# B1 — issuer / letterhead detection
# ---------------------------------------------------------------------------

def institution_name_spans(page_text: str) -> list[tuple[int, int]]:
    """Char spans of institution names (public roots + company forms)."""
    spans = [(m.start(), m.end()) for m in INSTITUTION_NAME_RE.finditer(page_text)]
    spans += [(m.start(), m.end()) for m in COMPANY_FORM_RE.finditer(page_text)]
    spans.sort()
    return spans


def letterhead_ranges(page_text: str, zoned_page: ZonedPage) -> list[tuple[int, int]]:
    """Char ranges considered letterhead: HEADER/FOOTER zones + first lines."""
    ranges: list[tuple[int, int]] = []

    for zone in zoned_page.zones:
        if zone.zone_type in (ZoneType.HEADER, ZoneType.FOOTER) and zone.text:
            start = zone.page_text_offset
            ranges.append((start, start + len(zone.text)))

    # First N lines of the page (covers letterheads below the HEADER Y-band).
    end = 0
    line_count = 0
    for i, ch in enumerate(page_text):
        if ch == "\n":
            line_count += 1
            end = i
            if line_count >= _LETTERHEAD_LINES:
                break
    else:
        end = len(page_text)
    if end > 0:
        ranges.append((0, end))

    return ranges


def _in_any_range(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    mid = (start + end) // 2
    return any(r0 <= mid < r1 for r0, r1 in ranges)


# ---------------------------------------------------------------------------
# B2 — institutional value allowlist
# ---------------------------------------------------------------------------

def institutional_allowlist(
    page_text: str,
    zoned_page: ZonedPage,
    entities: list[PiiEntity],
) -> dict[str, set[str]]:
    """Normalised contact values that appear in the letterhead → institutional.

    Returns {entity_type: {normalized_value, ...}} for PHONE/ADDRESS/POSTAL.
    These values are the issuer's own and may be suppressed wherever they recur.
    """
    ranges = letterhead_ranges(page_text, zoned_page)
    allowlist: dict[str, set[str]] = {t: set() for t in SUPPRESSIBLE_TYPES}

    for e in entities:
        if e.entity_type not in SUPPRESSIBLE_TYPES:
            continue
        if _in_any_range(e.start, e.end, ranges):
            value = e.text or page_text[e.start:e.end]
            allowlist[e.entity_type].add(normalize_value(e.entity_type, value))

    return allowlist
