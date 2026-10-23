import re
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings
from app.core.errors import DocumentTooLargeError, FileValidationError


PII_FILENAME_PATTERNS = [
    re.compile(r"\b\d{8}[A-Z]\b", re.IGNORECASE),
    re.compile(r"\b[XYZ]\d{7}[A-Z]\b", re.IGNORECASE),
    re.compile(r"\b[A-HJNPQRSUVW]\d{7}[0-9A-J]\b", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?<!\d)(?:\+34[\s\-]?)?(?:6|7|8|9)\d{2}[\s\-]?\d{3}[\s\-]?\d{3}(?!\d)"),
    re.compile(r"\bES\d{2}(?:[\s\-]?\d{4}){5}\b", re.IGNORECASE),
]


class FileTooLargeError(DocumentTooLargeError):
    pass


async def save_upload_to_path(upload: UploadFile, destination: Path) -> int:
    max_bytes = settings.max_upload_mb * 1024 * 1024
    written = 0

    with destination.open("wb") as out:
        while True:
            chunk = await upload.read(1024 * 1024)

            if not chunk:
                break

            written += len(chunk)

            if written > max_bytes:
                raise FileTooLargeError(
                    f"File exceeds max size of {settings.max_upload_mb} MB"
                )

            out.write(chunk)

    return written


def assert_pdf_magic(path: Path) -> None:
    with path.open("rb") as f:
        header = f.read(5)

    if header != b"%PDF-":
        raise FileValidationError("Uploaded file is not a valid PDF.")


DOWNLOAD_STEM = "documento_anonimizado"
# Solo se quitaron metadatos: el nombre lo dice, porque el documento sigue
# llevando los datos personales que tuviera.
METADATA_ONLY_STEM = "documento_sin_metadatos"


def download_filename(extension: str, *, partial_pages: tuple[int, int] | None = None,
                      metadata_only: bool = False) -> str:
    """Nombre GENÉRICO con el que se sirve la descarga.

    El servidor no conoce el nombre del fichero que subió el usuario y no debe
    conocerlo: en el ayuntamiento los nombres de expediente suelen llevar el
    del interesado, y ese nombre acabaría en el informe y en el disco durante
    horas.  Quien descarga desde la interfaz le pone el nombre que tiene en
    memoria; quien llama a la API directamente recibe este.
    """
    if metadata_only:
        return f"{METADATA_ONLY_STEM}.{extension}"
    if partial_pages:
        hasta, total = partial_pages
        return f"{DOWNLOAD_STEM}_parcial_1-{hasta}_de_{total}.{extension}"
    return f"{DOWNLOAD_STEM}.{extension}"
