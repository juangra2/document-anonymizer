"""Parar un documento que ya se está procesando.

Lo que se fija aquí es que parar libere DE VERDAD el permiso de GPU y borre los
ficheros: el fallo que esto arregla era que el borrado quitaba los ficheros de
debajo del trabajador, que seguía ocupando la tarjeta con un documento que ya
no existía.

El procesamiento se simula: así se pueden medir tiempos exactos —cuánto tarda
en soltar, cuánto tarda en arrancar el siguiente— sin depender de una GPU ni de
lo que tarde un OCR real.  La prueba con un escaneado de verdad es aparte.
"""
import threading
import time
from pathlib import Path

import pytest

from app.services import cancellation, progress
from app.services.jobs.store import JobStore


class _FakePipeline:
    """Procesa «páginas» que tardan, y atiende la parada entre una y otra."""

    def __init__(self, pages: int = 40, page_seconds: float = 0.05) -> None:
        self.pages = pages
        self.page_seconds = page_seconds
        self.started = threading.Event()
        self.finished = threading.Event()
        self.pages_processed = 0

    def process(self, input_path, output_path, job_id, mode,
                collect_entities, disabled_types):
        self.started.set()
        on_page = progress.claim(self.pages)
        try:
            for i in range(self.pages):
                on_page(i)
                cancellation.check()
                time.sleep(self.page_seconds)
                self.pages_processed += 1
            output_path.write_bytes(b"%PDF-1.4 salida\n")
            from app.schemas.report import ProcessingReport

            return ProcessingReport(job_id=job_id, status="completed", pages=self.pages)
        finally:
            self.finished.set()


def _store(pipeline) -> JobStore:
    store = JobStore(workers=1, queue_max=10, ttl_minutes=60, sweep_interval_s=3600)
    store._pipeline_factory = pipeline
    return store


def _submit(store: JobStore, name: str = "") -> object:
    # el nombre ya no viaja al servidor; el parametro se conserva por legibilidad
    job = store.create(content_type="application/pdf")
    job.input_path.write_bytes(b"%PDF-1.4 entrada\n")
    store.enqueue(job, size_bytes=16, disabled_types=frozenset())
    return job


def _wait_until(condition, timeout: float = 10.0, tick: float = 0.02) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(tick)
    return False


@pytest.fixture
def pipeline():
    return _FakePipeline()


@pytest.fixture
def store(pipeline):
    store = _store(pipeline)
    yield store
    store.shutdown(timeout=2.0)


def test_stopping_a_running_job_frees_the_gpu_slot(store, pipeline):
    """El permiso de GPU tiene que quedar libre, no solo el estado a 'parado'."""
    job = _submit(store)
    assert pipeline.started.wait(5.0)

    assert store.delete(job.id) == "stopping"

    assert _wait_until(lambda: store.get(job.id) is None), (
        "el trabajo debe desaparecer tras soltar la pagina en curso"
    )
    # El permiso se puede tomar: nadie quedó trabajando por detrás.
    assert store._slots.acquire(timeout=2.0), "la GPU seguia ocupada tras parar"
    store._slots.release()
    assert pipeline.pages_processed < pipeline.pages, "no llego a parar a mitad"


def test_the_next_document_starts_right_after_the_stop(store, pipeline):
    """Parar el de delante tiene que dar paso al siguiente, no esperar a nada."""
    first = _submit(store, "primero.pdf")
    second = _submit(store, "segundo.pdf")
    assert pipeline.started.wait(5.0)

    asked_at = time.monotonic()
    store.delete(first.id)

    assert _wait_until(lambda: store.get(second.id).status == "running", timeout=5.0)
    waited = time.monotonic() - asked_at
    # Una pagina simulada tarda 50 ms: el siguiente no puede tardar segundos.
    assert waited < 2.0, f"el siguiente tardo {waited:.2f}s en arrancar"


def test_stopping_removes_every_file_of_that_document(store, pipeline):
    job = _submit(store)
    directory: Path = job.directory
    assert pipeline.started.wait(5.0)
    assert directory.exists()

    store.delete(job.id)

    assert _wait_until(lambda: not directory.exists()), (
        "el borrado debe ser efectivo en disco, incluidos original y salida"
    )
    assert store.get(job.id) is None


