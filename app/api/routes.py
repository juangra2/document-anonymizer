import json
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.errors import (
    AnonymizerError,
    DocumentTooLargeError,
    EmptyPdfError,
    EncryptedPdfError,
    FileValidationError,
    ScannedPdfNotSupportedError,
    TooManyPagesError,
    UnsupportedDocumentError,
)
from app.schemas.entity_types import EntityType, EntityTypeResponse
from app.schemas.report import InputFileInfo
from app.services.jobs import job_store
from app.services.pii.entity_catalog import (
    entity_catalog,
    known_type_ids,
    mandatory_type_ids,
)
from app.services.pdf.pipeline import PdfAnonymizationPipeline
from app.services.storage.temp_storage import TempJobDirectory
from app.utils.files import (
    assert_pdf_magic,
    build_download_filename,
    sanitize_original_filename,
    save_upload_to_path,
)

from app.services.image.pipeline import ImageAnonymizationPipeline


router = APIRouter()

pdf_pipeline = PdfAnonymizationPipeline()
image_pipeline = ImageAnonymizationPipeline()


def report_to_json_bytes(report) -> bytes:
    return (report.model_dump_json(indent=2) + "\n").encode("utf-8")


def build_zip_response_bytes(
    pdf_bytes: bytes,
    pdf_filename: str,
    report_json_bytes: bytes,
) -> bytes:
    zip_buffer = BytesIO()

    with zipfile.ZipFile(
        zip_buffer,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
    ) as zip_file:
        zip_file.writestr(pdf_filename, pdf_bytes)
        zip_file.writestr("report.json", report_json_bytes)

    return zip_buffer.getvalue()


def _parse_disabled_types(raw: list[EntityType] | None) -> frozenset[str]:
    """Normaliza los tipos desactivados que llegan por query.

    La validacion la hace ya el tipado con Enum: FastAPI devuelve 422 con la
    lista de valores admitidos antes de entrar aqui.  Se conserva la
    comprobacion como red de seguridad para llamadas internas que no pasen por
    la capa HTTP.
    """
    if not raw:
        return frozenset()

    requested = {
        (item.value if isinstance(item, EntityType) else str(item)).strip().upper()
        for item in raw
    }
    unknown = sorted(requested - known_type_ids())
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Tipos de dato desconocidos: {', '.join(unknown)}. "
                "Consulte GET /entity-types para ver los identificadores validos."
            ),
        )

    # Los tipos obligatorios se rechazan aqui, no se ignoran en silencio: una
    # peticion que cree haber desactivado el DNI debe enterarse de que no es
    # posible, en vez de recibir un documento distinto del que pidio.
    forbidden = sorted(requested & mandatory_type_ids())
    if forbidden:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Tipos de dato obligatorios, no se pueden desactivar: "
                f"{', '.join(forbidden)}. Identifican directamente a una "
                "persona fisica. GET /entity-types marca cuales lo son."
            ),
        )
    return frozenset(requested)


@router.get("/entity-types", response_model=list[EntityTypeResponse])
async def list_entity_types() -> list[EntityTypeResponse]:
    """Tipos de dato que el sistema puede anonimizar.

    Devuelve, para cada tipo: su identificador tecnico, un nombre legible en
    castellano, una descripcion breve de que captura, si esta activo por
    defecto y si es obligatorio.  El frontend pinta con esto la lista de
    casillas —las obligatorias, marcadas y bloqueadas— y envia los que el
    usuario desmarque en `disabled_types` de POST /anonymize.
    """
    return [
        EntityTypeResponse(
            id=info.id,
            label=info.label,
            description=info.description,
            default_enabled=info.default_enabled,
            mandatory=info.mandatory,
        )
        for info in entity_catalog()
    ]


