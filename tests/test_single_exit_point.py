"""Bloque 6 — acoplamientos latentes cerrados por el punto único de salida.

F5, F10 y F11 no eran alcanzables por la API (PERSON es obligatorio), pero se
convertían en fuga el día que cambiase la lista de obligatorios.  Se prueban a
nivel de pipeline, con el analizador filtrado directamente.  O3 (recuento que
dependía de include_entities) sí afectaba al informe por defecto.
"""
import fitz
import pytest

from app.services.ocr.models import OcrPage, OcrToken
from app.services.pdf import plan_pages
from app.services.pdf.redactor import redact_pdf_digital
from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _pdf(path, lines, fontsize=9):
    doc = fitz.open()
    page = doc.new_page()
    for i, line in enumerate(lines):
        # En el cuerpo de la pagina: la cabecera excluye PHONE_NUMBER (telefonos
        # institucionales) y el caso F5 no se ejercitaria.
        page.insert_text((40, 320 + i * 16), line, fontsize=fontsize)
    doc.save(path)
    doc.close()
    return path


def _text(path):
    with fitz.open(path) as doc:
        return "\n".join(p.get_text() for p in doc)


# --- F5: supresión institucional con PERSON desactivado ------------------------------------

F5_LINES = [
    # Relleno: el filtro institucional trata las 5 primeras lineas de la pagina
    # como membrete y suprime ahi cualquier telefono, haya o no persona.
    "ACORD DE MODIFICACIO DE LES CONDICIONS DE TREBALL",
    "Les parts intervinents es reconeixen mutuament la capacitat necessaria.",
    "El present acord es formalitza per duplicat i a un sol efecte.",
    "Les condicions no modificades es mantenen en els termes vigents.",
    "La modificacio produeix efectes des de la data de signatura.",
    "Ambdues parts manifesten la seua conformitat amb el contingut.",
    "Actuant per delegació de la rectora/rector. D'ALTRA PART: MARCOS ALONSO MORENO,",
    "amb Doc. identitat: 72375841Q, telèfon +34679339028 i correu marcos.alonso@jazztel.es.",
    "Reconeixent-se capacitat legal suficient per a l'atorgament del present document.",
]


def test_disabling_person_does_not_turn_a_private_phone_into_institutional(tmp_path):
    src = _pdf(tmp_path / "f5.pdf", F5_LINES)
    ref = tmp_path / "ref.pdf"
    off = tmp_path / "off.pdf"
    redact_pdf_digital(input_path=src, output_path=ref, job_id="t", analyzer=analyzer)
    redact_pdf_digital(input_path=src, output_path=off, job_id="t",
                       analyzer=analyzer.with_disabled_types({"PERSON"}))
    assert "+34679339028" not in _text(ref)
    assert "+34679339028" not in _text(off), "el teléfono del particular no puede liberarse"


# --- F10: la celda-nombre aislada respeta el filtro -----------------------------------------

def test_standalone_name_cell_respects_disabled_person(tmp_path):
    lines = ["RELACION DE ASISTENTES A LA SESION ORDINARIA", "", "", "", "",
             "Roberto Iglesias Calvo", "", "", "", "", "", "Lucía Navarro Méndez"]
    src = _pdf(tmp_path / "f10.pdf", lines, fontsize=11)
    out = tmp_path / "out.pdf"
    report = redact_pdf_digital(input_path=src, output_path=out, job_id="t",
                                analyzer=analyzer.with_disabled_types({"PERSON"}), collect_entities=True)
    text = _text(out)
    assert "Roberto Iglesias Calvo" in text and "Lucía Navarro Méndez" in text
    assert "PERSON" not in report.entities_by_type


# --- O3: ver tests/test_counts_two_columns.py (necesita dos columnas intercaladas) ---


# --- F11: heurísticas del cajetín filtradas -------------------------------------------------

class _FakeWordEngine:
    """OCR limpio del cajetín: el fixture sintético sale truncado con EasyOCR."""

    def analyze_image(self, image_path, page_index=0):
        rows = [
            ["ARQUITECTO:", "LOLA", "ROMERA", "MARTINEZ"],
            ["SITUACION:", "CALLE", "MAYOR", "12"],
        ]
        tokens = []
        for r, words in enumerate(rows):
            x = 10
            for w in words:
                width = 12 * len(w)
                tokens.append(OcrToken(text=w, confidence=0.99,
                                       rect=fitz.Rect(x, 20 + r * 40, x + width, 50 + r * 40)))
                x += width + 14
        return OcrPage(page_index=page_index, width=900, height=200, tokens=tokens)


@pytest.fixture
def plan_page(monkeypatch):
    import app.services.ocr.factory as factory
    import app.services.vision.titleblock as titleblock

    monkeypatch.setattr(factory, "get_word_ocr_engine", lambda *_a, **_k: _FakeWordEngine())
    monkeypatch.setattr(titleblock, "detect_titleblock", lambda page: fitz.Rect(1900, 1100, 2200, 1200))
    doc = fitz.open()
    page = doc.new_page(width=2384, height=1684)
    yield page
    doc.close()


def test_titleblock_address_heuristic_is_filtered(plan_page):
    found, entities, _ = plan_pages.redact_plan_page(plan_page, analyzer)
    assert found
    assert any(e.entity_type == "ADDRESS" for e in entities), "sin filtro, la heurística aporta la dirección"

    found, entities, _ = plan_pages.redact_plan_page(plan_page, analyzer.with_disabled_types({"ADDRESS"}))
    assert not any(e.entity_type == "ADDRESS" for e in entities), "con ADDRESS desactivado no puede quedar"