def test_a_queued_job_is_stopped_before_it_starts(store, pipeline):
    busy = _submit(store, "ocupa.pdf")
    waiting = _submit(store, "espera.pdf")
    assert pipeline.started.wait(5.0)
    directory = waiting.directory

    assert store.delete(waiting.id) == "gone"
    assert store.get(waiting.id) is None
    assert not directory.exists()

    store.delete(busy.id)


def test_asking_twice_or_for_something_gone_does_not_break(store, pipeline):
    job = _submit(store)
    assert pipeline.started.wait(5.0)

    assert store.delete(job.id) == "stopping"
    # Insistir mientras se para responde lo mismo, sin efectos raros.
    assert store.delete(job.id) in ("stopping", "gone", "missing")

    assert _wait_until(lambda: store.get(job.id) is None)
    assert store.delete(job.id) == "missing"
    assert store.delete("nodeexiste") == "missing"


def test_a_finished_job_is_deleted_as_before():
    pipeline = _FakePipeline(pages=1, page_seconds=0.0)
    store = _store(pipeline)
    try:
        job = _submit(store)
        assert _wait_until(lambda: store.get(job.id).status == "completed", timeout=5.0)
        directory = job.directory

        assert store.delete(job.id) == "gone"
        assert not directory.exists()
        assert store.get(job.id) is None
    finally:
        store.shutdown(timeout=2.0)


def test_a_stopped_job_never_publishes_a_result(store, pipeline):
    """Si la parada llega al final, el resultado se tira: nadie lo descarga."""
    job = _submit(store)
    assert pipeline.started.wait(5.0)
    store.delete(job.id)
    assert _wait_until(lambda: store.get(job.id) is None)

    assert job.report is None
    assert job.status == "running", (
        "el trabajo parado no pasa por completed: desaparece"
    )


def test_leftovers_from_previous_runs_are_removed_at_startup(tmp_path, monkeypatch, caplog):
    """Un reinicio no puede dejar documentos huérfanos en disco.

    Sus identificadores murieron con el proceso anterior, así que nadie puede
    volver a pedirlos por la API, pero los ficheros siguen ahí.
    """
    from app.core.config import settings
    from app.services.jobs import job_store

    monkeypatch.setattr(settings, "temp_root", str(tmp_path))
    viejo = tmp_path / "jobs" / "3f2a9c1e"
    viejo.mkdir(parents=True)
    (viejo / "input.pdf").write_bytes(b"%PDF-1.4 documento de antes\n")
    (viejo / "output_redacted.pdf").write_bytes(b"%PDF-1.4 resultado de antes\n")
    suelto = tmp_path / "pdf_ocr_abc123"
    suelto.mkdir()
    (suelto / "page_1.png").write_bytes(b"imagen de pagina")

    with caplog.at_level("INFO"):
        removed = job_store.purge_previous_runs()

    assert removed == 2
    assert not viejo.exists() and not suelto.exists()
    assert not any(tmp_path.rglob("*.pdf")), "el borrado debe ser efectivo en disco"
    # El registro dice cuantas, nunca como se llamaban.
    registro = "\n".join(r.getMessage() for r in caplog.records)
    assert "2" in registro
    assert "3f2a9c1e" not in registro and "input.pdf" not in registro


def test_purging_survives_a_leftover_that_cannot_be_removed(tmp_path, monkeypatch):
    """Un resto bloqueado no puede impedir que el servicio arranque."""
    from app.core.config import settings
    from app.services.jobs import job_store

    monkeypatch.setattr(settings, "temp_root", str(tmp_path))
    (tmp_path / "bueno").mkdir()
    (tmp_path / "bueno" / "x.pdf").write_bytes(b"%PDF-1.4\n")

    import app.services.jobs.store as store_module

    real_rmtree = store_module.shutil.rmtree

    def _falla(path, *args, **kwargs):
        if Path(path).name == "bloqueado":
            raise OSError(13, "Permission denied")
        return real_rmtree(path, *args, **kwargs)

    (tmp_path / "bloqueado").mkdir()
    monkeypatch.setattr(store_module.shutil, "rmtree", _falla)

    assert job_store.purge_previous_runs() == 1
    assert not (tmp_path / "bueno").exists()


