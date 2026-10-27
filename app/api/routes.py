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
from app.api.query_normalization import normalize_type_id
from app.schemas.entity_types import EntityType, EntityTypeResponse
from app.schemas.report import InputFileInfo
from app.services.jobs import job_store
from app.services.pii.entity_catalog import (
    entity_catalog,
    default_disabled_ids,
    known_type_ids,
)
from app.services.pdf.pipeline import PdfAnonymizationPipeline
from app.services.storage.temp_storage import TempJobDirectory
from app.utils.files import (
    download_filename,
    save_upload_to_path,
)

from app.services.formatos import FORMATOS, comprobar, procesar, reconocer


router = APIRouter()

# Los pipelines se construyen con el PRIMER documento, no al importar el módulo.
# Construirlos aquí cargaba spaCy y el OCR en el arranque, y si faltaba un
# modelo el servicio no llegaba a arrancar: en vez de levantarse y decir «no
# estoy listo» —que es lo que mira el contenedor y lo que pinta en rojo el
# indicador de la interfaz—, se moría, y desde fuera no se distinguía de un
# servidor apagado.
_pdf_pipeline: PdfAnonymizationPipeline | None = None


def get_pdf_pipeline() -> PdfAnonymizationPipeline:
    global _pdf_pipeline
    if _pdf_pipeline is None:
        _pdf_pipeline = PdfAnonymizationPipeline()
    return _pdf_pipeline


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


def _validar_tipos(raw: list[EntityType] | None) -> frozenset[str]:
    """Normaliza y valida una lista de tipos recibida por query.

    Por HTTP el valor ya llega normalizado (DisabledTypesQueryMiddleware) y el
    Enum ya ha rechazado lo inexistente; la comprobación se repite aquí para las
    llamadas internas que no pasan por HTTP.
    """
    if not raw:
        return frozenset()
    requested = {
        normalize_type_id(item.value if isinstance(item, EntityType) else str(item))
        for item in raw
    }
    requested.discard("")
    unknown = sorted(requested - known_type_ids())
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Tipos de dato desconocidos: {', '.join(unknown)}. "
                "Consulte GET /entity-types para ver los identificadores validos."
            ),
        )
    return frozenset(requested)


def _parse_disabled_types(
    raw: list[EntityType] | None,
    enabled: list[EntityType] | None = None,
) -> frozenset[str]:
    """Tipos que NO se anonimizan en esta petición.

    Se parte del catálogo, el mismo valor por defecto que ve la interfaz: los
    tipos con `default_enabled` falso quedan fuera salvo que lleguen en
    `enabled_types`.  `disabled_types` apaga cualquier tipo.  Un tipo en las dos
    listas es una contradicción y se rechaza con 422.
    """
    apagar = _validar_tipos(raw)
    encender = _validar_tipos(enabled)
    ambos = sorted(apagar & encender)
    if ambos:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Tipos a la vez en enabled_types y disabled_types: {', '.join(ambos)}."
            ),
        )
    return frozenset((default_disabled_ids() - encender) | apagar)


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
            "Tipos de dato que NO se anonimizan en esta peticion. Cada valor va en su "
            "propio parametro: ?disabled_types=DATE_TIME&disabled_types=URL. "
            "Se normaliza antes de validar: se quitan los espacios sobrantes, se "
            "pasa a mayúsculas (date_time equivale a DATE_TIME) y se ignoran los "
            "valores vacíos, de modo que una lista vacía (?disabled_types=) equivale "
            "a no enviar el parametro. Se rechaza con 422, sin procesar nada, un "
            "tipo inexistente. No se admite separar varios valores con comas."
        ),
    ),
    enabled_types: list[EntityType] | None = Query(
        default=None,
        description=(
            "Tipos que vienen apagados en el catalogo (default_enabled falso) y "
            "SI se anonimizan en esta peticion: ?enabled_types=CASE_NUMBER. Sin "
            "este parametro se aplica el catalogo, igual que en la interfaz. Se "
            "normaliza como disabled_types; un tipo en las dos listas da 422."
        ),
    ),
) -> Response:
    # Único modo de redacción; el parámetro se retiró de la API (un query param
    # extra como ?mode=... se ignora, comportamiento estándar de FastAPI).
    mode = settings.default_redaction_mode

    disabled = _parse_disabled_types(disabled_types, enabled_types)

    supported_content_types = {
        "application/pdf",
        "application/octet-stream",
        "image/png",
        "image/jpeg",
        "image/tiff",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
        "application/zip",
    }

    if file.content_type not in supported_content_types:
        raise HTTPException(
            status_code=415,
            detail="Only PDF, PNG, JPG/JPEG and TIFF files are supported.",
        )

    try:
        with TempJobDirectory() as temp_job:
            assert temp_job.path is not None

            # El formato se decide por el contenido, no por la extension ni por
            # el tipo que declara el navegador.
            subida = Path(temp_job.path) / "subida"
            size_bytes = await save_upload_to_path(file, subida)
            formato = reconocer(subida)
            extension = FORMATOS[formato][0]
            input_path = subida.rename(Path(temp_job.path) / f"input.{extension}")
            output_path = Path(temp_job.path) / f"output_redacted.{extension}"
            comprobar(input_path, formato)

            # Esta ruta no pasa por la cola, pero sí compite por la misma GPU:
            # pide uno de los permisos de proceso concurrente para que dos
            # peticiones a la vez se serialicen en vez de agotar la memoria de
            # la tarjeta.  Se pide dentro del hilo de trabajo, no aquí, para no
            # bloquear el bucle de eventos mientras espera turno.
            def _run() -> object:
                with job_store.slot():
                    return procesar(formato, "anonymize", input_path, output_path,
                                    temp_job.job_id, get_pdf_pipeline, mode, disabled,
                                    include_entities)

            report = await run_in_threadpool(_run)

            output_bytes = output_path.read_bytes()

        if include_entities:
            report.security_checks.report_contains_pii_values = True

        report.output_format = output
        report.redaction_mode = mode
        report.input = InputFileInfo(
            content_type=file.content_type,
            size_bytes=size_bytes,
        )

        pdf_filename = download_filename(extension)

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
            zip_filename = download_filename("zip")

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
            media_type=FORMATOS[formato][1],
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