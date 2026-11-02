"""Robustez de Word y Excel: lo que se rechaza y lo que no puede colgar el servicio.

  - Un zip con dos partes del mismo nombre se rechaza: el anonimizador leeria
    una y Word o Excel podrian mostrar la otra.
  - Un Excel con demasiadas celdas o un Word con demasiados parrafos se rechaza
    al subirlo (office_too_large), en milisegundos, como un PDF con demasiadas
    paginas.
  - El tratamiento de un Word o un Excel tiene limite de tiempo, como el OCR:
    si se pasa, el trabajo falla con processing_timeout y un mensaje claro.
"""
import shutil
import time
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.errors import FileValidationError, ProcessingTimeoutError
from app.main import app
from app.services.office.paquete import Paquete
from app.services.office.xlsx import anonimizar_xlsx
from app.services.pii.analyzer import PiiAnalyzer
from evaluation.corpus_excel import listado

client = TestClient(app)
WORD = Path("data/samples/word_dificil/doc12_control_negativo.docx")
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _subir(datos: bytes, nombre: str):
    return client.post("/jobs", files={"file": (nombre, BytesIO(datos), "application/octet-stream")})


def _esperar(job_id: str) -> dict:
    fin = time.time() + 300
    while time.time() < fin:
        estado = client.get(f"/jobs/{job_id}").json()
        if estado["status"] in ("completed", "failed"):
            return estado
        time.sleep(0.3)
    raise AssertionError("no termino")


def test_un_zip_con_partes_repetidas_se_rechaza(tmp_path):
    entrada = tmp_path / "repetido.docx"
    shutil.copy(WORD, entrada)
    with zipfile.ZipFile(entrada, "a") as z, pytest.warns(UserWarning):
        z.writestr("word/document.xml", z.read("word/document.xml"))
    with pytest.raises(FileValidationError, match="repeated"):
        Paquete.abrir(entrada)
    r = _subir(entrada.read_bytes(), "repetido.docx")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_file"


def test_un_excel_con_demasiadas_celdas_se_rechaza_al_subirlo(tmp_path, monkeypatch):
    entrada = tmp_path / "listado.xlsx"
    listado(entrada)
    monkeypatch.setattr(settings, "max_office_cells", 20)
    r = _subir(entrada.read_bytes(), "listado.xlsx")
    assert r.status_code == 413 and r.json()["detail"]["code"] == "office_too_large"


def test_un_word_con_demasiados_parrafos_se_rechaza_al_subirlo(monkeypatch):
    monkeypatch.setattr(settings, "max_office_paragraphs", 3)
    r = _subir(WORD.read_bytes(), "doc12.docx")
    assert r.status_code == 413 and r.json()["detail"]["code"] == "office_too_large"


def test_dentro_del_limite_se_procesa(tmp_path):
    entrada = tmp_path / "listado.xlsx"
    listado(entrada)
    r = _subir(entrada.read_bytes(), "listado.xlsx")
    assert r.status_code == 202
    assert _esperar(r.json()["job_id"])["status"] == "completed"


def test_el_tratamiento_tiene_limite_de_tiempo(tmp_path, monkeypatch):
    entrada, salida = tmp_path / "listado.xlsx", tmp_path / "salida.xlsx"
    listado(entrada)
    monkeypatch.setattr(settings, "office_timeout_s", 0)
    with pytest.raises(ProcessingTimeoutError):
        anonimizar_xlsx(entrada, salida, PiiAnalyzer(), lambda e, s, f: s.write_bytes(e.read_bytes()), "t")
    r = _subir(entrada.read_bytes(), "listado.xlsx")
    estado = _esperar(r.json()["job_id"])
    assert estado["status"] == "failed" and estado["error"]["code"] == "processing_timeout"
