import json

import fitz

from app.services.pdf.pipeline import PdfAnonymizationPipeline


def create_pdf_with_sensitive_metadata(path) -> None:
    doc = fitz.open()
    page = doc.new_page()

    page.insert_text(
        (72, 72),
        """
INFORME DE PRUEBA

Solicitante: Juan Pérez García
DNI: 12345678Z
Email: juan.perez@example.com

Este documento contiene datos personales ficticios.
""",
        fontsize=12,
    )

    doc.set_metadata(
        {
            "title": "Documento de Juan Pérez García",
            "author": "juan.perez@example.com",
            "subject": "DNI 12345678Z",
            "keywords": "600123456, ES9121000418450200051332",
            "creator": "test-suite",
            "producer": "test-suite",
        }
    )

    doc.save(path)
    doc.close()


def test_pdf_metadata_is_cleaned_after_redaction(tmp_path):
    input_path = tmp_path / "input_with_metadata.pdf"
    output_path = tmp_path / "output.pdf"

    create_pdf_with_sensitive_metadata(input_path)

    pipeline = PdfAnonymizationPipeline()
    report = pipeline.process(
        input_path=input_path,
        output_path=output_path,
        job_id="metadata-test",
    )

    assert report.entities_detected > 0
    assert report.security_checks.metadata_cleanup_attempted is True
    assert report.security_checks.xml_metadata_cleanup_attempted is True

    output_doc = fitz.open(output_path)
    metadata = output_doc.metadata
    extracted_text = "\n".join(page.get_text() for page in output_doc)
    output_doc.close()

    serialized_metadata = json.dumps(metadata, ensure_ascii=False)
    output_bytes = output_path.read_bytes()

    sensitive_values = [
        "Juan Pérez García",
        "juan.perez@example.com",
        "12345678Z",
        "600123456",
        "ES9121000418450200051332",
    ]

    for value in sensitive_values:
        assert value not in extracted_text
        assert value not in serialized_metadata
        assert value.encode("utf-8") not in output_bytes


def test_report_does_not_contain_sensitive_values(tmp_path):
    input_path = tmp_path / "input_with_metadata.pdf"
    output_path = tmp_path / "output.pdf"

    create_pdf_with_sensitive_metadata(input_path)

    pipeline = PdfAnonymizationPipeline()
    report = pipeline.process(
        input_path=input_path,
        output_path=output_path,
        job_id="report-test",
    )

    serialized_report = report.model_dump_json()

    sensitive_values = [
        "Juan Pérez García",
        "juan.perez@example.com",
        "12345678Z",
        "600123456",
        "ES9121000418450200051332",
    ]

    for value in sensitive_values:
        assert value not in serialized_report

    assert report.security_checks.report_contains_pii_values is False