"""Lo que no es una celda ni un parrafo normal tambien es contenido del fichero.

Casos de los corpus de casos dificiles (evaluation/corpus_excel_dificil.py y
data/samples/word_dificil/): datos en listas desplegables, formato condicional,
autofiltro, titulo de un grafico, nombres definidos y textos dentro de
formulas (Excel); nombre de un marcador y nombre y etiqueta de un control de
contenido (Word).  Ninguno puede quedar en ninguna parte del fichero, y el resto
tiene que seguir igual: las formulas siguen siendo formulas y las fuentes
("Times New Roman" parece un nombre) no se tocan.
"""
import shutil
import zipfile
from pathlib import Path

import openpyxl
import pytest

from app.services.office.docx import anonimizar_docx
from app.services.office.xlsx import anonimizar_xlsx
from app.services.pii.analyzer import PiiAnalyzer
from evaluation.corpus_excel_dificil import casos as casos_excel
from evaluation.lectura_excel_dificil import compacto, donde_esta, partes_del_zip

WORD = Path("data/samples/word_dificil")


@pytest.fixture(scope="module")
def analyzer():
    return PiiAnalyzer()


@pytest.fixture(scope="module")
def excel(tmp_path_factory):
    carpeta = tmp_path_factory.mktemp("excel_dificil")
    return carpeta, {c["id"]: c for c in casos_excel(carpeta)}


def _sin_imagenes(entrada, salida, formato):
    salida.write_bytes(entrada.read_bytes())


def _excel(excel, analyzer, id_):
    carpeta, casos = excel
    caso = casos[id_]
    salida = carpeta / f"salida_{id_}.xlsx"
    anonimizar_xlsx(carpeta / caso["fichero"], salida, analyzer, _sin_imagenes, "t")
    return caso, salida


@pytest.mark.parametrize("id_", ["X03", "X14", "X15", "X19", "X21", "X22"])
def test_excel_ningun_dato_queda_fuera_de_las_celdas(excel, analyzer, id_):
    caso, salida = _excel(excel, analyzer, id_)
    assert donde_esta(partes_del_zip(salida), caso["datos"]) == {}
    openpyxl.load_workbook(salida)
    todo = compacto(" ".join(str(c.value) for ws in openpyxl.load_workbook(salida).worksheets
                             for f in ws.iter_rows() for c in f if c.value is not None))
    for texto in caso["legitimos"]:
        assert compacto(texto) in todo, texto


def test_excel_la_formula_sigue_siendo_formula(excel, analyzer):
    _, salida = _excel(excel, analyzer, "X03")
    aviso = openpyxl.load_workbook(salida)["Resumen"]["B3"].value
    assert aviso.startswith('="Llamar a ') and "[NOMBRE]" in aviso and "[TELÉFONO]" in aviso


def test_excel_las_reglas_y_los_filtros_siguen_ahi(excel, analyzer):
    _, salida = _excel(excel, analyzer, "X15")
    ws = openpyxl.load_workbook(salida).active
    reglas = [r.formula[0] for rango in ws.conditional_formatting for r in rango.rules]
    assert '$A2="[NOMBRE]"' in reglas and '$B2="[DNI]"' in reglas
    _, salida = _excel(excel, analyzer, "X22")
    nombres = openpyxl.load_workbook(salida).defined_names
    assert "Firmante" in nombres and "Telefono_contacto" in nombres


def _word(tmp_path, analyzer, nombre, cambiar=None):
    entrada = tmp_path / nombre
    shutil.copy(next(WORD.glob(nombre)), entrada)
    if cambiar:
        with zipfile.ZipFile(entrada) as z:
            partes = {n: z.read(n) for n in z.namelist()}
        partes["word/document.xml"] = cambiar(partes["word/document.xml"])
        with zipfile.ZipFile(entrada, "w") as z:
            for n, d in partes.items():
                z.writestr(n, d)
    salida = tmp_path / f"salida_{nombre}"
    anonimizar_docx(entrada, salida, analyzer, _sin_imagenes, "t")
    return entrada, salida


def test_word_el_nombre_de_un_marcador_no_queda(tmp_path, analyzer):
    _, salida = _word(tmp_path, analyzer, "doc09_marcador_control_contenido.docx")
    assert donde_esta(partes_del_zip(salida), [{"text": "Ferrandis"}, {"text": "Arnau Cerdà Fuster"}]) == {}
    texto = zipfile.ZipFile(salida).read("word/document.xml").decode("utf-8")
    assert "Esta frase visible no contiene información personal" in texto
    assert "w:bookmarkStart" in texto, "el marcador se renombra, no se quita"


def test_word_nombre_y_etiqueta_de_un_control_de_contenido(tmp_path, analyzer):
    def con_datos(xml: bytes) -> bytes:
        return (xml.replace('w:val="Nombre completo"'.encode(), 'w:val="Firmante: Vicenta Ribes Mollà"'.encode())
                .replace(b'w:val="NombreFicticio"', b'w:val="vicenta.ribes@correo.es"'))

    entrada, salida = _word(tmp_path, analyzer, "doc09_marcador_control_contenido.docx", con_datos)
    datos = [{"text": "Vicenta Ribes Mollà"}, {"text": "vicenta.ribes@correo.es"}]
    assert donde_esta(partes_del_zip(entrada), datos), "la entrada lleva los datos"
    assert donde_esta(partes_del_zip(salida), datos) == {}
    assert "Firmante: " in zipfile.ZipFile(salida).read("word/document.xml").decode("utf-8")


def test_word_las_fuentes_no_se_tocan(tmp_path, analyzer):
    entrada, salida = _word(tmp_path, analyzer, "doc12_control_negativo.docx")
    for parte in ("word/styles.xml", "word/fontTable.xml", "word/theme/theme1.xml"):
        antes = zipfile.ZipFile(entrada).read(parte).decode("utf-8")
        despues = zipfile.ZipFile(salida).read(parte).decode("utf-8")
        assert antes.count("Times New Roman") == despues.count("Times New Roman"), parte
        assert "[NOMBRE]" not in despues, parte
