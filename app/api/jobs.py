"""Anonimización asíncrona: se sube el documento y se consulta por su id.

Solo acepta PDF.  Las imágenes sueltas siguen atendidas por `POST /anonymize`,
que procesa en la propia petición; la interfaz del ayuntamiento trabaja con
expedientes en PDF.

No hay endpoint que liste trabajos, y es a propósito: el servicio no lleva
control de acceso, así que un listado dejaría a cualquiera ver —y descargar—
los documentos de los demás.  Sin él, hace falta conocer el identificador.
"""
from __future__ import annotations

import zipfile
from io import BytesIO
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.errors import DocumentTooLargeError, FileValidationError
from app.schemas.entity_types import EntityType
from app.schemas.jobs import (
    JobError,
    JobNotice,
    JobProgress,
    JobResponse,
    JobResultSummary,
)
from app.schemas.report import InputFileInfo, ProcessingReport
from app.services.jobs import Job, QueueFullError, job_store
from app.utils.files import (
    assert_pdf_magic,
    download_filename,
    save_upload_to_path,
)

router = APIRouter(prefix="/jobs", tags=["trabajos"])


_PDF_CONTENT_TYPES = {"application/pdf", "application/octet-stream"}


def _percent(done: int, total: int) -> int:
    if total <= 0:
        return 0
    return max(0, min(100, round(100 * done / total)))


def _to_response(job: Job) -> JobResponse:
    result = None
    if job.status == "completed" and job.report is not None:
        result = JobResultSummary(
            status=job.report.status,
            pages=job.report.pages,
            entities_detected=job.report.entities_detected,
            entities_redacted=job.report.entities_redacted,
            manual_review_required=job.report.manual_review_required,
            warnings=job.report.warnings,
            entity_types_disabled=job.report.entity_types_disabled,
            processing_ms=job.report.processing_ms,
        )

    error = None
    if job.error_code:
        error = JobError(code=job.error_code, message=job.error_message or "")

    return JobResponse(
        job_id=job.id,
        status=job.status,
        stopping=job.cancel_requested and job.status == "running",
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        expires_at=job.expires_at,
        queue_position=job_store.queue_position(job),
        progress=JobProgress(
            pages_done=job.pages_done,
            pages_total=job.pages_total,
            percent=_percent(job.pages_done, job.pages_total),
        ),
        notices=[
            JobNotice(page=n.page, kind=n.kind, reason=n.reason)
            for n in job.page_notices
        ],
        notices_total=job.notices_total,
        result=result,
        error=error,
    )


def _require_job(job_id: str) -> Job:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Trabajo desconocido o ya borrado.")
    return job


def _require_finished_report(job: Job) -> ProcessingReport:
    """Comprueba que hay resultado descargable y lo devuelve."""
    if job.status == "expired":
        raise HTTPException(
            status_code=410,
            detail="El resultado ha caducado y se ha borrado del servidor.",
        )
    if job.status == "failed":
        raise HTTPException(
            status_code=409,
            detail=f"El trabajo fallo ({job.error_code}): {job.error_message}",
        )
    if job.status != "completed" or job.report is None:
        raise HTTPException(
            status_code=409,
            detail=f"El trabajo aun no ha terminado (estado: {job.status}).",
        )
    return job.report


