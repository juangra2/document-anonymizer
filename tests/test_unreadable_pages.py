"""Red de seguridad: una página escaneada que el OCR no puede leer se tacha entera.

Medido en el corpus de extensión: un presupuesto escaneado girado cuyo giro se
eligió mal salía ilegible para el OCR y se entregaba intacto, con un aviso, y
en la salida se leían el nombre del técnico, la empresa y la fecha.  Lo que no
se ha podido leer tampoco se ha podido tachar.  Los motores de OCR se simulan
para que los tests sean rápidos y deterministas.
"""
import io
from concurrent.futures import TimeoutError as FuturesTimeout

import fitz
import pytest
from PIL import Image

import app.services.pdf.scanned_redactor as sr
from app.core.config import settings
from app.services.ocr.models import OcrPage, OcrToken
from app.services.pii.analyzer import PiiAnalyzer

_NOISE = ["8", "1", "g1", "%", ":", "~", "5", "H", "1", "2", "p", "4"]
_LEGIBLE = [
    "PRESUPUESTO DE EJECUCION MATERIAL", "CAPITULO PRIMERO MOVIMIENTO",
    "Excavacion en zanja en terreno", "Pintura plastica lisa interiores",
    "Total capitulo primero", "Resumen de mediciones", "Gastos generales",
    "Beneficio industrial", "Presupuesto de contrata",
]


class _FakeEngine:
    engine_name = "fake"
    provides_layout = False
    supports_pdf_input = False

    def __init__(self, words):
        self.words = words

    def analyze_image(self, image_path, page_index=0):
        with Image.open(image_path) as img:
            width, height = img.size
        tokens = [
            OcrToken(text=w, confidence=0.9, rect=fitz.Rect(40, 40 + i * 30, 400, 60 + i * 30))
            for i, w in enumerate(self.words)
        ]
        return OcrPage(page_index=page_index, width=width, height=height, tokens=tokens)


@pytest.fixture(scope="module")
def analyzer():
    return PiiAnalyzer()


@pytest.fixture
def scanned_pdf(tmp_path):
    buf = io.BytesIO()
    Image.new("RGB", (600, 850), "white").save(buf, format="PNG")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=buf.getvalue())
    path = tmp_path / "scan.pdf"
    doc.save(path)
    doc.close()
    return path


def _use_engine(monkeypatch, words):
    engine = _FakeEngine(words)
    monkeypatch.setattr(sr, "get_ocr_engine", lambda: engine)
    monkeypatch.setattr(sr, "get_word_ocr_engine", lambda *_a, **_k: engine)


def _ink(path) -> float:
    with fitz.open(path) as doc:
        pix = doc[0].get_pixmap(dpi=20, colorspace=fitz.csGRAY)
    return sum(1 for v in pix.samples if v < 60) / len(pix.samples)


def _run(scanned_pdf, tmp_path, analyzer):
    out = tmp_path / "out.pdf"
    report = sr.redact_pdf_scanned(scanned_pdf, out, "t", analyzer)
    return out, report


def test_illegible_page_is_redacted_in_full(monkeypatch, scanned_pdf, tmp_path, analyzer):
    _use_engine(monkeypatch, _NOISE)
    out, report = _run(scanned_pdf, tmp_path, analyzer)
    assert _ink(out) > 0.99
    assert any("could not read" in w and "ocr_unreadable: [1]" in w for w in report.warnings)
    # El informe lo dice tambien en un campo estructurado, que es lo que lee la
    # interfaz para decir QUE paginas revisar.
    assert [(p.page, p.reason) for p in report.pages_redacted_in_full] == [
        (1, "ocr_unreadable")
    ]


def test_timeout_page_is_redacted_in_full(monkeypatch, scanned_pdf, tmp_path, analyzer):
    _use_engine(monkeypatch, _LEGIBLE)

    def _timeout(fn, timeout_s):
        raise FuturesTimeout()

    monkeypatch.setattr(sr, "_run_with_timeout", _timeout)
    out, report = _run(scanned_pdf, tmp_path, analyzer)
    assert _ink(out) > 0.99
    assert any("ocr_timeout: [1]" in w for w in report.warnings)
    assert [(p.page, p.reason) for p in report.pages_redacted_in_full] == [
        (1, "ocr_timeout")
    ]
    assert not any("not fully redacted" in w for w in report.warnings)


def test_setting_off_restores_previous_behaviour(monkeypatch, scanned_pdf, tmp_path, analyzer):
    _use_engine(monkeypatch, _NOISE)
    monkeypatch.setattr(settings, "redact_unreadable_pages", False)
    out, report = _run(scanned_pdf, tmp_path, analyzer)
    assert _ink(out) < 0.01
    assert any("Low-quality OCR text" in w for w in report.warnings)
    assert not any("could not read" in w for w in report.warnings)


def test_legible_page_is_not_redacted_in_full(monkeypatch, scanned_pdf, tmp_path, analyzer):
    _use_engine(monkeypatch, _LEGIBLE)
    out, report = _run(scanned_pdf, tmp_path, analyzer)
    assert _ink(out) < 0.5
    assert not any("could not read" in w for w in report.warnings)
