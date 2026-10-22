"""Clasificación de páginas de redacción TOTAL (copia de DNI, autoliquidación).

Dos tipos de documento se anonimizan enteros, sin análisis selectivo de
entidades: la copia escaneada de un documento de identidad y el impreso de
autoliquidación tributaria (modelos 046, 600, 620…).

La decisión es TEXTUAL, sobre el texto ya extraído — sirve igual para un PDF
digital y para el resultado del OCR — y nunca visual: un DNI fotocopiado y
pegado en un A4 no conserva proporciones de tarjeta, y detectar foto/MRZ por
visión es caro y frágil.  El texto de estos impresos, en cambio, es muy
característico y redundante.

Se puntúa por SEÑALES DISTINTAS y se exige un mínimo configurable
(settings.dni_scan_min_signals / tax_form_min_signals).  El modo de fallo es
caro — un falso positivo pinta una página entera —, así que ante cualquier FP
sobre documentación normal se sube el umbral antes que arriesgar.

Alcance: POR PÁGINA.  Los expedientes reales del ayuntamiento son PDFs
concatenados (un modelo 046 pegado a una notificación y a un plano), de modo
que clasificar el documento entero arrastraría a negro material ajeno.  La
propagación a páginas contiguas exige evidencia explícita: compartir número de
modelo o número de justificante/referencia con la página que sí clasificó.
"""
from __future__ import annotations

import re
import unicodedata

from app.core.config import settings

DNI_SCAN = "dni_scan"
TAX_FORM = "tax_form"


def _norm(text: str) -> str:
    """Mayúsculas sin acentos: el OCR degrada tildes con frecuencia."""
    folded = unicodedata.normalize("NFKD", text.upper())
    return "".join(c for c in folded if not unicodedata.combining(c))


# --- B1: copia de documento de identidad -----------------------------------
# Señales FUERTES: solo aparecen en un documento de identidad.  Se exige al
# menos una; sin ese ancla, tres señales débiles bastaban para pintar en negro
# un certificado de empadronamiento ("…, ESPAÑA" + "VALIDEZ del certificado").
# Anverso y reverso puntúan por vías distintas: el reverso no lleva el rótulo
# grande, pero sí MRZ y filiación de nacimiento.
_DNI_STRONG_SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("rotulo", re.compile(
        r"DOCUMENTO NACIONAL DE IDENTIDAD|DOCUMENT NACIONAL D.IDENTITAT")),
    # Zona MRZ: cadena de rellenos "<" o el prefijo de documento español.
    ("mrz", re.compile(r"IDESP|<{5,}")),
    ("soporte", re.compile(r"N[.º°O]?\s*SOPORTE|NUM\.?\s*SOPORTE")),
    ("nacionalidad", re.compile(r"NACIONALIDAD\s+ESP\b|NACIONALITAT\s+ESP\b")),
    ("nacimiento", re.compile(
        r"LUGAR DE NACIMIENTO|LLOC DE NAIXEMENT|\bHIJO/?A? DE\b|\bFILL/?A? DE\b")),
)

# Señales DÉBILES: corroboran, pero por sí solas aparecen en documentación
# administrativa corriente (una dirección acaba en "España"; un certificado
# habla de su "validez"; cualquier formulario pide "apellidos y nombre").
_DNI_WEAK_SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pais", re.compile(r"\bESPANA\b|\bESPANYA\b|REINO DE ESPANA")),
    ("validez", re.compile(r"\bVALIDEZ\b|\bVALIDESA\b|FECHA DE CADUCIDAD")),
    ("filiacion", re.compile(
        r"\bAPELLIDOS\b[\s\S]{0,120}?\bNOMBRE\b|\bCOGNOMS\b[\s\S]{0,120}?\bNOM\b")),
)

_DNI_SIGNALS = _DNI_STRONG_SIGNALS + _DNI_WEAK_SIGNALS

# --- B2: autoliquidación tributaria ----------------------------------------
# Se exigen DOS FAMILIAS distintas: una resolución o notificación normal puede
# CITAR una tasa o un impuesto ("liquidación del IBI por importe de …"), y esa
# no debe redactarse entera.  El impreso, en cambio, trae a la vez la naturaleza
# del documento y la estructura de la liquidación.
_TAX_FAMILY_A: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("autoliquidacion", re.compile(r"\bAUTOLIQUIDACI[OU]N?\b")),
    ("modelo", re.compile(r"\bMODELO?\s*0?\d{3}\b")),
    ("sujeto_pasivo", re.compile(r"SUJETO PASIVO|SUBJECTE PASSIU")),
    ("ejemplar", re.compile(
        r"EJEMPLAR PARA (?:LA ADMINISTRACI|EL INTERESADO|LA ENTIDAD)")),
)
_TAX_FAMILY_B: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("base", re.compile(r"BASE IMPONIBLE|BASE LIQUIDABLE")),
    ("cuota", re.compile(r"CUOTA (?:TRIBUTARIA|INTEGRA|LIQUIDA)|CUOTA A INGRESAR")),
    ("total", re.compile(r"TOTAL A INGRESAR|IMPORTE A INGRESAR|TOTAL A PAGAR")),
    ("devengo", re.compile(r"FECHA DE DEVENGO|\bDEVENGO\b")),
    ("tipo", re.compile(r"TIPO (?:APLICABLE|IMPOSITIVO)|TIPO DE GRAVAMEN")),
    ("hecho", re.compile(r"HECHO IMPONIBLE")),
)

