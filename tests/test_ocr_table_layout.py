import fitz

from app.services.ocr.models import OcrToken
from app.services.ocr.table_layout import OcrTableLayoutAnalyzer


def token(text, x0, y0, x1, y1):
    return OcrToken(
        text=text,
        confidence=0.95,
        rect=fitz.Rect(x0, y0, x1, y1),
    )


def test_redacts_case_table_rows_after_case_id():
    tokens = [
        token("ID", 50, 50, 70, 65),
        token("caso", 75, 50, 115, 65),
        token("Persona", 160, 50, 230, 65),
        token("Contacto", 270, 50, 350, 65),
        token("Datos", 390, 50, 440, 65),
        token("a", 445, 50, 455, 65),
        token("anonimizar", 460, 50, 550, 65),

        token("CAS-001", 50, 80, 120, 95),
        token("Diego", 160, 80, 210, 95),
        token("Ferrer", 215, 80, 270, 95),
        token("diego@example.com", 270, 80, 420, 95),
        token("Calle", 430, 80, 470, 95),
        token("Falsa", 475, 80, 520, 95),
    ]

    rects = OcrTableLayoutAnalyzer().rects_for_tables(tokens)

    assert rects
    assert all(rect.x0 > 120 for rect in rects)


def test_redacts_type_example_table_value_column():
    tokens = [
        token("Tipo", 50, 50, 90, 65),
        token("Ejemplo", 250, 50, 320, 65),
        token("ficticio", 325, 50, 390, 65),

        token("Email", 50, 80, 110, 95),
        token("lucia@example.com", 250, 80, 400, 95),

        token("Telefono", 50, 110, 130, 125),
        token("+34", 250, 110, 280, 125),
        token("600", 285, 110, 320, 125),
        token("00", 325, 110, 350, 125),
    ]

    rects = OcrTableLayoutAnalyzer().rects_for_tables(tokens)

    assert rects
    assert all(rect.x0 >= 240 for rect in rects)