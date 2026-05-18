"""Smoke test for Fase 0 (layout models) and Fase 2.1 (OCR base interface)."""
import sys
import fitz

from app.services.layout.models import ZoneType, ZoneToken, PageZone, ZonedPage, ZonedDocument
from app.services.layout.zone_config import get_detector_config, ALL_PII_TYPES
from app.services.ocr.base import OcrEngine

errors = []

def check(condition, msg):
    if not condition:
        errors.append(msg)

# --- ZoneType ---
check(ZoneType.SIGNATURE.auto_redact is True,  "SIGNATURE debe ser auto_redact")
check(ZoneType.QR_CODE.auto_redact is True,    "QR_CODE debe ser auto_redact")
check(ZoneType.BODY.auto_redact is False,      "BODY NO debe ser auto_redact")
check(ZoneType.FORM.auto_redact is False,      "FORM NO debe ser auto_redact")
check(ZoneType.TABLE.auto_redact is False,     "TABLE NO debe ser auto_redact")

# --- ZoneToken ---
r = fitz.Rect(0, 0, 100, 20)
token = ZoneToken(text="prueba", rect=r, confidence=0.92)
check(token.text == "prueba",      "ZoneToken.text")
check(token.confidence == 0.92,   "ZoneToken.confidence")
check(token.rect == r,            "ZoneToken.rect")

digital_token = ZoneToken(text="digital", rect=r)
check(digital_token.confidence is None, "digital token confidence debe ser None")

# --- PageZone ---
zone = PageZone(
    zone_type=ZoneType.FORM,
    rect=r,
    text="Nombre: Juan",
    tokens=[token],
    confidence=0.92,
    source="ocr",
)
check(zone.is_auto_redact is False, "FORM no es auto_redact")

sig_zone = PageZone(zone_type=ZoneType.SIGNATURE, rect=r, text="")
check(sig_zone.is_auto_redact is True, "SIGNATURE es auto_redact")

qr_zone = PageZone(zone_type=ZoneType.QR_CODE, rect=r, text="")
check(qr_zone.is_auto_redact is True, "QR_CODE es auto_redact")

# --- ZonedPage ---
body_zone = PageZone(zone_type=ZoneType.BODY, rect=r, text="Texto libre aqui.")
page = ZonedPage(page_index=0, width=595.0, height=842.0, zones=[zone, sig_zone, body_zone])

check(len(page.zones_of_type(ZoneType.FORM)) == 1,      "zones_of_type(FORM)")
check(len(page.zones_of_type(ZoneType.BODY)) == 1,      "zones_of_type(BODY)")
check(len(page.zones_of_type(ZoneType.TABLE)) == 0,     "zones_of_type(TABLE) vacio")
check(len(page.auto_redact_zones) == 1,                  "auto_redact_zones")
check("Nombre" in page.full_text,                        "full_text incluye zona FORM")
check("Texto libre" in page.full_text,                   "full_text incluye zona BODY")
check(page.avg_confidence == 0.92,                       "avg_confidence solo de zonas con score")

# Pagina digital (sin confianza OCR)
digital_page = ZonedPage(page_index=1, width=595.0, height=842.0,
                          zones=[PageZone(zone_type=ZoneType.BODY, rect=r, text="texto")])
check(digital_page.avg_confidence is None, "digital page avg_confidence es None")

# --- ZonedDocument ---
doc = ZonedDocument(pages=[page], document_type="pdf_scanned", ocr_engine="easyocr")
check(doc.document_type == "pdf_scanned", "ZonedDocument.document_type")
check(doc.ocr_engine == "easyocr",        "ZonedDocument.ocr_engine")
check(len(doc.pages) == 1,                "ZonedDocument.pages")

doc_digital = ZonedDocument(document_type="pdf_digital")
check(doc_digital.ocr_engine is None, "digital doc sin ocr_engine")

# --- zone_config: SIGNATURE/QR → redaccion total ---
cfg_sig = get_detector_config(ZoneType.SIGNATURE)
check(cfg_sig.entity_types is None,          "SIGNATURE entity_types es None")
check(cfg_sig.apply_layout_analysis is False, "SIGNATURE no aplica layout analysis")

cfg_qr = get_detector_config(ZoneType.QR_CODE)
check(cfg_qr.entity_types is None,           "QR_CODE entity_types es None")

# --- zone_config: FORM/TABLE/BODY → todos los tipos ---
for zt in [ZoneType.FORM, ZoneType.TABLE, ZoneType.BODY]:
    cfg = get_detector_config(zt)
    check(cfg.entity_types is not None,      f"{zt.value} tiene entity_types")
    check("ES_DNI" in cfg.entity_types,      f"{zt.value} incluye ES_DNI")
    check("PERSON" in cfg.entity_types,      f"{zt.value} incluye PERSON")
    check("IBAN_CODE" in cfg.entity_types,   f"{zt.value} incluye IBAN_CODE")
    check(cfg.apply_layout_analysis is True, f"{zt.value} aplica layout analysis")

# --- zone_config: HEADER → tipos reducidos, sin layout analysis ---
cfg_h = get_detector_config(ZoneType.HEADER)
check(cfg_h.apply_layout_analysis is False,   "HEADER no aplica layout analysis")
check("PERSON" in cfg_h.entity_types,         "HEADER incluye PERSON")
check("ES_DNI" in cfg_h.entity_types,         "HEADER incluye ES_DNI")
check("IBAN_CODE" not in cfg_h.entity_types,  "HEADER no incluye IBAN_CODE")
check("CREDIT_CARD" not in cfg_h.entity_types,"HEADER no incluye CREDIT_CARD")

# --- zone_config: FOOTER ---
cfg_f = get_detector_config(ZoneType.FOOTER)
check("CASE_NUMBER" in cfg_f.entity_types,    "FOOTER incluye CASE_NUMBER")
check("URL" in cfg_f.entity_types,            "FOOTER incluye URL")
check("ES_DNI" not in cfg_f.entity_types,     "FOOTER no incluye ES_DNI")

# --- zone_config: todas las zonas tienen configuracion ---
for zt in ZoneType:
    c = get_detector_config(zt)
    check(c is not None, f"get_detector_config({zt.value}) no es None")

# --- ALL_PII_TYPES completo ---
check(len(ALL_PII_TYPES) == 25, f"ALL_PII_TYPES tiene {len(ALL_PII_TYPES)} tipos (esperado 25)")
check(len(ALL_PII_TYPES) == len(set(ALL_PII_TYPES)), "ALL_PII_TYPES sin duplicados")

# --- OcrEngine es abstracta ---
try:
    OcrEngine()
    errors.append("OcrEngine deberia ser abstracta (TypeError esperado)")
except TypeError:
    pass  # correcto

# --- Resultado ---
if errors:
    print(f"FALLOS ({len(errors)}):")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print(f"OK - {25 + len([c for c in dir() if not c.startswith('_')])} checks pasados")
    print("  layout/models.py     -> ZoneType, ZoneToken, PageZone, ZonedPage, ZonedDocument")
    print("  layout/zone_config.py -> ZONE_DETECTOR_CONFIG, get_detector_config, ALL_PII_TYPES")
    print("  ocr/base.py          -> OcrEngine (ABC)")
