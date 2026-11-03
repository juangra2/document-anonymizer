"""Catálogo de tipos y desmarcado por petición."""
import fitz
import json
import zipfile
from io import BytesIO

import pytest

from fastapi.testclient import TestClient

from app.main import app
from app.services.layout.zone_config import ALL_PII_TYPES
from app.services.pii.analyzer import PiiAnalyzer

client = TestClient(app)


def _sample_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Nombre: Roberto Iglesias Calvo", fontsize=11)
    page.insert_text((72, 120), "DNI: 12345678Z", fontsize=11)
    page.insert_text((72, 140), "Fecha: 06/11/2019", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


# --- GET /entity-types ------------------------------------------------------

def test_entity_types_lists_every_redactable_type():
    response = client.get("/entity-types")
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()}
    missing = set(ALL_PII_TYPES) - ids
    assert not missing, f"tipos redactables sin exponer: {sorted(missing)}"


def test_entity_types_have_readable_spanish_metadata():
    for item in client.get("/entity-types").json():
        assert item["label"] and item["label"] != item["id"], item["id"]
        assert len(item["description"]) > 15, item["id"]
        assert isinstance(item["default_enabled"], bool)


def test_known_types_are_the_valid_values_for_disabling():
    from app.services.pii.entity_catalog import known_type_ids
    ids = {item["id"] for item in client.get("/entity-types").json()}
    assert ids == known_type_ids()


# --- desactivar tipos en la petición -----------------------------------------

def test_unknown_type_is_rejected():
    # El tipado con Enum rechaza el valor y lista los admitidos.
    response = client.post(
        "/anonymize?disabled_types=NO_EXISTE",
        files={"file": ("in.pdf", BytesIO(_sample_pdf()), "application/pdf")},
    )
    assert response.status_code == 422


def test_several_types_are_disabled_with_repeated_parameters():
    response = client.post(
        "/anonymize?output=zip&disabled_types=DATE_TIME&disabled_types=URL",
        files={"file": ("in.pdf", BytesIO(_sample_pdf()), "application/pdf")},
    )
    assert response.status_code == 200
    with zipfile.ZipFile(BytesIO(response.content)) as zf:
        report = json.loads(zf.read("report.json"))
    assert set(report["entity_types_disabled"]) == {"DATE_TIME", "URL"}


def test_swagger_offers_the_valid_values():
    # El parámetro se renderiza como selector, no como texto libre.
    spec = client.get("/openapi.json").json()
    params = spec["paths"]["/anonymize"]["post"]["parameters"]
    schema = next(p for p in params if p["name"] == "disabled_types")["schema"]
    assert "EntityType" in json.dumps(schema)
    enum_values = spec["components"]["schemas"]["EntityType"]["enum"]
    assert set(enum_values) == set(ALL_PII_TYPES)


def test_disabling_a_type_leaves_it_in_the_document():
    pdf = _sample_pdf()
    response = client.post(
        "/anonymize?output=zip&disabled_types=DATE_TIME",
        files={"file": ("in.pdf", BytesIO(pdf), "application/pdf")},
    )
    assert response.status_code == 200
    with zipfile.ZipFile(BytesIO(response.content)) as zf:
        redacted = zf.read("documento_anonimizado.pdf")
        report = json.loads(zf.read("report.json"))

    with fitz.open(stream=redacted, filetype="pdf") as doc:
        text = "\n".join(page.get_text() for page in doc)

    assert "06/11/2019" in text, "la fecha desactivada debe conservarse"
    assert "12345678Z" not in text, "el DNI no se desactivó y debe redactarse"
    assert "DATE_TIME" in report["entity_types_disabled"]
    assert "DATE_TIME" not in report["entity_types_applied"]


def test_without_configuration_the_catalog_applies():
    # Sin configuracion, lo mismo que la interfaz al empezar: todo salvo lo que
    # el catalogo trae apagado (hoy, nada).
    response = client.post(
        "/anonymize?output=zip",
        files={"file": ("in.pdf", BytesIO(_sample_pdf()), "application/pdf")},
    )
    with zipfile.ZipFile(BytesIO(response.content)) as zf:
        report = json.loads(zf.read("report.json"))
    assert report["entity_types_disabled"] == []
    assert set(report["entity_types_applied"]) == set(ALL_PII_TYPES)


