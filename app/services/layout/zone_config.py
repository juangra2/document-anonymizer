from __future__ import annotations

from dataclasses import dataclass

from app.services.layout.models import ZoneType


# ---------------------------------------------------------------------------
# Canonical list of entity types the system REDACTS.
# Keep this in sync with the recognizers in pii/recognizers_es.py and the
# patterns in pii/analyzer.py.
#
# LOCATION and ORGANIZATION are intentionally absent: the ground truth almost
# never annotates them as personal PII (the personal location goes through
# ADDRESS, the person through PERSON), so redacting them produced hundreds of
# false positives (F1 ≈ 0.02 / 0.00).  They are still DETECTED internally by the
# NER engine (NER_ALLOWED_TYPES in analyzer.py) so they remain available as a
# signal for issuer/institution identification — they are only excluded from the
# redaction output and the verification residual count (see ALL_PII_TYPES use in
# verification/layers.py).
# ---------------------------------------------------------------------------

ALL_PII_TYPES: list[str] = [
    "ES_DNI",
    "ES_NIE",
    "ES_CIF",
    "ES_NSS",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "IBAN_CODE",
    "PERSON",
    "ADDRESS",
    "PASSPORT",
    "CREDIT_CARD",
    "USER_ID",
    "IP_ADDRESS",
    "MEDICAL_ID",
    "POLICY_NUMBER",
    "LICENSE_PLATE",
    "CADASTRAL_REFERENCE",
    "CASE_NUMBER",
    "REGISTRY_NUMBER",
    "CERT_SERIAL",
    "CSV",
    "PROFESSIONAL_LICENSE",
    "POSTAL_CODE",
    "URL",
    "DATE_OF_BIRTH",
    "DATE_TIME",
    "CASE_TABLE_ROW",
    "ROLE_TABLE_ROW",
    # Importe economico ligado a persona o cuenta (nomina, transferencia,
    # liquidacion).  Los importes de presupuesto de obra quedan fuera por el
    # gate de contexto del detector, no por la configuracion de zona.
    "MONEY",
    # Razon social de empresa PRIVADA (requisito del ayuntamiento).  La
    # institucion publica queda excluida por el veto PUBLIC_INSTITUTION_RE.
    "ORGANIZATION",
]


# ---------------------------------------------------------------------------
# Per-zone detection policy
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ZoneDetectorConfig:
    """Specifies how PII detection should be applied to a zone.

    entity_types:
        None   → skip entity detection and redact the entire zone (SIGNATURE,
                 QR_CODE).
        list   → run detection restricted to these entity types.

    apply_layout_analysis:
        Whether the redactor should run sub-layout analysis (label-value
        detection, table column mapping) inside this zone.  Disabled for
        HEADER and FOOTER where the structure is rarely form-like.
    """

    entity_types: list[str] | None
    apply_layout_analysis: bool = True


# Sentinel: entire zone is redacted without running any entity detector.
_REDACT_ZONE = ZoneDetectorConfig(entity_types=None, apply_layout_analysis=False)

# Cabecera: identifiers and actor names are plausible; full layout analysis
# is not useful here because headers are rarely form-like.
# PHONE_NUMBER excluded: institutional phones (Ayuntamiento, hospital,
# university) appear in document letterheads/headers and the GT corpus
# does not annotate them as personal PII, generating ~180 FPs.
# Personal phones virtually never appear in document headers.
_HEADER_CONFIG = ZoneDetectorConfig(
    entity_types=[
        "PERSON",
        "ES_DNI",
        "ES_NIE",
        "ES_CIF",
        "CASE_NUMBER",
        "DATE_OF_BIRTH",
        "DATE_TIME",
        "EMAIL_ADDRESS",
        # Un IBAN nunca es contacto institucional de membrete: es dato
        # financiero y se redacta siempre.  La franja superior de una página
        # interior ("FORMA DE PAGO … IBAN: ES25-…") se clasifica como header
        # y sin este tipo el IBAN se perdía (regex de alta precisión: mod-97).
        "IBAN_CODE",
    ],
    apply_layout_analysis=False,
)

# Pie de página: case references, URLs, and the electronic-signature block
# (certificate serials and output-register numbers).  CERT_SERIAL and
# REGISTRY_NUMBER are high-precision deterministic patterns, so enabling them in
# the footer does not add false positives.
_FOOTER_CONFIG = ZoneDetectorConfig(
    # IBAN_CODE: mismo criterio que en cabecera — dato financiero, nunca
    # contacto de plantilla; el mod-97 evita falsos positivos.
    # DATE_TIME / DATE_OF_BIRTH: por requisito del ayuntamiento se anonimiza
    # TODA fecha, sin excepcion — tambien la del bloque de firma electronica y
    # la del pie de pagina, que antes quedaban fuera de alcance.
    entity_types=["CASE_NUMBER", "URL", "CERT_SERIAL", "REGISTRY_NUMBER", "CSV",
                  "IBAN_CODE", "DATE_TIME", "DATE_OF_BIRTH"],
    apply_layout_analysis=False,
)

# Anotaciones marginales: sparse, low-structure text.
_MARGINALIA_CONFIG = ZoneDetectorConfig(
    entity_types=[
        "PERSON",
        "ES_DNI",
        "ES_NIE",
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        # Toda fecha se anonimiza, tambien en anotaciones marginales.
        "DATE_TIME",
        "DATE_OF_BIRTH",
    ],
    apply_layout_analysis=False,
)

# Zones where every entity type is relevant and layout sub-analysis applies.
_ALL_TYPES_CONFIG = ZoneDetectorConfig(entity_types=ALL_PII_TYPES)

_BODY_CONFIG = ZoneDetectorConfig(entity_types=list(ALL_PII_TYPES))


ZONE_DETECTOR_CONFIG: dict[ZoneType, ZoneDetectorConfig] = {
    ZoneType.HEADER:     _HEADER_CONFIG,
    ZoneType.FOOTER:     _FOOTER_CONFIG,
    ZoneType.FORM:       _ALL_TYPES_CONFIG,
    ZoneType.TABLE:      _ALL_TYPES_CONFIG,
    ZoneType.BODY:       _BODY_CONFIG,
    ZoneType.SIGNATURE:  _REDACT_ZONE,
    ZoneType.QR_CODE:    _REDACT_ZONE,
    ZoneType.MARGINALIA: _MARGINALIA_CONFIG,
    ZoneType.UNKNOWN:    _ALL_TYPES_CONFIG,
}


def get_detector_config(zone_type: ZoneType) -> ZoneDetectorConfig:
    """Return the detection policy for the given zone type."""
    return ZONE_DETECTOR_CONFIG.get(zone_type, _ALL_TYPES_CONFIG)
