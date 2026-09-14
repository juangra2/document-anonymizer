"""Contrato del parámetro disabled_types (F12, F13).

El frontend serializa la lista de tipos desmarcados: sin ninguno envía cadena
vacía.  Eso tiene que equivaler a no enviar nada.  Mayúsculas y espacios se
normalizan; lo inexistente o lo obligatorio sigue dando 422 sin procesar.
"""
import json
import zipfile
from io import BytesIO
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient

from app.api.query_normalization import normalize_query_string
from app.core.config import settings
from app.main import app

client = TestClient(app)


def _pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "DNI: 12345678Z", fontsize=11)
    page.insert_text((72, 130), "Fecha de la solicitud: 06/11/2019", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def _anonymize(query: str):
    return client.post(
        f"/anonymize?output=zip{query}",
        files={"file": ("in.pdf", BytesIO(_pdf()), "application/pdf")},
    )


def _disabled(response) -> list[str]:
    with zipfile.ZipFile(BytesIO(response.content)) as zf:
        return json.loads(zf.read("report.json"))["entity_types_disabled"]


def _job_dirs() -> set:
    root = Path(settings.temp_root) / "jobs"
    return set(root.iterdir()) if root.exists() else set()


# --- lista vacía ---------------------------------------------------------------

@pytest.mark.parametrize("query", ["&disabled_types=", "&disabled_types=%20", "&disabled_types=&disabled_types="])
def test_empty_list_is_the_same_as_omitting_the_parameter(query):
    response = _anonymize(query)
    assert response.status_code == 200
    assert _disabled(response) == []


def test_empty_list_is_accepted_by_jobs_too():
    response = client.post(
        "/jobs?disabled_types=",
        files={"file": ("in.pdf", BytesIO(_pdf()), "application/pdf")},
    )
    assert response.status_code == 202


# --- normalización -------------------------------------------------------------

@pytest.mark.parametrize("value", ["date_time", "Date_Time", "%20DATE_TIME", "DATE_TIME%20", "%20date_time%20"])
def test_case_and_surrounding_spaces_are_normalized(value):
    response = _anonymize(f"&disabled_types={value}")
    assert response.status_code == 200
    assert _disabled(response) == ["DATE_TIME"]


def test_an_empty_value_next_to_a_valid_one_is_ignored():
    response = _anonymize("&disabled_types=&disabled_types=url")
    assert response.status_code == 200
    assert _disabled(response) == ["URL"]


# --- lo que sigue rechazándose ---------------------------------------------------

@pytest.mark.parametrize("query", [
    "&disabled_types=no_existe",
    "&disabled_types=es_dni",
    "&disabled_types=%20Person%20",
    "&disabled_types=date_time&disabled_types=no_existe",
    "&disabled_types=url&disabled_types=es_dni",
])
def test_unknown_or_mandatory_types_are_still_rejected_atomically(query):
    before = _job_dirs()
    response = _anonymize(query)
    assert response.status_code == 422
    assert "X-Job-Id" not in response.headers
    assert _job_dirs() == before


def test_the_normalization_does_not_touch_other_parameters():
    raw = b"output=zip&include_entities=true&disabled_types=%20date_time"
    out = normalize_query_string(raw).decode()
    assert "output=zip" in out and "include_entities=true" in out
    assert "disabled_types=DATE_TIME" in out


# --- documentado en OpenAPI ------------------------------------------------------

@pytest.mark.parametrize("path, method", [("/anonymize", "post"), ("/jobs", "post")])
def test_openapi_documents_what_is_normalized_and_what_is_rejected(path, method):
    spec = client.get("/openapi.json").json()
    param = next(p for p in spec["paths"][path][method]["parameters"] if p["name"] == "disabled_types")
    description = param["description"].lower()
    assert "mayúsculas" in description
    assert "vacía" in description
    assert "422" in description
