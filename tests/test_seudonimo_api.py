"""Seudonimizacion por la API: desactivada por defecto, contrasena, tabla y reconstruccion.

Lo que se fija:
  - viene DESACTIVADA: sin PSEUDONYMIZATION_ENABLED, seudonimizar y reconstruir
    responden 403 con su codigo, y /health lo dice;
  - activada: contrasena de 12 caracteres como minimo; solo Word, Markdown y
    texto (un PDF se rechaza con su codigo, sin crear el trabajo);
  - el trabajo entrega el documento con etiquetas y, aparte, la tabla cifrada;
    el zip lleva los dos, el informe dice que seudonimizar no es anonimizar, y
    ni el informe ni el estado llevan la contrasena ni ningun valor;
  - sin HTTPS desde otro equipo, el informe avisa de que la contrasena viajo
    sin cifrar;
  - /reconstruct devuelve el original con el documento tal cual salio, y
    resuelve un texto con las etiquetas alteradas; con otra contrasena, 400.
"""
import base64
import json
import time
import zipfile
from io import BytesIO

import fitz
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.seudonimo.tabla import abrir_tabla
from tests.test_texto import CARTA, DNI, NOMBRE

client = TestClient(app)
CLAVE = "clave-de-prueba-larga"
ORIGINAL = CARTA.replace("\n", "\r\n").encode("cp1252")


@pytest.fixture
def activada(monkeypatch):
    monkeypatch.setattr(settings, "pseudonymization_enabled", True)


def _esperar(job_id: str, segundos: float = 120) -> dict:
    fin = time.time() + segundos
    while time.time() < fin:
        estado = client.get(f"/jobs/{job_id}").json()
        if estado["status"] in ("completed", "failed"):
            return estado
        time.sleep(0.3)
    raise AssertionError("no termino")


def _seudonimizar(nombre: str = "carta.txt", datos: bytes = ORIGINAL, clave: str | None = CLAVE):
    return client.post("/jobs?treatment=pseudonymize", files={"file": (nombre, BytesIO(datos), "text/plain")},
                       data={"password": clave} if clave is not None else {})


@pytest.fixture
def desactivada(monkeypatch):
    monkeypatch.setattr(settings, "pseudonymization_enabled", False)


def test_viene_desactivada_por_defecto():
    """Lo que dice el codigo, no el .env de este equipo (en local se prueba activada)."""
    from app.core.config import Settings

    assert Settings.model_fields["pseudonymization_enabled"].default is False


def test_desactivada_no_se_puede_usar(desactivada):
    assert client.get("/health").json()["pseudonymization"]["enabled"] is False
    r = _seudonimizar()
    assert r.status_code == 403 and r.json()["detail"]["code"] == "pseudonymization_disabled"
    r = client.post("/reconstruct", data={"password": CLAVE, "text": "[PERSONA_1]"},
                    files={"table": ("t.tabla", BytesIO(b"{}"), "application/octet-stream")})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "pseudonymization_disabled"


def test_activada_health_lo_dice(activada):
    info = client.get("/health").json()["pseudonymization"]
    assert info == {"enabled": True, "min_password_length": 12, "formats": ["docx", "md", "txt"]}


@pytest.mark.parametrize("clave", [None, "", "corta"])
def test_sin_contrasena_de_12_caracteres_no_se_acepta(activada, clave):
    r = _seudonimizar(clave=clave)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "password_too_short" and r.json()["detail"]["min_length"] == 12


def test_un_pdf_no_se_puede_seudonimizar(activada):
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Hola")
    antes = client.get("/health").json()["queue"]["queued"]
    r = client.post("/jobs?treatment=pseudonymize", data={"password": CLAVE},
                    files={"file": ("doc.pdf", BytesIO(doc.tobytes()), "application/pdf")})
    assert r.status_code == 415 and r.json()["detail"]["code"] == "pseudonymize_unsupported"
    assert client.get("/health").json()["queue"]["queued"] == antes


@pytest.fixture
def trabajo(activada):
    r = _seudonimizar()
    assert r.status_code == 202, r.text
    estado = _esperar(r.json()["job_id"])
    assert estado["status"] == "completed", estado
    return r.json()["job_id"], estado


def test_entrega_el_documento_con_etiquetas_y_la_tabla_cifrada(trabajo):
    job_id, estado = trabajo
    assert estado["result"]["treatment"] == "pseudonymize"
    documento = client.get(f"/jobs/{job_id}/result")
    assert "documento_seudonimizado.txt" in documento.headers["content-disposition"]
    texto = documento.content.decode("cp1252")
    assert NOMBRE not in texto and DNI not in texto and "[PERSONA_1]" in texto and "[DNI_1]" in texto
    tabla = client.get(f"/jobs/{job_id}/table")
    assert tabla.status_code == 200 and "documento_seudonimizado.tabla" in tabla.headers["content-disposition"]
    assert NOMBRE.encode() not in tabla.content and b"Teresa" not in tabla.content
    abierta = abrir_tabla(tabla.content, CLAVE)
    assert abierta["etiquetas"]["PERSONA_1"]["principal"].endswith(NOMBRE)


