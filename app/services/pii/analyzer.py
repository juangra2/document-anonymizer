import os

from app.core.config import settings

# Presidio reads PRESIDIO_DEVICE at import time to pick the NER device.  Drive it
# from settings so the transformer NER can use the GPU when available; spaCy
# degrades to CPU automatically if GPU support (cupy) is absent.
os.environ.setdefault(
    "PRESIDIO_DEVICE",
    "cuda" if settings.ner_use_gpu else "cpu",
)

import logging
import re
from collections import defaultdict
from typing import Literal

# Institutional name roots used to detect document-issuing organisations that are
# context entities, not PII subjects (RGPD Art. 4).  Centralised in
# institution_filter.py and reused here for NER false-positive filtering.
from app.services.pii.institution_filter import INSTITUTION_NAME_RE as _INSTITUTION_NAME_RE

from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider

from app.services.pii import gazetteer
from app.services.pii.models import PiiEntity
from app.services.pii.recognizers_es import build_spanish_pattern_recognizers
from app.services.pii.validators_es import (
    is_valid_cif,
    is_valid_dni,
    is_valid_nie,
    is_valid_spanish_iban,
    looks_like_cadastral_reference,
)

logger = logging.getLogger(__name__)


SUPPORTED_LANGUAGES = ["es"]
RedactionMode = Literal["strict", "balanced"]


