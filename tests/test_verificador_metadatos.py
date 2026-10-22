"""El verificador comprueba en la salida todo lo que la limpieza quita.

Sobre la entrada cargada avisa de cada cosa; sobre la salida limpia, de nada.  Y
no avisa por "format", que es la version del PDF y no un metadato guardado.
"""
import fitz
import pytest

from app.services.pii.analyzer import PiiAnalyzer
from app.services.verification.layers import check_metadata
from tests.casos_metadatos import digital_cargado, incremental


@pytest.fixture(scope="module")
def analyzer():
    return PiiAnalyzer()


def _avisos(ruta, analyzer):
    return " | ".join(check_metadata(ruta, analyzer).warnings)


def test_avisa_de_todo_lo_que_queda(tmp_path, analyzer):
    avisos = _avisos(digital_cargado(tmp_path / "d.pdf"), analyzer)
    for esperado in ("Metadata field 'author'", "XMP metadata blob", "Annotation 'Text' (with author)",
                     "Form field", "bookmark", "embedded file", "JavaScript",
                     "XMP metadata in", "EXIF/XMP/IPTC"):
        assert esperado in avisos, esperado
    assert "ZZ" not in avisos, "ningun valor se copia a los avisos"


def test_avisa_de_versiones_anteriores(tmp_path, analyzer):
    assert "revisions in the file" in _avisos(incremental(tmp_path / "i.pdf"), analyzer)


def test_format_no_es_un_aviso(tmp_path, analyzer):
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Resolucion", fontsize=11)
    ruta = tmp_path / "limpio.pdf"
    doc.save(ruta, garbage=4)
    assert check_metadata(ruta, analyzer).warnings == []


def test_la_nota_con_un_dato_cuenta_como_residuo(tmp_path, analyzer):
    tipos = {e.entity_type for e in check_metadata(digital_cargado(tmp_path / "d.pdf"), analyzer).entities_found}
    assert "ES_DNI" in tipos


def test_avisa_de_copias_de_texto(tmp_path, analyzer):
    from tests.test_metadatos_salida import _pdf_etiquetado

    assert "Text copies" in _avisos(_pdf_etiquetado(tmp_path / "e.pdf"), analyzer)
