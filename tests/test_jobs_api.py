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
    job.stop_mode = "delete"
    assert client.get(f"/jobs/{job_id}").json()["stopping"] is True


def test_the_state_publishes_page_notices():
    """El frontend los pinta mientras procesa; aquí se fija la forma."""
    from app.services import notices

    job_id = _upload().json()["job_id"]
    _wait(job_id)
    job = job_store.get(job_id)
    job.page_notices.append(
        notices.PageNotice(page=12, kind=notices.PAGE_REDACTED_IN_FULL,
                           reason=notices.OCR_TIMEOUT)
    )
    job.notices_total = 1

    estado = client.get(f"/jobs/{job_id}").json()
    assert estado["notices"] == [
        {"page": 12, "kind": "page_redacted_in_full", "reason": "ocr_timeout"}
    ]
    assert estado["notices_total"] == 1


def test_a_document_without_incidents_reports_no_notices():
    job_id = _upload().json()["job_id"]
    estado = _wait(job_id)
    assert estado["notices"] == []
    assert estado["notices_total"] == 0


def test_the_original_document_is_removed_as_soon_as_it_is_processed():
    job_id = _upload().json()["job_id"]
    _wait(job_id)
    job = job_store.get(job_id)
    assert not job.input_path.exists(), "el original no se conserva tras procesar"
    assert job.output_path.exists()


# --- parar y entregar lo procesado -------------------------------------------

def test_stop_answers_202_while_it_winds_down(monkeypatch):
    job_id = _upload().json()["job_id"]
    _wait(job_id)
    monkeypatch.setattr(job_store, "stop", lambda _id: "stopping")
    assert client.post(f"/jobs/{job_id}/stop").status_code == 202


def test_stopping_something_queued_says_why(monkeypatch):
    job_id = _upload().json()["job_id"]
    _wait(job_id)
    monkeypatch.setattr(job_store, "stop", lambda _id: "nothing_yet")

    respuesta = client.post(f"/jobs/{job_id}/stop")
    assert respuesta.status_code == 409
    assert respuesta.json()["detail"]["code"] == "nothing_processed_yet"


def test_stopping_something_finished_says_why(monkeypatch):
    job_id = _upload().json()["job_id"]
    _wait(job_id)
    monkeypatch.setattr(job_store, "stop", lambda _id: "too_late")

    respuesta = client.post(f"/jobs/{job_id}/stop")
    assert respuesta.status_code == 409
    assert respuesta.json()["detail"]["code"] == "already_finished"


def test_stopping_an_unknown_job_is_not_found():
    assert client.post("/jobs/nodeexiste/stop").status_code == 404


def test_a_partial_result_is_marked_everywhere():
    """En el estado, en el nombre de la descarga y en el informe."""
    job_id = _upload(pages=3).json()["job_id"]
    _wait(job_id)

    job = job_store.get(job_id)
    job.report.partial = True
    job.report.pages = 2
    job.report.pages_total_original = 3

    estado = client.get(f"/jobs/{job_id}").json()
    assert estado["result"]["partial"] is True
    assert estado["result"]["pages"] == 2
    assert estado["result"]["pages_total_original"] == 3

    descarga = client.get(f"/jobs/{job_id}/result")
    assert "documento_anonimizado_parcial_1-2_de_3.pdf" in (
        descarga.headers["content-disposition"]
    )

    informe = client.get(f"/jobs/{job_id}/report").json()
    assert informe["partial"] is True and informe["pages_total_original"] == 3


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
    assert state["result"]["entity_types_disabled"] == ["CASE_NUMBER", "DATE_TIME"]


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

def test_a_document_over_the_page_limit_is_rejected_at_upload(monkeypatch):
    """Antes se descubría al procesarlo, con la cola por delante; ahora, al subir."""
    monkeypatch.setattr(settings, "max_pdf_pages", 2)
    response = _upload(pages=3)

    assert response.status_code == 422
    detalle = response.json()["detail"]
    assert detalle["code"] == "too_many_pages"
    assert detalle["limit_pages"] == 2
    assert "3 pages" in detalle["message"], "dice cuantas tiene y cuantas caben"


def test_a_document_over_the_size_limit_says_which_limit(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 0)
    response = _upload()

    assert response.status_code == 413
    detalle = response.json()["detail"]
    assert detalle["code"] == "document_too_large"
    assert detalle["limit_mb"] == 0


def test_a_rejected_document_never_reaches_the_queue(monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(settings, "max_pdf_pages", 1)
    root = Path(settings.temp_root) / "jobs"
    antes = set(root.iterdir()) if root.exists() else set()

    assert _upload(pages=5).status_code == 422
    despues = set(root.iterdir()) if root.exists() else set()
    assert despues == antes, "no puede quedar ni el directorio del trabajo"


def test_a_password_protected_pdf_is_rejected_at_upload(tmp_path):
    """Antes se descubria al procesarlo; ahora al subirlo, en segundos."""
    import fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "Confidencial", fontsize=11)
    protegido = tmp_path / "con_clave.pdf"
    doc.save(protegido, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="secreto")
    doc.close()

    response = client.post(
        "/jobs",
        files={"file": ("con_clave.pdf", protegido.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "encrypted_pdf"


def test_a_damaged_pdf_is_told_apart_from_an_internal_error():
    """Antes salía como «error interno», indistinguible de un fallo nuestro."""
    roto = b"%PDF-1.4 esto no es un PDF de verdad" + bytes([10])
    response = client.post(
        "/jobs", files={"file": ("roto.pdf", BytesIO(roto), "application/pdf")},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_file"


def test_a_failed_job_keeps_no_original_on_disk(monkeypatch):
    """Un fallo durante el proceso tampoco puede dejar el original ahi."""
    from app.services.pdf import pipeline as pipeline_module

    def _revienta(*_a, **_k):
        raise RuntimeError("fallo simulado del pipeline")

    monkeypatch.setattr(
        pipeline_module.PdfAnonymizationPipeline, "process", _revienta,
    )
    job_id = _upload().json()["job_id"]
    state = _wait(job_id)

    assert state["status"] == "failed"
    job = job_store.get(job_id)
    assert not job.input_path.exists(), "un fallo no puede dejar el original ahi"
