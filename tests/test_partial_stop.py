"""PARAR: entregar lo procesado, sin colar ni una página sin tratar.

El riesgo de esta función es una sola página que se cuele sin procesar: saldría
con todos sus datos a la vista.  Por eso las páginas no procesadas se OMITEN en
vez de pintarse de negro: pintar negro sobre una página digital no borra su
texto —sigue siendo seleccionable debajo— y omitir convierte la garantía en
algo estructural, que no puede fallar por descuido.

Se prueba en los tres caminos, porque «entregar lo que haya» es técnicamente
distinto en cada uno: el digital redacta sobre el documento original y hay que
truncarlo; el escaneado y el híbrido construyen un PDF nuevo página a página.
"""
from pathlib import Path

import fitz
import pytest

from app.services import cancellation, progress
from app.services.pdf.partial import append_partial_notice, partial_notice_text
from app.services.pdf.pipeline import PdfAnonymizationPipeline
from app.services.pii.analyzer import PiiAnalyzer

SECRETO = "Marcos Villalba Tena"
DNI = "12345678Z"


@pytest.fixture(scope="module")
def analyzer():
    return PiiAnalyzer()


@pytest.fixture(scope="module")
def pipeline():
    return PdfAnonymizationPipeline()


def _pdf_digital(path: Path, pages: int) -> Path:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), f"Expediente, pagina {i + 1}", fontsize=11)
        page.insert_text((72, 120), f"Interesado: {SECRETO}", fontsize=11)
        page.insert_text((72, 140), f"DNI: {DNI}", fontsize=11)
    doc.save(path)
    doc.close()
    return path


def _texto(path: Path) -> str:
    with fitz.open(path) as doc:
        return "\n".join(page.get_text() for page in doc)


def _paginas(path: Path) -> int:
    with fitz.open(path) as doc:
        return doc.page_count


class _ParaEn:
    """Pide PARAR justo cuando empieza la página *n* (base 0).

    Se engancha al aviso de avance, que llega al EMPEZAR cada página, en vez de
    contar consultas del testigo: dentro de una página escaneada el testigo se
    consulta varias veces (entre los pasos pesados) y contarlas pararía antes
    de tiempo.
    """

    def __init__(self, n: int) -> None:
        self.n = n
        self.parar = False

    def on_progress(self, done: int, total: int) -> None:
        if done >= self.n:
            self.parar = True

    def __call__(self) -> str | None:
        return "partial" if self.parar else None


def _parando_en(n: int):
    """Contexto que procesa y pide parar al llegar a la página *n*."""
    from contextlib import ExitStack

    control = _ParaEn(n)
    pila = ExitStack()
    pila.enter_context(progress.track(control.on_progress))
    pila.enter_context(cancellation.track(control))
    return pila


# --- la hoja de aviso --------------------------------------------------------

def test_the_notice_page_says_what_is_missing_and_nothing_else(tmp_path):
    doc = fitz.open()
    doc.new_page()
    append_partial_notice(doc, pages_done=34, pages_total=118)
    salida = tmp_path / "con_aviso.pdf"
    doc.save(salida)
    doc.close()

    texto = " ".join(_texto(salida).split())
    assert "páginas 1 a 34 de 118" in texto
    assert "no se han anonimizado" in texto


def test_the_notice_never_carries_the_file_name():
    texto = partial_notice_text(3, 10)
    for prohibido in ("expediente", ".pdf", SECRETO):
        assert prohibido.lower() not in texto.lower()


# --- camino digital ----------------------------------------------------------

def test_a_stopped_digital_document_only_keeps_processed_pages(tmp_path, pipeline):
    origen = _pdf_digital(tmp_path / "in.pdf", pages=6)
    salida = tmp_path / "out.pdf"

    with _parando_en(3):
        informe = pipeline.process(origen, salida, "t", "strict", False, frozenset())

    assert informe.partial is True
    assert informe.pages == 3
    assert informe.pages_total_original == 6
    # 3 paginas procesadas + la hoja de aviso
    assert _paginas(salida) == 4

    texto = _texto(salida)
    assert SECRETO not in texto and DNI not in texto, (
        "una pagina sin procesar se ha colado con sus datos"
    )
    assert "pagina 4" not in texto.lower(), "la primera sin procesar no puede estar"
    assert "páginas 1 a 3 de 6" in " ".join(texto.split())


def test_the_kept_pages_are_identical_to_the_complete_run(tmp_path, pipeline):
    """Lo entregado tiene que ser lo mismo que en una corrida entera."""
    origen = _pdf_digital(tmp_path / "in.pdf", pages=5)

    entero = tmp_path / "entero.pdf"
    pipeline.process(origen, entero, "t", "strict", False, frozenset())

    parcial = tmp_path / "parcial.pdf"
    with _parando_en(2):
        pipeline.process(origen, parcial, "t", "strict", False, frozenset())

    with fitz.open(entero) as a, fitz.open(parcial) as b:
        for i in range(2):
            assert a[i].get_text() == b[i].get_text(), f"la pagina {i + 1} difiere"


