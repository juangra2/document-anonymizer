"""Bloque D1 — estado del servicio.

El indicador de la interfaz y quien opere el servidor del ayuntamiento leen lo
mismo de aquí, así que la comprobación tiene que decir la verdad sobre lo que
hay instalado y no limitarse a responder «ok».
"""
from fastapi.testclient import TestClient

from app.api import health as health_module
from app.core.config import settings
from app.main import app

client = TestClient(app)


def test_health_answers_while_the_process_is_alive():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("ok", "degraded")
    assert body["app"] == settings.app_name


def test_health_reports_the_gpu():
    gpu = client.get("/health").json()["gpu"]
    assert set(gpu) >= {"requested", "available", "devices", "torch", "cuda"}
    assert isinstance(gpu["available"], bool)


def test_health_reports_every_model_the_pipeline_needs():
    models = client.get("/health").json()["models"]
    assert set(models) == {"mineru", "easyocr", "spacy"}
    for name, info in models.items():
        assert isinstance(info["present"], bool), name


def test_health_reports_the_queue_and_the_limits():
    body = client.get("/health").json()
    assert body["queue"]["max_concurrent_documents"] >= 1
    assert body["queue"]["running"] >= 0 and body["queue"]["queued"] >= 0
    assert body["limits"]["max_pdf_pages"] == settings.max_pdf_pages
    assert body["limits"]["result_ttl_minutes"] == settings.result_ttl_minutes


def test_a_missing_model_is_reported_as_an_issue(monkeypatch):
    monkeypatch.setattr(settings, "ner_spacy_model", "no_existe_este_modelo")
    body = client.get("/health").json()
    assert body["models"]["spacy"]["present"] is False
    assert body["status"] == "degraded"
    assert any("spaCy" in issue for issue in body["issues"])


def test_readiness_fails_when_something_essential_is_missing(monkeypatch):
    monkeypatch.setattr(settings, "ner_spacy_model", "no_existe_este_modelo")
    assert client.get("/health/ready").status_code == 503


def test_readiness_tolerates_the_absence_of_gpu(monkeypatch):
    # Sin GPU el servicio va lento, pero anonimiza igual: no puede quedarse
    # fuera de servicio por eso.
    monkeypatch.setattr(
        health_module, "_gpu_state",
        lambda: {"requested": True, "available": False, "devices": 0,
                 "name": None, "cuda": None, "torch": "2.0.0"},
    )
    response = client.get("/health/ready")
    body = response.json()
    if body["models"]["spacy"]["present"] and body["models"]["easyocr"]["present"]:
        assert response.status_code == 200
    assert any("CPU" in issue for issue in body["issues"])


def test_the_health_check_does_not_load_any_model():
    # Debe poder consultarse mil veces sin coste: si cargara spaCy o el OCR,
    # el indicador de la interfaz tumbaria el servicio.
    import time

    start = time.perf_counter()
    for _ in range(20):
        client.get("/health")
    assert (time.perf_counter() - start) < 5.0
