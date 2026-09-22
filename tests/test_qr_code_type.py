"""QR_CODE es un tipo normal, y LAYOUT_VALUE no figura en el resumen por tipo.

Desmarcar QR_CODE solo libera los códigos cuyo contenido no lleva ningún tipo
que siga ACTIVO.  Consecuencia práctica, que estos tests dejan por escrito: el
QR de verificación de la sede lleva dentro una URL y un código seguro de
verificación, así que para conservarlo hay que desmarcar también esos dos.  Se verifica decodificando un render
del PDF de salida con el mismo detector que usa el sistema.
"""
from io import BytesIO
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.main import app
from app.services.pdf.pipeline import PdfAnonymizationPipeline
from app.services.pii.analyzer import PiiAnalyzer
from app.services.pii.code_policy import code_may_stay_visible
from app.services.vision.code_detector import detect_redactable_codes

client = TestClient(app)

SEDE = "https://sede.valencia.es/verificar?csv=ABCD1234EFGH5678"
PERSONAL = "Pilar Medina Vázquez | 51622172Y | 28/7034367386"


def _qr(c, payload: str, x: float, y: float, size: float = 130) -> None:
    widget = QrCodeWidget(payload)
    b = widget.getBounds()
    drawing = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0, size / (b[3] - b[1]), 0, 0])
    drawing.add(widget)
    renderPDF.draw(drawing, c, x, y)


def _pdf_with_two_qrs(path: Path) -> Path:
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica", 11)
    c.drawString(60, 790, "AJUNTAMENT DE VALENCIA - Justificante de registro")
    c.drawString(60, 770, "El presente documento puede verificarse en la sede electronica.")
    _qr(c, SEDE, 60, 560)
    _qr(c, PERSONAL, 360, 260)
    c.save()
    return path


def _codes_in(pdf: Path) -> list[str]:
    found: list[str] = []
    with fitz.open(pdf) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            found += [text for _, text in detect_redactable_codes(image)]
    return found


@pytest.fixture(scope="module")
def pipeline():
    return PdfAnonymizationPipeline()


# --- catálogo y parámetro --------------------------------------------------------

def test_qr_code_is_listed_as_an_optional_type():
    items = {i["id"]: i for i in client.get("/entity-types").json()}
    assert "QR_CODE" in items
    assert "barras" in items["QR_CODE"]["description"]


def test_qr_code_can_be_disabled_through_the_api():
    doc = fitz.open()
    page = doc.new_page()
    # Texto suficiente para no caer por debajo del mínimo de contenido digital.
    page.insert_text((72, 100), "Solicitud de licencia presentada por el interesado.", fontsize=11)
    page.insert_text((72, 120), "DNI: 12345678Z", fontsize=11)
    response = client.post(
        "/anonymize?disabled_types=qr_code",
        files={"file": ("in.pdf", BytesIO(doc.tobytes()), "application/pdf")},
    )
    assert response.status_code == 200


# --- la regla -------------------------------------------------------------------

def test_the_policy_only_frees_codes_without_active_types_inside():
    base = PiiAnalyzer()
    solo_qr = base.with_disabled_types({"QR_CODE"})
    qr_y_contenido = base.with_disabled_types({"QR_CODE", "URL", "CSV"})

    assert not code_may_stay_visible(SEDE, base), "con el tipo activo se redacta siempre"
    assert not code_may_stay_visible(SEDE, solo_qr), (
        "el QR de sede lleva dentro una URL y un CSV, y ambos siguen activos"
    )
    assert code_may_stay_visible(SEDE, qr_y_contenido), (
        "desmarcando tambien lo que lleva dentro, el codigo se conserva"
    )
    assert not code_may_stay_visible(PERSONAL, qr_y_contenido), "lleva nombre, DNI y NSS"
    assert not code_may_stay_visible("", qr_y_contenido), (
        "sin contenido legible no se puede descartar que lleve datos"
    )


# --- sobre el PDF de salida -------------------------------------------------------

def test_the_input_codes_are_readable(tmp_path):
    codes = _codes_in(_pdf_with_two_qrs(tmp_path / "in.pdf"))
    assert SEDE in codes and PERSONAL in codes


def test_both_codes_are_redacted_by_default(tmp_path, pipeline):
    src = _pdf_with_two_qrs(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    report = pipeline.process(src, out, "t", "strict", False, frozenset())
    assert _codes_in(out) == []
    assert report.entities_by_type.get("QR_CODE") == 2


def test_unmarking_qr_and_what_it_contains_keeps_only_that_code(tmp_path, pipeline):
    src = _pdf_with_two_qrs(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    report = pipeline.process(
        src, out, "t", "strict", False, frozenset({"QR_CODE", "URL", "CSV"}),
    )
    codes = _codes_in(out)
    assert SEDE in codes, "el QR de verificacion de sede debe conservarse"
    assert PERSONAL not in codes, (
        "el QR con nombre y DNI se redacta aunque QR_CODE este desmarcado"
    )
    assert "QR_CODE" in report.entity_types_disabled


def test_unmarking_only_qr_still_redacts_the_sede_code(tmp_path, pipeline):
    """La consecuencia de la regla, por escrito: no basta con desmarcar el QR."""
    src = _pdf_with_two_qrs(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    pipeline.process(src, out, "t", "strict", False, frozenset({"QR_CODE"}))
    assert _codes_in(out) == [], "su contenido lleva una URL y un CSV, que siguen activos"


def test_qr_count_does_not_depend_on_include_entities(tmp_path, pipeline):
    src = _pdf_with_two_qrs(tmp_path / "in.pdf")
    without = pipeline.process(src, tmp_path / "a.pdf", "t", "strict", False, frozenset())
    with_list = pipeline.process(src, tmp_path / "b.pdf", "t", "strict", True, frozenset())
    assert without.entities_by_type.get("QR_CODE") == with_list.entities_by_type.get("QR_CODE")


# --- el informe cuadra -----------------------------------------------------------

def test_layout_value_is_not_reported_as_an_entity_type(tmp_path, pipeline):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 100), "Nombre", fontsize=11)
    page.insert_text((200, 100), "Roberto Iglesias Calvo", fontsize=11)
    page.insert_text((60, 130), "Teléfono", fontsize=11)
    page.insert_text((200, 130), "612 345 678", fontsize=11)
    src = tmp_path / "form.pdf"
    doc.save(src)
    report = pipeline.process(src, tmp_path / "out.pdf", "t", "strict", False, frozenset())
    assert "LAYOUT_VALUE" not in report.entities_by_type
    known = set(report.entity_types_applied) | set(report.entity_types_disabled)
    assert set(report.entities_by_type) <= known
    assert report.structural_redactions_untyped >= 0