# --- resumen de lo aplicado --------------------------------------------------

def test_report_summarises_applied_types_and_counts():
    response = client.post(
        "/anonymize?output=zip&disabled_types=DATE_TIME",
        files={"file": ("in.pdf", BytesIO(_sample_pdf()), "application/pdf")},
    )
    with zipfile.ZipFile(BytesIO(response.content)) as zf:
        report = json.loads(zf.read("report.json"))
    # El usuario ve qué se buscó, qué quedó fuera y cuánto se redactó de cada uno.
    assert report["entity_types_applied"]
    assert report["entities_by_type"]
    assert "DATE_TIME" not in report["entities_by_type"]


# --- el filtro alcanza a todos los caminos del analizador ---------------------

def test_disabled_types_filter_is_applied_inside_the_analyzer():
    analyzer = PiiAnalyzer()
    text = "Fecha: 06/11/2019 y DNI 12345678Z de Roberto Iglesias Calvo"
    restricted = analyzer.with_disabled_types({"DATE_TIME", "PERSON"})
    types = {e.entity_type for e in restricted.analyze(text, mode="strict", zone_type="body")}
    assert "DATE_TIME" not in types and "PERSON" not in types
    assert "ES_DNI" in types


def test_clone_shares_the_loaded_ner_engine():
    # Evita recargar el modelo por petición.
    analyzer = PiiAnalyzer()
    analyzer._ensure_engine()
    clone = analyzer.with_disabled_types({"URL"})
    assert clone._engine is analyzer._engine
    assert analyzer.disabled_types == frozenset()


# --- todos los tipos son desmarcables -------------------------------------------

# Decision del ayuntamiento: no hay tipos que la API imponga.  El catalogo no
# marca ninguno como obligatorio y ninguna combinacion se rechaza por serlo; lo
# que quede sin anonimizar queda escrito en entity_types_disabled del informe.

def test_the_catalog_does_not_impose_any_type():
    items = client.get("/entity-types").json()
    assert items
    assert all("mandatory" not in item for item in items), (
        "el catalogo ya no marca tipos obligatorios"
    )
    apagados = {item["id"] for item in items if not item["default_enabled"]}
    assert apagados == set(), (
        "todo activado por defecto, tambien el numero de expediente (decision del "
        "ayuntamiento, 22-10) y los importes de presupuesto de obra (03-11)"
    )


@pytest.mark.parametrize("tipo", ["ES_DNI", "PERSON", "IBAN_CODE", "DATE_OF_BIRTH"])
def test_types_that_used_to_be_mandatory_can_now_be_unmarked(tipo):
    response = client.post(
        f"/anonymize?disabled_types={tipo}",
        files={"file": ("in.pdf", BytesIO(_sample_pdf()), "application/pdf")},
    )
    assert response.status_code == 200


def test_unmarking_is_recorded_in_the_report():
    """El rastro de quien decidio no ocultar que."""
    response = client.post(
        "/anonymize?output=zip&disabled_types=ES_DNI&disabled_types=PERSON",
        files={"file": ("in.pdf", BytesIO(_sample_pdf()), "application/pdf")},
    )
    assert response.status_code == 200
    with zipfile.ZipFile(BytesIO(response.content)) as zf:
        report = json.loads(zf.read("report.json"))
    assert report["entity_types_disabled"] == ["ES_DNI", "PERSON"]
    assert "ES_DNI" not in report["entity_types_applied"]


def test_optional_types_can_still_be_disabled():
    response = client.post(
        "/anonymize?output=zip&disabled_types=URL&disabled_types=DATE_TIME",
        files={"file": ("in.pdf", BytesIO(_sample_pdf()), "application/pdf")},
    )
    assert response.status_code == 200
