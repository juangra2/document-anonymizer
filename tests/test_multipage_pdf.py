import fitz

from app.services.pdf.pipeline import PdfAnonymizationPipeline


def create_multipage_pdf(path) -> None:
    doc = fitz.open()

    page1 = doc.new_page()
    page1.insert_text(
        (72, 72),
        """
INFORME MULTIPÁGINA

Solicitante: Juan Pérez García
DNI: 12345678Z
Email: juan.perez@example.com
""",
        fontsize=12,
    )

    page2 = doc.new_page()
    page2.insert_text(
        (72, 72),
        """
ANEXO

Promotor: María López Sánchez
Teléfono: 612 345 678
IBAN: ES91 2100 0418 4502 0005 1332
""",
        fontsize=12,
    )

    doc.save(path)
    doc.close()


def extract_text(path) -> str:
    doc = fitz.open(path)
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    return text


def test_multipage_pdf_redaction(tmp_path):
    input_path = tmp_path / "multipage.pdf"
    output_path = tmp_path / "output.pdf"

    create_multipage_pdf(input_path)

    pipeline = PdfAnonymizationPipeline()
    report = pipeline.process(
        input_path=input_path,
        output_path=output_path,
        job_id="multipage-test",
        mode="strict",
    )

    text = extract_text(output_path)

    assert report.pages == 2
    assert report.entities_detected > 0
    assert report.residual_entities == 0

    assert "12345678Z" not in text
    assert "juan.perez@example.com" not in text
    assert "María López Sánchez" not in text
    assert "612 345 678" not in text
    assert "ES91 2100" not in text