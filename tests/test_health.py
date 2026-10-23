"""Estado del servicio.

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


def test_health_says_whether_the_original_is_kept(monkeypatch):
    # El aviso de borrado de la pantalla se compone con esto: no puede
    # afirmar que el original se borra si el servidor lo guarda.
    for valor in (False, True):
        monkeypatch.setattr(settings, "store_original", valor)
        assert client.get("/health").json()["limits"]["store_original"] is valor


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


# --- el trabajador vivo ------------------------------------------------------
# El fallo que esto cubre: el hilo que procesa la cola muere y el servicio sigue
# aceptando documentos sin avanzar ninguno, contestando «activo» mientras tanto.

class _HiloMuerto:
    def is_alive(self) -> bool:
        return False


def test_a_store_that_never_started_is_healthy():
    """Los trabajadores arrancan con el primer documento: aún no haberlos es normal."""
    from app.services.jobs.store import JobStore

    store = JobStore(workers=1, start_workers=False)
    salud = store.worker_health()
    assert salud["healthy"] is True
    assert salud["started"] is False


def test_a_dead_worker_makes_the_service_not_ready(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services.jobs import job_store

    monkeypatch.setattr(job_store, "_started", True)
    monkeypatch.setattr(job_store, "_worker_threads", [_HiloMuerto()])
    monkeypatch.setattr(job_store, "_sweeper_thread", _HiloMuerto())

    cliente = TestClient(app)
    estado = cliente.get("/health").json()

    assert estado["ready"] is False, "un trabajador muerto no es «listo»"
    assert estado["status"] == "degraded"
    assert estado["queue"]["workers_alive"] == 0
    assert any("cola no se esta atendiendo" in i for i in estado["issues"])

    # /health/ready es lo que mira el contenedor y el indicador del frontend.
    assert cliente.get("/health/ready").status_code == 503


def test_a_live_worker_keeps_the_service_ready(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services.jobs import job_store

    class _HiloVivo:
        def is_alive(self) -> bool:
            return True

    monkeypatch.setattr(job_store, "_started", True)
    monkeypatch.setattr(job_store, "_worker_threads", [_HiloVivo()])
    monkeypatch.setattr(job_store, "_sweeper_thread", _HiloVivo())

    cliente = TestClient(app)
    estado = cliente.get("/health").json()
    assert estado["queue"]["healthy"] is True
    assert cliente.get("/health/ready").status_code == 200
