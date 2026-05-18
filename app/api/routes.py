import json
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

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
from app.schemas.report import InputFileInfo
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


@router.post("/anonymize")
async def anonymize(
    file: UploadFile = File(...),
    output: Literal["pdf", "zip"] = Query(
        default="pdf",
        description="Response format: pdf or zip. zip includes anonymized PDF and report.json.",
    ),
    mode: Literal["strict", "balanced"] = Query(
        default="strict",
        description="Redaction mode. strict redacts more aggressively; balanced redacts less.",
    ),
) -> Response:
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

            if is_image:
                report = await run_in_threadpool(
                    image_pipeline.process,
                    input_path,
                    output_path,
                    temp_job.job_id,
                    mode,
                )
            else:
                assert_pdf_magic(input_path)

                report = await run_in_threadpool(
                    pdf_pipeline.process,
                    input_path,
                    output_path,
                    temp_job.job_id,
                    mode,
                )

            output_bytes = output_path.read_bytes()

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