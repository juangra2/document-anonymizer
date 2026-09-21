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

from app.services import cancellation
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
        try:
            for _ in range(self.pages):
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


def _submit(store: JobStore, name: str = "expediente.pdf"):
    job = store.create(filename=name, content_type="application/pdf")
    job.input_path.write_bytes(b"%PDF-1.4 entrada\n")
    store.enqueue(job, size_bytes=16, disabled_types=frozenset(), include_entities=False)
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
            assert seen.cancel_requested is True
        assert _wait_until(lambda: slow_store.get(job.id) is None)
    finally:
        slow_store.shutdown(timeout=2.0)
