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

# Only a switchboard *phone* reliably recurs in the letterhead and the body as
# the issuer's own datum.  Letterhead-position and value-allowlist signals are
# therefore restricted to PHONE_NUMBER: on administrative documents an address
# or postal code sitting in the header is usually the citizen's / work-centre's
# own (personal data to redact), not the issuer's, so those two signals only
# add false negatives for ADDRESS/POSTAL_CODE.  Proximity to an issuer name
# still applies to all three types.
LETTERHEAD_SUPPRESS_TYPES = {"PHONE_NUMBER"}
ALLOWLIST_TYPES = {"PHONE_NUMBER"}

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

# Company legal forms (S.L., S.A., S.L.U., S.Coop).  Kept for reference but no
# longer used as a proximity anchor: on this corpus it produced only false
# negatives (and bare-letter matches), never a correct suppression.
COMPANY_FORM_RE = re.compile(
    r"(?i)\bS\.?\s?(?:L\.?(?:U\.?)?|A\.?|Coop\.?|L\.?L\.?)\b"
)

# Narrow issuer roots used ONLY for the proximity rule.  Deliberately excludes
# the work-unit / ambiguous roots from INSTITUTION_NAME_RE — departament(o),
# institut(o), clínica, tècnica, enginyeria/ingeniería — because those name the
# *department / research group / work centre* a person belongs to and are
# typically followed by that person's own address ("centro habitual de trabajo
# Instituto X, Calle Y 1"), which is PII to redact, not the issuer's address.
PROXIMITY_ISSUER_RE = re.compile(
    r"(?i)\b(?:"
    r"universi(?:tat|dad|dade)"
    r"|ajuntament|ayuntamiento"
    r"|escola\b|escuela"
    r"|generalitat|govern\b|gobierno"
    r"|diputaci[oó]|consell(?:eria)?|consej(?:ería|eria)|conselleria"
    r"|patronat|patronato"
    r"|fundaci[oó]"
    r"|hospital"
    r"|mancomunitat|mancomunidad|consorci|c[aà]mara"
    r"|ag[èe]ncia|tresoreria|tesorer[ií]a"
    r"|delegaci[oó]|ministeri[oo]?|direcci[oó]n\s+general"
    r"|col[·.]?legi|colegio"
    r")\b"
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
    """Char spans of institution names (public roots + company forms).

    Used to decide whether a header/first-lines range qualifies as letterhead.
    """
    spans = [(m.start(), m.end()) for m in INSTITUTION_NAME_RE.finditer(page_text)]
    spans += [(m.start(), m.end()) for m in COMPANY_FORM_RE.finditer(page_text)]
    spans.sort()
    return spans


def proximity_issuer_spans(page_text: str) -> list[tuple[int, int]]:
    """Char spans of the narrow issuer roots used by the proximity rule."""
    return [(m.start(), m.end()) for m in PROXIMITY_ISSUER_RE.finditer(page_text)]


def letterhead_ranges(page_text: str, zoned_page: ZonedPage) -> list[tuple[int, int]]:
    """Char ranges considered letterhead: HEADER/FOOTER zones + first lines.

    A masthead is the issuer's own header, so a candidate range only qualifies
    as letterhead when it actually contains an institution name.  Without this
    guard the top lines of an ordinary personal form (applicant name + phone)
    would be mistaken for a letterhead and the citizen's contact data wrongly
    suppressed.
    """
    inst_spans = institution_name_spans(page_text)
    candidates: list[tuple[int, int]] = []

    for zone in zoned_page.zones:
        if zone.zone_type in (ZoneType.HEADER, ZoneType.FOOTER) and zone.text:
            start = zone.page_text_offset
            candidates.append((start, start + len(zone.text)))

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
        candidates.append((0, end))

    return [
        (r0, r1)
        for r0, r1 in candidates
        if any(r0 <= s < r1 for s, _ in inst_spans)
    ]


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
    allowlist: dict[str, set[str]] = {t: set() for t in ALLOWLIST_TYPES}

    for e in entities:
        if e.entity_type not in ALLOWLIST_TYPES:
            continue
        if _in_any_range(e.start, e.end, ranges):
            value = e.text or page_text[e.start:e.end]
            allowlist[e.entity_type].add(normalize_value(e.entity_type, value))

    return allowlist


# ---------------------------------------------------------------------------
# B3 — institutional suppression decision
# ---------------------------------------------------------------------------

def _form_ranges(zoned_page: ZonedPage) -> list[tuple[int, int]]:
    """Char ranges covered by FORM/TABLE zones (structurally redacted elsewhere)."""
    ranges: list[tuple[int, int]] = []
    for zone in zoned_page.zones:
        if zone.zone_type in (ZoneType.FORM, ZoneType.TABLE) and zone.text:
            start = zone.page_text_offset
            ranges.append((start, start + len(zone.text)))
    return ranges


def _person_in_range(start: int, end: int, person_spans: list[tuple[int, int]]) -> bool:
    """True if any PERSON span overlaps the open interval (start, end)."""
    for ps, pe in person_spans:
        if ps < end and pe > start:
            return True
    return False


def _is_institutional(
    entity: PiiEntity,
    page_text: str,
    inst_spans: list[tuple[int, int]],
    ranges: list[tuple[int, int]],
    allowlist: dict[str, set[str]],
    person_spans: list[tuple[int, int]],
) -> bool:
    """Whether *entity* is the issuer's contact datum (→ suppress, not PII)."""
    value = entity.text or page_text[entity.start:entity.end]
    norm = normalize_value(entity.entity_type, value)

    # Strong signal: same value recurs in the letterhead → issuer's own datum.
    # (allowlist only ever holds PHONE_NUMBER values; see ALLOWLIST_TYPES.)
    if norm in allowlist.get(entity.entity_type, set()):
        return True

    # The datum itself sits in the letterhead — only suppressed for PHONE_NUMBER;
    # header addresses/postal codes are usually personal/work-centre data here.
    if entity.entity_type in LETTERHEAD_SUPPRESS_TYPES and _in_any_range(
        entity.start, entity.end, ranges
    ):
        return True

    # An institution name appears just before the datum with no person between
    # them ("Ajuntament de Sueca, Plaça Major 1, 46410 Sueca").
    window_start = entity.start - PROXIMITY_WINDOW
    nearest_inst_end: int | None = None
    for s, e in inst_spans:
        if window_start <= e <= entity.start and (
            nearest_inst_end is None or e > nearest_inst_end
        ):
            nearest_inst_end = e
    if nearest_inst_end is not None and not _person_in_range(
        nearest_inst_end, entity.start, person_spans
    ):
        return True

    return False


def filter_institutional(
    page_text: str,
    zoned_page: ZonedPage,
    entities: list[PiiEntity],
) -> set[int]:
    """Indices of *entities* that are institutional contact data → suppress.

    Only PHONE_NUMBER / ADDRESS / POSTAL_CODE are ever suppressed.  Data inside
    FORM/TABLE zones is structurally redacted elsewhere and always kept here.
    "Ante duda, conservar": a datum is suppressed only on a positive issuer
    signal (allowlist match, letterhead position, or institution proximity with
    no intervening person).
    """
    inst_spans = proximity_issuer_spans(page_text)
    ranges = letterhead_ranges(page_text, zoned_page)
    allowlist = institutional_allowlist(page_text, zoned_page, entities)
    form_ranges = _form_ranges(zoned_page)
    person_spans = sorted(
        (e.start, e.end) for e in entities if e.entity_type == "PERSON"
    )

    suppress: set[int] = set()
    for i, e in enumerate(entities):
        if e.entity_type not in SUPPRESSIBLE_TYPES:
            continue
        if _in_any_range(e.start, e.end, form_ranges):
            continue
        if _is_institutional(e, page_text, inst_spans, ranges, allowlist, person_spans):
            suppress.add(i)
    return suppress
