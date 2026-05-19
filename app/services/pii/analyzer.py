import os

os.environ.setdefault("PRESIDIO_DEVICE", "cpu")

import re
from collections import defaultdict
from typing import Literal

# Compiled once at import: institutional name root-words used to detect
# document-issuing organisations that are context entities, not PII subjects.
# Heuristic: any NER span containing these roots is a public institution name,
# not personal data under RGPD Art. 4.
# LEARNABLE: expand this list from a labelled corpus; or replace with a
# NER model fine-tuned on Spanish administrative documents.
_INSTITUTION_NAME_RE = re.compile(
    r"(?i)\b(?:"
    r"universitat|universidad|universidade"
    r"|ajuntament|ayuntamiento"
    r"|institut(?:o|ució|ución)?"
    r"|escola\b|escuela"
    r"|departament\b|departamento"
    r"|generalitat|govern\b|gobierno"
    r"|diputaci[oó]|consell(?:eria)?|consej(?:ería|eria)"
    r"|patronat|patronato"
    r"|fundaci[oó]"
    r"|hospital|cl[ií]nica"
    r"|enginyeria|ingenier[ií]a"
    r"|t[eè]cnica?\b"
    r")\b"
)

from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider

from app.core.config import settings
from app.services.pii.models import PiiEntity
from app.services.pii.recognizers_es import build_spanish_pattern_recognizers
from app.services.pii.validators_es import (
    is_valid_cif,
    is_valid_dni,
    is_valid_nie,
    is_valid_spanish_iban,
    looks_like_cadastral_reference,
)


SUPPORTED_LANGUAGES = ["es"]
RedactionMode = Literal["strict", "balanced"]


