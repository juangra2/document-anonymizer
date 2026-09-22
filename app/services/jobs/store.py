"""Cola de trabajos de anonimización, en proceso.

Un expediente de cientos de páginas tarda minutos: mantener la conexión HTTP
abierta hasta el final no es viable ni desde un navegador ni tras un proxy.  El
cliente sube el documento, recibe un identificador y consulta el estado.

Dos garantías que no son evidentes leyendo solo las rutas:

* **Un documento a la vez**.  El límite (`max_concurrent_documents`) es a la
  vez el número de trabajadores y el permiso de acceso a la GPU, y lo respeta
  también la ruta síncrona `POST /anonymize`.  Sin él, dos peticiones de OCR
  simultáneas agotan la memoria de la tarjeta y devuelven 500.
* **El borrado no depende del cliente**.  Un hilo limpiador barre los trabajos
  caducados aunque nadie los consulte, y borra los ficheros del disco, no solo
  la marca en memoria.  El documento original se borra antes, en cuanto acaba
  el proceso: a partir de ahí no hace falta para nada.
* **Parar un documento en proceso es cooperativo**.  `delete()` no le quita los
  ficheros al trabajador de debajo —eso dejaba la GPU ocupada con algo que ya
  no existía—: marca la parada, y es el propio trabajador quien borra al salir,
  cuando ya nadie está usando nada.

Nada de esto sobrevive a un reinicio, y es deliberado: un broker externo sería
un servicio más que operar en un despliegue de un solo nodo.
"""
from __future__ import annotations

import logging
import shutil
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Empty, Queue
from typing import Iterator, Literal
from uuid import uuid4

from app.core.config import settings
from app.core.errors import error_code_for
from app.schemas.report import ProcessingReport
from app.services import cancellation, notices, progress

logger = logging.getLogger(__name__)


JobStatus = Literal["queued", "running", "completed", "failed", "expired"]

# Tope de avisos que se conservan en el estado.  Un escaneado ilegible de 300
# páginas genera uno por página, y el estado se consulta cada dos segundos: la
# lista completa va en el informe, aquí basta con las primeras y el total.
MAX_NOTICES = 200

# Resultado de pedir la parada de un trabajo.  "stopping" es el único caso en
# que el trabajo sigue existiendo al responder: se está procesando y hay que
# esperar a que suelte la página que tiene entre manos.
DeleteOutcome = Literal["gone", "stopping", "missing"]


