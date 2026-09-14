"""Plan de redacción de una página: el único punto de salida (bloque 6).

Antes, cada productor —analizador, redacción estructural, zonas de QR y firma,
celda-nombre aislada, heurísticas del cajetín— pintaba y contaba por su lado, y
el filtro de `disabled_types` solo vivía en el analizador.  Así aparecieron
tres acoplamientos (F5, F10, F11) y un recuento que dependía de
include_entities (O3).

Ahora todo productor da de alta en el plan lo que quiere tachar, con su tipo, y
**nadie pinta ni cuenta directamente**.  El plan:

* aplica en un único sitio el filtro de tipos desactivados y la regla del
  colegiado (que no se tacha nunca);
* respeta excepciones explícitas y con nombre (`always=True`): la redacción de
  página completa y el QR cuyo contenido lleva un tipo obligatorio;
* deduplica siempre, así que los rects que se pintan y las entidades que se
  cuentan salen de la misma lista, pida o no la petición la lista de entidades.

Es agnóstico a la geometría: sirve igual para puntos de PDF que para píxeles.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import fitz

from app.services.pii.label_types import NON_REDACTABLE_LABEL_TYPES
from app.services.pii.models import PiiEntity


def _same_rect(a: fitz.Rect, b: fitz.Rect, tolerance: float = 0.5) -> bool:
    return (
        abs(a.x0 - b.x0) <= tolerance and abs(a.y0 - b.y0) <= tolerance
        and abs(a.x1 - b.x1) <= tolerance and abs(a.y1 - b.y1) <= tolerance
    )


@dataclass
class RedactionPlan:
    """Lo que se tacha y se cuenta en una página, filtrado en un único sitio."""

    disabled_types: frozenset[str] = field(default_factory=frozenset)
    _rects: list[fitz.Rect] = field(default_factory=list)
    _entities: list[PiiEntity] = field(default_factory=list)
    _entity_keys: set[tuple[int, int, str]] = field(default_factory=set)
    untyped_structural: int = 0

    def admits(self, entity_type: str | None, *, always: bool = False) -> bool:
        """Si un tachado de *entity_type* entra en el plan."""
        if entity_type in NON_REDACTABLE_LABEL_TYPES:
            return False
        if always or entity_type is None:
            return True
        return entity_type not in self.disabled_types

    def add_rect(
        self,
        rect: fitz.Rect,
        entity_type: str | None,
        *,
        structural: bool = False,
        always: bool = False,
    ) -> bool:
        """Da de alta un tachado sin entidad asociada (estructural, firma, sello)."""
        if not self.admits(entity_type, always=always):
            return False
        if not any(_same_rect(rect, existing) for existing in self._rects):
            self._rects.append(fitz.Rect(rect))
        if structural and entity_type is None:
            self.untyped_structural += 1
        return True

    def add_entity(
        self,
        entity: PiiEntity,
        rects: list[fitz.Rect] | None = None,
        *,
        always: bool = False,
    ) -> bool:
        """Da de alta una entidad y, si los hay, los rects que la tachan.

        Una entidad sin rects se cuenta pero no pinta nada (p. ej. el valor de
        un formulario ya cubierto por su rect estructural).
        """
        if not self.admits(entity.entity_type, always=always):
            return False
        for rect in rects or []:
            if not any(_same_rect(rect, existing) for existing in self._rects):
                self._rects.append(fitz.Rect(rect))
        key = (entity.start, entity.end, entity.entity_type)
        if key not in self._entity_keys:
            self._entity_keys.add(key)
            self._entities.append(entity)
        return True

    @property
    def rects(self) -> list[fitz.Rect]:
        return list(self._rects)

    @property
    def entities(self) -> list[PiiEntity]:
        return list(self._entities)
