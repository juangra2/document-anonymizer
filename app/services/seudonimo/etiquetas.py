"""Etiquetas numeradas y consistentes: el mismo dato, la misma etiqueta.

"[PERSONA_1]", "[DNI_1]", "[TELEFONO_2]": entre corchetes, en mayusculas y sin
tildes (un modelo de lenguaje puede quitarlas, nunca ponerlas), numeradas por
tipo en el orden en que aparecen en el documento.

Dos apariciones llevan la misma etiqueta solo si coincide su forma
normalizada, que depende del tipo:
  - identificadores (DNI, NIE, CIF, IBAN, tarjeta, matricula...): sin puntos,
    guiones ni espacios, en mayusculas;
  - telefono: solo las cifras, sin el prefijo 34;
  - correo: en minusculas;
  - lo demas (nombres, direcciones...): sin distinguir mayusculas ni tildes,
    con los espacios juntados; a un nombre se le quita el tratamiento de
    delante (don, dona, Sr., Sra.).
Es el nivel seguro: "Sra. Lopez" y "Maria Lopez Perez" NO se unen.  Unir mal
es el error caro (al reconstruir se cambiarian los nombres de dos personas);
separar de mas solo hace que el modelo vea dos etiquetas para una persona.

La tabla guarda de cada etiqueta su tipo, su forma principal (la mas larga de
las que no van escritas como URL) y todas sus variantes; y, en `orden`, que
variante era cada aparicion del documento, para devolver cada una a su sitio.
Si el dato cruzaba sintaxis de Markdown ("Teresa** Navarro"), la aparicion
anota tambien como iba repartido: cuantos caracteres de sintaxis siguen a la
etiqueta y que trozo del valor iba detras de cada uno.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import unquote_plus

VERSION = 1

#: Nombre de la etiqueta por tipo: una sola palabra, sin tildes ni guiones bajos.
NOMBRES = {
    "PERSON": "PERSONA", "ES_DNI": "DNI", "ES_NIE": "NIE", "ES_CIF": "CIF", "ES_NSS": "NSS",
    "PASSPORT": "PASAPORTE", "ORGANIZATION": "EMPRESA", "ADDRESS": "DIRECCION",
    "POSTAL_CODE": "CP", "PHONE_NUMBER": "TELEFONO", "EMAIL_ADDRESS": "CORREO", "IBAN_CODE": "IBAN",
    "MONEY": "IMPORTE", "BUDGET_AMOUNT": "IMPORTE", "CREDIT_CARD": "TARJETA",
    "DATE_OF_BIRTH": "NACIMIENTO", "PLACE_OF_BIRTH": "LUGAR", "DATE_TIME": "FECHA",
    "LICENSE_PLATE": "MATRICULA", "CADASTRAL_REFERENCE": "CATASTRO", "CASE_NUMBER": "EXPEDIENTE",
    "REGISTRY_NUMBER": "REGISTRO", "CSV": "CSV", "CERT_SERIAL": "CERTIFICADO",
    "MEDICAL_ID": "SANITARIO", "POLICY_NUMBER": "POLIZA", "USER_ID": "USUARIO",
    "IP_ADDRESS": "IP", "URL": "WEB", "QR_CODE": "QR",
}
_IDENTIFICADORES = {"ES_DNI", "ES_NIE", "ES_CIF", "ES_NSS", "PASSPORT", "IBAN_CODE", "CREDIT_CARD",
                    "LICENSE_PLATE", "CADASTRAL_REFERENCE", "CSV", "CERT_SERIAL", "MEDICAL_ID",
                    "POLICY_NUMBER", "CASE_NUMBER", "REGISTRY_NUMBER"}
_TRATAMIENTO = re.compile(r"^(?:(?:don|dona|d|da|dna|sr|sra|srta|senor|senora|senyor|senyora)\b\.?\s*)+")
_EN_URL = re.compile(r"%[0-9A-Fa-f]{2}|\+")


def nombre_de(tipo: str) -> str:
    return NOMBRES.get(tipo) or re.sub(r"[^A-Z]", "", tipo.upper()) or "DATO"


def sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def normalizar(tipo: str, valor: str) -> str:
    valor = unquote_plus(valor) if _EN_URL.search(valor) and tipo not in ("PHONE_NUMBER",) else valor
    if tipo in _IDENTIFICADORES:
        return re.sub(r"[^0-9A-Za-z]", "", sin_tildes(valor)).upper()
    if tipo == "PHONE_NUMBER":
        cifras = re.sub(r"\D", "", valor)
        if len(cifras) > 9 and cifras.startswith("0034"):
            cifras = cifras[4:]
        elif len(cifras) > 9 and cifras.startswith("34"):
            cifras = cifras[2:]
        return cifras
    if tipo == "EMAIL_ADDRESS":
        return valor.strip().lower()
    plano = re.sub(r"\s+", " ", sin_tildes(valor).casefold().replace("ª", "a").replace("º", "o")).strip(" .,;:")
    if tipo == "PERSON":
        plano = _TRATAMIENTO.sub("", plano).strip() or plano
    return plano


class Asignador:
    """Reparte las etiquetas de un documento y guarda de que valor es cada una."""

    def __init__(self) -> None:
        self._por_clave: dict[tuple[str, str], str] = {}
        self._contador: dict[str, int] = {}
        self.etiquetas: dict[str, dict] = {}
        self.orden: list[list] = []

    def _de(self, tipo: str, valor: str) -> tuple[str, int]:
        nombre = nombre_de(tipo)
        clave = (nombre, normalizar(tipo, valor))
        etiqueta = self._por_clave.get(clave)
        if etiqueta is None:
            self._contador[nombre] = self._contador.get(nombre, 0) + 1
            etiqueta = f"{nombre}_{self._contador[nombre]}"
            self._por_clave[clave] = etiqueta
            self.etiquetas[etiqueta] = {"tipo": tipo, "variantes": []}
        variantes = self.etiquetas[etiqueta]["variantes"]
        if valor not in variantes:
            variantes.append(valor)
        return etiqueta, variantes.index(valor)

    def etiqueta(self, tipo: str, valor: str) -> str:
        """La etiqueta de un valor, sin anotar donde aparece."""
        return f"[{self._de(tipo, valor)[0]}]"

    def anotar(self, tipo: str, valor: str, reparto: list | None = None) -> str:
        """La etiqueta de la siguiente aparicion del documento, en orden.
        *reparto*: [[n de caracteres de sintaxis, trozo del valor que seguia], ...]."""
        etiqueta, variante = self._de(tipo, valor)
        self.orden.append([etiqueta, variante] + ([reparto] if reparto else []))
        return f"[{etiqueta}]"

    def tabla(self, formato: str, salida: bytes) -> dict:
        """La tabla del documento seudonimizado *salida* (sus bytes)."""
        etiquetas = {}
        for etiqueta, e in self.etiquetas.items():
            limpias = [v for v in e["variantes"] if not _EN_URL.search(v)] or e["variantes"]
            etiquetas[etiqueta] = {"tipo": e["tipo"], "principal": max(limpias, key=len),
                                   "variantes": list(e["variantes"])}
        return {
            "version": VERSION,
            "creada": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "formato": formato,
            "huella": hashlib.sha256(salida).hexdigest(),
            "etiquetas": etiquetas,
            "orden": [list(o) for o in self.orden],
        }
