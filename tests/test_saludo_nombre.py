"""Un nombre de pila suelto tras el saludo de una carta es un nombre de persona.

En la carta en valenciano del corpus de Word (doc11) quedaba "Benvolguda Aina,":
el nombre completo se sustituia, pero el de pila suelto no.  Tras un saludo
(Benvolgut/Benvolguda, Estimado/Estimada, Querido/Querida, Distinguido/a,
Sr./Sra....), en castellano y en valenciano, la palabra que sigue es un nombre si
esta en el padron; un cargo o una palabra corriente ("señor", "Director",
"Comisión", "veïns") no.  Va en el analizador, asi que vale para todos los
formatos; aqui se prueba tambien por Word y por Excel.
"""
import shutil
import zipfile
from pathlib import Path

import openpyxl
import pytest

from app.services.office.docx import anonimizar_docx
from app.services.office.xlsx import anonimizar_xlsx
from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _personas(texto):
    return [texto[e.start:e.end] for e in analyzer.analyze(texto, mode="strict", zone_type="body")
            if e.entity_type == "PERSON"]


@pytest.mark.parametrize("texto, nombre", [
    ("Benvolguda Aina,\nUs comuniquem que la sol·licitud ha estat admesa.", "Aina"),
    ("Benvolgut Pau:\nAdjunt us remetem la resolució.", "Pau"),
    ("Estimada Laura:\nLe remitimos la documentación solicitada.", "Laura"),
    ("Querido Javier,\nTe escribo para confirmar la cita.", "Javier"),
    ("Distinguida Sra. Climent:\nEn relación con su escrito...", "Climent"),
    ("Sr. Peris,\nSe le comunica que su solicitud ha sido admitida.", "Peris"),
    ("Estimada Sra. Marta Ferrer:\nLe informamos de lo siguiente.", "Marta Ferrer"),
])
def test_el_nombre_tras_el_saludo_es_un_nombre(texto, nombre):
    assert any(nombre in p for p in _personas(texto)), _personas(texto)


@pytest.mark.parametrize("texto", [
    "Estimado señor:\nLe remitimos la documentación solicitada.",
    "Distinguida Comisión:\nSe somete a su consideración el expediente.",
    "Sr. Director:\nAdjunto el informe técnico.",
    "Benvolguts veïns i veïnes,\nUs informem de les obres al carrer.",
    "Querido equipo,\nGracias por vuestro trabajo.",
])
def test_lo_que_sigue_al_saludo_y_no_es_un_nombre_no(texto):
    assert _personas(texto) == []


def _sin_imagenes(entrada, salida, formato):
    salida.write_bytes(entrada.read_bytes())


def test_en_word(tmp_path):
    entrada, salida = tmp_path / "carta.docx", tmp_path / "salida.docx"
    shutil.copy(Path("data/samples/word_dificil/doc11_carta_valencia.docx"), entrada)
    anonimizar_docx(entrada, salida, analyzer, _sin_imagenes, "t")
    texto = zipfile.ZipFile(salida).read("word/document.xml").decode("utf-8")
    assert "Benvolguda Aina" not in texto and "Benvolguda [NOMBRE]" in texto


def test_en_excel(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Expediente", "Observaciones"])
    ws.append(["EXP-2026-401", "Estimada Laura: adjunto la documentación pedida."])
    entrada, salida = tmp_path / "e.xlsx", tmp_path / "s.xlsx"
    wb.save(entrada)
    anonimizar_xlsx(entrada, salida, analyzer, _sin_imagenes, "t")
    assert openpyxl.load_workbook(salida).active["B2"].value.startswith("Estimada [NOMBRE]")
