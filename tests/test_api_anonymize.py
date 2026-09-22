from io import BytesIO

import fitz
import json
import zipfile
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def build_sample_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "Juan Perez con DNI 12345678Z y email juan.perez@example.com",
        fontsize=12,
    )

    data = doc.tobytes()
    doc.close()
    return data


def test_anonymize_pdf_removes_text():
    pdf_bytes = build_sample_pdf()

    response = client.post(
            "/anonymize",
            files={
                "file": ("input.pdf", BytesIO(pdf_bytes), "application/pdf")
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert 'filename="documento_anonimizado.pdf"' in response.headers["content-disposition"]

    redacted_doc = fitz.open(stream=response.content, filetype="pdf")
    text = "\n".join(page.get_text() for page in redacted_doc)
    redacted_doc.close()

    assert "12345678Z" not in text
    assert "juan.perez@example.com" not in text

def test_anonymize_can_return_zip_with_pdf_and_report():
    pdf_bytes = build_sample_pdf()

    response = client.post(
        "/anonymize?output=zip",
        files={
            "file": ("input.pdf", BytesIO(pdf_bytes), "application/pdf")
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert 'filename="documento_anonimizado.zip"' in response.headers["content-disposition"]

    zip_buffer = BytesIO(response.content)

    with zipfile.ZipFile(zip_buffer, mode="r") as zip_file:
        names = set(zip_file.namelist())

        assert "documento_anonimizado.pdf" in names
        assert "report.json" in names

        report = json.loads(zip_file.read("report.json").decode("utf-8"))
        redacted_pdf_bytes = zip_file.read("documento_anonimizado.pdf")

    assert report["job_id"]
    assert report["status"] in {"completed", "completed_with_warnings"}
    assert report["entities_detected"] > 0
    assert report["entities_redacted"] > 0
    assert "entities_by_type" in report

    serialized_report = json.dumps(report, ensure_ascii=False)

    assert "12345678Z" not in serialized_report
    assert "juan.perez@example.com" not in serialized_report
    assert "Juan Perez" not in serialized_report
    assert "Juan Pérez" not in serialized_report

    redacted_doc = fitz.open(stream=redacted_pdf_bytes, filetype="pdf")
    redacted_text = "\n".join(page.get_text() for page in redacted_doc)
    redacted_doc.close()

    assert "12345678Z" not in redacted_text
    assert "juan.perez@example.com" not in redacted_text

def test_anonymize_zip_report_contains_output_and_mode():
    pdf_bytes = build_sample_pdf()

    response = client.post(
        "/anonymize?output=zip&mode=strict",
        files={
            "file": ("input.pdf", BytesIO(pdf_bytes), "application/pdf")
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["x-redaction-mode"] == "strict"
    assert response.headers["x-output-format"] == "zip"

    zip_buffer = BytesIO(response.content)

    with zipfile.ZipFile(zip_buffer, mode="r") as zip_file:
        report = json.loads(zip_file.read("report.json").decode("utf-8"))

    assert report["output_format"] == "zip"
    assert report["redaction_mode"] == "strict"
    assert report["redaction_strategy"] == "physical_pdf_redaction"

def test_mode_param_removed_and_ignored():
    # El parámetro mode se retiró de la API: cualquier valor enviado se ignora
    # (comportamiento estándar de FastAPI) y siempre se procesa en strict.
    pdf_bytes = build_sample_pdf()
    response = client.post(
        "/anonymize?mode=balanced",
        files={"file": ("input.pdf", BytesIO(pdf_bytes), "application/pdf")},
    )
    assert response.status_code == 200
    assert response.headers["x-redaction-mode"] == "strict"
