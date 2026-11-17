"""Que es cada fichero y por que camino va.  Un solo sitio para la cola y la ruta
sincrona: con dos, se arregla uno y el otro no.

El formato se decide por el contenido, no por la extension ni por el tipo que
declara el navegador.  El texto es la excepcion, porque no lleva marca: un
fichero que no es ninguno de los otros formatos y se llama .txt, .md o
.markdown se trata como texto o Markdown si se lee como texto (sin bytes
nulos, en alguna codificacion); si no, se rechaza.  Del nombre solo se mira
la extension: no se guarda.  Un Word o Excel antiguo (.doc, .xls: un contenedor OLE)
se rechaza con su propio codigo, para poder decir que hay que guardarlo en el
formato nuevo; uno con macros (.docm, .xlsm), tambien: no se procesa codigo.
"""
from __future__ import annotations

from pathlib import Path

from app.core.errors import (
    FileValidationError,
    LegacyOfficeError,
    OfficeMacrosError,
    PseudonymizeUnsupportedError,
)

# formato -> (extension, tipo MIME)
FORMATOS = {
    "pdf": ("pdf", "application/pdf"),
    "jpeg": ("jpg", "image/jpeg"),
    "png": ("png", "image/png"),
    "tiff": ("tif", "image/tiff"),
    "docx": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "xlsx": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "txt": ("txt", "text/plain"),
    "md": ("md", "text/markdown"),
    "csv": ("csv", "text/csv"),
}
_TEXTO = {".txt": "txt", ".md": "md", ".markdown": "md", ".csv": "csv"}
#: Lo que se puede seudonimizar: donde el dato se sustituye por texto.
SEUDONIMIZABLES = ("docx", "md", "txt")


def comprobar_seudonimizable(formato: str) -> None:
    if formato not in SEUDONIMIZABLES:
        raise PseudonymizeUnsupportedError(
            "Only Word (.docx), Markdown (.md) and text (.txt) documents can be pseudonymized.")

_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def reconocer(ruta: Path, nombre: str | None = None) -> str:
    """pdf, jpeg, png, tiff, docx, xlsx, txt o md.  Lanza el error que explica el
    rechazo.  *nombre*, el del fichero subido: solo cuenta su extension."""
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
    texto = _TEXTO.get(Path(nombre or "").suffix.lower())
    if texto:
        from app.services.texto.codificacion import leer

        leer(ruta.read_bytes())         # FileValidationError si no es texto
        return texto
    raise FileValidationError(
        "Uploaded file is not a PDF, JPEG, PNG, TIFF, Word, Excel, text or Markdown document.")


def comprobar(ruta: Path, formato: str) -> None:
    """Antes de encolar: lo que se puede rechazar en milisegundos."""
    if formato == "pdf":
        from app.services.pdf.inspector import assert_pdf_within_limits

        assert_pdf_within_limits(ruta)
    elif formato in ("docx", "xlsx"):
        from app.services.office.paquete import Paquete, comprobar_tamano

        comprobar_tamano(Paquete.abrir(ruta))
    elif formato in ("txt", "md"):
        return                          # ya se leyo al reconocerlo
    elif formato == "csv":
        from app.services.texto.tabla_csv import comprobar_csv

        comprobar_csv(ruta)
    else:
        from app.services.image.imagen import comprobar_imagen

        comprobar_imagen(ruta)


def procesar(formato: str, tratamiento: str, entrada: Path, salida: Path, job_id: str, pipeline,
             mode: str, disabled: frozenset[str], include_entities: bool = False, *,
             cifrador=None, tabla: Path | None = None, sin_https: bool = False):
    """El informe del documento procesado por su camino.

    Seudonimizar (`pseudonymize`): como anonimizar un Word o un texto, pero con
    etiquetas numeradas; la tabla sale cifrada con *cifrador* en *tabla*, y en
    claro no se escribe en ningun sitio."""
    asignador = None
    if tratamiento == "pseudonymize":
        from app.services.seudonimo.etiquetas import Asignador

        comprobar_seudonimizable(formato)
        if cifrador is None or tabla is None:
            raise ValueError("pseudonymize needs the key and the table path")
        asignador = Asignador()
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
        if formato in ("txt", "md", "csv"):
            from app.services.texto.texto import solo_metadatos_texto
            return solo_metadatos_texto(entrada, salida, job_id, formato)
        from app.services.image.imagen import solo_metadatos_imagen
        return solo_metadatos_imagen(entrada, salida, formato, job_id)

    from app.services.image.imagen import anonimizar_imagen

    def _pdf(e: Path, s: Path):
        return pipeline().process(e, s, job_id, mode, include_entities, disabled)

    if formato == "pdf":
        return _pdf(entrada, salida)
    if formato in ("docx", "xlsx", "txt", "md", "csv"):
        from app.services.office.docx import anonimizar_docx
        from app.services.office.xlsx import anonimizar_xlsx

        base = pipeline()._analyzer
        analyzer = base.with_disabled_types(disabled) if disabled else base
        from app.services.layout.zone_config import ALL_PII_TYPES

        if formato in ("txt", "md"):
            from app.services.texto.texto import anonimizar_texto

            informe = anonimizar_texto(entrada, salida, analyzer, job_id, mode, formato, asignador)
        elif formato == "csv":
            from app.services.texto.tabla_csv import anonimizar_csv

            informe = anonimizar_csv(entrada, salida, analyzer, job_id, mode)
        elif formato == "docx":
            informe = anonimizar_docx(entrada, salida, analyzer,
                                      lambda e, s, f: anonimizar_imagen(e, s, f, _pdf), job_id, mode,
                                      asignador)
        else:
            informe = anonimizar_xlsx(entrada, salida, analyzer,
                                      lambda e, s, f: anonimizar_imagen(e, s, f, _pdf), job_id, mode)
        if asignador is not None:
            from app.schemas.report import (
                PASSWORD_WITHOUT_HTTPS,
                PSEUDONYM_UNRECOVERABLE,
                PSEUDONYMIZED_NOT_ANONYMOUS,
                CodedWarning,
            )

            tabla.write_bytes(cifrador.cifrar(asignador.tabla(formato, salida.read_bytes())))
            informe.pseudonym_labels = len(asignador.etiquetas)
            informe.warnings.append("Pseudonymized, NOT anonymized: with the table the data can be "
                                    "restored, so the document still contains personal data.")
            informe.coded_warnings.append(CodedWarning(code=PSEUDONYMIZED_NOT_ANONYMOUS))
            perdidos = informe.pseudonym_unrecoverable
            if perdidos:
                informe.warnings.append(
                    f"{sum(perdidos.values())} personal data items carry their type marker instead of a "
                    "label (content controls, shapes, images): they cannot be restored with the table.")
                informe.coded_warnings.append(CodedWarning(code=PSEUDONYM_UNRECOVERABLE,
                                                           types=sorted(perdidos), counts=dict(perdidos)))
            if sin_https:
                informe.warnings.append("The table password was sent without HTTPS.")
                informe.coded_warnings.append(CodedWarning(code=PASSWORD_WITHOUT_HTTPS))
            if informe.status == "completed":
                informe.status = "completed_with_warnings"
        informe.entity_types_applied = sorted(t for t in ALL_PII_TYPES if t not in disabled)
        informe.entity_types_disabled = sorted(disabled)
        return informe
    return anonimizar_imagen(entrada, salida, formato, _pdf)
