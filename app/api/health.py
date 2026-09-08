"""Estado del servicio: GPU, modelos y carga de la cola.

Existe para dos lectores distintos:

* **La interfaz**, que enseña un indicador arriba.  Antes de subir un
  expediente de cien páginas conviene saber que la GPU está y que los modelos
  están, no descubrirlo cuando el trabajo falla veinte minutos después.
* **Quien opera el servidor**, que necesita comprobarlo sin nosotros.  En el
  ayuntamiento puede no haber salida a internet: que falte un modelo tiene que
  verse aquí, no en un registro.

`/health` responde 200 siempre mientras el proceso viva —es señal de vida— y
dice en `status` si el servicio está entero.  `/health/ready` responde 503
cuando no lo está, que es lo que sabe interpretar un `HEALTHCHECK` de Docker o
un proxy inverso.

Ninguna comprobación carga un modelo: se miran ficheros y se pregunta a CUDA.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Response

from app.core.config import settings
from app.services.jobs import job_store

router = APIRouter()


_EASYOCR_WEIGHTS = ("craft_mlt_25k.pth", "latin_g2.pth")


@lru_cache(maxsize=1)
def _gpu_state() -> dict[str, Any]:
    """Estado de la GPU.  Se consulta una vez: no cambia en caliente."""
    state: dict[str, Any] = {
        "requested": settings.ocr_use_gpu or settings.ner_use_gpu,
        "available": False,
        "devices": 0,
        "name": None,
        "cuda": None,
        "torch": None,
    }
    try:
        import torch
    except Exception:
        state["detail"] = "torch no esta instalado"
        return state

    state["torch"] = torch.__version__
    state["cuda"] = torch.version.cuda
    try:
        if torch.cuda.is_available():
            state["available"] = True
            state["devices"] = torch.cuda.device_count()
            state["name"] = torch.cuda.get_device_name(0)
    except Exception as exc:  # noqa: BLE001 — la salud nunca debe reventar
        state["detail"] = str(exc)
    return state


def _easyocr_dir() -> Path:
    if settings.easyocr_model_path:
        return Path(settings.easyocr_model_path)
    return Path.home() / ".EasyOCR" / "model"


def _models_state() -> dict[str, Any]:
    mineru_path = Path(settings.ocr_mineru_model_path)
    easyocr_dir = _easyocr_dir()

    try:
        import spacy.util

        spacy_present = spacy.util.is_package(settings.ner_spacy_model)
    except Exception:
        spacy_present = False

    return {
        "mineru": {
            "path": str(mineru_path),
            "present": mineru_path.is_dir() and any(mineru_path.iterdir()),
        },
        "easyocr": {
            "path": str(easyocr_dir),
            "present": all((easyocr_dir / name).is_file() for name in _EASYOCR_WEIGHTS),
            "expected": list(_EASYOCR_WEIGHTS),
            "download_allowed": settings.easyocr_allow_download,
        },
        "spacy": {
            "name": settings.ner_spacy_model,
            "present": spacy_present,
        },
    }


def _snapshot() -> dict[str, Any]:
    gpu = _gpu_state()
    models = _models_state()
    issues: list[str] = []

    if gpu["requested"] and not gpu["available"]:
        # No es un fallo: el servicio funciona en CPU, solo que mucho mas
        # despacio.  Se avisa para que nadie lo descubra por el reloj.
        issues.append("Se pidio GPU pero CUDA no esta disponible; se procesara en CPU.")

    if not models["spacy"]["present"]:
        issues.append(
            f"Falta el modelo de spaCy {settings.ner_spacy_model}: no se detectaran nombres."
        )
    if not models["easyocr"]["present"]:
        detail = "" if settings.easyocr_allow_download else " y la descarga esta desactivada"
        issues.append(f"Faltan los pesos de EasyOCR en {models['easyocr']['path']}{detail}.")
    if not models["mineru"]["present"]:
        issues.append(
            f"Falta el modelo de MinerU en {models['mineru']['path']}: "
            "el OCR usara solo el motor de respaldo."
        )

    # Que no haya GPU degrada el rendimiento, no el resultado; que falte spaCy o
    # el OCR de respaldo si cambia lo que sale del sistema.
    broken = not models["spacy"]["present"] or not models["easyocr"]["present"]

    return {
        "status": "degraded" if broken or issues else "ok",
        "ready": not broken,
        "app": settings.app_name,
        "environment": settings.environment,
        "gpu": gpu,
        "models": models,
        "queue": {**job_store.stats(), "queue_max": settings.job_queue_max},
        "limits": {
            "max_upload_mb": settings.max_upload_mb,
            "max_pdf_pages": settings.max_pdf_pages,
            "result_ttl_minutes": settings.result_ttl_minutes,
        },
        "issues": issues,
    }


@router.get("/health")
def health() -> dict[str, Any]:
    """Señal de vida con el detalle de GPU, modelos y cola."""
    return _snapshot()


@router.get("/health/ready")
def ready(response: Response) -> dict[str, Any]:
    """Como `/health`, pero responde 503 si al servicio le falta algo esencial."""
    snapshot = _snapshot()
    if not snapshot["ready"]:
        response.status_code = 503
    return snapshot