LABELED_FIELD_PATTERNS: list[tuple[str, str]] = [
    (
        "PERSON",
        r"(?im)^[^\S\r\n]*(?:solicitante|interesado|titular|promotor(?:a)?|propietario(?:a)?|representante|arquitecto(?:a)?|t[eé]cnico(?:a)?|m[eé]dico[^\S\r\n]+responsable|doctor(?:a)?|nombre(?:[^\S\r\n]+completo)?|nombre(?:[^\S\r\n]+y[^\S\r\n]+apellidos)?|apellidos|persona)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "DATE_OF_BIRTH",
        r"(?im)^[^\S\r\n]*(?:fecha[^\S\r\n]+de[^\S\r\n]+nacimiento|fecha[^\S\r\n]+nacimiento|fecha[^\S\r\n]+nac\.?)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "ES_DNI",
        r"(?im)^[^\S\r\n]*(?:dni|nif|dni/nif|nif/dni|documento[^\S\r\n]+nacional|documento[^\S\r\n]+de[^\S\r\n]+identidad)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "ES_NIE",
        r"(?im)^[^\S\r\n]*(?:nie|nif/nie|nie/nif|nie(?:[^\S\r\n]+representante)?)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "ES_CIF",
        r"(?im)^[^\S\r\n]*(?:cif(?:[^\S\r\n]+empresa(?:[^\S\r\n]+constructora)?)?)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "PASSPORT",
        r"(?im)^[^\S\r\n]*(?:pasaporte)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "EMAIL_ADDRESS",
        r"(?im)^[^\S\r\n]*(?:email(?:[^\S\r\n]+personal|[^\S\r\n]+contacto)?|e-mail|correo(?:[^\S\r\n]+personal)?|correo[^\S\r\n]+electr[oó]nico)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "PHONE_NUMBER",
        r"(?im)^[^\S\r\n]*(?:tel[eé]fono(?:[^\S\r\n]+m[oó]vil|[^\S\r\n]+alternativo|[^\S\r\n]+contacto)?|telefono(?:[^\S\r\n]+movil)?|telf\.?|m[oó]vil)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "IBAN_CODE",
        r"(?im)^[^\S\r\n]*(?:iban|cuenta[^\S\r\n]+bancaria|n[uú]mero[^\S\r\n]+de[^\S\r\n]+cuenta)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "ADDRESS",
        r"(?im)^[^\S\r\n]*(?:direcci[oó]n(?:[^\S\r\n]+postal|[^\S\r\n]+de[^\S\r\n]+notificaci[oó]n)?|domicilio|domicilio[^\S\r\n]+fiscal)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "CASE_NUMBER",
        r"(?im)^[^\S\r\n]*(?:expediente(?:[^\S\r\n]+municipal)?|n[uú]mero[^\S\r\n]+de[^\S\r\n]+expediente|exp\.?|id[^\S\r\n]+caso)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "PROFESSIONAL_LICENSE",
        r"(?im)^[^\S\r\n]*(?:n[ºo][^\S\r\n]+colegiado|colegiado|n[uú]mero[^\S\r\n]+de[^\S\r\n]+colegiado)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "LICENSE_PLATE",
        r"(?im)^[^\S\r\n]*(?:matr[ií]cula(?:[^\S\r\n]+veh[ií]culo(?:[^\S\r\n]+visita(?:[^\S\r\n]+obra)?)?)?)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "URL",
        r"(?im)^[^\S\r\n]*(?:web|url(?:[^\S\r\n]+documentaci[oó]n)?)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "CADASTRAL_REFERENCE",
        r"(?im)^[^\S\r\n]*(?:referencia[^\S\r\n]+catastral|ref\.[^\S\r\n]+catastral|catastral)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "CREDIT_CARD",
        r"(?im)^[^\S\r\n]*(?:tarjeta(?:[^\S\r\n]+bancaria)?(?:[^\S\r\n]+de[^\S\r\n]+prueba)?|tarjeta[^\S\r\n]+de[^\S\r\n]+prueba)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "USER_ID",
        r"(?im)^[^\S\r\n]*(?:usuario(?:[^\S\r\n]+interno)?|user[^\S\r\n]+id|identificador[^\S\r\n]+usuario)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "IP_ADDRESS",
        r"(?im)^[^\S\r\n]*(?:ip(?:[^\S\r\n]+(?:de[^\S\r\n]+)?[uú]ltima[^\S\r\n]+conexi[oó]n)?|ip[^\S\r\n]+reservada(?:[^\S\r\n]+para[^\S\r\n]+documentaci[oó]n)?)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "MEDICAL_ID",
        r"(?im)^[^\S\r\n]*(?:paciente[^\S\r\n]+id|identificador[^\S\r\n]+m[eé]dico|identificador[^\S\r\n]+sanitario(?:[^\S\r\n]+ficticio)?)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "POLICY_NUMBER",
        r"(?im)^[^\S\r\n]*(?:p[oó]liza|seguro[^\S\r\n]+m[eé]dico)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
]


# -----------------------------------------------------------------------
# Valencian / Catalan labeled-field patterns (colon form)
# -----------------------------------------------------------------------
LABELED_FIELD_PATTERNS_VAL_CAT: list[tuple[str, str]] = [
    (
        "PERSON",
        r"(?im)^[^\S\r\n]*(?:nom(?:[^\S\r\n]+complet|[^\S\r\n]+i[^\S\r\n]+cognoms)?|cognoms|sollicitant|titular)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "DATE_OF_BIRTH",
        r"(?im)^[^\S\r\n]*(?:data[^\S\r\n]+de[^\S\r\n]+naixement|data[^\S\r\n]+naixement)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "ES_DNI",
        r"(?im)^[^\S\r\n]*(?:dni|nif|n[uú]mero[^\S\r\n]+d'identificaci[oó](?:[^\S\r\n]+fiscal)?)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "ES_NIE",
        r"(?im)^[^\S\r\n]*(?:nie)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "PASSPORT",
        r"(?im)^[^\S\r\n]*(?:passaport)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "EMAIL_ADDRESS",
        r"(?im)^[^\S\r\n]*(?:correu(?:[^\S\r\n]+electr[oò]nic)?|adre[cç]a[^\S\r\n]+electr[oò]nica)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "PHONE_NUMBER",
        r"(?im)^[^\S\r\n]*(?:tel[eè]fon(?:[^\S\r\n]+m[oò]bil)?|m[oò]bil)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "IBAN_CODE",
        r"(?im)^[^\S\r\n]*(?:compte[^\S\r\n]+bancari|iban)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "ADDRESS",
        r"(?im)^[^\S\r\n]*(?:adre[cç]a(?:[^\S\r\n]+postal)?|domicili(?:[^\S\r\n]+fiscal)?)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "CASE_NUMBER",
        r"(?im)^[^\S\r\n]*(?:expedient(?:[^\S\r\n]+municipal)?|n[uú]mero[^\S\r\n]+d'expedient)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "PROFESSIONAL_LICENSE",
        r"(?im)^[^\S\r\n]*(?:n[uú]mero[^\S\r\n]+de[^\S\r\n]+col[·l]egiat|num[^\S\r\n]+col[·l]egiat)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "LICENSE_PLATE",
        r"(?im)^[^\S\r\n]*(?:matr[ií]cula(?:[^\S\r\n]+del[^\S\r\n]+vehicle)?)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "POSTAL_CODE",
        r"(?im)^[^\S\r\n]*(?:codi[^\S\r\n]+postal)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "CADASTRAL_REFERENCE",
        r"(?im)^[^\S\r\n]*(?:refer[eè]ncia[^\S\r\n]+cadastral)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    # Party declarations in Valencian/Spanish bilateral legal contracts.
    # "D'UNA PART: NAME, role" / "D'ALTRA PART: NAME, role"
    # "DE UNA PARTE: NAME, role" / "DE OTRA PARTE: NAME, role"
    # Heuristic: captures up to the first comma so the role title is excluded.
    # Generalizable to any bilingual administrative contract using this structure.
    # LEARNABLE: if the corpus includes contracts with different party markers,
    # extend the alternation list (e.g. "DE PART VENDEDORA", "EN QUALITAT DE").
    (
        "PERSON",
        r"(?im)^[^\S\r\n]*d[e'][^\S\r\n]*(?:una|altra|otra)[^\S\r\n]+part(?:e)?[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n,]+?)[^\S\r\n]*(?:,|$)",
    ),
]

# Append Valencian/Catalan patterns to the main list
LABELED_FIELD_PATTERNS = LABELED_FIELD_PATTERNS + LABELED_FIELD_PATTERNS_VAL_CAT  # type: ignore[name-defined]


NO_COLON_FIELD_PATTERNS: list[tuple[str, str]] = [
    (
        "PERSON",
        r"(?im)^[^\S\r\n]*(?:nombre[^\S\r\n]+completo|nombre|persona)[^\S\r\n]+(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "DATE_OF_BIRTH",
        r"(?im)^[^\S\r\n]*(?:fecha[^\S\r\n]+de[^\S\r\n]+nacimiento|fecha[^\S\r\n]+nacimiento|fecha[^\S\r\n]+nac\.?)[^\S\r\n]+(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "ES_DNI",
        r"(?im)^[^\S\r\n]*(?:dni/nif|dni[^\S\r\n]+nif|nif/dni|nif[^\S\r\n]+dni|documento[^\S\r\n]+nacional|dni|nif)[^\S\r\n]+(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "ES_NIE",
        r"(?im)^[^\S\r\n]*(?:nif/nie|nif[^\S\r\n]+nie|nie/nif|nie[^\S\r\n]+nif|nie)[^\S\r\n]+(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "PASSPORT",
        r"(?im)^[^\S\r\n]*(?:pasaporte)[^\S\r\n]+(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "PHONE_NUMBER",
        r"(?im)^[^\S\r\n]*(?:tel[eé]fono[^\S\r\n]+m[oó]vil|telefono[^\S\r\n]+movil|tel[eé]fono|telefono)[^\S\r\n]+(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "EMAIL_ADDRESS",
        r"(?im)^[^\S\r\n]*(?:email[^\S\r\n]+personal|email|correo)[^\S\r\n]+(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "ADDRESS",
        r"(?im)^[^\S\r\n]*(?:direcci[oó]n[^\S\r\n]+postal|direccion[^\S\r\n]+postal|direcci[oó]n|direccion)[^\S\r\n]+(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "IBAN_CODE",
        r"(?im)^[^\S\r\n]*(?:iban)[^\S\r\n]+(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "CREDIT_CARD",
        r"(?im)^[^\S\r\n]*(?:tarjeta(?:[^\S\r\n]+bancaria)?(?:[^\S\r\n]+de[^\S\r\n]+prueba)?|tarjeta[^\S\r\n]+de[^\S\r\n]+prueba)[^\S\r\n]+(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "USER_ID",
        r"(?im)^[^\S\r\n]*(?:usuario(?:[^\S\r\n]+interno)?|usuario)[^\S\r\n]+(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "IP_ADDRESS",
        r"(?im)^[^\S\r\n]*(?:ip(?:[^\S\r\n]+(?:de[^\S\r\n]+)?[uú]ltima[^\S\r\n]+conexi[oó]n)?|ip[^\S\r\n]+reservada(?:[^\S\r\n]+para[^\S\r\n]+documentaci[oó]n)?)[^\S\r\n]+(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "MEDICAL_ID",
        r"(?im)^[^\S\r\n]*(?:paciente[^\S\r\n]+id|identificador[^\S\r\n]+m[eé]dico|identificador[^\S\r\n]+sanitario)[^\S\r\n]+(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "POLICY_NUMBER",
        r"(?im)^[^\S\r\n]*(?:p[oó]liza|seguro[^\S\r\n]+m[eé]dico)[^\S\r\n]+(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "LICENSE_PLATE",
        r"(?im)^[^\S\r\n]*(?:matr[ií]cula|matricula)[^\S\r\n]+(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
]


ROLE_TABLE_ROW_PATTERN = (
    r"(?im)^[^\S\r\n]*"
    r"(?:arquitecto|arquitecta|promotor|promotora|constructor|constructora|"
    r"t[eé]cnico|t[eé]cnica|propietario|propietaria|representante)"
    r"[^\S\r\n]+(?P<value>[^\r\n]+?)[^\S\r\n]*$"
)


CASE_TABLE_ROW_PATTERN = (
    r"(?im)^[^\S\r\n]*(?:CAS|CASE|ID)[-/_.]?\d+"
    r"[^\S\r\n]+(?P<value>[^\r\n]+?)[^\S\r\n]*$"
)


DIRECT_REGEX_PATTERNS: list[tuple[str, str, float]] = [
    (
        "EMAIL_ADDRESS",
        r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
        0.95,
    ),
    (
        "ES_DNI",
        r"\b\d{8}[ \t\-]?[A-HJ-NP-TV-Z]\b",
        0.95,
    ),
    (
        "ES_NIE",
        r"\b[XYZ][ \t\-]?\d{7}[ \t\-]?[A-HJ-NP-TV-Z]\b",
        0.95,
    ),
    (
        "ES_CIF",
        r"\b[A-HJNPQRSUVW][ \t\-]?\d{7}[ \t\-]?[0-9A-J]\b",
        0.80,
    ),
    (
        "IBAN_CODE",
        r"\bES\d{2}(?:[ \t\-]?\d{4}){5}\b",
        0.95,
    ),
    (
        "PHONE_NUMBER",
        r"(?<!\d)(?:\+34[ \t.\-]*)?(?:6|7|8|9)(?:[ \t.\-]?\d){8}(?!\d)",
        0.85,
    ),
    (
        "URL",
        r"\bhttps?://[^\s<>()]+|\bwww\.[^\s<>()]+",
        0.80,
    ),
    (
        "IP_ADDRESS",
        r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b",
        0.75,
    ),
    (
        "POSTAL_CODE",
        r"\b(?:0[1-9]|[1-4]\d|5[0-2])\d{3}\b",
        0.45,
    ),
    (
        "DATE_TIME",
        # DD/MM/YYYY administrative dates (Spanish/Valencian standard format).
        # Heuristic: 4-digit year in 1900–2099 keeps the false-positive rate low
        # on legal citations (e.g. "art 3.1.c ET y 1255 CC").
        # Optional time component HH:MM captures "04/05/2026 10:30" as one entity
        # so the time portion is not left visible after the date is redacted.
        # LEARNABLE: a date-context classifier could distinguish contract dates,
        # birth dates, and institutional-reference dates (e.g. DOGV publication)
        # for selective redaction once a labelled corpus is available.
        r"\b(?:0?[1-9]|[12]\d|3[01])/(?:0?[1-9]|1[0-2])/(?:19|20)\d{2}(?:[^\S\r\n]+(?:[01]?\d|2[0-3]):[0-5]\d)?\b",
        0.90,
    ),
    (
        "LICENSE_PLATE",
        r"\b\d{4}[- \t]?[A-Z]{3}\b",
        0.75,
    ),
    (
        "CADASTRAL_REFERENCE",
        r"\b\d{7}[A-Z]{2}\d{4}[A-Z]\d{4}[A-Z]{2}\b",
        0.90,
    ),
    (
        "CASE_NUMBER",
        r"\b(?:EXP|REF|EXPDTE|EXPTE)[-/_.]?[A-Z0-9]{2,}(?:[-/_.][A-Z0-9]{2,})+\b",
        0.80,
    ),
    (
        "PROFESSIONAL_LICENSE",
        r"\b(?:COACV[-/_.]?\d{3,}|COA[-/_.]?[A-Z0-9]{3,}|COL[-/_.]?[A-Z0-9]{3,}|COLEG[-/_.][A-Z0-9]{3,})\b",
        0.80,
    ),
    (
        "PASSPORT",
        r"\b(?:[A-Z]{2,5}[- \t]?[A-Z]?[- \t]?\d{4,}|FAKE[- \t]?[A-Z]?[- \t]?\d{4,})\b",
        0.80,
    ),
    (
        "CREDIT_CARD",
        r"\b(?:\d{4}[ \t\-]){3}\d{4}\b(?:[ \t]*-[ \t]*cad\.?[ \t]*\d{2}/\d{2}[ \t]*-[ \t]*CVV?[ \t]*\d{3,4})?",
        0.85,
    ),
    (
        "USER_ID",
        r"\busr-[a-z0-9][a-z0-9\-_.]{3,}\b",
        0.75,
    ),
    (
        "MEDICAL_ID",
        r"\bPAC-[A-Z0-9][A-Z0-9\-_.]{3,}\b",
        0.80,
    ),
    (
        "POLICY_NUMBER",
        r"\bPOLIZA-[A-Z0-9][A-Z0-9\-_.]{3,}\b",
        0.80,
    ),
]


NER_ALLOWED_TYPES = {
    "PERSON",
    "LOCATION",
    "ORGANIZATION",
}


FIELD_LABEL_FALSE_POSITIVES = {
    "CAMPO",
    "VALOR",
    "VALOR FICTICIO",
    "CAMPO VALOR",
    "CAMPO VALOR FICTICIO",
    "TIPO",
    "EJEMPLO",
    "EJEMPLO FICTICIO",
    "TIPO EJEMPLO",
    "TIPO EJEMPLO FICTICIO",
    "ID CASO",
    "PERSONA",
    "CONTACTO",
    "DATOS A ANONIMIZAR",
    "ROL",
    "NOMBRE",
    "NOMBRE COMPLETO",
    "FECHA",
    "FECHA DE NACIMIENTO",
    "FECHA NACIMIENTO",
    "DNI/NIF",
    "DNI NIF",
    "NIF/DNI",
    "NIF DNI",
    "NIF/NIE",
    "NIF NIE",
    "NIE/NIF",
    "NIE NIF",
    "DNI",
    "NIF",
    "NIE",
    "DOCUMENTO NACIONAL",
    "PASAPORTE",
    "TELEFONO",
    "TELÉFONO",
    "TELEFONO MOVIL",
    "TELÉFONO MÓVIL",
    "EMAIL",
    "EMAIL PERSONAL",
    "CORREO",
    "CORREO PERSONAL",
    "DIRECCION",
    "DIRECCIÓN",
    "DIRECCION POSTAL",
    "DIRECCIÓN POSTAL",
    "IBAN",
    "TARJETA",
    "TARJETA DE PRUEBA",
    "TARJETA BANCARIA",
    "TARJETA BANCARIA DE PRUEBA",
    "USUARIO",
    "USUARIO INTERNO",
    "IP",
    "IP DE ULTIMA CONEXION",
    "IP DE ÚLTIMA CONEXIÓN",
    "IP ULTIMA CONEXION",
    "IP ÚLTIMA CONEXIÓN",
    "IP RESERVADA",
    "IP RESERVADA PARA DOCUMENTACION",
    "IP RESERVADA PARA DOCUMENTACIÓN",
    "IDENTIFICADOR MEDICO",
    "IDENTIFICADOR MÉDICO",
    "IDENTIFICADOR SANITARIO",
    "PACIENTE ID",
    "POLIZA",
    "PÓLIZA",
    "SEGURO MEDICO",
    "SEGURO MÉDICO",
    "MATRICULA",
    "MATRÍCULA",
    "ULTIMA CONEXION",
    "ÚLTIMA CONEXIÓN",
    "ASUNTO",
    "DE",
    "PARA",
    "CC",
    "CCO",
    "NIE REPRESENTANTE",
    "CIF EMPRESA CONSTRUCTORA",
    "CIF EMPRESA",
    "TELEFONO ALTERNATIVO",
    "TELÉFONO ALTERNATIVO",
    "NUMERO DE COLEGIADO",
    "NÚMERO DE COLEGIADO",
    "MATRICULA VEHICULO VISITA OBRA",
    "MATRÍCULA VEHÍCULO VISITA OBRA",
    "MATRICULA VEHICULO",
    "MATRÍCULA VEHÍCULO",
}


LABEL_SUFFIX_FALSE_VALUES = {
    "COMPLETO",
    "PERSONAL",
    "POSTAL",
    "MOVIL",
    "MÓVIL",
    "DE NACIMIENTO",
    "NACIMIENTO",
    "DE PRUEBA",
    "BANCARIA",
    "BANCARIA DE PRUEBA",
    "INTERNO",
    "DE ULTIMA CONEXION",
    "DE ÚLTIMA CONEXIÓN",
    "ULTIMA CONEXION",
    "ÚLTIMA CONEXIÓN",
    "CONEXION",
    "CONEXIÓN",
    "RESERVADA",
    "RESERVADA PARA DOCUMENTACION",
    "RESERVADA PARA DOCUMENTACIÓN",
    "MEDICO",
    "MÉDICO",
    "SANITARIO",
    "NACIONAL",
    "FICTICIO",
    "FICTICIA",
    "VALOR FICTICIO",
    "CAMPO VALOR FICTICIO",
    "CAMPO",
    "VALOR",
    "FECHA",
    "TELEFONO",
    "TELÉFONO",
    "DIRECCIÓN",
    "DIRECCION",
    "NIF",
    "NIE",
    "DNI",
    "IBAN",
    "ASUNTO",
    "REPRESENTANTE",
    "ALTERNATIVO",
    "EMPRESA",
    "CONSTRUCTORA",
    "EMPRESA CONSTRUCTORA",
    "COLEGIADO",
    "DE COLEGIADO",
    "VEHICULO",
    "VEHÍCULO",
    "VEHICULO VISITA OBRA",
    "VEHÍCULO VISITA OBRA",
    "VISITA OBRA",
}


GENERIC_FALSE_POSITIVES = {
    "PRUEBA",
    "INFORME",
    "DOCUMENTO",
    "ANEXO",
    "COPIA",
    "BORRADOR",
    "EMAIL",
    "TELÉFONO",
    "TELEFONO",
    "IBAN",
    "DNI",
    "NIE",
    "NIF",
    "CIF",
    "DIRECCIÓN",
    "DIRECCION",
    "EXPEDIENTE",
    "REFERENCIA",
    "CATASTRAL",
    "URL",
    "WEB",
    "CONTACTO",
    "DOCUMENTACIÓN",
    "DOCUMENTACION",
    "CAMPO",
    "VALOR",
    "VALOR FICTICIO",
    "TIPO",
    "EJEMPLO",
    "EJEMPLO FICTICIO",
}


SOURCE_PRIORITY = {
    "labeled_field": 5,
    "no_colon_labeled_field": 4,
    "case_table_row": 4,
    "role_table_row": 3,
    "deterministic_regex": 2,
    "presidio": 1,
}


class PiiAnalyzer:
    def __init__(self) -> None:
        nlp_config = {
            "nlp_engine_name": "spacy",
            "models": [
                {
                    "lang_code": "es",
                    "model_name": "es_core_news_md",
                },
            ],
            "ner_model_configuration": {
                "model_to_presidio_entity_mapping": {
                    "PER": "PERSON",
                    "PERSON": "PERSON",
                    "LOC": "LOCATION",
                    "GPE": "LOCATION",
                    "ORG": "ORGANIZATION",
                    "ORGANIZATION": "ORGANIZATION",
                },
                "low_confidence_score_multiplier": 0.4,
                "labels_to_ignore": [
                    "CARDINAL",
                    "DATE",
                    "EVENT",
                    "LANGUAGE",
                    "LAW",
                    "MISC",
                    "MONEY",
                    "ORDINAL",
                    "PERCENT",
                    "PRODUCT",
                    "QUANTITY",
                    "TIME",
                    "WORK_OF_ART",
                ],
            },
        }

        provider = NlpEngineProvider(nlp_configuration=nlp_config)
        nlp_engine = provider.create_engine()

        registry = RecognizerRegistry(supported_languages=SUPPORTED_LANGUAGES)
        registry.add_nlp_recognizer(nlp_engine=nlp_engine)

        for recognizer in build_spanish_pattern_recognizers():
            registry.add_recognizer(recognizer)

        self._engine = AnalyzerEngine(
            nlp_engine=nlp_engine,
            registry=registry,
            supported_languages=SUPPORTED_LANGUAGES,
        )

    def analyze(
        self,
        text: str,
        mode: RedactionMode = "strict",
        zone_type: str | None = None,
    ) -> list[PiiEntity]:
        """Detect PII in *text*.

        Args:
            text:      The text to analyse.
            mode:      "strict" (all heuristics) or "balanced" (validated only).
            zone_type: Layout zone this text comes from ("body", "header",
                       "footer", "form", "table", …).  When provided, enables
                       zone-aware NER filtering to reduce false positives in
                       dense legal prose.  None → no zone-specific adjustment.
        """
        if not text.strip():
            return []

        entities: list[PiiEntity] = []

        entities.extend(self._detect_labeled_fields(text, mode=mode))

        if mode == "strict":
            entities.extend(self._detect_no_colon_labeled_fields(text, mode=mode))
            entities.extend(self._detect_case_table_rows(text))
            entities.extend(self._detect_role_table_rows(text))

        entities.extend(self._detect_direct_regex(text, mode=mode))
        entities.extend(self._detect_ner_entities(text, zone_type=zone_type))

        return self._merge_overlaps(entities)

    @staticmethod
    def count_by_type(entities: list[PiiEntity]) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)

        for entity in entities:
            counts[entity.entity_type] += 1

        return dict(counts)

    def _detect_ner_entities(
        self,
        text: str,
        zone_type: str | None = None,
    ) -> list[PiiEntity]:
        # Zone-aware NER behaviour.
        # For FORM zones (structured label:value rows), NER adds false positives
        # with no benefit: labeled_field + direct regex cover all relevant entity
        # types with better precision. NER is skipped entirely.
        # LEARNABLE: a domain-adapted NER model could re-enable NER for forms once
        # precision on form text is measured on a labelled corpus.
        if zone_type == "form":
            return []

        # Heuristic: spaCy es_core_news_md was trained on news text and produces
        # many false positives on dense legal prose, especially in Valencian.
        # Raising the score threshold in BODY zones reduces FP rate.
        # LEARNABLE: optimal per-zone threshold should be calibrated on a labelled
        # corpus; a domain-adapted NER model would make this unnecessary.
        is_dense_body = zone_type == "body"
        ner_threshold = (
            max(settings.presidio_score_threshold, 0.75)
            if is_dense_body
            else settings.presidio_score_threshold
        )

        results = self._engine.analyze(
            text=text,
            language="es",
            score_threshold=ner_threshold,
        )

        entities: list[PiiEntity] = []

        for result in results:
            if result.entity_type not in NER_ALLOWED_TYPES:
                continue

            span_parts = self._split_span_by_lines(
                text=text,
                start=result.start,
                end=result.end,
            )

            for part_start, part_end in span_parts:
                start, end = self._trim_greeting_prefix(
                    text=text,
                    start=part_start,
                    end=part_end,
                )

                if start >= end:
                    continue

                value = text[start:end]
                full_line = self._line_for_span(text, start, end)

                if self._starts_with_email_header_label(value):
                    continue

                if self._starts_with_email_header_label(full_line):
                    continue

                if self._is_field_label_only(value):
                    continue

                if self._is_field_label_only(full_line):
                    continue

                if self._starts_with_known_field_label(value):
                    continue

                if self._starts_with_known_field_label(full_line):
                    continue

                if self._looks_like_false_positive(result.entity_type, value):
                    continue

                # ── Zone-aware content filters ──────────────────────────────

                # All-caps short spans are section headings, not PII.
                # Heuristic: REUNITS, EXPOSEN, CLÀUSULES, SIGN, DOGV, etc.
                # Generalizable: documents across all domains use all-caps headers.
                # LEARNABLE: max word count (4) could be tuned per document type.
                if self._is_section_header(value):
                    continue

                # PERSON and LOCATION spans that start with a lowercase letter are
                # NER false positives (role titles, preposition phrases, common
                # nouns like "d'enginyeria", "lloc d'adscripció").
                # Proper names and place names always start uppercase.
                if result.entity_type in {"PERSON", "LOCATION"} and value and value[0].islower():
                    continue

                # In BODY zones with dense legal prose, single-word NER
                # detections are rarely genuine person/org/location names.
                # Heuristic: require ≥2 words for NER entities in body text.
                # LEARNABLE: min_words threshold per zone type.
                if is_dense_body and result.entity_type in NER_ALLOWED_TYPES:
                    if len(value.split()) < 2:
                        continue

                # Institutional names are context entities (employers, issuers),
                # not personal data under RGPD Art. 4.  Detected when NER
                # misclassifies an organisation as PERSON or LOCATION.
                # Heuristic: roots like "universitat", "ajuntament", "institut".
                # LEARNABLE: replace with a named-entity disambiguation model
                # that distinguishes "person mentions" from "org mentions".
                if _INSTITUTION_NAME_RE.search(value):
                    continue

                entities.append(
                    PiiEntity(
                        entity_type=result.entity_type,
                        start=start,
                        end=end,
                        score=float(result.score),
                        source="presidio",
                    )
                )

        return entities

    @staticmethod
    def _detect_labeled_fields(text: str, mode: RedactionMode) -> list[PiiEntity]:
        entities: list[PiiEntity] = []

        for entity_type, pattern in LABELED_FIELD_PATTERNS:
            for match in re.finditer(pattern, text):
                value = match.group("value")

                if not value.strip():
                    continue

                if not PiiAnalyzer._passes_validation(
                    entity_type=entity_type,
                    value=value,
                    mode=mode,
                    source="labeled_field",
                ):
                    continue

                entities.append(
                    PiiEntity(
                        entity_type=entity_type,
                        start=match.start("value"),
                        end=match.end("value"),
                        score=0.97,
                        source="labeled_field",
                    )
                )

        return entities

    @staticmethod
    def _detect_no_colon_labeled_fields(text: str, mode: RedactionMode) -> list[PiiEntity]:
        entities: list[PiiEntity] = []

        for entity_type, pattern in NO_COLON_FIELD_PATTERNS:
            for match in re.finditer(pattern, text):
                value = match.group("value")
                full_line = match.group(0)

                if PiiAnalyzer._is_field_label_only(full_line):
                    continue

                if not value.strip():
                    continue

                if PiiAnalyzer._looks_like_false_positive(entity_type, value):
                    continue

                if not PiiAnalyzer._passes_validation(
                    entity_type=entity_type,
                    value=value,
                    mode=mode,
                    source="no_colon_labeled_field",
                ):
                    continue

                entities.append(
                    PiiEntity(
                        entity_type=entity_type,
                        start=match.start("value"),
                        end=match.end("value"),
                        score=0.94,
                        source="no_colon_labeled_field",
                    )
                )

        return entities

    @staticmethod
    def _detect_role_table_rows(text: str) -> list[PiiEntity]:
        entities: list[PiiEntity] = []

        for match in re.finditer(ROLE_TABLE_ROW_PATTERN, text):
            value = match.group("value")

            if not value.strip():
                continue

            entities.append(
                PiiEntity(
                    entity_type="ROLE_TABLE_ROW",
                    start=match.start("value"),
                    end=match.end("value"),
                    score=0.92,
                    source="role_table_row",
                )
            )

        return entities

    @staticmethod
    def _detect_case_table_rows(text: str) -> list[PiiEntity]:
        entities: list[PiiEntity] = []

        for match in re.finditer(CASE_TABLE_ROW_PATTERN, text):
            value = match.group("value")

            if not value.strip():
                continue

            entities.append(
                PiiEntity(
                    entity_type="CASE_TABLE_ROW",
                    start=match.start("value"),
                    end=match.end("value"),
                    score=0.93,
                    source="case_table_row",
                )
            )

        return entities

    @staticmethod
    def _detect_direct_regex(text: str, mode: RedactionMode) -> list[PiiEntity]:
        entities: list[PiiEntity] = []

        for entity_type, pattern, score in DIRECT_REGEX_PATTERNS:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                value = match.group(0)

                if "\n" in value or "\r" in value:
                    continue

                # PASSPORT patterns use re.IGNORECASE so that letter separators
                # match both cases — but this causes legal citations such as
                # "art 3.1.c ET y 1255 CC" to false-match (IGNORECASE makes "i"
                # satisfy the [A-Z]? letter slot + space satisfies [- \t]?).
                # Guard: a real passport/document number has no lowercase letters.
                if entity_type == "PASSPORT" and value != value.upper():
                    continue

                if PiiAnalyzer._looks_like_false_positive(entity_type, value):
                    continue

                if not PiiAnalyzer._passes_validation(
                    entity_type=entity_type,
                    value=value,
                    mode=mode,
                    source="deterministic_regex",
                ):
                    continue

                entities.append(
                    PiiEntity(
                        entity_type=entity_type,
                        start=match.start(),
                        end=match.end(),
                        score=score,
                        source="deterministic_regex",
                    )
                )

        return entities

    @staticmethod
    def _passes_validation(
        entity_type: str,
        value: str,
        mode: RedactionMode,
        source: str,
    ) -> bool:
        if mode == "strict":
            return True

        if entity_type == "ES_DNI":
            return is_valid_dni(value)

        if entity_type == "ES_NIE":
            return is_valid_nie(value)

        if entity_type == "ES_CIF":
            return is_valid_cif(value)

        if entity_type == "IBAN_CODE":
            return is_valid_spanish_iban(value)

        if entity_type == "CADASTRAL_REFERENCE":
            return looks_like_cadastral_reference(value)

        return True

    @staticmethod
    def _looks_like_false_positive(entity_type: str, value: str) -> bool:
        normalized = PiiAnalyzer._normalize_false_positive_text(value)

        if normalized in PiiAnalyzer._false_positive_catalog():
            return True

        if entity_type in {"PERSON", "LOCATION", "ORGANIZATION"}:
            if len(normalized) <= 3:
                return True

            if normalized in PiiAnalyzer._false_positive_catalog():
                return True

            if normalized.isdigit():
                return True

            label_fragments = {
                "COMPLETO",
                "NACIMIENTO",
                "PASAPORTE",
                "MOVIL",
                "PERSONAL",
                "POSTAL",
                "TARJETA",
                "PRUEBA",
                "INTERNO",
                "CONEXION",
                "FICTICIO",
                "DNI NIF",
                "DIRECCION POSTAL",
                "ASUNTO",
            }

            if normalized in label_fragments:
                return True

        return False

    @staticmethod
    def _is_section_header(value: str) -> bool:
        """True when *value* looks like an all-caps structural section heading.

        Heuristic: short spans that are fully uppercase (REUNITS, EXPOSEN,
        CLÀUSULES, DOGV, SIGN …) are document headings, never PII.
        Generalizable across all document types that use all-caps headings.
        LEARNABLE: max_words threshold (currently 4) could be calibrated per
        document type once a labelled corpus is available.
        """
        stripped = value.strip()
        if not stripped:
            return False
        words = stripped.split()
        if len(words) > 4:
            return False
        return stripped == stripped.upper() and any(c.isalpha() for c in stripped)

    @staticmethod
    def _merge_overlaps(entities: list[PiiEntity]) -> list[PiiEntity]:
        if not entities:
            return []

        ordered = sorted(
            entities,
            key=lambda e: (e.start, e.end),
        )

        merged: list[PiiEntity] = []

        for entity in ordered:
            if not merged:
                merged.append(entity)
                continue

            previous = merged[-1]
            overlaps = entity.start < previous.end

            if not overlaps:
                merged.append(entity)
                continue

            previous_priority = SOURCE_PRIORITY.get(previous.source, 0)
            current_priority = SOURCE_PRIORITY.get(entity.source, 0)

            previous_len = previous.end - previous.start
            current_len = entity.end - entity.start

            should_replace = (
                current_priority > previous_priority
                or (
                    current_priority == previous_priority
                    and current_len > previous_len
                )
                or (
                    current_priority == previous_priority
                    and current_len == previous_len
                    and entity.score > previous.score
                )
            )

            if should_replace:
                merged[-1] = entity

        return merged

    @staticmethod
    def _line_for_span(text: str, start: int, end: int) -> str:
        line_start = text.rfind("\n", 0, start)

        if line_start == -1:
            line_start = 0
        else:
            line_start += 1

        line_end = text.find("\n", end)

        if line_end == -1:
            line_end = len(text)

        return text[line_start:line_end]

    @staticmethod
    def _normalize_false_positive_text(value: str) -> str:
        replacements = {
            "Á": "A",
            "É": "E",
            "Í": "I",
            "Ó": "O",
            "Ú": "U",
            "Ü": "U",
            "Ñ": "N",
        }

        normalized = value.strip().upper().strip(":. ")

        for source, target in replacements.items():
            normalized = normalized.replace(source, target)

        normalized = normalized.replace("/", " ")
        normalized = normalized.replace("-", " ")
        normalized = normalized.replace("_", " ")
        normalized = " ".join(normalized.split())

        return normalized

    @staticmethod
    def _false_positive_catalog() -> set[str]:
        values = (
            FIELD_LABEL_FALSE_POSITIVES
            | LABEL_SUFFIX_FALSE_VALUES
            | GENERIC_FALSE_POSITIVES
        )

        return {
            PiiAnalyzer._normalize_false_positive_text(item)
            for item in values
        }

    @staticmethod
    def _field_label_catalog() -> set[str]:
        return {
            PiiAnalyzer._normalize_false_positive_text(item)
            for item in FIELD_LABEL_FALSE_POSITIVES
        }

    @staticmethod
    def _is_field_label_only(value: str) -> bool:
        lines = [
            line.strip()
            for line in value.splitlines()
            if line.strip()
        ]

        if not lines:
            return False

        catalog = PiiAnalyzer._false_positive_catalog()

        normalized_lines = [
            PiiAnalyzer._normalize_false_positive_text(line)
            for line in lines
        ]

        return all(line in catalog for line in normalized_lines)

    @staticmethod
    def _starts_with_known_field_label(value: str) -> bool:
        normalized = PiiAnalyzer._normalize_false_positive_text(value)

        if not normalized:
            return False

        labels = sorted(
            PiiAnalyzer._field_label_catalog(),
            key=len,
            reverse=True,
        )

        small_header_labels = {"DE", "PARA", "CC", "CCO"}

        for label in labels:
            if normalized == label:
                return True

            if label in small_header_labels:
                continue

            if normalized.startswith(label + " "):
                return True

        return False

    @staticmethod
    def _starts_with_email_header_label(value: str) -> bool:
        return bool(
            re.match(
                r"(?i)^\s*(de|para|cc|cco|asunto)\s*:",
                value.strip(),
            )
        )

    @staticmethod
    def _trim_greeting_prefix(
        text: str,
        start: int,
        end: int,
    ) -> tuple[int, int]:
        value = text[start:end]

        match = re.match(
            r"(?i)^\s*(hola|estimado|estimada|estimados|estimadas|buenos\s+d[ií]as|buenas\s+tardes|buenas\s+noches)[,\s]+",
            value,
        )

        if not match:
            return start, end

        return start + match.end(), end
    
    @staticmethod
    def _split_span_by_lines(
        text: str,
        start: int,
        end: int,
    ) -> list[tuple[int, int]]:
        value = text[start:end]

        parts: list[tuple[int, int]] = []
        cursor = start

        for raw_line in value.splitlines(keepends=True):
            line_start = cursor
            line_end = cursor + len(raw_line)
            cursor = line_end

            clean_line = raw_line.rstrip("\r\n")

            leading_spaces = len(clean_line) - len(clean_line.lstrip(" \t,;:"))
            trailing_end = len(clean_line.rstrip(" \t,;:."))

            part_start = line_start + leading_spaces
            part_end = line_start + trailing_end

            if part_start < part_end:
                parts.append((part_start, part_end))

        return parts