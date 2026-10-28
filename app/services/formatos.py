"""Que es cada fichero y por que camino va.  Un solo sitio para la cola y la ruta
sincrona: con dos, se arregla uno y el otro no.

El formato se decide por el contenido, no por la extension ni por el tipo que
declara el navegador.  Un Word o Excel antiguo (.doc, .xls: un contenedor OLE)
se rechaza con su propio codigo, para poder decir que hay que guardarlo en el
formato nuevo; uno con macros (.docm, .xlsm), tambien: no se procesa codigo.
"""
from __future__ import annotations

from pathlib import Path

from app.core.errors import FileValidationError, LegacyOfficeError, OfficeMacrosError

# formato -> (extension, tipo MIME)
FORMATOS = {
    "pdf": ("pdf", "application/pdf"),
    "jpeg": ("jpg", "image/jpeg"),
    "png": ("png", "image/png"),
    "tiff": ("tif", "image/tiff"),
    "docx": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "xlsx": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
}

_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def reconocer(ruta: Path) -> str:
    """pdf, jpeg, png, tiff, docx o xlsx.  Lanza el error que explica el rechazo."""
    from app.services.image.imagen import formato_de
    from app.services.office.paquete import tipo_office

    with open(ruta, "rb") as fh:
        cabecera = fh.read(8)
    formato = formato_de(cabecera)
    if formato:
        return formato
    if cabecera == _OLE:
        raise LegacyOfficeError("Old Word or Excel format (.doc, .xls): save it as .docx or .xlsx.")
    if cabecera[:4] == b"PK\x03\x04":
        tipo = tipo_office(ruta)
        if tipo in ("docx", "xlsx"):
            return tipo
        if tipo in ("docm", "xlsm"):
            raise OfficeMacrosError("Office document with macros: save it without macros.")
    raise FileValidationError("Uploaded file is not a PDF, JPEG, PNG, TIFF, Word or Excel document.")


def comprobar(ruta: Path, formato: str) -> None:
    """Antes de encolar: lo que se puede rechazar en milisegundos."""
    if formato == "pdf":
        from app.services.pdf.inspector import assert_pdf_within_limits

        assert_pdf_within_limits(ruta)
    elif formato in ("docx", "xlsx"):
        from app.services.office.paquete import Paquete

        Paquete.abrir(ruta)
    else:
        from app.services.image.imagen import comprobar_imagen

        comprobar_imagen(ruta)


def procesar(formato: str, tratamiento: str, entrada: Path, salida: Path, job_id: str, pipeline,
             mode: str, disabled: frozenset[str], include_entities: bool = False):
    """El informe del documento procesado por su camino."""
    if tratamiento == "metadata_only":
        if formato == "pdf":
            from app.services.pdf.solo_metadatos import quitar_metadatos
            return quitar_metadatos(entrada, salida, job_id)
        if formato == "docx":
            from app.services.office.docx import solo_metadatos_docx
            return solo_metadatos_docx(entrada, salida, job_id)
        if formato == "xlsx":
            from app.services.office.xlsx import solo_metadatos_xlsx
            return solo_metadatos_xlsx(entrada, salida, job_id)
        from app.services.image.imagen import solo_metadatos_imagen
        return solo_metadatos_imagen(entrada, salida, formato, job_id)

    from app.services.image.imagen import anonimizar_imagen

    def _pdf(e: Path, s: Path):
        return pipeline().process(e, s, job_id, mode, include_entities, disabled)

    if formato == "pdf":
        return _pdf(entrada, salida)
    if formato in ("docx", "xlsx"):
        from app.services.office.docx import anonimizar_docx
        from app.services.office.xlsx import anonimizar_xlsx

        base = pipeline()._analyzer
        analyzer = base.with_disabled_types(disabled) if disabled else base
        anonimizar = anonimizar_docx if formato == "docx" else anonimizar_xlsx
        return anonimizar(entrada, salida, analyzer,
                          lambda e, s, f: anonimizar_imagen(e, s, f, _pdf), job_id, mode)
    return anonimizar_imagen(entrada, salida, formato, _pdf)