def test_el_zip_lleva_documento_tabla_e_informe(trabajo):
    job_id, _ = trabajo
    with zipfile.ZipFile(BytesIO(client.get(f"/jobs/{job_id}/result?output=zip").content)) as z:
        assert sorted(z.namelist()) == ["documento_seudonimizado.tabla", "documento_seudonimizado.txt",
                                        "informe.pdf", "report.json"]


def test_el_informe_dice_que_no_es_anonimizar_y_no_lleva_ni_contrasena_ni_valores(trabajo):
    job_id, estado = trabajo
    informe = client.get(f"/jobs/{job_id}/report").json()
    assert informe["treatment"] == "pseudonymize"
    assert "pseudonymized_not_anonymous" in {a["code"] for a in informe["coded_warnings"]}
    assert "password_without_https" not in {a["code"] for a in informe["coded_warnings"]}
    todo = json.dumps(informe, ensure_ascii=False) + json.dumps(estado, ensure_ascii=False)
    assert CLAVE not in todo and NOMBRE not in todo and DNI not in todo
    pdf = client.get(f"/jobs/{job_id}/report.pdf").content
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        texto = " ".join(" ".join(p.get_text() for p in doc).split())
    assert "Informe de seudonimización" in texto and "siguen siendo datos personales" in texto


def test_sin_https_desde_otro_equipo_el_informe_avisa(activada, monkeypatch):
    from app.api import jobs

    monkeypatch.setattr(jobs, "_EQUIPO_LOCAL", frozenset())
    r = _seudonimizar()
    estado = _esperar(r.json()["job_id"])
    codigos = {a["code"] for a in estado["result"]["coded_warnings"]}
    assert "password_without_https" in codigos
    r = client.post("/jobs?treatment=pseudonymize", data={"password": CLAVE},
                    headers={"X-Forwarded-Proto": "https"},
                    files={"file": ("carta.txt", BytesIO(ORIGINAL), "text/plain")})
    estado = _esperar(r.json()["job_id"])
    assert "password_without_https" not in {a["code"] for a in estado["result"]["coded_warnings"]}


def test_un_trabajo_que_no_es_de_seudonimizar_no_tiene_tabla(activada):
    r = client.post("/jobs", files={"file": ("carta.txt", BytesIO(ORIGINAL), "text/plain")})
    _esperar(r.json()["job_id"])
    assert client.get(f"/jobs/{r.json()['job_id']}/table").status_code == 404


def test_reconstruir_el_documento_tal_cual_devuelve_el_original(trabajo):
    job_id, _ = trabajo
    seudo = client.get(f"/jobs/{job_id}/result").content
    tabla = client.get(f"/jobs/{job_id}/table").content
    r = client.post("/reconstruct", data={"password": CLAVE},
                    files={"file": ("carta.txt", BytesIO(seudo), "text/plain"),
                           "table": ("carta.tabla", BytesIO(tabla), "application/octet-stream")})
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["exact"] is True and base64.b64decode(cuerpo["file_base64"]) == ORIGINAL
    assert cuerpo["filename"] == "documento_reconstruido.txt" and not cuerpo["unresolved"]


def test_reconstruir_un_resumen_con_las_etiquetas_alteradas(trabajo):
    job_id, _ = trabajo
    tabla = client.get(f"/jobs/{job_id}/table").content
    resumen = "Resumen: [Persona 1] (DNI_1) pide la licencia. Ver también [PERSONA_7]."
    r = client.post("/reconstruct", data={"password": CLAVE, "text": resumen},
                    files={"table": ("carta.tabla", BytesIO(tabla), "application/octet-stream")})
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["exact"] is False
    assert NOMBRE in cuerpo["text"] and DNI in cuerpo["text"] and "[PERSONA_7]" in cuerpo["text"]
    assert cuerpo["unresolved"] == ["[PERSONA_7]"]
    assert {a["label"] for a in cuerpo["altered"]} == {"PERSONA_1", "DNI_1"}
    assert NOMBRE not in json.dumps({k: v for k, v in cuerpo.items() if k not in ("text", "file_base64")})


def test_con_otra_contrasena_la_tabla_no_se_abre(trabajo):
    job_id, _ = trabajo
    tabla = client.get(f"/jobs/{job_id}/table").content
    r = client.post("/reconstruct", data={"password": "otra-clave-bien-larga", "text": "[PERSONA_1]"},
                    files={"table": ("carta.tabla", BytesIO(tabla), "application/octet-stream")})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "table_unreadable"
    r = client.post("/reconstruct", data={"password": CLAVE},
                    files={"table": ("carta.tabla", BytesIO(tabla), "application/octet-stream")})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "nothing_to_reconstruct"