def test_the_stop_flag_is_visible_while_it_winds_down(store, pipeline):
    """Mientras suelta la pagina, el estado sigue siendo running + stopping."""
    slow = _FakePipeline(pages=200, page_seconds=0.05)
    slow_store = _store(slow)
    try:
        job = _submit(slow_store)
        assert slow.started.wait(5.0)
        slow_store.delete(job.id)

        seen = slow_store.get(job.id)
        if seen is not None:  # si aun no ha salido, asi es como se ve
            assert seen.status == "running"
            assert seen.stop_mode == "delete"
        assert _wait_until(lambda: slow_store.get(job.id) is None)
    finally:
        slow_store.shutdown(timeout=2.0)


# --- PARAR: la otra intencion ------------------------------------------------

def test_stopping_a_running_document_asks_for_a_partial(store, pipeline):
    """Con páginas hechas, PARAR entrega lo procesado."""
    job = _submit(store)
    assert pipeline.started.wait(5.0)
    assert _wait_until(lambda: job.pages_done >= 1)

    assert store.stop(job.id) == "stopping"
    assert job.stop_mode == "partial"


def test_stopping_before_the_first_page_deletes_instead(store, pipeline):
    """Un PDF de cero páginas no es un documento: no hay nada que entregar."""
    job = _submit(store)
    assert pipeline.started.wait(5.0)
    if job.pages_done == 0:
        assert store.stop(job.id) == "deleting"
        assert job.stop_mode == "delete"
        assert _wait_until(lambda: store.get(job.id) is None)


def test_stopping_something_still_queued_is_a_conflict(store, pipeline):
    ocupa = _submit(store, "ocupa.pdf")
    espera = _submit(store, "espera.pdf")
    assert pipeline.started.wait(5.0)

    assert store.stop(espera.id) == "nothing_yet"
    assert espera.status == "queued", "sigue en la cola: no se toca"
    store.delete(ocupa.id)
    store.delete(espera.id)


def test_stopping_something_finished_or_unknown(store):
    pipeline_rapido = _FakePipeline(pages=1, page_seconds=0.0)
    otro = _store(pipeline_rapido)
    try:
        job = _submit(otro)
        assert _wait_until(lambda: otro.get(job.id).status == "completed", timeout=5.0)
        assert otro.stop(job.id) == "too_late"
        assert otro.stop("nodeexiste") == "missing"
    finally:
        otro.shutdown(timeout=2.0)


def test_deleting_wins_over_stopping(store, pipeline):
    """Si ya se pidió eliminar, parar no puede resucitar el resultado."""
    job = _submit(store)
    assert pipeline.started.wait(5.0)

    assert store.delete(job.id) == "stopping"
    assert store.stop(job.id) == "deleting"
    assert job.stop_mode == "delete"
    assert _wait_until(lambda: store.get(job.id) is None)


def test_a_worker_survives_a_stop_that_nobody_handled(store, pipeline):
    """El trabajador no puede morir: con uno solo, el servicio se pararia entero.

    El pipeline simulado no atrapa la parada (los redactores de verdad si), asi
    que la excepcion llega al trabajador.  Debe sobrevivir y seguir con la cola.
    """
    primero = _submit(store, "primero.pdf")
    segundo = _submit(store, "segundo.pdf")
    assert pipeline.started.wait(5.0)
    assert _wait_until(lambda: primero.pages_done >= 1)

    store.stop(primero.id)

    assert _wait_until(lambda: store.get(primero.id) is None), (
        "sin parcial que entregar, el trabajo se elimina"
    )
    assert _wait_until(lambda: store.get(segundo.id).status in ("running", "completed")), (
        "el trabajador siguio vivo y cogio el siguiente"
    )
