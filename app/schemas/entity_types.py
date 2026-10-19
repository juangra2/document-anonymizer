"""Esquema de `GET /entity-types` — catálogo de tipos anonimizables."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.services.layout.zone_config import ALL_PII_TYPES


# Enum construido desde la lista canonica de tipos redactables: Swagger lo
# renderiza como selector con los valores validos en vez de un texto libre,
# y una peticion con un tipo inexistente se rechaza antes de llegar al codigo.
EntityType = Enum(  # type: ignore[misc]
    "EntityType",
    {name: name for name in ALL_PII_TYPES},
    type=str,
)


class EntityTypeResponse(BaseModel):
    """Un tipo de dato que el sistema puede anonimizar."""

    id: str = Field(
        description="Identificador técnico. Es el valor que se envía en "
                    "`disabled_types` de POST /anonymize.",
        examples=["IBAN_CODE"],
    )
    label: str = Field(
        description="Nombre legible en castellano, para mostrar al usuario.",
        examples=["Cuenta bancaria"],
    )
    description: str = Field(
        description="Qué captura este tipo, en lenguaje no técnico.",
        examples=["Cuentas IBAN y cuentas antiguas de veinte dígitos."],
    )
    default_enabled: bool = Field(
        default=True,
        description=(
            "Si el tipo se anonimiza cuando la peticion no dice nada de el. Es el "
            "mismo valor por defecto en la interfaz y en la API; los apagados se "
            "encienden con enabled_types."
        ),
    )
