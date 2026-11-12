"""Recuperar los datos: un texto con etiquetas, su tabla y la contrasena.

`POST /reconstruct` trabaja en la propia peticion y no guarda nada: ni el
texto, ni la tabla, ni la contrasena, ni el resultado.  Lo que llega se lee en
memoria (un Word, en una carpeta temporal que se borra al terminar) y la
respuesta lleva el texto o el fichero reconstruido y los avisos.  Los avisos
llevan etiquetas y formas, nunca valores; la contrasena va en el cuerpo, nunca
en la URL.

Solo existe si el servicio tiene la seudonimizacion activada.
"""
from __future__ import annotations

import base64
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app.api.jobs import exigir_seudonimizacion
from app.core.config import settings
from app.core.errors import (
    AnonymizerError,
    FileValidationError,
    PseudonymizeUnsupportedError,
    TablaIlegibleError,
    error_code_for,
)
from app.services.formatos import FORMATOS, comprobar_seudonimizable, reconocer

router = APIRouter(tags=["seudonimizacion"])

_MAX_TABLA = 50 * 1024 * 1024


async def _leer(fichero: UploadFile, maximo: int) -> bytes:
    datos = await fichero.read(maximo + 1)
    if len(datos) > maximo:
        raise HTTPException(status_code=413, detail={"code": "document_too_large",
                                                     "message": "The file is too large."})
    return datos


def _reconstruir(tabla_cifrada: bytes, contrasena: str, texto: str | None, datos: bytes | None,
                 nombre: str | None) -> dict:
    from app.services.seudonimo.reconstruir import reconstruir_fichero, reconstruir_texto
    from app.services.seudonimo.tabla import abrir_tabla

    tabla = abrir_tabla(tabla_cifrada, contrasena)
    if datos is None:
        r = reconstruir_texto(texto or "", tabla)
        return {**r.resumen(), "text": r.texto, "file_base64": None, "filename": None, "media_type": None}
    with tempfile.TemporaryDirectory(dir=settings.temp_root if Path(settings.temp_root).is_dir() else None) as tmp:
        ruta = Path(tmp) / "subida"
        ruta.write_bytes(datos)
        formato = reconocer(ruta, nombre)
    comprobar_seudonimizable(formato)
    r = reconstruir_fichero(datos, formato, tabla)
    extension, tipo = FORMATOS[formato]
    return {**r.resumen(), "text": r.texto,
            "file_base64": base64.b64encode(r.datos).decode("ascii"),
            "filename": f"documento_reconstruido.{extension}", "media_type": tipo}


@router.post("/reconstruct")
async def reconstruct(
    table: UploadFile = File(..., description="La tabla cifrada (.tabla) que se descargo al seudonimizar."),
    password: str = Form(..., description="La contrasena con la que se cifro la tabla."),
    text: str | None = Form(default=None, description="El texto con etiquetas, pegado."),
    file: UploadFile | None = File(default=None, description="O un fichero con etiquetas: .txt, .md o .docx."),
) -> dict:
    """Devuelve los valores reales a un texto con etiquetas.

    Con el documento seudonimizado tal cual salio, la reconstruccion es exacta
    (`exact`).  Con cualquier otro texto (el resumen de un modelo de lenguaje),
    es tolerante: `altered` lista las etiquetas que se han reconocido con otra
    forma, `unresolved` las que no estan en la tabla (se dejan como estan) y
    `unused` las de la tabla que no aparecen."""
    exigir_seudonimizacion()
    tabla_cifrada = await _leer(table, _MAX_TABLA)
    datos = await _leer(file, settings.max_upload_mb * 1024 * 1024) if file is not None else None
    if datos is None and not (text or "").strip():
        raise HTTPException(status_code=422, detail={
            "code": "nothing_to_reconstruct", "message": "Send the labelled text or a file."})
    try:
        return await run_in_threadpool(_reconstruir, tabla_cifrada, password, text, datos,
                                       file.filename if file is not None else None)
    except TablaIlegibleError as exc:
        raise HTTPException(status_code=400, detail={"code": error_code_for(exc), "message": str(exc)}) from exc
    except PseudonymizeUnsupportedError as exc:
        raise HTTPException(status_code=415, detail={"code": error_code_for(exc), "message": str(exc)}) from exc
    except FileValidationError as exc:
        raise HTTPException(status_code=400, detail={"code": error_code_for(exc), "message": str(exc)}) from exc
    except AnonymizerError as exc:
        raise HTTPException(status_code=422, detail={"code": error_code_for(exc), "message": str(exc)}) from exc
