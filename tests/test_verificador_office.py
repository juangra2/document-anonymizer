"""La segunda opinion de Office: lee la salida entera, como el verificador de los PDF.

  - Ve lo que el anonimizador no detecto: con el barrido apagado, los nombres
    de una lista desplegable quedan en el fichero y el informe pide revision
    (residual_pii), sin escribir los valores.
  - Ve los datos de una ficha en vertical (X08), que la clasificacion por
    columnas no reconoce.
  - En un documento bien anonimizado o sin datos, no dice nada: la revision
    manual no puede saltar por ruido.
"""
import json
import shutil
from pathlib import Path

import pytest

import app.services.office.xlsx as xlsx
from app.services.office.docx import anonimizar_docx
from app.services.office.verificador import segunda_opinion
from app.services.office.xlsx import anonimizar_xlsx
from app.services.pii.analyzer import PiiAnalyzer
from evaluation.corpus_excel import estadistica, listado, valenciano_con_titulo
from evaluation.corpus_excel_dificil import casos as casos_excel

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


def test_ve_lo_que_no_se_detecto(excel, analyzer, monkeypatch):
    carpeta, casos = excel
    monkeypatch.setattr(xlsx, "barrer", lambda *a, **k: None)
    salida = carpeta / "x14_sin_barrido.xlsx"
    informe = anonimizar_xlsx(carpeta / casos["X14"]["fichero"], salida, analyzer, _sin_imagenes, "t")
    assert segunda_opinion(salida, analyzer, "strict")["PERSON"] >= 2
    assert informe.manual_review_required and "residual_pii" in informe.manual_review_reasons
    assert informe.residual_entities >= 2
    assert "Ribes" not in json.dumps(informe.model_dump(mode="json"), ensure_ascii=False), "sin valores"


def test_ve_una_ficha_en_vertical(excel, analyzer):
    carpeta, casos = excel
    salida = carpeta / "x08.xlsx"
    informe = anonimizar_xlsx(carpeta / casos["X08"]["fichero"], salida, analyzer, _sin_imagenes, "t")
    if not informe.manual_review_required:
        pytest.fail("la ficha en vertical sale sin revision manual")
    assert "residual_pii" in informe.manual_review_reasons


@pytest.mark.parametrize("hacer", [listado, estadistica, valenciano_con_titulo])
def test_un_excel_bien_anonimizado_no_pide_revision(tmp_path, analyzer, hacer):
    """Tampoco por "Ciutat Vella" (un distrito) ni por "CURS DE VALENCIÀ PER A ADULTS"."""
    entrada, salida = tmp_path / "libro.xlsx", tmp_path / "salida.xlsx"
    hacer(entrada)
    informe = anonimizar_xlsx(entrada, salida, analyzer, _sin_imagenes, "t")
    assert segunda_opinion(salida, analyzer, "strict") == {}
    assert not informe.manual_review_required


@pytest.mark.parametrize("nombre", ["doc04_control_cambios.docx", "doc08_tabla_etiqueta_valor.docx",
                                    "doc12_control_negativo.docx"])
def test_un_word_bien_anonimizado_no_pide_revision(tmp_path, analyzer, nombre):
    entrada, salida = tmp_path / nombre, tmp_path / f"salida_{nombre}"
    shutil.copy(WORD / nombre, entrada)
    informe = anonimizar_docx(entrada, salida, analyzer, _sin_imagenes, "t")
    assert segunda_opinion(salida, analyzer, "strict") == {}
    assert not informe.manual_review_required