LABELED_FIELD_PATTERNS: list[tuple[str, str]] = [
    (
        "PERSON",
        r"(?im)^[^\S\r\n]*(?:solicitante|interesado|titular|promotor(?:a)?|propietario(?:a)?|representante|arquitecto(?:a)?|t[eé]cnico(?:a)?|m[eé]dico[^\S\r\n]+responsable|doctor(?:a)?|nombre(?:[^\S\r\n]+completo)?|nombre(?:[^\S\r\n]+y[^\S\r\n]+apellidos)?|apellidos|persona|paciente(?:[^\S\r\n]+nombre)?|trabajador(?:a)?|empleado(?:a)?|notificado(?:a)?|destinatario(?:a)?|empadronado(?:a)?|persona[^\S\r\n]+empadronada)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n:]+?)[^\S\r\n]*$",
    ),
    (
        # Date-only value capture stops at the date token, not end-of-line.
        # Prevents run-on lines like "Nacimiento: 12/03/1983 Domicilio:Calle X"
        # from absorbing the address into the DOB value (IoU < 0.5 → FN).
        # Abbreviations: "F. nac.", "F. nacim.", "F. nacimiento" cover
        # form fields in notificacion_ayuntamiento, certificado_empadronamiento,
        # and contrato_laboral.  Automatic next-line variant (_COLON_NL_SUFFIX)
        # handles "F. nac.:\n31/05/1985" formats.
        "DATE_OF_BIRTH",
        r"(?im)^[^\S\r\n]*(?:fecha[^\S\r\n]+de[^\S\r\n]+nacimiento|fecha[^\S\r\n]+nacimiento|fecha[^\S\r\n]+nac(?:im?)?\.?|f\.[^\S\r\n]*nac(?:im?)?\.?|f\.[^\S\r\n]*naix\.?|f\.[^\S\r\n]+nacimiento|nacimiento)[^\S\r\n]*:[^\S\r\n]*(?P<value>(?:0?[1-9]|[12]\d|3[01])[/.\-](?:0?[1-9]|1[0-2])[/.\-](?:19|20)\d{2}(?:[^\S\r\n]+(?:[01]?\d|2[0-3]):[0-5]\d)?)",
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
        r"(?im)^[^\S\r\n]*(?:email(?:[^\S\r\n]+personal|[^\S\r\n]+contacto)?|e-mail|correo(?:[^\S\r\n]+personal)?|correo[^\S\r\n]+electr[oó]nico)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n:]+?)[^\S\r\n]*$",
    ),
    (
        "PHONE_NUMBER",
        r"(?im)^[^\S\r\n]*(?:tel[eé]fono(?:[^\S\r\n]+m[oó]vil|[^\S\r\n]+alternativo|[^\S\r\n]+contacto)?|telefono(?:[^\S\r\n]+movil)?|telf\.?|m[oó]vil)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n:]+?)[^\S\r\n]*$",
    ),
    (
        "IBAN_CODE",
        r"(?im)^[^\S\r\n]*(?:iban|cuenta[^\S\r\n]+bancaria|n[uú]mero[^\S\r\n]+de[^\S\r\n]+cuenta|n[^\S\r\n]*[º°.]?[^\S\r\n]*de[^\S\r\n]+cuenta|num\.?[^\S\r\n]+cuenta)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        # ES-specific IBAN colon: captures full IBAN including newline-split groups.
        # Flexible grouping (20 digits, any separators) covers irregular layouts.
        "IBAN_CODE",
        r"(?im)^[^\S\r\n]*(?:iban|cuenta[^\S\r\n]+bancaria|n[uú]mero[^\S\r\n]+de[^\S\r\n]+cuenta)[^\S\r\n]*:[^\S\r\n]*(?P<value>ES\d{2}(?:[ \t\n\-]?\d){20})",
    ),
    (
        "ADDRESS",
        r"(?im)^[^\S\r\n]*(?:direcci[oó]n(?:[^\S\r\n]+postal|[^\S\r\n]+de[^\S\r\n]+notificaci[oó]n|[^\S\r\n]+habitual|[^\S\r\n]+fiscal)?|domicilio(?:[^\S\r\n]+fiscal|[^\S\r\n]+habitual|[^\S\r\n]+de[^\S\r\n]+notificaci[oó]n|[^\S\r\n]+de[^\S\r\n]+empadronamiento)?|residencia|via[^\S\r\n]+notificaci[oó]n)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "POSTAL_CODE",
        r"(?im)^[^\S\r\n]*(?:c[oó]digo[^\S\r\n]+postal|cp)[^\S\r\n]*:[^\S\r\n]*(?P<value>\d{5})[^\S\r\n]*$",
    ),
    (
        "CASE_NUMBER",
        r"(?im)^[^\S\r\n]*(?:expediente(?:[^\S\r\n]+municipal)?|n[uú]mero[^\S\r\n]+de[^\S\r\n]+expediente|exp\.?(?:[^\S\r\n]+n[uú]m\.?)?|id[^\S\r\n]+caso)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
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
        r"(?im)^[^\S\r\n]*(?:ip(?:[^\S\r\n]+(?:de[^\S\r\n]+)?[uú]ltima[^\S\r\n]+conexi[oó]n)?|ip[^\S\r\n]+reservada(?:[^\S\r\n]+para[^\S\r\n]+documentaci[oó]n)?)[^\S\r\n]*:[^\S\r\n]*(?P<value>\d{1,3}(?:\.\d{1,3}){3}[^\r\n]*?)[^\S\r\n]*$",
    ),
    (
        # "Acceso al expediente: IP" — notificacion_sancionador log field.
        "IP_ADDRESS",
        r"(?im)^[^\S\r\n]*acceso[^\S\r\n]+al[^\S\r\n]+expediente[^\S\r\n]*:[^\S\r\n]*(?P<value>\d{1,3}(?:\.\d{1,3}){3})[^\S\r\n]*$",
    ),
    (
        "MEDICAL_ID",
        r"(?im)^[^\S\r\n]*(?:paciente[^\S\r\n]+id|identificador[^\S\r\n]+m[eé]dico|identificador[^\S\r\n]+sanitario(?:[^\S\r\n]+ficticio)?)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "POLICY_NUMBER",
        r"(?im)^[^\S\r\n]*(?:p[oó]liza|seguro[^\S\r\n]+m[eé]dico)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "ES_NSS",
        r"(?im)^[^\S\r\n]*(?:nass|n[uú]mero[^\S\r\n]+de[^\S\r\n]+afiliaci[oó]n|n[uú]m\.?[^\S\r\n]+afiliaci[oó]n)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
]


# -----------------------------------------------------------------------
# Valencian / Catalan labeled-field patterns (colon form)
# -----------------------------------------------------------------------
LABELED_FIELD_PATTERNS_VAL_CAT: list[tuple[str, str]] = [
    (
        "PERSON",
        r"(?im)^[^\S\r\n]*(?:nom(?:[^\S\r\n]+complet|[^\S\r\n]+i[^\S\r\n]+cognoms)?|cognoms|sollicitant|titular)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n:]+?)[^\S\r\n]*$",
    ),
    (
        "DATE_OF_BIRTH",
        r"(?im)^[^\S\r\n]*(?:data[^\S\r\n]+de[^\S\r\n]+naixement|data[^\S\r\n]+naixement|data[^\S\r\n]+nac\.?)[^\S\r\n]*:[^\S\r\n]*(?P<value>(?:0?[1-9]|[12]\d|3[01])[/.\-](?:0?[1-9]|1[0-2])[/.\-](?:19|20)\d{2}(?:[^\S\r\n]+(?:[01]?\d|2[0-3]):[0-5]\d)?)",
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
        r"(?im)^[^\S\r\n]*(?:correu(?:[^\S\r\n]+electr[oò]nic)?|adre[cç]a[^\S\r\n]+electr[oò]nica)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n:]+?)[^\S\r\n]*$",
    ),
    (
        "PHONE_NUMBER",
        r"(?im)^[^\S\r\n]*(?:tel[eè]fon(?:[^\S\r\n]+m[oò]bil)?|m[oò]bil)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n:]+?)[^\S\r\n]*$",
    ),
    (
        "IBAN_CODE",
        r"(?im)^[^\S\r\n]*(?:compte[^\S\r\n]+bancari|iban)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        "ADDRESS",
        r"(?im)^[^\S\r\n]*(?:adre[cç]a(?:[^\S\r\n]+postal)?|domicili(?:[^\S\r\n]+fiscal|[^\S\r\n]+habitual)?|resid[eè]ncia|via[^\S\r\n]+notificaci[oó])[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
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
    (
        "ES_NSS",
        r"(?im)^[^\S\r\n]*(?:nass|n[uú]mero[^\S\r\n]+d'afiliaci[oó])[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    # Party declarations in Valencian/Spanish bilateral legal contracts.
    # "D'UNA PART: NAME, role" / "D'ALTRA PART: NAME, role"
    # "DE UNA PARTE: NAME, role" / "DE OTRA PARTE: NAME, role"
    # Heuristic: captures up to the first comma so the role title is excluded.
    # Generalizable to any bilingual administrative contract using this structure.
    # LEARNABLE: if the corpus includes contracts with different party markers,
    # extend the alternation list (e.g. "DE PART VENDEDORA", "EN QUALITAT DE").
    (
        # Party declarations: accept both ASCII apostrophe (') and Unicode right
        # single quotation mark (’ — common in PDF-extracted text).
        "PERSON",
        "(?im)^[^\\S\\r\\n]*d(?:e|'|’|‘)[^\\S\\r\\n]*(?:una|altra|otra)[^\\S\\r\\n]+part(?:e)?[^\\S\\r\\n]*:[^\\S\\r\\n]*(?P<value>[^\\r\\n,]+?)[^\\S\\r\\n]*(?:,|$)",
    ),
]

# Append Valencian/Catalan patterns to the main list
LABELED_FIELD_PATTERNS = LABELED_FIELD_PATTERNS + LABELED_FIELD_PATTERNS_VAL_CAT  # type: ignore[name-defined]


NO_COLON_FIELD_PATTERNS: list[tuple[str, str]] = [
    (
        "PERSON",
        r"(?im)^[^\S\r\n]*(?:nombre[^\S\r\n]+completo|nombre|persona|trabajador(?:a)?|empleado(?:a)?)[^\S\r\n]+(?P<value>[^\r\n:]+?)[^\S\r\n]*$",
    ),
    (
        "DATE_OF_BIRTH",
        r"(?im)^[^\S\r\n]*(?:fecha[^\S\r\n]+de[^\S\r\n]+nacimiento|fecha[^\S\r\n]+nacimiento|fecha[^\S\r\n]+nac(?:im?)?\.?|f\.[^\S\r\n]*nac(?:im?)?\.?|f\.[^\S\r\n]*naix\.?|f\.[^\S\r\n]+nacimiento|nacimiento)[^\S\r\n]+(?P<value>(?:0?[1-9]|[12]\d|3[01])[/.\-](?:0?[1-9]|1[0-2])[/.\-](?:19|20)\d{2}(?:[^\S\r\n]+(?:[01]?\d|2[0-3]):[0-5]\d)?)",
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
        r"(?im)^[^\S\r\n]*(?:direcci[oó]n[^\S\r\n]+postal|direccion[^\S\r\n]+postal|direcci[oó]n(?![^\S\r\n]+general)|direccion(?![^\S\r\n]+general)|residencia)[^\S\r\n]+(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        # ADDRESS next-line no-colon: "Domicilio\nCalle X, N, CP Ciudad"
        # Covers formulario_medico table format where label and value are on
        # separate lines with no separator.
        "ADDRESS",
        r"(?im)^[^\S\r\n]*domicilio[^\S\r\n]*\r?\n[^\S\r\n]*(?P<value>[^\r\n]+?)[^\S\r\n]*$",
    ),
    (
        # No-colon IBAN: require value to start with ES IBAN format.
        # Removed the generic variant that captured arbitrary text like "pago"
        # from column headers such as "IBAN pago" in nómina documents.
        "IBAN_CODE",
        r"(?im)^[^\S\r\n]*(?:iban)[^\S\r\n]+(?P<value>ES\d{2}(?:[ \t\n\-]?\d){20})",
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
        r"(?im)^[^\S\r\n]*(?:ip(?:[^\S\r\n]+(?:de[^\S\r\n]+)?[uú]ltima[^\S\r\n]+conexi[oó]n)?|ip[^\S\r\n]+reservada(?:[^\S\r\n]+para[^\S\r\n]+documentaci[oó]n)?)[^\S\r\n]+(?P<value>\d{1,3}(?:\.\d{1,3}){3})[^\S\r\n]*$",
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
    (
        # License plate on the next line (table format without colon separator):
        #   "Matrícula vehículo\n7338-ZWL"
        # Matches "Matrícula" + optional extra label text + newline + plate value.
        # Value restricted to NNNN-LLL format to avoid capturing prose from next line.
        "LICENSE_PLATE",
        r"(?im)^[^\S\r\n]*(?:matr[ií]cula(?:[^\S\r\n]+veh[ií]culo)?)[^\S\r\n]*\r?\n[^\S\r\n]*(?P<value>\d{4}[- \t]?[A-Z]{3})[^\S\r\n]*$",
    ),
    (
        # NASS no-colon: restrict value to the NN/NNNNNNNNNN format so the
        # pattern does not absorb subsequent entities on the same line.
        "ES_NSS",
        r"(?im)^[^\S\r\n]*(?:nass)[^\S\r\n]+(?P<value>\d{2}/\d{10})",
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
        r"\b[A-HJKLMNPQRSUVW][ \t\-]?\d{7,8}[ \t\-]?[0-9A-J]\b",
        0.80,
    ),
    (
        "IBAN_CODE",
        # Allow a single \n between digit groups: PDF text extraction sometimes
        # splits the last IBAN group onto the next line (ES91 2100 0418 4502 0005\n1332).
        r"\bES\d{2}(?:[ \t\n\-]?\d{4}){5}\b",
        0.95,
    ),
    (
        "PHONE_NUMBER",
        # Spanish phones: +34 optional prefix, must start 6/7/8/9.
        # Dot excluded from separators: IPs like 95.73.233.44 also start with 9x
        # and would be incorrectly matched.
        r"(?<!\d)(?:\+34[ \t\n\-]{0,3})?(?:6|7|8|9)(?:[ \t\n\-]{0,3}\d){8}(?!\d)",
        0.85,
    ),
    (
        "PHONE_NUMBER",
        # International phones: +CC followed by 7–13 digits with separators.
        # Requires explicit + prefix to avoid matching bare local numbers.
        # Excludes +34 (covered above) to avoid duplicate detections.
        r"(?<!\d)\+(?!34\b)(?:\d{1,3})(?:[ \t\-\.\(\)]{0,3}\d{2,5}){2,4}(?!\d)",
        0.80,
    ),
    (
        "PHONE_NUMBER",
        # US/Canada style: (NXX) NXX-XXXX or NXX-NXX-XXXX.
        r"(?<!\d)(?:\(\d{3}\)[ \t\-]?\d{3}|\d{3}[ \t\-]\d{3})[ \t\-]\d{4}(?!\d)",
        0.75,
    ),
    (
        "ES_NSS",
        # Spanish Social Security affiliation number: 2-digit province code + 10 digits.
        # Format NN/NNNNNNNNNN (e.g. 46/1234567890).
        # LEARNABLE: province code validation (01–52) would cut false-positive rate further.
        r"\b\d{2}/\d{10}\b",
        0.88,
    ),
    (
        "URL",
        # Trim trailing sentence punctuation (.,;:!?) that is not part of the URL.
        r"\bhttps?://[^\s<>()]+[^\s<>().,;:!?]|\bwww\.[^\s<>()]+[^\s<>().,;:!?]",
        0.80,
    ),
    (
        "IP_ADDRESS",
        r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b",
        0.75,
    ),
    (
        "DATE_TIME",
        # DD/MM/YYYY administrative dates (Spanish/Valencian standard format).
        # Heuristic: 4-digit year in 1900–2099 keeps the false-positive rate low
        # on legal citations (e.g. "art 3.1.c ET y 1255 CC").
        # Optional time component HH:MM captures "04/05/2026 10:30" as one entity
        # so the time portion is not left visible after the date is redacted.
        r"\b(?:0?[1-9]|[12]\d|3[01])/(?:0?[1-9]|1[0-2])/(?:19|20)\d{2}(?:[^\S\r\n]+(?:[01]?\d|2[0-3]):[0-5]\d)?\b",
        0.90,
    ),
    (
        "DATE_TIME",
        # Written-out dates in Spanish and Valencian/Catalan:
        # "9 de febrero de 2021", "15 de febrer de 2020", "16 de junio de 2024".
        # Month list covers all 12 months in both languages plus common variants.
        r"(?i)\b(?:0?[1-9]|[12]\d|3[01])\s+de\s+"
        r"(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre"
        r"|gener|febrer|mar[çc]|maig|juny|juliol|agost|setembre|novembre|desembre)"
        r"\s+de\s+(?:19|20)\d{2}\b",
        0.88,
    ),
    (
        "DATE_OF_BIRTH",
        # Context-aware birth date: "nacido/a el DATE", "nacida el DATE", etc.
        # Also handles form-field variant "Nacido/a el:DATE" (colon separator
        # used in compressed solicitud_generica form fields).
        # Named group "value" captures only the date so the prefix is not redacted.
        r"(?i)nacid[ao](?:/(?:[ao]|nacid[ao]))?\s+(?:el[^\S\r\n]*:?|la[^\S\r\n]*:?)[^\S\r\n]*(?P<value>"
        r"(?:0?[1-9]|[12]\d|3[01])[/.\-][^\S\r\n]*(?:0?[1-9]|1[0-2])[/.\-][^\S\r\n]*(?:19|20)\d{2}"
        r"(?:[^\S\r\n]+(?:[01]?\d|2[0-3]):[0-5]\d)?"
        r"|(?:0?[1-9]|[12]\d|3[01])\s+de\s+"
        r"(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre"
        r"|gener|febrer|mar[çc]|maig|juny|juliol|agost|setembre|novembre|desembre)"
        r"\s+de\s+(?:19|20)\d{2})",
        0.92,
    ),
    (
        "DATE_OF_BIRTH",
        # Inline label + colon + date, non-anchored.
        # Score 0.95 > DATE_TIME 0.90 ensures DOB wins in _merge_overlaps tie.
        # Covers: "nacimiento:DATE", split-label "Fecha de\nnacimiento:DATE",
        # and run-on "...Fecha nacimiento:27/01/1954 Domicilio:...".
        # Abbreviated forms: "D.N." (Data de Naixement, Catalan),
        # "F.N." (Fecha de Nacimiento), "F. nac.", "F. nacim.", "Fecha nacim."
        # handle acuerdo_adscripcion, formulario_medico, contrato_laboral formats.
        r"(?i)(?:nacimiento|D\.N\.|F\.N\.|F\.[^\S\r\n]*nac(?:im?)?\.?|F\.[^\S\r\n]*naix\.?|F\.[^\S\r\n]+nacimiento|fecha[^\S\r\n]+nac(?:im?)?\.?)[^\S\r\n]*:[^\S\r\n]*(?P<value>"
        r"(?:0?[1-9]|[12]\d|3[01])[/.\-](?:0?[1-9]|1[0-2])[/.\-](?:19|20)\d{2}"
        r"(?:[^\S\r\n]+(?:[01]?\d|2[0-3]):[0-5]\d)?)",
        0.95,
    ),
    (
        "DATE_OF_BIRTH",
        # No-colon next-line: "Fecha de nacimiento\nDD/MM/YYYY", "Nacimiento\nDD/MM/YYYY",
        # "Data de naixement\nDD/MM/YYYY".
        # Requires DATE_OF_BIRTH in the newline-allowed set of _detect_direct_regex.
        r"(?i)(?:fecha[^\S\r\n]+de[^\S\r\n]+nacimiento|fecha[^\S\r\n]+nacimiento|nacimiento|data[^\S\r\n]+de[^\S\r\n]+naixement|data[^\S\r\n]+nac\.?)[^\S\r\n]*\r?\n[^\S\r\n]*(?P<value>"
        r"(?:0?[1-9]|[12]\d|3[01])[/.\-](?:0?[1-9]|1[0-2])[/.\-](?:19|20)\d{2}"
        r"(?:[^\S\r\n]+(?:[01]?\d|2[0-3]):[0-5]\d)?)",
        0.95,
    ),
    (
        # ADDRESS run-on (Format A): "...Domicilio:Calle X, N, CP Ciudad Teléfono:..."
        # Extended to also cover "Residencia:Pza. X" and "Dirección:C/ X" inline
        # form fields (contrato_laboral, solicitud_generica compressed forms).
        # Negative lookahead on "dirección" prevents matching "Dirección General:".
        # Non-anchored; fires when label appears mid-line (^ anchor fails).
        # Stops before the next "Word:" token via lookahead.
        "ADDRESS",
        r"(?i)(?:domicilio(?:[^\S\r\n]+(?:fiscal|social|habitual))?|residencia|dom\.|direcci[oó]n(?![^\S\r\n]+general))[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n:]+?)[^\S\r\n]*(?=\r?\n|$|[^\S\r\n]*[A-Za-záéíóúÁÉÍÓÚüÜñÑ./°ºª]+[^\S\r\n]*:)",
        0.88,
    ),
    (
        # ADDRESS prose (Format C): "con domicilio a efectos de notificaciones en
        # Calle X, N, CP Ciudad", "domicilio en", and "domiciliado/a en".
        # Fix: "domiciliado/a" uses "/a" gender suffix — handle with (?:/[ao])?.
        # "en" connector after "domiciliado/a" consumed by optional group.
        # Requires an embedded 5-digit postal code to anchor the end of the value.
        "ADDRESS",
        (
            r"(?i)domicili(?:"
            r"o(?:[^\S\r\n]+(?:fiscal|social|habitual))?[^\S\r\n]+(?:a[^\S\r\n]+efectos[^\S\r\n]+de[^\S\r\n]+notificaciones[^\S\r\n]+en|en|a)"
            r"|ado?(?:/[ao])?(?:[^\S\r\n]+en)?"
            r"|ada(?:[^\S\r\n]+en)?"
            r"|at(?:/da)?(?:[^\S\r\n]+a)?"
            r"|[^\S\r\n]+a(?=[^\S\r\n])"
            r")[^\S\r\n]*\r?\n?[^\S\r\n]*(?P<value>[^\r\n:]+?\d{5}(?:[^\S\r\n]*\r?\n?[^\S\r\n]*[^\r\n,;:\d]+?)?)[^\S\r\n]*(?=\r?\n|$|[^\S\r\n]*[A-Za-záéíóúÁÉÍÓÚüÜñÑ./°ºª]+[^\S\r\n]*:|,)"
        ),
        0.88,
    ),
    # POSTAL_CODE standalone regex removed: it fired on institutional/signing
    # location postal codes (e.g. "46022 València" in document footers) that
    # GT does not annotate as personal PII, generating 600+ FPs.
    # Postal codes are now extracted only from:
    #   (a) labeled fields ("Código postal: XXXXX") via LABELED_FIELD_PATTERNS, and
    #   (b) detected ADDRESS spans via _extract_postal_codes_from_addresses.

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
        # Standalone IPv4 (octet-range validated) for access-log lines that carry
        # no "IP:" label, e.g. "10.41.64.148" in resolución/notificación bodies.
        # Moderate score so the labeled IP pattern (0.97) wins on overlap.
        "IP_ADDRESS",
        r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b",
        0.60,
    ),
    (
        "CASE_NUMBER",
        r"\b(?:EXP|REF|EXPDTE|EXPTE)[-/_.]?[A-Z0-9]{2,}(?:[-/_.][A-Z0-9]{2,})+\b",
        0.80,
    ),
    (
        "CASE_NUMBER",
        # Administrative case references of the form PREFIX/YYYY/NNNN(/NN):
        # AMO/2019/0892/00, AMO-2018-0011, EXP/2023/001, etc.
        # Requires a 4-digit year component to reduce FPs on other alphanumeric codes.
        r"\b[A-Z]{2,5}[-/]\d{4}[-/]\d{2,6}(?:[-/]\d{2})?\b",
        0.80,
    ),
    (
        "PROFESSIONAL_LICENSE",
        r"\b(?:COACV[-/_.]?\d{3,}|COA[-/_.]?[A-Z0-9]{3,}|COL[-/_.][A-Z0-9]{3,}|COLEG[-/_.][A-Z0-9]{3,})\b",
        0.80,
    ),
    (
        "PASSPORT",
        # Real passport numbers: 2–5 letters + optional single letter + 6–9 digits,
        # no dash separator (e.g. PZ3849201, ABC 123456).
        # Dashes are NOT used in real passports; allowing them causes FPs on document
        # references like "INV-2027" or "QR-000723".
        # FAKE branch keeps the synthetic test format FAKE-P-NNNNNN intact.
        r"\b(?:[A-Z]{2,5}[ \t]?[A-Z]?[ \t]?\d{6,9}|FAKE[- \t]?[A-Z]?[- \t]?\d{4,})\b",
        0.80,
    ),
    (
        "PASSPORT",
        # Non-anchored context match for run-on lines like
        # "Teléfono:947... Pasaporte:PZ2166554".  Score 0.95 prevents absorption
        # by PHONE_NUMBER labeled_field.  Named group so prefix is not redacted.
        r"(?i)pasaporte[^\S\r\n]*:[^\S\r\n]*(?P<value>[A-Z]{2,5}\d{6,9})",
        0.95,
    ),
    # CREDIT_CARD removed from DIRECT_REGEX_PATTERNS: the (\d{4}[ \t-]){3}\d{4}
    # pattern matches IBAN fragments (e.g. "0128 4441 8867 9113" from ES92 0128…).
    # When IBAN is not detected in a zone, the CC regex leaks through, causing FPs.
    # Detection is kept via NO_COLON_FIELD_PATTERNS (tarjeta: <value>) which
    # requires an explicit label and avoids IBAN fragment confusion.
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
    (
        # ADDRESS work-location: "centro/centre habitual de trabajo/treball <Org>,
        # <Dirección CP Ciudad>".  The org name may wrap a line so [\s\S]{0,140}?
        # bounds the skip; full match contains \n → needs "ADDRESS" in newline-allowed set.
        "ADDRESS",
        (
            r"(?i)(?:centro|centre)[^\S\r\n]+habitual[^\S\r\n]+de[^\S\r\n]+(?:trabajo|treball)[\s\S]{0,140}?,\s*"
            r"(?P<value>(?:Calle|C/|Avd?a?\.?|Avinguda|Avenida|Plaza|Pla[çc]a|Pza\.?|Paseo|Passeig|Ronda|Carrer|Trav(?:essia|\.)?|Cam[ií](?:no)?|Carretera|Ctra\.?|Cno\.?)"
            r"[^\r\n]*?\d{5}[^\S\r\n]*\r?\n?[^\S\r\n]*[^\r\n,.;:\d]+)"
        ),
        0.85,
    ),
    (
        # PERSON run-on labeled: "Nombre y apellidos: Pedro Ramos Ruiz  DNI:..."
        # Non-anchored; fires when label appears mid-line (^ anchor fails).
        # Stops before the next "Word:" token via lookahead (extended char class
        # to handle abbreviated labels: Tfno.: N.º: DNI/NIE:).
        "PERSON",
        r"(?i)(?:nombre[^\S\r\n]+y[^\S\r\n]+apellidos|nom[^\S\r\n]+i[^\S\r\n]+cognoms|apellidos[^\S\r\n]+y[^\S\r\n]+nombre|apellidos|representante[^\S\r\n]+legal|representant[^\S\r\n]+legal)[^\S\r\n]*:[^\S\r\n]*(?P<value>[^\r\n:]+?)[^\S\r\n]*(?=\r?\n|$|[^\S\r\n]*[A-Za-záéíóúÁÉÍÓÚüÜñÑ./°ºª]+[^\S\r\n]*:)",
        0.88,
    ),
    (
        # PERSON party-intro prose: "El/La trabajador/a, Pedro Ruiz," or
        # "La persona contratada, Ana López,".  Fires in employment contracts.
        "PERSON",
        r"(?i)(?:el/la[^\S\r\n]+(?:trabajador|treballador)/a|la[^\S\r\n]+persona[^\S\r\n]+(?:contratada|empleada|trabajadora|contractada|treballadora))[^\S\r\n]*,?[^\S\r\n]+(?P<value>[A-ZÁÉÍÓÚÑÜ][\wÀ-ÿ''\-]+(?:[^\S\r\n]+[A-ZÁÉÍÓÚÑÜ][\wÀ-ÿ''\-]+){1,3})[^\S\r\n]*,",
        0.85,
    ),
    (
        # PERSON signature-block standalone name: a 2–4 capitalised-word name on
        # its own line immediately followed by "Signatura" / "Firma" on the next
        # line, or the same line suffixed by " | " (table-style signature blocks).
        # Full match contains \n → needs "PERSON" in newline-allowed set.
        "PERSON",
        r"(?im)^[^\S\r\n]*(?P<value>[A-ZÁÉÍÓÚÑÜ][\wÀ-ÿ''\-]+(?:[^\S\r\n]+[A-ZÁÉÍÓÚÑÜ][\wÀ-ÿ''\-]+){1,3})[^\S\r\n]*(?:\r?\n[^\S\r\n]*(?:Signatura|Firma)|[^\S\r\n]*\|)",
        0.88,
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
    "COLEGIADO",
    "COLEGIADA",
    "COLEGIADO NUM",
    "COLEGIADA NUM",
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
    # Role/job labels detected by NER as PERSON
    "INVESTIGADOR",
    "INVESTIGADORA",
    "TESTIGO",
    "FIRMA",
    "REPRESENTANT",
    "COLEGIADO",
    "COLEGIADA",
}


SOURCE_PRIORITY = {
    "labeled_field": 5,
    "no_colon_labeled_field": 4,
    "case_table_row": 4,
    "role_table_row": 3,
    "deterministic_regex": 2,
    "presidio": 1,
}

# Used by _detect_labeled_fields to re-type PERSON entities whose value is
# actually a document number (e.g. "representante:05078944S").
_DNI_FULL_RE = re.compile(r"^\d{8}[A-HJ-NP-TV-Z]$", re.IGNORECASE)
_NIE_FULL_RE = re.compile(r"^[XYZ]\d{7}[A-HJ-NP-TV-Z]$", re.IGNORECASE)

# Compact identifiers whose value never contains a comma/semicolon.  A labeled
# value that swallowed following prose (e.g. after reflow merged a wrapped line:
# "DNI: 12345678Z, domiciliada en …") is trimmed at the first such separator.
_COMPACT_ID_TYPES = {
    "ES_DNI",
    "ES_NIE",
    "ES_CIF",
    "ES_NSS",
    "POSTAL_CODE",
    "IBAN_CODE",
    "LICENSE_PLATE",
}


# Spanish/Catalan street-type lexicon for the compositional ADDRESS extractor.
_VIA_TYPES = (
    r"(?:Calle|C/|Avenida|Avd?a?\.?|Av\.|Avinguda|Carrer|Plaza|Pla[çc]a|Pza\.?|"
    r"Pl\.|Paseo|Passeig|Pg\.?|Ronda|Rda\.?|Camino|Cam[ií]|Cno\.?|Carretera|"
    r"Ctra\.?|Traves[íi]a|Travessia|Trav\.?|Glorieta|Gran[ \t]+V[ií]a|V[ií]a|"
    r"Pol[ií]gono|Pol\.|Urbanitzaci[oó]|Urbanizaci[oó]n|Urb\.|Barrio|Barri)"
)

# A street type, then up to ~80 chars of street name + number/floor (no sentence
# break), then a province-valid 5-digit postal code, then an optional capitalised
# municipality token.  Anchored on a capitalised street name to avoid prose.
_ADDRESS_GAZETTEER_RE = re.compile(
    r"(?P<addr>\b(?i:" + _VIA_TYPES + r")\.?[ \t]*"
    r"(?i:de[ \t]+|del[ \t]+|de[ \t]+la[ \t]+|de[ \t]+les[ \t]+|d'|dels[ \t]+)?"
    r"[A-ZÁÉÍÓÚÑÜÀÈÒÏÇ][^\n:;]{0,80}?"
    r"\b(?P<cp>(?:0[1-9]|[1-4]\d|5[0-2])\d{3})\b"
    r"(?:[ \t]+(?P<muni>[A-ZÁÉÍÓÚÑÜÀÈÒÏÇ][^\n,;:0-9]{1,38}))?"
    r")"
)

# Tokenises the trailing municipality candidate.
_MUNI_TOKEN_RE = re.compile(r"\S+")

# Alphabetic word tokens (incl. accented) used for PERSON gazetteer matching.
_NAME_TOKEN_RE = re.compile(r"[A-Za-zÁÉÍÓÚÑÜáéíóúñüÀÈÒÏÇàèòïç]{2,}")


def _name_tokens(value: str) -> list[str]:
    """Normalised alphabetic tokens of *value* for gazetteer name lookups."""
    return [gazetteer.normalize(m.group()) for m in _NAME_TOKEN_RE.finditer(value)]


class PiiAnalyzer:
    # Label → Presidio entity mapping shared by both NER backends.  Covers spaCy
    # labels (PER/LOC/GPE/ORG) and CAPITEL transformer labels (PER/LOC/ORG/OTH).
    _NER_ENTITY_MAPPING = {
        "PER": "PERSON",
        "PERSON": "PERSON",
        "LOC": "LOCATION",
        "GPE": "LOCATION",
        "LOCATION": "LOCATION",
        "ORG": "ORGANIZATION",
        "ORGANIZATION": "ORGANIZATION",
    }

    _NER_LABELS_TO_IGNORE = [
        "CARDINAL", "DATE", "EVENT", "LANGUAGE", "LAW", "MISC", "MONEY",
        "ORDINAL", "OTH", "O", "PERCENT", "PRODUCT", "QUANTITY", "TIME",
        "WORK_OF_ART",
    ]

    def __init__(self) -> None:
        # Engine is built lazily on the first NER call so importing/constructing
        # the analyzer never triggers a model download or load.
        self._engine: AnalyzerEngine | None = None
        self._ner_backend: str | None = None  # actual backend used after fallback

    # ------------------------------------------------------------------
    # Lazy engine construction
    # ------------------------------------------------------------------

    def _ensure_engine(self) -> None:
        if self._engine is not None:
            return

        nlp_engine, backend = self._build_nlp_engine()

        registry = RecognizerRegistry(supported_languages=SUPPORTED_LANGUAGES)
        registry.add_nlp_recognizer(nlp_engine=nlp_engine)

        for recognizer in build_spanish_pattern_recognizers():
            registry.add_recognizer(recognizer)

        self._engine = AnalyzerEngine(
            nlp_engine=nlp_engine,
            registry=registry,
            supported_languages=SUPPORTED_LANGUAGES,
        )
        self._ner_backend = backend
        logger.info("PiiAnalyzer NER backend: %s", backend)

    def _build_nlp_engine(self):
        """Build the NLP engine, falling back to spaCy if transformers fail."""
        if settings.ner_engine == "transformers":
            try:
                return self._build_transformers_engine(), "transformers"
            except Exception as exc:  # ImportError, model download/load, etc.
                logger.warning(
                    "Transformer NER unavailable (%s); falling back to spaCy.",
                    exc,
                )
        return self._build_spacy_engine(), "spacy"

    def _build_spacy_engine(self):
        nlp_config = {
            "nlp_engine_name": "spacy",
            "models": [
                {"lang_code": "es", "model_name": settings.ner_spacy_model},
            ],
            "ner_model_configuration": {
                "model_to_presidio_entity_mapping": dict(self._NER_ENTITY_MAPPING),
                "low_confidence_score_multiplier": 0.4,
                "labels_to_ignore": list(self._NER_LABELS_TO_IGNORE),
            },
        }
        return NlpEngineProvider(nlp_configuration=nlp_config).create_engine()

    def _build_transformers_engine(self):
        from presidio_analyzer.nlp_engine import TransformersNlpEngine

        if not TransformersNlpEngine.is_available:
            raise RuntimeError(
                "spacy-huggingface-pipelines not installed "
                "(pip install '.[transformers-ner]')."
            )

        nlp_config = {
            "nlp_engine_name": "transformers",
            "models": [
                {
                    "lang_code": "es",
                    "model_name": {
                        "spacy": settings.ner_spacy_model,
                        "transformers": settings.ner_transformers_model,
                    },
                },
            ],
            "ner_model_configuration": {
                "model_to_presidio_entity_mapping": dict(self._NER_ENTITY_MAPPING),
                "low_confidence_score_multiplier": 0.4,
                "labels_to_ignore": list(self._NER_LABELS_TO_IGNORE),
                "aggregation_strategy": "max",
                "alignment_mode": "expand",
            },
        }
        return NlpEngineProvider(nlp_configuration=nlp_config).create_engine()

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
            entities.extend(self._detect_address_gazetteer(text))

        entities.extend(self._detect_direct_regex(text, mode=mode))
        entities.extend(self._detect_ner_entities(text, zone_type=zone_type))

        merged = self._merge_overlaps(entities)
        merged.extend(self._extract_postal_codes_from_addresses(text, merged))
        return merged

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

        # Build the NER engine on first use (lazy: no model load at import time).
        self._ensure_engine()

        # Heuristic: spaCy es_core_news_md was trained on news text and produces
        # many false positives on dense legal prose, especially in Valencian.
        # Raising the score threshold in BODY zones reduces FP rate.
        # LEARNABLE: optimal per-zone threshold should be calibrated on a labelled
        # corpus; a domain-adapted NER model would make this unnecessary.
        is_dense_body = zone_type == "body"
        base_threshold = settings.presidio_score_threshold
        # In dense body the effective PERSON/LOC/ORG threshold is 0.75 to cut FPs.
        # When the person gazetteer is on we lower the engine threshold to base so
        # low-confidence PERSON candidates surface; they are kept below 0.75 only
        # when backed by an INE name/surname match (the 0.75 gate still applies to
        # non-PERSON types and to PERSON spans with no gazetteer evidence).
        if is_dense_body:
            ner_threshold = (
                base_threshold if settings.gazetteer_person
                else max(base_threshold, 0.75)
            )
        else:
            ner_threshold = base_threshold

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
                # PERSON is exempted: "MARTA SOLER VIDAL" is a valid all-caps
                # person name in bilingual legal contracts (party declarations).
                # LEARNABLE: max word count (4) could be tuned per document type.
                if result.entity_type != "PERSON" and self._is_section_header(value):
                    continue

                # PERSON and LOCATION spans that start with a lowercase letter are
                # NER false positives (role titles, preposition phrases, common
                # nouns like "d'enginyeria", "lloc d'adscripció").
                # Proper names and place names always start uppercase.
                if result.entity_type in {"PERSON", "LOCATION"} and value and value[0].islower():
                    continue

                # In BODY zones with dense legal prose, low-confidence and
                # single-word NER detections are rarely genuine names.  Non-PERSON
                # types keep the 0.75 threshold + the ≥2-word rule.  PERSON spans
                # are reinforced with the INE gazetteer: a span below 0.75 or with
                # a single token is kept only when matched against known Spanish
                # given names / surnames (recovers low-confidence true positives
                # without lowering the bar for unmatched spans).
                if is_dense_body and result.entity_type in NER_ALLOWED_TYPES:
                    if result.entity_type == "PERSON" and settings.gazetteer_person:
                        tokens = _name_tokens(value)
                        name_hits = sum(
                            1 for t in tokens if gazetteer.is_person_token(t)
                        )
                        gaz_ok = name_hits >= (2 if len(tokens) >= 2 else 1)
                        confident = result.score >= 0.75 and len(value.split()) >= 2
                        if not (confident or gaz_ok):
                            continue
                    else:
                        if result.score < 0.75 or len(value.split()) < 2:
                            continue

                # PERSON spans that are actually a municipality/province name
                # (INE) are toponym false positives — drop them, unless a token is
                # a known given name (a person genuinely named like a place).
                if (
                    settings.gazetteer_person
                    and result.entity_type == "PERSON"
                    and PiiAnalyzer._looks_like_toponym(value)
                ):
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

    # Colon suffix shared by all labeled-field patterns; used to generate the
    # "next-line value" variant automatically without duplicating every regex.
    _COLON_SUFFIX = r":[^\S\r\n]*(?P<value>"

    # Same suffix with an optional newline: covers "Label:\nValue" formats
    # common in administrative certificates (e.g. "Domicilio de empadronamiento:\n
    # Carrer dels Àngels, 25, ...").
    _COLON_NL_SUFFIX = r":[^\S\r\n]*(?:\r?\n[^\S\r\n]*)?(?P<value>"

    @staticmethod
    def _detect_labeled_fields(text: str, mode: RedactionMode) -> list[PiiEntity]:
        entities: list[PiiEntity] = []
        seen_spans: set[tuple[int, int]] = set()

        for entity_type, pattern in LABELED_FIELD_PATTERNS:
            # Run both inline ("Label: Value") and next-line ("Label:\nValue").
            patterns_to_try: list[str] = [pattern]
            if PiiAnalyzer._COLON_SUFFIX in pattern:
                patterns_to_try.append(
                    pattern.replace(PiiAnalyzer._COLON_SUFFIX, PiiAnalyzer._COLON_NL_SUFFIX, 1)
                )

            for p_idx, p in enumerate(patterns_to_try):
                is_next_line = p_idx > 0
                for match in re.finditer(p, text):
                    value = match.group("value")

                    if not value.strip():
                        continue

                    span = (match.start("value"), match.end("value"))
                    if span in seen_spans:
                        continue

                    # Next-line variants can incorrectly capture the label of the
                    # following field when the current field's value is empty or
                    # has been redacted (e.g. "Nombre:\nDNI: 12345678Z").
                    if is_next_line and (
                        PiiAnalyzer._starts_with_known_field_label(value)
                        or PiiAnalyzer._is_field_label_only(value)
                    ):
                        continue

                    # Re-type PERSON labeled_field values that are actually
                    # document numbers (e.g. "representante:05078944S").
                    # Without this, PERSON (priority 5) would suppress ES_DNI
                    # (priority 2) in _merge_overlaps.
                    emit_type = entity_type
                    if entity_type == "PERSON":
                        v = value.strip()
                        if _DNI_FULL_RE.fullmatch(v):
                            emit_type = "ES_DNI"
                        elif _NIE_FULL_RE.fullmatch(v):
                            emit_type = "ES_NIE"

                    # Trim compact identifiers at the first comma/semicolon: their
                    # formats never contain one, so anything after it is run-on
                    # prose absorbed when reflow merged a wrapped line.
                    if emit_type in _COMPACT_ID_TYPES:
                        cut = len(value)
                        for sep in (",", ";"):
                            i = value.find(sep)
                            if i != -1:
                                cut = min(cut, i)
                        trimmed = value[:cut].rstrip()
                        if cut < len(value):
                            if not trimmed:
                                continue
                            value = trimmed
                            span = (span[0], span[0] + len(trimmed))

                    # PHONE values must contain at least 7 digits — prevents
                    # capturing label suffixes like "de contacto:" when the
                    # no-colon PHONE pattern fires on "Teléfono de contacto:\n".
                    if emit_type == "PHONE_NUMBER" and sum(c.isdigit() for c in value) < 7:
                        continue

                    if not PiiAnalyzer._passes_validation(
                        entity_type=emit_type,
                        value=value,
                        mode=mode,
                        source="labeled_field",
                    ):
                        continue

                    seen_spans.add(span)
                    entities.append(
                        PiiEntity(
                            entity_type=emit_type,
                            start=span[0],
                            end=span[1],
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

                if entity_type == "PHONE_NUMBER" and sum(c.isdigit() for c in value) < 7:
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
                full_match = match.group(0)

                # Named group "value" allows context-aware patterns to include a
                # non-PII prefix (e.g. "nacido el") while only capturing the PII
                # portion as the entity span.
                try:
                    value = match.group("value")
                    start = match.start("value")
                    end = match.end("value")
                except IndexError:
                    value = full_match
                    start = match.start()
                    end = match.end()

                # Allow phone, IBAN, DOB, address, and person to span OCR line breaks.
                if ("\n" in full_match or "\r" in full_match) and entity_type not in {
                    "PHONE_NUMBER",
                    "IBAN_CODE",
                    "DATE_OF_BIRTH",
                    "ADDRESS",
                    "PERSON",
                }:
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
                        start=start,
                        end=end,
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

    # Spanish postal-code pattern (01000–52999) used to extract CP entities
    @staticmethod
    def _confirm_municipality(muni_text: str, province: str) -> int | None:
        """End offset (within *muni_text*) of the longest leading municipality
        whose INE province code equals *province*; None if none matches.
        """
        toks = list(_MUNI_TOKEN_RE.finditer(muni_text))
        for k in range(min(len(toks), 5), 0, -1):
            phrase = muni_text[toks[0].start():toks[k - 1].end()]
            codes = gazetteer.municipality_province_codes(gazetteer.normalize(phrase))
            if province in codes:
                return toks[k - 1].end()
        return None

    @staticmethod
    def _looks_like_toponym(value: str) -> bool:
        """True if *value* is a municipality/province name with no given-name
        token — i.e. a place misclassified as PERSON (INE gazetteer)."""
        norm = gazetteer.normalize(value)
        if norm not in gazetteer.toponyms():
            return False
        return not any(t in gazetteer.given_names() for t in norm.split())

    @staticmethod
    def _detect_address_gazetteer(text: str) -> list[PiiEntity]:
        """Compositional ADDRESS detection: <street type> … <CP> [municipio].

        Recovers unlabeled addresses (no "Domicilio:" anchor) that the
        context-based patterns miss.  The postal code's first two digits must be
        a valid province (01–52); when a recognised municipality follows whose
        INE province matches the CP, the span is extended through it and scored
        higher (boundary confirmed by the gazetteer).
        """
        if not settings.gazetteer_address:
            return []

        entities: list[PiiEntity] = []
        for m in _ADDRESS_GAZETTEER_RE.finditer(text):
            cp = m.group("cp")
            province = cp[:2]
            if province not in gazetteer.provinces():
                continue

            start = m.start("addr")
            end = m.end("cp")
            score = 0.62

            muni = m.group("muni")
            if muni:
                muni_end = PiiAnalyzer._confirm_municipality(muni, province)
                if muni_end is not None:
                    end = m.start("muni") + muni_end
                    score = 0.80

            entities.append(
                PiiEntity(
                    entity_type="ADDRESS",
                    start=start,
                    end=end,
                    score=score,
                    source="deterministic_regex",
                )
            )
        return entities

    # embedded inside ADDRESS spans after _merge_overlaps.
    _CP_IN_ADDRESS_RE = re.compile(r"\b(?:0[1-9]|[1-4]\d|5[0-2])\d{3}\b")

    @staticmethod
    def _extract_postal_codes_from_addresses(
        text: str,
        entities: list[PiiEntity],
    ) -> list[PiiEntity]:
        """Extract POSTAL_CODE entities embedded in ADDRESS spans.

        ADDRESS labeled_field (priority 5) absorbs any POSTAL_CODE direct_regex
        (priority 2) in _merge_overlaps.  Running this after the merge creates
        the POSTAL_CODE entities without going through overlap resolution again.
        """
        existing_cp = {
            (e.start, e.end) for e in entities if e.entity_type == "POSTAL_CODE"
        }
        result: list[PiiEntity] = []
        for e in entities:
            if e.entity_type != "ADDRESS":
                continue
            # Extract postal codes from any ADDRESS span that was detected by a
            # high-confidence source.  The old restriction to labeled_field only
            # was needed when a standalone ADDRESS regex ended at the postal code
            # boundary; the current prose patterns (domicilio:/domiciliado en)
            # capture the full address including the city after the CP, so
            # extracting the embedded CP is now safe for all sources.
            if e.source not in {
                "labeled_field",
                "no_colon_labeled_field",
                "deterministic_regex",
            }:
                continue
            addr_text = text[e.start : e.end]
            for m in PiiAnalyzer._CP_IN_ADDRESS_RE.finditer(addr_text):
                cp_start = e.start + m.start()
                cp_end = e.start + m.end()
                if (cp_start, cp_end) not in existing_cp:
                    result.append(
                        PiiEntity(
                            entity_type="POSTAL_CODE",
                            start=cp_start,
                            end=cp_end,
                            score=0.88,
                            source="deterministic_regex",
                        )
                    )
        return result