def test_a_partial_is_verified_like_a_complete_document(tmp_path, pipeline):
    origen = _pdf_digital(tmp_path / "in.pdf", pages=4)
    salida = tmp_path / "out.pdf"
    with _parando_en(2):
        informe = pipeline.process(origen, salida, "t", "strict", False, frozenset())

    assert informe.verification is not None, (
        "el parcial pasa por la misma verificacion final que un documento entero"
    )
    assert informe.verification.layers_run


# --- camino escaneado --------------------------------------------------------

class _FakeEngine:
    """OCR simulado: rápido y determinista, como en test_unreadable_pages."""

    engine_name = "fake"
    provides_layout = False
    supports_pdf_input = False

    def analyze_image(self, image_path, page_index=0):
        from PIL import Image

        from app.services.ocr.models import OcrPage, OcrToken

        with Image.open(image_path) as img:
            width, height = img.size
        palabras = ["Expediente", "Interesado", "Marcos", "Villalba", "Tena"]
        tokens = [
            OcrToken(text=w, confidence=0.9,
                     rect=fitz.Rect(40, 40 + i * 30, 400, 60 + i * 30))
            for i, w in enumerate(palabras)
        ]
        return OcrPage(page_index=page_index, width=width, height=height, tokens=tokens)


def _pdf_escaneado(path: Path, pages: int) -> Path:
    """PDF sin capa de texto: obliga al camino de OCR."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (600, 850), "white").save(buf, format="PNG")
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(path)
    doc.close()
    return path


def test_a_stopped_scanned_document_only_keeps_processed_pages(
    tmp_path, monkeypatch, analyzer,
):
    import app.services.pdf.scanned_redactor as sr

    motor = _FakeEngine()
    monkeypatch.setattr(sr, "get_ocr_engine", lambda: motor)
    monkeypatch.setattr(sr, "get_word_ocr_engine", lambda *_a, **_k: motor)

    origen = _pdf_escaneado(tmp_path / "scan.pdf", pages=4)
    salida = tmp_path / "out.pdf"

    with _parando_en(2):
        informe = sr.redact_pdf_scanned(origen, salida, "t", analyzer)

    assert informe.partial is True
    assert informe.pages == 2
    assert informe.pages_total_original == 4
    assert _paginas(salida) == 3, "dos paginas procesadas mas la hoja de aviso"
    assert "páginas 1 a 2 de 4" in " ".join(_texto(salida).split())


# --- camino híbrido ----------------------------------------------------------

def _pdf_hibrido(path: Path, pages: int) -> Path:
    """Páginas con poco texto y una imagen: es lo que enruta al camino híbrido."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buf, format="PNG")
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 80), f"Pagina {i + 1}", fontsize=11)
        page.insert_image(fitz.Rect(72, 200, 472, 500), stream=buf.getvalue())
    doc.save(path)
    doc.close()
    return path


def test_a_stopped_hybrid_document_only_keeps_processed_pages(
    tmp_path, monkeypatch, analyzer,
):
    import app.services.pdf.hybrid_redactor as hr
    import app.services.pdf.scanned_redactor as sr

    # El hibrido ya no tiene motor de OCR propio: usa el MISMO bucle que el
    # camino escaneado, asi que el motor se sustituye alli.
    motor = _FakeEngine()
    monkeypatch.setattr(sr, "get_ocr_engine", lambda: motor)
    monkeypatch.setattr(sr, "get_word_ocr_engine", lambda *_a, **_k: motor)

    origen = _pdf_hibrido(tmp_path / "mixto.pdf", pages=5)
    salida = tmp_path / "out.pdf"

    with _parando_en(3):
        informe = hr.redact_pdf_hybrid_secure(origen, salida, "t", analyzer)

    assert informe.partial is True
    assert informe.pages == 3
    assert informe.pages_total_original == 5
    assert _paginas(salida) == 4, "tres paginas procesadas mas la hoja de aviso"
    assert "páginas 1 a 3 de 5" in " ".join(_texto(salida).split())


def test_the_hybrid_digital_pass_is_not_cut_in_half(tmp_path, monkeypatch, analyzer):
    """PARAR durante el pase digital previo espera a que ese pase termine.

    Cortarlo dejaría el documento intermedio a medias y el bucle siguiente
    leería páginas que no existen.  Es el pase rápido: se le deja acabar.
    """
    import app.services.pdf.hybrid_redactor as hr
    import app.services.pdf.scanned_redactor as sr

    # El hibrido ya no tiene motor de OCR propio: usa el MISMO bucle que el
    # camino escaneado, asi que el motor se sustituye alli.
    motor = _FakeEngine()
    monkeypatch.setattr(sr, "get_ocr_engine", lambda: motor)
    monkeypatch.setattr(sr, "get_word_ocr_engine", lambda *_a, **_k: motor)

    origen = _pdf_hibrido(tmp_path / "mixto.pdf", pages=4)
    salida = tmp_path / "out.pdf"

    with _parando_en(0):  # parar desde la primerisima pagina
        informe = hr.redact_pdf_hybrid_secure(origen, salida, "t", analyzer)

    # El pase digital termino entero; el bucle de OCR no llego a hacer ninguna.
    assert informe.partial is True
    assert informe.pages == 0
    assert _paginas(salida) == 1, "solo la hoja de aviso"
