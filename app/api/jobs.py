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

from app.core.config import settings
from app.core.errors import DocumentTooLargeError, FileValidationError
from app.schemas.entity_types import EntityType
from app.schemas.jobs import JobError, JobProgress, JobResponse, JobResultSummary
from app.schemas.report import InputFileInfo, ProcessingReport
from app.services.jobs import Job, QueueFullError, job_store
from app.utils.files import (
    assert_pdf_magic,
    build_download_filename,
    sanitize_original_filename,
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
    include_entities: bool = Query(
        default=False,
        description="Incluir la lista de entidades detectadas en el informe. "
                    "Para evaluacion y auditoria; desactivado por defecto.",
    ),
    disabled_types: list[EntityType] | None = Query(
        default=None,
        description="Tipos de dato que NO se anonimizan. Cada valor en su propio "
                    "parametro. Los marcados como obligatorios en "
                    "GET /entity-types se rechazan con 422.",
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
        job = job_store.create(
            filename=sanitize_original_filename(file.filename) or "document.pdf",
            content_type=file.content_type,
        )
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
        include_entities=include_entities,
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

    pdf_bytes = job.output_path.read_bytes()
    pdf_filename = build_download_filename(
        job.filename, suffix="_anonymized", extension="pdf",
    )
    headers = {
        "X-Job-Id": job.id,
        "X-Anonymization-Status": report.status,
        "X-Entities-Redacted": str(report.entities_redacted),
    }

    if output == "zip":
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(pdf_filename, pdf_bytes)
            zf.writestr("report.json", report.model_dump_json(indent=2) + "\n")
        zip_filename = build_download_filename(
            job.filename, suffix="_anonymization_result", extension="zip",
        )
        return Response(
            content=buffer.getvalue(),
            media_type="application/zip",
            headers={
                **headers,
                "Content-Disposition": f'attachment; filename="{zip_filename}"',
            },
        )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            **headers,
            "Content-Disposition": f'attachment; filename="{pdf_filename}"',
        },
    )


@router.get("/{job_id}/report", response_model=ProcessingReport)
async def get_job_report(job_id: str) -> ProcessingReport:
    """Informe completo del proceso, en JSON."""
    job = _require_job(job_id)
    report = _require_finished_report(job)
    report.input = InputFileInfo(
        filename_sanitized=job.filename,
        content_type=job.content_type,
        size_bytes=job.size_bytes,
    )
    return report


@router.delete("/{job_id}", status_code=204)
async def delete_job(job_id: str) -> Response:
    """Borra el trabajo y sus ficheros sin esperar a que caduquen."""
    if not job_store.delete(job_id):
        raise HTTPException(status_code=404, detail="Trabajo desconocido o ya borrado.")
    return Response(status_code=204)
