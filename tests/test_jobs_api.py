"""Bloque D1 — anonimización asíncrona por cola de trabajos.

Lo que se fija aquí es el contrato que consumirá el frontend (nombres de
endpoints, forma del estado, códigos de estado HTTP) y las dos garantías que no
se ven leyendo las rutas: que solo se procesa un documento a la vez y que los
ficheros se borran del disco, no solo de la memoria.
"""
import time
from io import BytesIO

import fitz
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.jobs import job_store

client = TestClient(app)


def _pdf(pages: int = 1) -> bytes:
    doc = fitz.open()
    for index in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), f"Pagina {index + 1}", fontsize=11)
        page.insert_text((72, 120), "Nombre: Roberto Iglesias Calvo", fontsize=11)
        page.insert_text((72, 140), "DNI: 12345678Z", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def _upload(query: str = "", pages: int = 1):
    return client.post(
        f"/jobs{query}",
        files={"file": ("expediente.pdf", BytesIO(_pdf(pages)), "application/pdf")},
    )


def _wait(job_id: str, timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get(f"/jobs/{job_id}").json()
        if state["status"] in ("completed", "failed", "expired"):
            return state
        time.sleep(0.2)
    raise AssertionError(f"el trabajo {job_id} no termino en {timeout}s")


# --- alta y ciclo de vida ----------------------------------------------------

def test_upload_answers_immediately_with_a_job():
    response = _upload()
    assert response.status_code == 202
    body = response.json()
    assert body["job_id"]
    assert body["status"] in ("queued", "running")
    assert body["result"] is None and body["error"] is None
    # La cabecera Location apunta al estado, en ruta relativa al prefijo.
    assert response.headers["Location"] == f"/jobs/{body['job_id']}"


def test_the_job_reaches_completion_with_a_summary():
    job_id = _upload().json()["job_id"]
    state = _wait(job_id)

    assert state["status"] == "completed"
    assert state["error"] is None
    assert state["finished_at"] and state["expires_at"]
    assert state["queue_position"] is None

    result = state["result"]
    assert result["pages"] == 1
    assert result["entities_redacted"] > 0
    assert result["status"] in (
        "completed", "completed_with_warnings", "manual_review_required",
    )


def test_progress_is_complete_when_the_job_finishes():
    job_id = _upload(pages=3).json()["job_id"]
    state = _wait(job_id)
    assert state["progress"]["pages_total"] == 3
    assert state["progress"]["pages_done"] == 3
    assert state["progress"]["percent"] == 100


# --- descarga ----------------------------------------------------------------

def test_the_result_is_the_anonymized_document():
    job_id = _upload().json()["job_id"]
    _wait(job_id)

    response = client.get(f"/jobs/{job_id}/result")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    with fitz.open(stream=response.content, filetype="pdf") as doc:
        text = "\n".join(page.get_text() for page in doc)
    assert "12345678Z" not in text


def test_the_download_never_carries_the_uploaded_name():
    """El nombre lo pone quien descarga, no el servidor."""
    response = client.post(
        "/jobs",
        files={"file": ("Expediente Luis Vela.pdf", BytesIO(_pdf()), "application/pdf")},
    )
    job_id = response.json()["job_id"]
    _wait(job_id)

    descarga = client.get(f"/jobs/{job_id}/result")
    disposicion = descarga.headers["content-disposition"]
    assert "documento_anonimizado.pdf" in disposicion
    assert "Juan" not in disposicion and "Expediente" not in disposicion


def test_the_result_can_be_downloaded_as_zip_with_the_report():
    import json
    import zipfile

    job_id = _upload().json()["job_id"]
    _wait(job_id)

    response = client.get(f"/jobs/{job_id}/result?output=zip")
    assert response.status_code == 200
    with zipfile.ZipFile(BytesIO(response.content)) as zf:
        names = zf.namelist()
        report = json.loads(zf.read("report.json"))
    assert "report.json" in names
    assert report["job_id"] == job_id


def test_the_report_is_available_on_its_own():
    job_id = _upload().json()["job_id"]
    _wait(job_id)
    report = client.get(f"/jobs/{job_id}/report").json()
    assert report["job_id"] == job_id
    assert report["entity_types_applied"]
    # El servidor no guarda el nombre del fichero: suele llevar el del interesado.
    assert "filename_sanitized" not in report["input"]
    assert report["input"]["content_type"] == "application/pdf"


def test_downloading_before_it_finishes_is_a_conflict():
    job_id = _upload().json()["job_id"]
    # Puede haber terminado ya; solo se comprueba el caso en que no.
    state = client.get(f"/jobs/{job_id}").json()
    if state["status"] in ("queued", "running"):
        response = client.get(f"/jobs/{job_id}/result")
        assert response.status_code == 409
    _wait(job_id)


def test_an_unknown_job_is_not_found():
    assert client.get("/jobs/nodeexiste").status_code == 404
    assert client.get("/jobs/nodeexiste/result").status_code == 404
    assert client.delete("/jobs/nodeexiste").status_code == 404


# --- borrado -----------------------------------------------------------------

def test_deleting_a_job_removes_its_files_from_disk():
    job_id = _upload().json()["job_id"]
    _wait(job_id)
    directory = job_store.get(job_id).directory
    assert directory.exists()

    assert client.delete(f"/jobs/{job_id}").status_code == 204
    assert not directory.exists(), "el borrado debe ser efectivo en disco"
    assert client.get(f"/jobs/{job_id}").status_code == 404


def test_stopping_a_running_document_is_accepted_not_immediate(monkeypatch):
    """202 = «se está parando»; el trabajo aún existe al responder.

    La parada de verdad (que suelte la GPU y borre) se prueba en
    test_job_cancel.py; aquí solo se fija lo que ve el cliente.
    """
    job_id = _upload().json()["job_id"]
    _wait(job_id)
    monkeypatch.setattr(job_store, "delete", lambda _id: "stopping")

    assert client.delete(f"/jobs/{job_id}").status_code == 202


def test_the_state_says_when_a_document_is_winding_down():
    job_id = _upload().json()["job_id"]
    state = _wait(job_id)
    assert state["stopping"] is False

    job = job_store.get(job_id)
    job.status = "running"
    job.cancel_requested = True
    assert client.get(f"/jobs/{job_id}").json()["stopping"] is True


def test_the_original_document_is_removed_as_soon_as_it_is_processed():
    job_id = _upload().json()["job_id"]
    _wait(job_id)
    job = job_store.get(job_id)
    assert not job.input_path.exists(), "el original no se conserva tras procesar"
    assert job.output_path.exists()


# --- caducidad ---------------------------------------------------------------

def test_an_expired_job_is_swept_even_if_nobody_asks_for_it():
    from datetime import timedelta

    from app.services.jobs.store import _now

    job_id = _upload().json()["job_id"]
    _wait(job_id)
    job = job_store.get(job_id)
    directory = job.directory

    # Justo caducado: el registro debe seguir existiendo para responder
    # "caducado"; solo se descarta mucho despues.
    job.expires_at = _now() - timedelta(seconds=1)
    job_store.sweep()

    assert not directory.exists()
    # El registro sobrevive un rato para poder decir "caducado" y no "no existe".
    state = client.get(f"/jobs/{job_id}").json()
    assert state["status"] == "expired"
    assert state["result"] is None
    assert client.get(f"/jobs/{job_id}/result").status_code == 410


# --- validación de la subida -------------------------------------------------

def test_a_non_pdf_is_rejected_before_creating_a_job():
    response = client.post(
        "/jobs", files={"file": ("foto.png", BytesIO(b"\x89PNG"), "image/png")},
    )
    assert response.status_code == 415


def test_a_file_that_is_not_really_a_pdf_is_rejected():
    response = client.post(
        "/jobs", files={"file": ("falso.pdf", BytesIO(b"no soy un pdf"), "application/pdf")},
    )
    assert response.status_code == 400


def test_a_rejected_upload_leaves_no_directory_behind():
    from pathlib import Path

    from app.core.config import settings

    root = Path(settings.temp_root) / "jobs"
    before = set(root.iterdir()) if root.exists() else set()
    client.post(
        "/jobs", files={"file": ("falso.pdf", BytesIO(b"no soy un pdf"), "application/pdf")},
    )
    after = set(root.iterdir()) if root.exists() else set()
    assert after == before


def test_any_type_can_be_unmarked_here_too():
    """Sin tipos obligatorios, solo se rechaza lo que no existe."""
    assert _upload("?disabled_types=ES_DNI").status_code == 202
    assert _upload("?disabled_types=no_existe").status_code == 422


def test_optional_types_are_honoured():
    job_id = _upload("?disabled_types=DATE_TIME").json()["job_id"]
    state = _wait(job_id)
    assert state["result"]["entity_types_disabled"] == ["DATE_TIME"]


# --- concurrencia ------------------------------------------------------------

def test_only_one_document_is_processed_at_a_time():
    """El límite de GPU: con el permiso tomado, nadie más entra."""
    assert job_store._slots._initial_value == 1, (
        "por defecto se procesa un documento a la vez"
    )
    with job_store.slot():
        acquired = job_store._slots.acquire(blocking=False)
    assert not acquired, "el permiso estaba libre mientras otro lo tenia"


def test_the_queue_refuses_more_than_the_configured_backlog():
    from app.services.jobs.store import JobStore, QueueFullError

    store = JobStore(workers=1, queue_max=2, ttl_minutes=60,
                     sweep_interval_s=3600, start_workers=False)
    try:
        first = store.create(content_type="application/pdf")
        second = store.create(content_type="application/pdf")
        store.enqueue(first, size_bytes=1, disabled_types=frozenset())
        store.enqueue(second, size_bytes=1, disabled_types=frozenset())
        with pytest.raises(QueueFullError):
            store.create(content_type="application/pdf")
    finally:
        store.shutdown(timeout=1.0)


def test_queue_position_counts_the_documents_ahead():
    from app.services.jobs.store import JobStore

    store = JobStore(workers=1, queue_max=10, ttl_minutes=60,
                     sweep_interval_s=3600, start_workers=False)
    try:
        first = store.create(content_type="application/pdf")
        second = store.create(content_type="application/pdf")
        assert store.queue_position(first) == 0
        assert store.queue_position(second) == 1
    finally:
        store.shutdown(timeout=1.0)


# --- servido tras un proxy inverso -------------------------------------------

def test_the_location_header_respects_the_path_prefix():
    """Tras un proxy la aplicación puede colgar de /anonimizador, no de la raíz.

    Si la cabecera se construyera con el host o la ruta absolutos, apuntaría al
    servidor interno y el navegador no llegaría.
    """
    prefixed = TestClient(app, root_path="/anonimizador")
    response = prefixed.post(
        "/jobs",
        files={"file": ("expediente.pdf", BytesIO(_pdf()), "application/pdf")},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert response.headers["Location"] == f"/anonimizador/jobs/{job_id}"
    _wait(job_id)


# --- límites de entrada ------------------------------------------------------

def test_a_document_over_the_page_limit_fails_with_a_stable_code(monkeypatch):
    """El fallo llega como estado del trabajo, no como error de la subida.

    El número de páginas no se conoce hasta abrir el PDF, y para entonces la
    petición de subida ya ha respondido.  El frontend traduce `code` a una
    frase; el mensaje de la API es técnico a propósito.
    """
    monkeypatch.setattr(settings, "max_pdf_pages", 2)
    job_id = _upload(pages=3).json()["job_id"]
    state = _wait(job_id)

    assert state["status"] == "failed"
    assert state["error"]["code"] == "too_many_pages"
    assert state["result"] is None
    assert state["expires_at"], "un trabajo fallido tambien caduca y se borra"


def test_a_failed_job_keeps_no_original_on_disk():
    from app.core.config import settings as live

    original = live.max_pdf_pages
    live.max_pdf_pages = 2
    try:
        job_id = _upload(pages=3).json()["job_id"]
        _wait(job_id)
    finally:
        live.max_pdf_pages = original

    job = job_store.get(job_id)
    assert not job.input_path.exists(), "un fallo no puede dejar el original ahi"
