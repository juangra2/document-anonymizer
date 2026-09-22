"""Avisos de página en vivo: el usuario se entera mientras se anonimiza.

Una página que el OCR no consigue leer se tacha entera.  Enterarse al final, en
el informe, llega tarde: con un expediente largo, quien lo ha subido quiere
saberlo cuando pasa para decidir si lo para y lo vuelve a escanear.

El OCR no hace falta para probar esto: lo que se fija aquí es el camino del
aviso —de quien tacha la página hasta el estado del trabajo— y su forma.
"""
import threading
import time

import pytest

from app.services import notices
from app.services.jobs.store import MAX_NOTICES, JobStore


class _PipelineQueAvisa:
    """Simula un documento cuyas páginas 2 y 3 no se pueden leer."""

    def __init__(self, avisos: int = 2, pages: int = 5) -> None:
        self.avisos = avisos
        self.pages = pages
        self.started = threading.Event()

    def process(self, input_path, output_path, job_id, mode,
                collect_entities, disabled_types):
        from app.schemas.report import PageRedactedInFull, ProcessingReport

        self.started.set()
        for i in range(self.avisos):
            motivo = notices.OCR_TIMEOUT if i % 2 else notices.OCR_UNREADABLE
            notices.page_redacted_in_full(i + 1, motivo)
            time.sleep(0.01)
        output_path.write_bytes(b"%PDF-1.4 salida\n")
        return ProcessingReport(
            job_id=job_id, status="completed_with_warnings", pages=self.pages,
            pages_redacted_in_full=[
                PageRedactedInFull(
                    page=i + 2,
                    reason=notices.OCR_TIMEOUT if i % 2 else notices.OCR_UNREADABLE,
                )
                for i in range(self.avisos)
            ],
        )


def _store(pipeline) -> JobStore:
    store = JobStore(workers=1, queue_max=10, ttl_minutes=60, sweep_interval_s=3600)
    store._pipeline_factory = pipeline
    return store


def _submit(store: JobStore):
    job = store.create(content_type="application/pdf")
    job.input_path.write_bytes(b"%PDF-1.4 entrada\n")
    store.enqueue(job, size_bytes=16, disabled_types=frozenset())
    return job


def _wait_until(condition, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


# --- el canal ----------------------------------------------------------------

def test_a_notice_carries_user_page_numbers_and_stable_codes():
    recogidos: list[notices.PageNotice] = []
    with notices.track(recogidos.append):
        notices.page_redacted_in_full(11, notices.OCR_TIMEOUT)

    assert len(recogidos) == 1
    aviso = recogidos[0]
    assert aviso.page == 12, "la página 11 en base 0 es la 12 para el usuario"
    assert aviso.kind == "page_redacted_in_full"
    assert aviso.reason == "ocr_timeout"


def test_without_a_listener_nothing_breaks():
    """La ruta síncrona no escucha: el aviso no puede reventar el proceso."""
    notices.page_redacted_in_full(3, notices.OCR_UNREADABLE)


# --- llegan al estado del trabajo --------------------------------------------

def test_notices_reach_the_job_state_while_it_runs():
    pipeline = _PipelineQueAvisa(avisos=2)
    store = _store(pipeline)
    try:
        job = _submit(store)
        assert _wait_until(lambda: len(job.page_notices) == 2)

        paginas = [n.page for n in job.page_notices]
        motivos = {n.reason for n in job.page_notices}
        assert paginas == [2, 3]
        assert motivos == {"ocr_unreadable", "ocr_timeout"}
        assert job.notices_total == 2
    finally:
        store.shutdown(timeout=2.0)


def test_the_list_is_capped_but_the_total_is_not():
    """Un escaneado ilegible entero no puede inflar cada consulta de estado."""
    pipeline = _PipelineQueAvisa(avisos=MAX_NOTICES + 25)
    store = _store(pipeline)
    try:
        job = _submit(store)
        assert _wait_until(
            lambda: job.notices_total == MAX_NOTICES + 25, timeout=20.0,
        )
        assert len(job.page_notices) == MAX_NOTICES
        assert job.page_notices[0].page == 2, "se conservan los primeros"
    finally:
        store.shutdown(timeout=2.0)


def test_a_notice_for_an_unknown_job_is_ignored():
    store = _store(_PipelineQueAvisa())
    try:
        store.report_notice("nodeexiste", notices.PageNotice(1, "k", "r"))
    finally:
        store.shutdown(timeout=2.0)


# --- el informe final --------------------------------------------------------

def test_the_report_lists_every_page_with_its_reason():
    pipeline = _PipelineQueAvisa(avisos=3)
    store = _store(pipeline)
    try:
        job = _submit(store)
        assert _wait_until(lambda: job.status == "completed")
        listado = [(p.page, p.reason) for p in job.report.pages_redacted_in_full]
        assert listado == [
            (2, "ocr_unreadable"), (3, "ocr_timeout"), (4, "ocr_unreadable"),
        ]
    finally:
        store.shutdown(timeout=2.0)


@pytest.mark.parametrize("motivo", ["ocr_unreadable", "ocr_timeout"])
def test_the_reasons_are_the_two_documented_ones(motivo):
    assert motivo in (notices.OCR_UNREADABLE, notices.OCR_TIMEOUT)
