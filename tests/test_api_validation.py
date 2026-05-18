from io import BytesIO

import fitz
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_rejects_non_pdf_file():
    response = client.post(
        "/anonymize",
        files={
            "file": ("not_a_pdf.txt", BytesIO(b"hello world"), "text/plain")
        },
    )

    assert response.status_code == 415


def test_rejects_invalid_pdf_magic():
    response = client.post(
        "/anonymize",
        files={
            "file": ("fake.pdf", BytesIO(b"not a real pdf"), "application/pdf")
        },
    )

    assert response.status_code == 400


def build_zero_page_pdf_bytes() -> bytes:
    objects = [
        "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        "2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n",
    ]

    content = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    offsets = [0]

    for obj in objects:
        offsets.append(len(content))
        content += obj.encode("ascii")

    xref_offset = len(content)

    xref = f"xref\n0 {len(objects) + 1}\n"
    xref += "0000000000 65535 f \n"

    for offset in offsets[1:]:
        xref += f"{offset:010d} 00000 n \n"

    trailer = (
        f"trailer\n"
        f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n"
        f"{xref_offset}\n"
        f"%%EOF\n"
    )

    return content + xref.encode("ascii") + trailer.encode("ascii")


def test_rejects_empty_pdf():
    pdf_bytes = build_zero_page_pdf_bytes()

    response = client.post(
        "/anonymize",
        files={
            "file": ("empty.pdf", BytesIO(pdf_bytes), "application/pdf")
        },
    )

    assert response.status_code == 422
    assert "no pages" in response.json()["detail"].lower()


def test_rejects_pdf_with_no_selectable_text():
    doc = fitz.open()
    doc.new_page()
    pdf_bytes = doc.tobytes()
    doc.close()

    response = client.post(
        "/anonymize",
        files={
            "file": ("blank.pdf", BytesIO(pdf_bytes), "application/pdf")
        },
    )

    assert response.status_code == 422
    assert "selectable text" in response.json()["detail"].lower()