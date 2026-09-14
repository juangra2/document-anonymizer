"""Tipo de dato que anuncia una etiqueta de formulario o cabecera de tabla.

La redacción estructural tacha el valor que sigue a una etiqueta («Teléfono:
612…») sin analizarlo.  Para que `disabled_types` pueda liberarlo, cada rect
tiene que saber de qué tipo es, y ese tipo solo lo da la etiqueta.  Esta es la
única tabla etiqueta → tipo, compartida por el camino digital y el OCR.

Devuelve None cuando la etiqueta no identifica un tipo concreto («Entidad
bancaria», «Contacto», «Datos a anonimizar»): esos valores se siguen tachando
siempre, porque sin tipo no hay forma segura de liberarlos.

El orden de las reglas importa: lo específico va antes que lo genérico
(«Nombre de la vía» es una dirección, no una persona; «Cuenta del
beneficiario» es una cuenta, no un nombre).
"""
from __future__ import annotations

import re
import unicodedata

# Tipos de etiqueta cuyo valor NO se redacta nunca, aunque la etiqueta se
# reconozca: el número de colegiado se conserva (requisito del ayuntamiento).
NON_REDACTABLE_LABEL_TYPES: frozenset[str] = frozenset({"PROFESSIONAL_LICENSE"})

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (etype, re.compile(pattern))
    for etype, pattern in (
        ("PROFESSIONAL_LICENSE", r"colegiad|col ?legiat"),
        ("DATE_OF_BIRTH", r"nacimiento|naixement|\bf\s?n\b"),
        ("IBAN_CODE", r"\biban\b|\bcuenta\b|\bcompte\b|\bc/c\b|\bccc\b"),
        ("CADASTRAL_REFERENCE", r"catastral|cadastral"),
        ("POSTAL_CODE", r"codigo postal|codi postal|^cp$"),
        ("ES_NIE", r"\bnie\b"),
        ("ES_DNI", r"\bdni\b|\bnif\b|documento nacional|document nacional|identificacio|\bidentidad\b|\bidentitat\b"),
        ("ES_CIF", r"\bcif\b"),
        ("ES_NSS", r"\bnass\b|afiliaci|seguridad social|seguretat social|\bnaf\b"),
        ("PASSPORT", r"pasaporte|passaport"),
        ("EMAIL_ADDRESS", r"e-?mail|correo|correu|electronica\b"),
        ("PHONE_NUMBER", r"telefon|\bmovil\b|\bmobil\b|\btelf\b"),
        ("CREDIT_CARD", r"tarjeta|targeta"),
        ("USER_ID", r"usuario|usuari|user id"),
        ("IP_ADDRESS", r"\bip\b"),
        ("MEDICAL_ID", r"paciente id|identificador (?:medico|sanitari)|\bsip\b"),
        ("POLICY_NUMBER", r"poliza|polissa|seguro medico"),
        ("LICENSE_PLATE", r"matricula"),
        ("CASE_NUMBER", r"expedient|^caso$|^exp\b"),
        ("URL", r"\burl\b|\bweb\b"),
        ("ADDRESS", r"direccion|adreca|domicili|residencia|\bvia\b|municipi|poblacion|localidad|provincia"),
        ("PERSON", r"nombre|apellido|\bnom\b|cognom|^persona\b|ordenante|beneficiari|titular|"
                   r"solicitante|sollicitant|interesad|interessat|representant|trabajador|treballador|"
                   r"\bfirma\b|signatura"),
        ("DATE_TIME", r"^fecha\b|^data\b"),
    )
)


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("·", " ").replace("l.l", "l l")
    text = re.sub(r"[:;]+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" .")


def select_structural_rects(
    rects: list,
    types: list[str | None] | None,
    disabled_types: frozenset[str] | set[str],
) -> tuple[list, int]:
    """Rects estructurales que se pintan con esta configuración.

    * Colegiado (NON_REDACTABLE_LABEL_TYPES): no se pinta nunca.
    * Tipo desactivado: no se pinta.
    * Sin tipo: se pinta siempre, porque no hay forma segura de liberarlo.

    *types* va en paralelo a *rects*; si falta o es más corta (productor antiguo),
    los rects sin tipo cuentan como no tipados.

    Returns:
        (rects a pintar, cuántos de ellos no tienen tipo).
    """
    types = types or []
    kept: list = []
    untyped = 0
    for index, rect in enumerate(rects):
        etype = types[index] if index < len(types) else None
        if etype in NON_REDACTABLE_LABEL_TYPES:
            continue
        if etype is not None and etype in disabled_types:
            continue
        kept.append(rect)
        if etype is None:
            untyped += 1
    return kept, untyped


def structural_rects_released(
    types: list[str | None] | None,
    disabled_types: frozenset[str] | set[str],
) -> bool:
    """Si algún rect estructural se ha dejado de pintar por un tipo desactivado.

    El redactor lo usa para analizar la zona en ese caso: el rect liberado podía
    cubrir también datos de otros tipos.  El colegiado no cuenta (se conserva
    siempre, con o sin tipos desactivados, y no es motivo para reanalizar).
    """
    return any(
        etype is not None
        and etype not in NON_REDACTABLE_LABEL_TYPES
        and etype in disabled_types
        for etype in (types or [])
    )


def label_entity_type(label: str | None) -> str | None:
    """Tipo que anuncia *label*, o None si no identifica ninguno."""
    if not label:
        return None
    normalized = _normalize(label)
    if not normalized:
        return None
    for etype, pattern in _RULES:
        if pattern.search(normalized):
            return etype
    return None
