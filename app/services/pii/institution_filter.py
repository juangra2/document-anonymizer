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
    # Instituciones publicas anadidas por requisito del ayuntamiento: no se
    # anonimiza NINGUNA institucion publica mencionada, no solo la emisora.
    r"|registr(?:o|e)\s+(?:mercantil|civil|de\s+la\s+propiedad)"
    r"|notar[ií]a|jutjat|juzgado|tribunal|audiencia\s+provincial"
    r"|seguretat\s+social|seguridad\s+social"
    r"|ag[èe]ncia\s+tribut[àa]ria|agencia\s+tributaria"
    r"|subdelegaci[oó]n?|cabildo|entidad\s+local"
    r"|federaci[oó]n?|confederaci[oó]n?"
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

# Un tratamiento o una marca de destinatario entre la institucion y el dato:
# empieza el bloque de una PERSONA (el destinatario de una carta va arriba, en la
# franja del membrete).  Cuenta como persona aunque su nombre no se haya
# detectado —en un escaneado el OCR puede estropearlo—: en la duda, se tacha.
_TRATAMIENTO_RE = re.compile(
    r"(?:\b(?:Sr|Sra|Srta|Dña|Dª|Excm[oa]|Il[·.]?lm[oa]|Ilm[oa])\.?\s+[A-ZÁÉÍÓÚÀÈÒÇÑ]"
    r"|\b(?:D|Don|Doña|En|Na)\.?\s+[A-ZÁÉÍÓÚÀÈÒÇÑ][a-záéíóúàèòçñ]"
    r"|\bA/A\b|\bAtt?\.\s"
    r"|(?i:\bdestinatari[oa]?\b|\binteresad[oa]\b|\binteressad[ae]\b))"
)

# Letterhead is also approximated by the first N text lines of the page, to
# catch issuer contact data that fell below the HEADER Y-band.
_LETTERHEAD_LINES = 5
# Una linea de mas caracteres ya es un parrafo, no un membrete.
_LETTERHEAD_MAX_LINE = 100


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

    # First N lines of the page (covers letterheads below the HEADER Y-band),
    # hasta la primera linea LARGA: un membrete son lineas cortas, y en un
    # escaneado MinerU devuelve un parrafo entero como una sola linea —sin este
    # corte, el primer parrafo del cuerpo contaba como membrete—.
    end = 0
    line_count = 0
    line_start = 0
    for i, ch in enumerate(page_text + "\n"):
        if ch == "\n":
            if i - line_start > _LETTERHEAD_MAX_LINE:
                break
            line_count += 1
            end = min(i, len(page_text))
            line_start = i + 1
            if line_count >= _LETTERHEAD_LINES:
                break
    if end > 0:
        candidates.append((0, end))

    return [
        (r0, r1)
        for r0, r1 in candidates
        if any(r0 <= s < r1 for s, _ in inst_spans)
    ]


def _en_membrete(entity: PiiEntity, page_text: str, ranges: list[tuple[int, int]]) -> bool:
    """El dato cae en el membrete y antes de el no empieza el bloque de una persona.

    Las "primeras lineas" de la pagina pueden alcanzar al destinatario de una
    carta o al primer parrafo; un tratamiento o una marca de interesado entre el
    principio del membrete y el dato dice que ya no es la institucion.
    """
    for r0, r1 in ranges:
        if _in_any_range(entity.start, entity.end, [(r0, r1)]) and not \
                _TRATAMIENTO_RE.search(page_text, r0, entity.start):
            return True
    return False


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
        if _en_membrete(e, page_text, ranges):
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
    # Salvo que entre el principio del membrete y el dato empiece el bloque de
    # una persona (el destinatario): ver _TRATAMIENTO_RE.
    if entity.entity_type in LETTERHEAD_SUPPRESS_TYPES and _en_membrete(entity, page_text, ranges):
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
    if (
        nearest_inst_end is not None
        and not _person_in_range(nearest_inst_end, entity.start, person_spans)
        and not _TRATAMIENTO_RE.search(page_text, nearest_inst_end, entity.start)
    ):
        return True

    return False


def filter_institutional(
    page_text: str,
    zoned_page: ZonedPage,
    entities: list[PiiEntity],
    person_spans: list[tuple[int, int]] | None = None,
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
    # La prueba de "hay una persona en medio" puede venir de fuera: el redactor
    # la calcula sin filtrar cuando PERSON está desactivado.  Si se sacara de
    # las entidades ya filtradas, desactivar PERSON haría pasar por
    # institucional el teléfono de un particular (F5).
    if person_spans is None:
        person_spans = [(e.start, e.end) for e in entities if e.entity_type == "PERSON"]
    person_spans = sorted(person_spans)

    suppress: set[int] = set()
    for i, e in enumerate(entities):
        if e.entity_type not in SUPPRESSIBLE_TYPES:
            continue
        if _in_any_range(e.start, e.end, form_ranges):
            continue
        if _is_institutional(e, page_text, inst_spans, ranges, allowlist, person_spans):
            suppress.add(i)
    return suppress


# Veto estricto de institucion PUBLICA, usado para decidir si una organizacion
# se anonimiza.  Es deliberadamente mas estrecho que INSTITUTION_NAME_RE: aquel
# incluye raices genericas de profesion ("ingenieria", "tecnica") utiles para
# filtrar falsos positivos del NER sobre PERSON/LOCATION, pero que vetarian
# empresas privadas legitimas como "Grupotec Ingenieria".
#
# Por requisito del ayuntamiento no se anonimiza ninguna institucion publica
# mencionada — no solo la emisora del documento —; solo la razon social de
# entidad privada es dato a proteger.
PUBLIC_INSTITUTION_RE = re.compile(
    r"(?i)\b(?:"
    r"ajuntament|ayuntamiento|alcald[ií]a"
    r"|universi(?:tat|dad|dade)"
    r"|generalitat|govern\b|gobierno"
    r"|diputaci[oó]n?|consell(?:eria)?|consej(?:ería|eria)|conselleria"
    r"|ministeri[oo]?|delegaci[oó]n?|subdelegaci[oó]n?"
    r"|direcci[oó]n\s+general"
    r"|hospital|centro\s+de\s+salud|centre\s+de\s+salut"
    r"|seguretat\s+social|seguridad\s+social|tresoreria|tesorer[ií]a"
    r"|ag[èe]ncia\s+tribut[àa]ria|agencia\s+tributaria"
    r"|registr(?:o|e)\s+(?:mercantil|civil|de\s+la\s+propiedad)"
    r"|notar[ií]a|jutjat|juzgado|tribunal|audiencia\s+provincial"
    r"|mancomunitat|mancomunidad|consorci(?:o|)\b|cabildo|comarca\b"
    r"|patronat|patronato|instituto\s+nacional|institut\s+nacional"
    r"|c[aà]mara\s+(?:de\s+comercio|oficial)"
    r"|colegio\s+oficial|col[·.]?legi\s+oficial"
    r"|federaci[oó]n?\s+espa[ñn]ola|conselleria"
    r")\b"
)
