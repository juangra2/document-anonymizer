"""Shared QR-payload helper.

A detected QR code is always redacted as a whole (its rect is painted black), so
its visual content is covered regardless.  But the decoded payload often embeds
typed PII — e.g. ``CTR:CTR/2020/7949/00|Antonio Muñoz Gómez`` carries a
CASE_NUMBER and a PERSON.  Running the payload through the analyzer surfaces
those as proper typed entities for the audit report and the verification layers,
instead of one opaque ``QR_CODE`` blob.
"""
from __future__ import annotations

from app.services.pii.analyzer import PiiAnalyzer
from app.services.pii.models import PiiEntity


def qr_payload_entities(
    analyzer: PiiAnalyzer,
    payload: str,
    mode: str,
) -> list[PiiEntity]:
    """Return typed PII entities embedded in a decoded QR payload.

    Spans are relative to *payload*.  No rects are produced — the QR zone rect is
    already redacted by the caller; these entities are for reporting/verification.
    """
    if not payload or not payload.strip():
        return []

    found = analyzer.analyze(payload, mode=mode)

    return [
        PiiEntity(
            entity_type=e.entity_type,
            start=e.start,
            end=e.end,
            score=e.score,
            source="qr_payload",
            text=payload[e.start:e.end],
        )
        for e in found
        if e.entity_type != "QR_CODE"
    ]
