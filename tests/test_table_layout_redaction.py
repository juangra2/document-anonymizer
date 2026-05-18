import fitz

from app.services.pdf.pipeline import PdfAnonymizationPipeline


def create_table_like_pdf(path) -> None:
    doc = fitz.open()
    page = doc.new_page()

    rows = [
        ("Campo", "Valor ficticio"),
        ("Nombre completo", "Lucia Martin Pruebas"),
        ("Fecha de nacimiento", "14/09/1988"),
        ("DNI/NIF", "00000000X"),
        ("Pasaporte", "FAKE-P-742019"),
        ("Telefono movil", "+34 600 00 00 00"),
        ("Email personal", "lucia.pruebas@example.com"),
        ("Direccion postal", "Calle Falsa 123, 28000 Madrid"),
        ("IBAN", "ES91 0000 0000 0000 0000 0000"),
        ("Tarjeta de prueba", "4111 1111 1111 1111"),
        ("Usuario interno", "usr-demo-martin-001"),
        ("IP de ultima conexion", "192.0.2.24"),
    ]

    y = 72

    for label, value in rows:
        page.insert_text((72, y), label, fontsize=10)
        page.insert_text((220, y), value, fontsize=10)
        y += 18

    doc.save(path)
    doc.close()


def extract_text(path) -> str:
    doc = fitz.open(path)
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    return text


def test_table_layout_redacts_values_but_not_labels(tmp_path):
    input_path = tmp_path / "table.pdf"
    output_path = tmp_path / "output.pdf"

    create_table_like_pdf(input_path)

    pipeline = PdfAnonymizationPipeline()
    report = pipeline.process(
        input_path=input_path,
        output_path=output_path,
        job_id="table-layout-test",
        mode="strict",
    )

    text = extract_text(output_path)

    assert report.entities_detected > 0
    assert report.residual_entities == 0

    assert "Nombre completo" in text
    assert "Fecha de nacimiento" in text
    assert "DNI/NIF" in text
    assert "Pasaporte" in text
    assert "Telefono movil" in text
    assert "Email personal" in text
    assert "Direccion postal" in text
    assert "IBAN" in text
    assert "Tarjeta de prueba" in text
    assert "Usuario interno" in text
    assert "IP de ultima conexion" in text

    assert "Lucia Martin Pruebas" not in text
    assert "14/09/1988" not in text
    assert "00000000X" not in text
    assert "FAKE-P-742019" not in text
    assert "+34 600 00 00 00" not in text
    assert "lucia.pruebas@example.com" not in text
    assert "Calle Falsa 123" not in text
    assert "ES91 0000" not in text
    assert "4111 1111" not in text
    assert "usr-demo-martin-001" not in text
    assert "192.0.2.24" not in text