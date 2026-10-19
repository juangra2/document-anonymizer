"""La comprobacion independiente (ExifTool + qpdf) da limpia la salida de los tres
caminos y ve la carga en la entrada.  Se salta si las herramientas no estan:
no son dependencias del servicio (variables EXIFTOOL y QPDF, o en el PATH)."""
import pytest

from evaluation.comprobar_metadatos import comprobar, herramientas
from tests.casos_metadatos import CASOS
from tests.test_metadatos_salida import analyzer, caminos  # noqa: F401  (fixtures)

pytestmark = pytest.mark.skipif(herramientas() is None, reason="sin ExifTool o qpdf")


def test_la_entrada_cargada_tiene_de_todo(tmp_path):
    queda = " | ".join(comprobar(CASOS["digital_cargado"](tmp_path / "d.pdf"), *herramientas()))
    for esperado in ("diccionario de informacion", "XMP", "accion de apertura", "marcadores",
                     "anotaciones", "JavaScript", "adjunto", "GPS", "EXIF"):
        assert esperado in queda, esperado


@pytest.mark.parametrize("camino", ["digital", "escaneado", "hibrido"])
@pytest.mark.parametrize("caso", list(CASOS))
def test_la_salida_queda_limpia(caso, camino, caminos, tmp_path):  # noqa: F811
    entrada = CASOS[caso](tmp_path / f"{caso}.pdf")
    salida = tmp_path / f"{caso}_{camino}.pdf"
    caminos[camino](entrada, salida)
    assert comprobar(salida, *herramientas()) == []
