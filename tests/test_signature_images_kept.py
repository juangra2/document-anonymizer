"""A2.1 — la imagen de la firma manuscrita NO se redacta.

Requisito del ayuntamiento: la rúbrica se conserva; lo que se anonimiza es el
NOMBRE que la acompaña ("Fdo.: NOMBRE"), que es texto y va por otro camino.
Estos tests fijan la decisión para que nadie reactive los detectores visuales
por considerarlos una mejora.
"""
import fitz

from app.core.config import settings
from app.services.layout.models import ZoneType
from app.services.layout.zone_config import get_detector_config
from app.services.pii.analyzer import PiiAnalyzer


def test_visual_signature_and_stamp_detectors_are_off_by_default():
    assert settings.detect_visual_signatures is False
    assert settings.detect_visual_stamps is False


def test_digital_path_creates_no_signature_zone_by_default(tmp_path):
    from app.services.pdf.extractor import extract_page_text
    from app.services.layout.segmenters import DigitalPageSegmenter
    from app.services.pdf.redactor import _add_signature_zones

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 700), "Fdo.: Lola Romera Martinez", fontsize=11)
    # Trazo que simula una rúbrica manuscrita.
    page.draw_bezier((90, 660), (120, 620), (160, 700), (200, 650))

    page_text = extract_page_text(page, 0)
    zoned = DigitalPageSegmenter().segment(page_text, page.rect)
    _add_signature_zones(page, zoned, page_text)
    sig_zones = [z for z in zoned.zones if z.zone_type == ZoneType.SIGNATURE]
    doc.close()

    assert sig_zones == [], "no debe crearse ninguna zona de firma manuscrita"


def test_signature_zone_would_redact_everything_if_ever_enabled():
    # Documenta por qué importa: una zona SIGNATURE se redacta entera, sin
    # análisis de entidades.  Por eso el detector visual debe seguir apagado.
    assert get_detector_config(ZoneType.SIGNATURE).entity_types is None


def test_signature_name_text_is_still_redacted():
    analyzer = PiiAnalyzer()
    text = "Fdo.: Lola Romera Martinez"
    persons = [
        text[e.start:e.end]
        for e in analyzer.analyze(text, mode="strict", zone_type="body")
        if e.entity_type == "PERSON"
    ]
    assert "Lola Romera Martinez" in persons
