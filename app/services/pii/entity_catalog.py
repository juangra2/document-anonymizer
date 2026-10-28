"""Catálogo de tipos de PII: identificador técnico, nombre legible y descripción.

Lo consume `GET /entity-types`, que el frontend usa para pintar la lista de
casillas con la que el personal del ayuntamiento decide qué se anonimiza.  Los
textos están en castellano y sin jerga: quien los lee no es técnico.

La fuente de verdad de QUÉ se redacta sigue siendo `ALL_PII_TYPES`
(layout/zone_config.py); aquí solo se le pone nombre y explicación.  Un tipo
presente en `ALL_PII_TYPES` y ausente de este catálogo se expone igualmente,
con su identificador como nombre, para que nunca quede invisible en la interfaz.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.layout.zone_config import ALL_PII_TYPES


@dataclass(frozen=True)
class EntityTypeInfo:
    id: str
    label: str
    description: str
    default_enabled: bool = True


# Orden pensado para la interfaz: primero lo que el usuario reconoce al vuelo.
_CATALOG: tuple[EntityTypeInfo, ...] = (
    EntityTypeInfo("PERSON", "Nombre de persona",
                   "Nombres y apellidos de personas físicas, incluidos los que "
                   "acompañan a una firma («Fdo.: …»)."),
    EntityTypeInfo("ES_DNI", "DNI / NIF",
                   "Documento nacional de identidad, con o sin puntos "
                   "(12345678A, 47.215.601-M)."),
    EntityTypeInfo("ES_NIE", "NIE",
                   "Número de identidad de extranjero (X, Y o Z seguidos de "
                   "siete cifras y letra)."),
    EntityTypeInfo("ES_CIF", "CIF de entidad",
                   "Código de identificación fiscal de empresas y entidades."),
    EntityTypeInfo("ES_NSS", "Número de la Seguridad Social",
                   "Número de afiliación a la Seguridad Social."),
    EntityTypeInfo("PASSPORT", "Pasaporte", "Número de pasaporte."),
    EntityTypeInfo("ORGANIZATION", "Nombre de empresa",
                   "Razón social de empresa privada. Las instituciones "
                   "públicas mencionadas no se anonimizan."),
    EntityTypeInfo("ADDRESS", "Dirección postal",
                   "Domicilios completos: tipo de vía, número, piso y "
                   "municipio."),
    EntityTypeInfo("POSTAL_CODE", "Código postal",
                   "Código postal de cinco cifras."),
    EntityTypeInfo("PHONE_NUMBER", "Teléfono",
                   "Teléfonos fijos y móviles, en formato español o "
                   "internacional."),
    EntityTypeInfo("EMAIL_ADDRESS", "Correo electrónico",
                   "Direcciones de correo electrónico."),
    # Cubre también la cuenta antigua (CCC) de veinte dígitos: no son dos tipos
    # distintos en el detector, así que marcar este obligatorio protege ambas.
    EntityTypeInfo("IBAN_CODE", "Cuenta bancaria",
                   "Cuentas IBAN y cuentas antiguas de veinte dígitos (CCC)."),
    EntityTypeInfo("MONEY", "Importe económico",
                   "Importes ligados a una persona o a una cuenta: nóminas, "
                   "transferencias, liquidaciones."),
    EntityTypeInfo("BUDGET_AMOUNT", "Importes de presupuesto de obra",
                   "Precios, mediciones y totales de un presupuesto de obra "
                   "(partidas, capítulos, ejecución material). Viene "
                   "desmarcado: márcalo si el presupuesto también se tiene "
                   "que tachar.",
                   default_enabled=False),
    EntityTypeInfo("CREDIT_CARD", "Tarjeta bancaria",
                   "Números de tarjeta de crédito o débito."),
    EntityTypeInfo("DATE_OF_BIRTH", "Fecha de nacimiento",
                   "Fecha de nacimiento de una persona."),
    EntityTypeInfo("PLACE_OF_BIRTH", "Lugar de nacimiento",
                   "Provincia, municipio o lugar de nacimiento, solo en un campo "
                   "etiquetado como tal. Fuera de esa etiqueta los lugares no se "
                   "tachan."),
    EntityTypeInfo("DATE_TIME", "Fecha",
                   "Cualquier fecha del documento, incluidas las de registro y "
                   "firma."),
    EntityTypeInfo("LICENSE_PLATE", "Matrícula de vehículo",
                   "Matrículas españolas de vehículo."),
    EntityTypeInfo("CADASTRAL_REFERENCE", "Referencia catastral",
                   "Referencia catastral de un inmueble."),
    # Decision del ayuntamiento (22-10-2026): se tacha por defecto, en todos sus
    # formatos; si se desmarca, no se tacha ninguno.
    EntityTypeInfo("CASE_NUMBER", "Número de expediente",
                   "Referencias de expediente administrativo, también las "
                   "municipales (E-03001-2022-000074-00)."),
    EntityTypeInfo("REGISTRY_NUMBER", "Número de registro",
                   "Número de registro de entrada o salida del documento."),
    EntityTypeInfo("CSV", "Código seguro de verificación",
                   "Código que permite verificar el documento en la sede "
                   "electrónica."),
    EntityTypeInfo("CERT_SERIAL", "Número de serie de certificado",
                   "Número de serie del certificado de firma electrónica."),
    EntityTypeInfo("MEDICAL_ID", "Identificador sanitario",
                   "Número de historia clínica o tarjeta sanitaria."),
    EntityTypeInfo("POLICY_NUMBER", "Número de póliza",
                   "Número de póliza de seguro."),
    EntityTypeInfo("USER_ID", "Identificador de usuario",
                   "Nombres de usuario y identificadores internos."),
    EntityTypeInfo("IP_ADDRESS", "Dirección IP",
                   "Direcciones IP registradas en el documento."),
    EntityTypeInfo("URL", "Dirección web",
                   "Enlaces y direcciones de páginas web."),
    EntityTypeInfo("CASE_TABLE_ROW", "Fila de tabla de expediente",
                   "Filas de tabla que combinan expediente e interesado."),
    EntityTypeInfo("ROLE_TABLE_ROW", "Fila de tabla de cargos",
                   "Filas de tabla que asocian un cargo con una persona."),
    EntityTypeInfo("QR_CODE", "Código QR o de barras",
                   "Códigos QR y códigos de barras. Si el contenido del código "
                   "lleva dentro un dato de un tipo que sigue activo (por "
                   "ejemplo un DNI, con DNI marcado), se oculta aunque este "
                   "tipo esté desmarcado. Los códigos que no se pueden leer se "
                   "ocultan siempre."),
)

_BY_ID = {info.id: info for info in _CATALOG}


def entity_catalog() -> list[EntityTypeInfo]:
    """Catálogo completo, en el orden pensado para la interfaz.

    Incluye los tipos redactables que no estén descritos explícitamente, para
    que la interfaz nunca oculte algo que el pipeline sí anonimiza.
    """
    described = [info for info in _CATALOG if info.id in set(ALL_PII_TYPES)]
    described_ids = {info.id for info in described}
    extra = [
        EntityTypeInfo(t, t, "Tipo detectado por el sistema.")
        for t in ALL_PII_TYPES
        if t not in described_ids
    ]
    return described + extra


def known_type_ids() -> set[str]:
    """Identificadores válidos que el cliente puede enviar."""
    return {info.id for info in entity_catalog()}




def default_disabled_ids() -> frozenset[str]:
    """Tipos que no se anonimizan si la petición no dice nada de ellos.

    Es el único valor por defecto: lo usan la interfaz (casillas desmarcadas al
    empezar) y la API (sin `enabled_types`, estos tipos quedan fuera).
    """
    return frozenset(info.id for info in entity_catalog() if not info.default_enabled)
