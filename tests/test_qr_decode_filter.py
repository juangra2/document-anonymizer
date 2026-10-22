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
    monkeypatch.setattr(
        "app.services.vision.code_detector.detect_and_decode_visual_codes",
        lambda img, padding=8: fake,
    )

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
        (fitz.Rect(700, 235, 1546, 945), ""),   # a diagram that looks like a code
    ]
    monkeypatch.setattr(
        "app.services.vision.code_detector.detect_and_decode_visual_codes",
        lambda img, padding=8: fake,
    )

    zp = _zoned()
    redactor._add_visual_qr_zones(page, zp)
    doc.close()

    assert not any(z.zone_type == ZoneType.QR_CODE for z in zp.zones)


def test_ocr_segmenter_drops_undecoded_qr(monkeypatch):
    # Scanned path: a large region that does not decode (e.g. a notary seal)
    # must not become a QR_CODE zone.
    from PIL import Image
    from app.services.layout.segmenters import OcrPageSegmenter
    from app.services.ocr.models import OcrPage

    img = Image.new("RGB", (1000, 1000), "white")
    fake = [
        (fitz.Rect(50, 50, 150, 150), "ABC123"),    # decoded, small → keep
        (fitz.Rect(100, 100, 850, 850), ""),         # seal, no payload → drop
    ]
    monkeypatch.setattr(
        "app.services.vision.code_detector.detect_and_decode_visual_codes",
        lambda im, padding=8: fake,
    )
    zoned = OcrPageSegmenter().segment(OcrPage(0, 1000, 1000, []), image=img)
    qrs = [z for z in zoned.zones if z.zone_type == ZoneType.QR_CODE]
    assert len(qrs) == 1 and qrs[0].text == "ABC123"


def test_detect_redactable_codes_filters(monkeypatch):
    from PIL import Image
    import app.services.vision.code_detector as cd
    img = Image.new("RGB", (1000, 1000), "white")
    fake = [
        (fitz.Rect(10, 10, 110, 110), "PAYLOAD"),   # keep
        (fitz.Rect(0, 0, 900, 900), "PAYLOAD"),      # too large → drop
        (fitz.Rect(20, 20, 120, 120), ""),           # no payload → drop
    ]
    monkeypatch.setattr(cd, "detect_and_decode_visual_codes", lambda im, padding=8: fake)
    out = cd.detect_redactable_codes(img)
    assert len(out) == 1 and out[0][1] == "PAYLOAD"
