"""A2.2 — el número de colegiado no se anonimiza."""
import pytest

from app.services.layout.zone_config import ALL_PII_TYPES
from app.services.pdf.plan_pages import _COLEGIADO_RE
from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


@pytest.mark.parametrize("text", [
    "Lola Romera Martinez\nArquitecta\nColegiado n\n6.402",
    "Luis Ortenbach Alonso Arquitecto Colegiado n 14.226",
    "Col·legiat núm.: 3.221",
    "Magdalena Dudek Arquitecta Colegiado ns 14.360",
    "Colegiado nº: 8.905",
])
def test_colegiado_span_is_located(text):
    # Debe localizarse (etiqueta + número) para poder protegerlo del recorte.
    assert _COLEGIADO_RE.search(text) is not None, text


def test_professional_license_is_never_redacted():
    assert "PROFESSIONAL_LICENSE" not in ALL_PII_TYPES


def test_colegiado_number_is_not_money():
    # Tiene forma de importe con separador de miles.
    out = [e for e in analyzer.analyze("Arquitecto Colegiado nº 14.226",
                                       mode="strict", zone_type="body")
           if e.entity_type == "MONEY"]
    assert out == []


def test_overshooting_person_span_is_trimmed_before_the_number():
    # Simula el recorte que aplica redact_plan_page sobre un span desbordado.
    zone_text = "Luis Ortenbach Alonso Arquitecto Colegiado n 14.226"
    protected = [(m.start(), m.end()) for m in _COLEGIADO_RE.finditer(zone_text)]
    assert protected
    start, end = 0, len(zone_text)          # span que se lo lleva todo
    for p_start, p_end in protected:
        if start < p_end and p_start < end:
            if start < p_start:
                end = min(end, p_start)
            else:
                start = max(start, p_end)
    kept = zone_text[start:end]
    assert "14.226" not in kept, "el número de colegiado debe quedar fuera"
    assert "Luis Ortenbach Alonso" in kept, "el nombre sí se redacta"
