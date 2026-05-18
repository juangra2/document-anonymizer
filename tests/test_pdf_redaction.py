import fitz

from app.services.pdf.pipeline import PdfAnonymizationPipeline


def create_pdf(path, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=12)
    doc.save(path)
    doc.close()


def extract_text(path) -> str:
    doc = fitz.open(path)
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    return text


def test_pdf_redaction_removes_sensitive_values_but_keeps_labels(tmp_path):
    input_path = tmp_path / "input.pdf"
    output_path = tmp_path / "output.pdf"

    text = """
INFORME DE PRUEBA

Solicitante: Juan Pérez García
DNI: 12345678Z
Email: juan.perez@example.com
Teléfono: 600 123 456
IBAN: ES91 2100 0418 4502 0005 1332
Dirección: Calle Mayor 12, 46001 Valencia
Expediente: EXP-2026-ABC-001

Este documento contiene datos personales ficticios para probar la anonimización.
"""

    create_pdf(input_path, text)

    pipeline = PdfAnonymizationPipeline()
    report = pipeline.process(
        input_path=input_path,
        output_path=output_path,
        job_id="test-job",
    )

    redacted_text = extract_text(output_path)

    assert report.entities_detected > 0
    assert report.residual_entities == 0

    assert "INFORME DE PRUEBA" in redacted_text
    assert "Solicitante:" in redacted_text
    assert "DNI:" in redacted_text
    assert "Email:" in redacted_text
    assert "Teléfono:" in redacted_text
    assert "IBAN:" in redacted_text
    assert "Dirección:" in redacted_text
    assert "Expediente:" in redacted_text

    assert "Juan Pérez García" not in redacted_text
    assert "12345678Z" not in redacted_text
    assert "juan.perez@example.com" not in redacted_text
    assert "600 123 456" not in redacted_text
    assert "ES91 2100 0418 4502 0005 1332" not in redacted_text
    assert "Calle Mayor 12, 46001 Valencia" not in redacted_text
    assert "EXP-2026-ABC-001" not in redacted_text


def test_pdf_redaction_accepts_scanned_or_image_only_pdf(tmp_path):
    input_path = tmp_path / "image_only.pdf"
    output_path = tmp_path / "output.pdf"

    # Creamos una imagen sintética con texto, como si fuera un escaneo.
    image_path = tmp_path / "scan.png"

    from PIL import Image, ImageDraw

    image = Image.new("RGB", (800, 400), "white")
    draw = ImageDraw.Draw(image)
    draw.text((50, 50), "Nombre: Juan Perez", fill="black")
    draw.text((50, 90), "DNI: 12345678Z", fill="black")
    draw.text((50, 130), "Email: juan.perez@example.com", fill="black")
    image.save(image_path)

    # Insertamos esa imagen en un PDF.
    doc = fitz.open()
    page = doc.new_page(width=800, height=400)

    page.insert_image(
        page.rect,
        filename=str(image_path),
    )

    doc.save(input_path)
    doc.close()

    pipeline = PdfAnonymizationPipeline()

    report = pipeline.process(
        input_path=input_path,
        output_path=output_path,
        job_id="test-job",
    )

    assert output_path.exists()
    assert report.pages == 1
    assert report.document_type in {
        "pdf_scanned",
        "pdf_rasterized_ocr",
        "pdf_hybrid",
    }