class QueueFullError(RuntimeError):
    """La cola está llena; el cliente debe reintentar más tarde."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Job:
    """Estado de un trabajo.  Solo el almacén lo muta, siempre bajo su cerrojo."""

    id: str
    directory: Path
    # Orden de llegada.  La marca de tiempo no sirve para ordenar la cola:
    # dos altas seguidas caen en el mismo instante con la resolucion del
    # reloj del sistema.
    seq: int = 0
    content_type: str | None = None
    size_bytes: int = 0

    disabled_types: frozenset[str] = frozenset()

    status: JobStatus = "queued"
    # Se pidió parar este documento.  Lo lee el procesamiento entre páginas,
    # así que se escribe una sola vez y nunca se vuelve a poner en falso.
    cancel_requested: bool = False
    created_at: datetime = field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    expires_at: datetime | None = None

    pages_done: int = 0
    pages_total: int = 0

    page_notices: list[notices.PageNotice] = field(default_factory=list)
    notices_total: int = 0

    error_code: str | None = None
    error_message: str | None = None

    report: ProcessingReport | None = None

    @property
    def input_path(self) -> Path:
        return self.directory / "input.pdf"

    @property
    def output_path(self) -> Path:
        return self.directory / "output_redacted.pdf"

    @property
    def is_terminal(self) -> bool:
        return self.status in ("completed", "failed", "expired")


class JobStore:
    """Cola, trabajadores y caducidad.  Hay una instancia por proceso."""

    def __init__(
        self,
        *,
        workers: int | None = None,
        queue_max: int | None = None,
        ttl_minutes: int | None = None,
        sweep_interval_s: int | None = None,
        start_workers: bool = True,
    ) -> None:
        self._workers = max(1, workers if workers is not None else settings.max_concurrent_documents)
        self._queue_max = queue_max if queue_max is not None else settings.job_queue_max
        self._ttl = timedelta(
            minutes=ttl_minutes if ttl_minutes is not None else settings.result_ttl_minutes
        )
        self._sweep_interval = (
            sweep_interval_s if sweep_interval_s is not None else settings.job_sweep_interval_s
        )

        self._jobs: dict[str, Job] = {}
        self._seq = 0
        self._lock = threading.RLock()
        self._queue: Queue[str | None] = Queue()
        # El permiso de GPU se cuenta aparte de los trabajadores porque lo pide
        # también la ruta síncrona, que no pasa por la cola.
        self._slots = threading.BoundedSemaphore(self._workers)
        self._threads: list[threading.Thread] = []
        self._stopping = threading.Event()
        # Con los trabajadores desactivados el almacén encola pero no procesa:
        # así los tests de cola y caducidad no dependen de una GPU ni de que un
        # hilo les gane la carrera.
        self._start_workers = start_workers
        self._started = not start_workers
        self._pipeline_factory = None

    # -- ciclo de vida --------------------------------------------------

    def purge_previous_runs(self) -> int:
        """Borra lo que dejaron ejecuciones anteriores bajo el directorio temporal.

        La cola no sobrevive a un reinicio, así que al arrancar no hay ningún
        trabajo vivo: lo que quede en disco son documentos huérfanos.  Nadie
        puede volver a pedirlos —su identificador se perdió con el proceso—,
        pero ahí siguen, con los datos personales dentro, al alcance de
        cualquiera que entre en la máquina.  En el contenedor el directorio
        vive en memoria y desaparece al reiniciar; fuera de él, nadie lo
        limpia, y ese es el caso que esto cubre.

        Se registra CUÁNTAS carpetas se borran, nunca sus nombres: el nombre de
        un fichero puede llevar el del interesado.

        Da por supuesto que el directorio temporal es de este servicio y de un
        solo proceso, que es como se despliega: un nodo con una GPU.
        """
        root = Path(settings.temp_root)
        if not root.exists():
            return 0

        removed = 0
        for entry in list(root.iterdir()):
            try:
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=False)
                else:
                    entry.unlink()
            except OSError as exc:
                # Un resto que no se puede borrar (permisos, fichero abierto por
                # otro proceso) no puede impedir que arranque el servicio.
                logger.warning("could not remove leftover entry: %s", exc.strerror)
                continue
            removed += 1

        if removed:
            logger.info(
                "removed %d leftover work director%s from previous runs",
                removed, "y" if removed == 1 else "ies",
            )
        return removed

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            for index in range(self._workers):
                thread = threading.Thread(
                    target=self._work, name=f"anonymizer-worker-{index}", daemon=True,
                )
                thread.start()
                self._threads.append(thread)
            sweeper = threading.Thread(
                target=self._sweep_forever, name="anonymizer-sweeper", daemon=True,
            )
            sweeper.start()
            self._threads.append(sweeper)

    def shutdown(self, timeout: float = 5.0) -> None:
        self._stopping.set()
        for _ in self._threads:
            self._queue.put(None)
        for thread in self._threads:
            thread.join(timeout=timeout)
        with self._lock:
            self._threads.clear()
            self._started = False

    # -- permiso de GPU compartido con la ruta síncrona -----------------

    @contextmanager
    def slot(self) -> Iterator[None]:
        """Reserva uno de los permisos de proceso concurrente."""
        self._slots.acquire()
        try:
            yield
        finally:
            self._slots.release()

    # -- alta y consulta ------------------------------------------------

    def create(self, *, content_type: str | None = None) -> Job:
        """Reserva identificador y directorio; el fichero aún no está escrito.

        No recibe el nombre del fichero: el servicio no lo guarda en ninguna
        parte.  Un nombre de expediente suele llevar el del interesado, y aquí
        sobreviviría al documento durante toda la retención.
        """
        self.start()
        with self._lock:
            self._purge_expired_locked()
            pending = sum(1 for j in self._jobs.values() if j.status == "queued")
            if pending >= self._queue_max:
                raise QueueFullError(
                    f"Hay {pending} documentos esperando turno; reintente mas tarde."
                )
            job_id = uuid4().hex
            self._seq += 1
            directory = Path(settings.temp_root) / "jobs" / job_id
            directory.mkdir(parents=True, exist_ok=True)
            job = Job(
                id=job_id,
                seq=self._seq,
                directory=directory,
                content_type=content_type,
            )
            self._jobs[job_id] = job
            return job

    def enqueue(
        self,
        job: Job,
        *,
        size_bytes: int,
        disabled_types: frozenset[str],
    ) -> None:
        with self._lock:
            job.size_bytes = size_bytes
            job.disabled_types = disabled_types
        self._queue.put(job.id)

    def discard(self, job: Job) -> None:
        """Descarta un trabajo que nunca llegó a la cola (subida fallida)."""
        with self._lock:
            self._jobs.pop(job.id, None)
        self._remove_files(job)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            self._purge_expired_locked()
            return self._jobs.get(job_id)

    def delete(self, job_id: str) -> DeleteOutcome:
        """Para el trabajo y borra sus ficheros a petición del cliente.

        En cola o terminado, el borrado es inmediato.  En proceso no se puede:
        el trabajador está leyendo el original y escribiendo el resultado, así
        que quitarle los ficheros lo dejaría trabajando sobre algo que ya no
        existe, con la GPU ocupada.  Se marca la parada y el trabajador borra
        al salir, en cuanto suelte la página en curso.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return "missing"
            if job.status == "running":
                if not job.cancel_requested:
                    logger.info("job %s: stop requested while running", job.id)
                job.cancel_requested = True
                return "stopping"
            # Un trabajo en cola desaparece del registro y el trabajador que lo
            # saque de la cola no lo encontrará: no llega a empezar.
            self._jobs.pop(job_id, None)
        self._remove_files(job)
        return "gone"

    def stats(self) -> dict[str, int]:
        """Carga actual, para el endpoint de salud."""
        with self._lock:
            return {
                "running": sum(1 for j in self._jobs.values() if j.status == "running"),
                "queued": sum(1 for j in self._jobs.values() if j.status == "queued"),
                "max_concurrent_documents": self._workers,
            }

    def queue_position(self, job: Job) -> int | None:
        """Cuántos trabajos hay por delante.  0 = es el siguiente."""
        with self._lock:
            if job.status != "queued":
                return None
            waiting = [
                j for j in self._jobs.values()
                if j.status == "queued" and j.seq < job.seq
            ]
            return len(waiting)

    # -- proceso --------------------------------------------------------

    def _pipeline(self):
        # Se construye perezosamente y una sola vez: cargar spaCy y el motor de
        # OCR en el import haría lentísimo arrancar y cualquier test.
        if self._pipeline_factory is None:
            from app.services.pdf.pipeline import PdfAnonymizationPipeline

            self._pipeline_factory = PdfAnonymizationPipeline()
        return self._pipeline_factory

    def _work(self) -> None:
        while not self._stopping.is_set():
            try:
                job_id = self._queue.get(timeout=0.5)
            except Empty:
                continue
            if job_id is None:
                return
            job = self.get(job_id)
            if job is None or job.status != "queued":
                continue
            with self.slot():
                self._run(job)

    def _run(self, job: Job) -> None:
        with self._lock:
            job.status = "running"
            job.started_at = _now()

        def on_progress(done: int, total: int) -> None:
            self.report_progress(job.id, done, total)

        def on_notice(notice: notices.PageNotice) -> None:
            self.report_notice(job.id, notice)

        cancelled = False

        try:
            with progress.track(on_progress), notices.track(on_notice),                     cancellation.track(lambda: job.cancel_requested):
                report = self._pipeline().process(
                    job.input_path,
                    job.output_path,
                    job.id,
                    settings.default_redaction_mode,
                    # Nunca con los valores detectados: el informe de un trabajo
                    # se conserva horas, y con ellos dentro sería conservar los
                    # datos personales que acabamos de tapar.  Quien los
                    # necesita para evaluar usa la ruta sincrona, que no retiene.
                    False,
                    job.disabled_types,
                )
        except cancellation.JobCancelled:
            cancelled = True
        except Exception as exc:  # noqa: BLE001 — el trabajo registra el fallo
            # Una parada puede provocar de paso un fallo cualquiera (un fichero
            # a medias, por ejemplo); si se pidió parar, lo que vale es eso.
            if job.cancel_requested:
                cancelled = True
            else:
                logger.exception("job %s failed", job.id)
                with self._lock:
                    job.status = "failed"
                    job.error_code = error_code_for(exc)
                    job.error_message = str(exc) or exc.__class__.__name__
                    job.finished_at = _now()
                    job.expires_at = job.finished_at + self._ttl
        else:
            with self._lock:
                # La parada pudo llegar entre la última página y este momento:
                # entonces el resultado no se publica, se tira.
                if job.cancel_requested:
                    cancelled = True
                else:
                    job.report = report
                    job.pages_total = report.pages or job.pages_total
                    job.pages_done = job.pages_total
                    job.status = "completed"
                    job.finished_at = _now()
                    job.expires_at = job.finished_at + self._ttl
        finally:
            # El original ya no hace falta y es el fichero con más PII de los
            # dos: se borra en cuanto termina el proceso, salga bien o mal.
            if not settings.store_original:
                job.input_path.unlink(missing_ok=True)

        if cancelled:
            # Lo borra quien lo estaba procesando, no quien pidió la parada: a
            # estas alturas ya nadie tiene abierto ninguno de estos ficheros.
            with self._lock:
                self._jobs.pop(job.id, None)
            self._remove_files(job)
            logger.info("job %s stopped and deleted on request", job.id)

    def report_notice(self, job_id: str, notice: notices.PageNotice) -> None:
        """Anota un aviso de página para que el cliente lo vea en su próxima consulta."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.notices_total += 1
            if len(job.page_notices) < MAX_NOTICES:
                job.page_notices.append(notice)

    def report_progress(self, job_id: str, done: int, total: int) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.pages_done = done
            job.pages_total = total

    # -- caducidad ------------------------------------------------------

    def _sweep_forever(self) -> None:
        while not self._stopping.wait(self._sweep_interval):
            try:
                self.sweep()
            except Exception:  # noqa: BLE001 — el limpiador nunca debe morir
                logger.exception("job sweep failed")

    def sweep(self) -> None:
        with self._lock:
            self._purge_expired_locked()

    def _purge_expired_locked(self) -> None:
        now = _now()
        for job in list(self._jobs.values()):
            if job.expires_at is None or now < job.expires_at:
                continue
            if job.status != "expired":
                self._remove_files(job)
                job.status = "expired"
                job.report = None
            # El registro se conserva un rato más para poder responder
            # "caducado" en vez de "no existe", que confunde al usuario.
            elif now >= job.expires_at + self._ttl:
                self._jobs.pop(job.id, None)

    @staticmethod
    def _remove_files(job: Job) -> None:
        shutil.rmtree(job.directory, ignore_errors=True)


job_store = JobStore()
