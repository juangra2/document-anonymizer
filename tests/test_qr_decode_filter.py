"""Digital QR redaction: only decoded, plausibly-sized codes are redacted."""
import fitz

import app.services.pdf.redactor as redactor
from app.services.layout.models import ZonedPage, ZoneType


def _zoned():
    return ZonedPage(page_index=0, width=595, height=842, zones=[])


def test_only_decoded_small_codes_redacted(monkeypatch):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Rendered at 300 DPI → ~2479x3508 px.  small ≈ 0.001 of area; huge ≈ 0.10.
    fake = [
        (fitz.Rect(100, 100, 220, 220), "https://verifica/abc"),   # decoded, small → keep
        (fitz.Rect(200, 200, 1400, 1300), "PAYLOAD"),              # decoded but huge → drop
        (fitz.Rect(300, 300, 420, 420), ""),                       # no payload → drop
    ]
    monkeypatch.setattr(redactor, "detect_and_decode_visual_codes", lambda img: fake)

    zp = _zoned()
    redactor._add_visual_qr_zones(page, zp)
    doc.close()

    qrs = [z for z in zp.zones if z.zone_type == ZoneType.QR_CODE]
    assert len(qrs) == 1, "only the small decoded code should be redacted"
    assert qrs[0].text == "https://verifica/abc"


def test_no_codes_when_none_decode(monkeypatch):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    fake = [
        (fitz.Rect(700, 235, 1546, 945), ""),   # the TOMO_02 diagram false positive
    ]
    monkeypatch.setattr(redactor, "detect_and_decode_visual_codes", lambda img: fake)

    zp = _zoned()
    redactor._add_visual_qr_zones(page, zp)
    doc.close()

    assert not any(z.zone_type == ZoneType.QR_CODE for z in zp.zones)