@router.post("/anonymize")
async def anonymize(
    file: UploadFile = File(...),
    output: Literal["pdf", "zip"] = Query(
        default="pdf",
        description="Response format: pdf or zip. zip includes anonymized PDF and report.json.",
    ),
    include_entities: bool = Query(
        default=False,
        description=(
            "Include detected entity list (text + type) in report.json. "
            "Intended for evaluation and audit. Disabled by default for privacy."
        ),
    ),
    disabled_types: list[EntityType] | None = Query(
        default=None,
        description=(
            "Tipos de dato que NO se anonimizan en esta peticion. Si se omite, "
            "se aplican todos. Cada valor va en su propio parametro: "
            "?disabled_types=DATE_TIME&disabled_types=URL. El nombre legible de "
            "cada tipo esta en GET /entity-types."
        ),
    ),
) -> Response:
    # Único modo de redacción; el parámetro se retiró de la API (un query param
    # extra como ?mode=... se ignora, comportamiento estándar de FastAPI).
    mode = settings.default_redaction_mode

    disabled = _parse_disabled_types(disabled_types)

    supported_content_types = {
        "application/pdf",
        "application/octet-stream",
        "image/png",
        "image/jpeg",
        "image/tiff",
    }

    if file.content_type not in supported_content_types:
        raise HTTPException(
            status_code=415,
            detail="Only PDF, PNG, JPG/JPEG and TIFF files are supported.",
        )

    try:
        with TempJobDirectory() as temp_job:
            assert temp_job.path is not None

            suffix = Path(file.filename or "").suffix.lower()

            if file.content_type == "image/png" or suffix == ".png":
                input_path = Path(temp_job.path) / "input.png"
            elif file.content_type == "image/jpeg" or suffix in {".jpg", ".jpeg"}:
                input_path = Path(temp_job.path) / "input.jpg"
            elif file.content_type == "image/tiff" or suffix in {".tif", ".tiff"}:
                input_path = Path(temp_job.path) / "input.tiff"
            else:
                input_path = Path(temp_job.path) / "input.pdf"

            output_path = Path(temp_job.path) / "output_redacted.pdf"

            size_bytes = await save_upload_to_path(file, input_path)

            is_image = file.content_type in {
                "image/png",
                "image/jpeg",
                "image/tiff",
            } or suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}

            # Esta ruta no pasa por la cola, pero sí compite por la misma GPU:
            # pide uno de los permisos de proceso concurrente para que dos
            # peticiones a la vez se serialicen en vez de agotar la memoria de
            # la tarjeta.  Se pide dentro del hilo de trabajo, no aquí, para no
            # bloquear el bucle de eventos mientras espera turno.
            if is_image:
                def _run() -> object:
                    with job_store.slot():
                        return image_pipeline.process(
                            input_path, output_path, temp_job.job_id, mode, disabled,
                        )
            else:
                assert_pdf_magic(input_path)

                def _run() -> object:
                    with job_store.slot():
                        return pdf_pipeline.process(
                            input_path, output_path, temp_job.job_id, mode,
                            include_entities, disabled,
                        )

            report = await run_in_threadpool(_run)

            output_bytes = output_path.read_bytes()

        if include_entities:
            report.security_checks.report_contains_pii_values = True

        report.output_format = output
        report.redaction_mode = mode
        report.input = InputFileInfo(
            filename_sanitized=sanitize_original_filename(file.filename),
            content_type=file.content_type,
            size_bytes=size_bytes,
        )

        pdf_filename = build_download_filename(
            file.filename,
            suffix="_anonymized",
            extension="pdf",
        )

        report_json_bytes = report_to_json_bytes(report)

        common_headers = {
            "X-Job-Id": report.job_id,
            "X-Anonymization-Status": report.status,
            "X-Entities-Detected": str(report.entities_detected),
            "X-Entities-Redacted": str(report.entities_redacted),
            "X-Residual-Entities": str(report.residual_entities),
            "X-Manual-Review-Required": str(report.manual_review_required).lower(),
            "X-Redaction-Mode": report.redaction_mode,
            "X-Output-Format": report.output_format,
        }

        if output == "zip":
            zip_filename = build_download_filename(
                file.filename,
                suffix="_anonymization_result",
                extension="zip",
            )

            zip_bytes = build_zip_response_bytes(
                pdf_bytes=output_bytes,
                pdf_filename=pdf_filename,
                report_json_bytes=report_json_bytes,
            )

            headers = {
                **common_headers,
                "Content-Disposition": f'attachment; filename="{zip_filename}"',
            }

            return Response(
                content=zip_bytes,
                media_type="application/zip",
                headers=headers,
            )

        headers = {
            **common_headers,
            "Content-Disposition": f'attachment; filename="{pdf_filename}"',
        }

        return Response(
            content=output_bytes,
            media_type="application/pdf",
            headers=headers,
        )

    except DocumentTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    except FileValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    except EncryptedPdfError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    except EmptyPdfError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    except TooManyPagesError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    except ScannedPdfNotSupportedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    except UnsupportedDocumentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    except AnonymizerError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="Failed to serialize report.") from exc

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc