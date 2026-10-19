"""Normalización de `disabled_types` y `enabled_types` antes de que FastAPI lo valide.

El parámetro se tipa con el Enum de tipos para que Swagger ofrezca un selector
y un valor inexistente se rechace con 422.  Pero esa validación ocurre antes de
entrar en la ruta, así que cualquier normalización hecha dentro llegaba tarde:
`date_time` o `DATE_TIME ` se rechazaban, y `?disabled_types=` —lo que envía
un cliente que serializa una lista vacía— daba 422 en vez de equivaler a no
enviar nada.

Este middleware reescribe solo ese parámetro de la query antes del enrutado:

* quita espacios al principio y al final;
* pasa a mayúsculas;
* descarta los valores vacíos (una lista vacía equivale a omitir el parámetro).

Lo que sigue rechazándose con 422, y sin procesar nada: un tipo inexistente
(lo rechaza el Enum) y un tipo obligatorio (lo rechaza la ruta).
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode

PARAMS = ("disabled_types", "enabled_types")


def normalize_type_id(value: str) -> str:
    """Forma canónica de un identificador de tipo recibido por query."""
    return value.strip().upper()


def normalize_query_string(query_string: bytes) -> bytes:
    """Devuelve la query con `disabled_types` normalizado; el resto, intacto."""
    if not any(p.encode() in query_string for p in PARAMS):
        return query_string
    pairs = parse_qsl(query_string.decode("latin-1"), keep_blank_values=True)
    out: list[tuple[str, str]] = []
    for key, value in pairs:
        if key not in PARAMS:
            out.append((key, value))
            continue
        canonical = normalize_type_id(value)
        if canonical:
            out.append((key, canonical))
    return urlencode(out).encode("latin-1")


class DisabledTypesQueryMiddleware:
    """Middleware ASGI: normaliza `disabled_types` en peticiones HTTP."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope.get("query_string"):
            normalized = normalize_query_string(scope["query_string"])
            if normalized != scope["query_string"]:
                scope = dict(scope, query_string=normalized)
        await self.app(scope, receive, send)
