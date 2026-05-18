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


def test_pdf_without_pii_is_processed_successfully(tmp_path):
    input_path = tmp_path / "input.pdf"
    output_path = tmp_path / "output.pdf"

    text = """
INFORME GENERAL

Este documento describe el estado general de un proyecto.
No contiene datos personales ni información sensible evidente.
El objetivo es comprobar que un documento sin entidades también se procesa.
"""

    create_pdf(input_path, text)

    pipeline = PdfAnonymizationPipeline()
    report = pipeline.process(
        input_path=input_path,
        output_path=output_path,
        job_id="no-pii-test",
        mode="strict",
    )

    redacted_text = extract_text(output_path)

    assert report.status == "completed"
    assert report.entities_detected == 0
    assert report.entities_redacted == 0
    assert report.residual_entities == 0

    assert "INFORME GENERAL" in redacted_text
    assert "estado general de un proyecto" in redacted_text