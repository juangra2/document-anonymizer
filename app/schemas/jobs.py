"""Esquemas del trabajo asíncrono de anonimización.

Este es el contrato que consume el frontend: cambiar nombres o forma aquí
obliga a tocarlo también, así que se añaden campos antes que renombrarlos.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class JobProgress(BaseModel):
    """Avance por páginas.  La interfaz muestra «página N de M», no un giro."""

    pages_done: int = Field(default=0, description="Páginas ya procesadas.")
    pages_total: int = Field(
        default=0,
        description="Páginas del documento. Vale 0 hasta que se abre el fichero.",
    )
    percent: int = Field(default=0, ge=0, le=100, description="Avance en tanto por ciento.")


class JobError(BaseModel):
    """Motivo del fallo.

    `code` es estable y es lo que el frontend traduce a una frase para el
    usuario; `message` es técnico y sirve para el registro y el soporte.
    """

    code: str = Field(examples=["encrypted_pdf"])
    message: str


class JobResultSummary(BaseModel):
    """Resumen de lo hecho, para pintar la pantalla de resultado."""

    status: str = Field(
        description="Resultado del documento: completed, completed_with_warnings, "
                    "manual_review_required o failed.",
    )
    pages: int = 0
    entities_detected: int = 0
    entities_redacted: int = 0
    manual_review_required: bool = False
    warnings: list[str] = Field(default_factory=list)
    entity_types_disabled: list[str] = Field(default_factory=list)
    processing_ms: int | None = None


class JobResponse(BaseModel):
    """Estado completo de un trabajo."""

    job_id: str
    status: Literal["queued", "running", "completed", "failed", "expired"]

    stopping: bool = Field(
        default=False,
        description="Se pidió parar este documento y todavía está soltando la "
                    "página en curso. El estado sigue siendo `running` hasta "
                    "que termina; a partir de ahí el trabajo ya no existe.",
    )

    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    expires_at: datetime | None = Field(
        default=None,
        description="Momento en que el resultado se borra del servidor. "
                    "Nulo mientras el trabajo no ha terminado.",
    )

    queue_position: int | None = Field(
        default=None,
        description="Documentos por delante en la cola. 0 = es el siguiente. "
                    "Nulo si ya no está esperando.",
    )

    progress: JobProgress = Field(default_factory=JobProgress)
    result: JobResultSummary | None = None
    error: JobError | None = None
