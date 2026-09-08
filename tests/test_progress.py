"""Bloque D1 — avance por páginas.

La interfaz enseña «página N de M» mientras procesa, así que lo que se
comprueba aquí es que el aviso llega desde el bucle real de páginas y que un
bucle interno no adelanta el contador (el caso del camino híbrido, que recorre
las mismas páginas dos veces).
"""
import fitz

from app.services import progress
from app.services.pdf.redactor import redact_pdf_digital
from app.services.pii.analyzer import PiiAnalyzer


def _pdf(path, pages: int) -> None:
    doc = fitz.open()
    for index in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), f"Pagina {index + 1}", fontsize=11)
        page.insert_text((72, 120), "Nombre: Roberto Iglesias Calvo", fontsize=11)
    doc.save(path)
    doc.close()


# --- semántica del aviso -----------------------------------------------------

def test_without_a_listener_the_callback_is_inert():
    on_page = progress.claim(10)
    on_page(3)  # no debe fallar ni hacer nada


def test_the_first_claim_receives_the_pages():
    seen = []
    with progress.track(lambda done, total: seen.append((done, total))):
        on_page = progress.claim(4)
        for index in range(4):
            on_page(index)

    # Al reclamar se anuncia el total, que es lo que la interfaz necesita para
    # pintar «de M» antes de que termine la primera página.
    assert seen[0] == (0, 4)
    assert seen[1:] == [(0, 4), (1, 4), (2, 4), (3, 4)]


def test_an_inner_loop_does_not_move_the_counter():
    seen = []
    with progress.track(lambda done, total: seen.append((done, total))):
        outer = progress.claim(100)
        inner = progress.claim(3)
        inner(2)
        outer(7)

    assert (2, 3) not in seen, "el bucle interno no debe informar"
    assert seen[-1] == (7, 100)


def test_each_document_starts_unclaimed():
    first, second = [], []
    with progress.track(lambda d, t: first.append((d, t))):
        progress.claim(2)(1)
    with progress.track(lambda d, t: second.append((d, t))):
        progress.claim(5)(4)

    assert first[-1] == (1, 2)
    assert second[-1] == (4, 5), "un documento nuevo puede volver a reclamar"


def test_the_listener_is_restored_when_the_scope_ends():
    outer = []
    with progress.track(lambda d, t: outer.append((d, t))):
        with progress.track(lambda d, t: None):
            progress.claim(9)(1)
        progress.claim(2)(1)

    assert outer[-1] == (1, 2), "el aviso interior no debe robar el exterior"


# --- el aviso sale del bucle real de páginas ---------------------------------

def test_the_digital_path_reports_every_page(tmp_path):
    source = tmp_path / "in.pdf"
    _pdf(source, pages=3)

    seen = []
    with progress.track(lambda done, total: seen.append((done, total))):
        redact_pdf_digital(
            input_path=source,
            output_path=tmp_path / "out.pdf",
            job_id="progress",
            analyzer=PiiAnalyzer(),
        )

    assert seen[0] == (0, 3), "el total se conoce antes de procesar nada"
    assert (2, 3) in seen, "la ultima pagina debe anunciarse"
    assert all(total == 3 for _, total in seen)