@router.post("", status_code=202, response_model=JobResponse)
async def create_job(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    disabled_types: list[EntityType] | None = Query(
        default=None,
        description=(
            "Tipos de dato que NO se anonimizan en esta peticion. Cada valor va en su "
            "propio parametro: ?disabled_types=DATE_TIME&disabled_types=URL. "
            "Se normaliza antes de validar: se quitan los espacios sobrantes, se "
            "pasa a mayúsculas (date_time equivale a DATE_TIME) y se ignoran los "
            "valores vacíos, de modo que una lista vacía (?disabled_types=) equivale "
            "a no enviar el parametro. Se rechaza con 422, sin procesar nada, un "
            "tipo inexistente. No se admite separar varios valores con comas."
        ),
    ),
) -> JobResponse:
    """Encola un PDF para anonimizar y devuelve el trabajo con su identificador.

    Responde 202 al terminar la subida, no al terminar el proceso.  La cabecera
    `Location` apunta al estado, que hay que consultar hasta que pase a
    `completed` o `failed`.
    """
    # Se importa aqui para no crear una dependencia circular entre los dos
    # modulos de rutas; la validacion de tipos es la misma en ambos caminos.
    from app.api.routes import _parse_disabled_types

    if file.content_type not in _PDF_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="Solo se admiten ficheros PDF.")

    disabled = _parse_disabled_types(disabled_types)

    try:
        # El nombre del fichero NO se guarda: suele llevar el del interesado.
        job = job_store.create(content_type=file.content_type)
    except QueueFullError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
            headers={"Retry-After": "60"},
        ) from exc

    try:
        size_bytes = await save_upload_to_path(file, job.input_path)
        assert_pdf_magic(job.input_path)
    except DocumentTooLargeError as exc:
        job_store.discard(job)
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except FileValidationError as exc:
        job_store.discard(job)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        job_store.discard(job)
        raise

    job_store.enqueue(
        job,
        size_bytes=size_bytes,
        disabled_types=disabled,
    )

    # Ruta relativa al prefijo servido: tras un proxy inverso, una URL absoluta
    # construida aqui apuntaria al host interno.
    root = request.scope.get("root_path", "") or settings.root_path
    response.headers["Location"] = f"{root}/jobs/{job.id}"
    return _to_response(job)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str) -> JobResponse:
    """Estado del trabajo: cola, avance por páginas, resultado o error."""
    return _to_response(_require_job(job_id))


@router.get("/{job_id}/result")
async def get_job_result(
    job_id: str,
    output: Literal["pdf", "zip"] = Query(
        default="pdf",
        description="pdf: solo el documento. zip: documento e informe.",
    ),
) -> Response:
    """Descarga el documento anonimizado."""
    job = _require_job(job_id)
    report = _require_finished_report(job)

    pdf_filename = download_filename("pdf")
    headers = {
        "X-Job-Id": job.id,
        "X-Anonymization-Status": report.status,
        "X-Entities-Redacted": str(report.entities_redacted),
    }

    if output == "zip":
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(job.output_path, arcname=pdf_filename)
            zf.writestr("report.json", report.model_dump_json(indent=2) + "\n")
        zip_filename = download_filename("zip")
        return Response(
            content=buffer.getvalue(),
            media_type="application/zip",
            headers={
                **headers,
                "Content-Disposition": f'attachment; filename="{zip_filename}"',
            },
        )

    # Se envía en flujo desde el disco: con expedientes de cientos de páginas el
    # PDF de salida pesa decenas de MB y cargarlo entero en memoria por cada
    # descarga no aporta nada.
    return FileResponse(
        path=job.output_path,
        media_type="application/pdf",
        filename=pdf_filename,
        headers=headers,
    )


@router.get("/{job_id}/report", response_model=ProcessingReport)
async def get_job_report(job_id: str) -> ProcessingReport:
    """Informe completo del proceso, en JSON."""
    job = _require_job(job_id)
    report = _require_finished_report(job)
    report.input = InputFileInfo(
        content_type=job.content_type,
        size_bytes=job.size_bytes,
    )
    return report


@router.delete(
    "/{job_id}",
    status_code=204,
    responses={
        202: {"description": "Se está procesando: se ha pedido la parada y "
                             "terminará de soltar la página en curso."},
        204: {"description": "Parado y borrado."},
        404: {"description": "Trabajo desconocido o ya borrado."},
    },
)
async def delete_job(job_id: str) -> Response:
    """Para el trabajo y borra sus ficheros sin esperar a que caduquen.

    En cola o terminado el borrado es inmediato (204).  Si se está procesando
    no se puede cortar a mitad de página: se responde 202, el estado pasa a
    `stopping` y el trabajo desaparece en cuanto suelte la página que tiene
    entre manos —como mucho, el presupuesto por página—.  Pedirlo dos veces
    responde lo mismo la segunda vez.
    """
    outcome = job_store.delete(job_id)
    if outcome == "missing":
        raise HTTPException(status_code=404, detail="Trabajo desconocido o ya borrado.")
    return Response(status_code=202 if outcome == "stopping" else 204)