# Referencias que identifican el impreso, para propagar a páginas contiguas.
_MODEL_REF_RE = re.compile(r"\bMODELO?\s*(0?\d{3})\b")
_DOC_REF_RE = re.compile(
    r"\b(\d{3}-\d{4}-\d{6,})\b"
    r"|N[.º°O]?\s*JUSTIFICANTE[:\s]*([A-Z0-9\-]{6,})"
)


def _dni_signals(norm_text: str) -> tuple[set[str], set[str]]:
    """(señales fuertes, señales débiles) presentes en el texto."""
    strong = {name for name, rx in _DNI_STRONG_SIGNALS if rx.search(norm_text)}
    weak = {name for name, rx in _DNI_WEAK_SIGNALS if rx.search(norm_text)}
    return strong, weak


def _tax_signals(norm_text: str) -> tuple[set[str], set[str]]:
    fam_a = {name for name, rx in _TAX_FAMILY_A if rx.search(norm_text)}
    fam_b = {name for name, rx in _TAX_FAMILY_B if rx.search(norm_text)}
    return fam_a, fam_b


def classify_page(text: str) -> str | None:
    """Devuelve DNI_SCAN, TAX_FORM o None para el texto de UNA página."""
    if not settings.full_redaction_enabled or not text or not text.strip():
        return None

    norm = _norm(text)

    # DNI: al menos una señal fuerte (ancla) y el total exigido por config.
    strong, weak = _dni_signals(norm)
    if strong and len(strong) + len(weak) >= settings.dni_scan_min_signals:
        return DNI_SCAN

    fam_a, fam_b = _tax_signals(norm)
    # "AUTOLIQUIDACIÓN" junto a "MODELO NNN" NO basta por sí solo.
    #
    # Nombrar el impreso no es ser el impreso: «en relación con el modelo 600
    # presentado, se le comunica que la autoliquidación ha sido revisada» lleva
    # las dos palabras en prosa corriente, y con esa regla una notificación
    # normal saldría ENTERA EN NEGRO.  Esa frase está en toda la correspondencia
    # tributaria.
    #
    # Lo que distingue al impreso de quien lo menciona es su ESTRUCTURA de
    # liquidación —base imponible, cuota, total a ingresar, devengo—, que ningún
    # texto corrido tiene.  Así que se exige también una señal de la familia B.
    if {"autoliquidacion", "modelo"} <= fam_a and fam_b:
        return TAX_FORM
    # En los demás casos, al menos una señal de CADA familia (naturaleza del
    # impreso + estructura de liquidación) y el total exigido por configuración.
    if fam_a and fam_b and len(fam_a) + len(fam_b) >= settings.tax_form_min_signals:
        return TAX_FORM
    return None


def form_references(text: str) -> set[str]:
    """Identificadores del impreso para la propagación: SOLO justificante o referencia.

    El número de modelo NO cuenta: una notificación que solo dice «en relación
    con el modelo 600 presentado…» compartiría `modelo:600` con la
    autoliquidación de al lado y saldría ENTERA EN NEGRO.  No es una fuga, es
    destruir contenido legítimo, y esa frase aparece en toda la correspondencia
    tributaria: cualquier resolución que mencione el impreso al que responde.

    Un número de modelo identifica un TIPO de impreso, no un expediente
    concreto; el justificante o la referencia sí identifican el documento.  La
    propagación necesita lo segundo; con una regla floja, propagar a todas las
    páginas de un documento multiplicaría el problema.
    """
    norm = _norm(text)
    refs: set[str] = set()
    for match in _DOC_REF_RE.finditer(norm):
        ref = match.group(1) or match.group(2)
        if ref:
            refs.add("ref:" + ref)
    return refs


def classify_pages(page_texts: list[str]) -> dict[int, str]:
    """Clasifica todas las páginas y propaga a contiguas del MISMO impreso.

    Propagación conservadora: una página vecina sin clasificar solo se arrastra
    si comparte una referencia explícita (número de modelo o de justificante)
    con la página clasificada.  Sin esa evidencia no se propaga — un expediente
    concatenado no puede arrastrar a negro la notificación siguiente.

    Las copias de DNI no propagan: cada cara puntúa por sí misma y no comparten
    referencia de impreso.
    """
    classified: dict[int, str] = {}
    for index, text in enumerate(page_texts):
        kind = classify_page(text)
        if kind:
            classified[index] = kind

    if not classified:
        return classified

    refs = [form_references(text) for text in page_texts]
    for index in sorted(i for i, kind in classified.items() if kind == TAX_FORM):
        for step in (-1, 1):
            neighbour = index + step
            while 0 <= neighbour < len(page_texts) and neighbour not in classified:
                if not (refs[index] & refs[neighbour]):
                    break
                classified[neighbour] = TAX_FORM
                neighbour += step
    return classified
