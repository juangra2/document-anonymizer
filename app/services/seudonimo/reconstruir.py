"""Devolver los valores reales a un texto con etiquetas, con su tabla.

Dos casos:
  - EXACTA: el fichero es el documento seudonimizado tal cual salio (su huella
    coincide con la de la tabla).  Cada etiqueta vuelve a ser la variante que
    habia en ese sitio ("TERESA NAVARRO GIL" donde iba en mayusculas): en texto
    y Markdown se recupera el original byte a byte.
  - TOLERANTE: cualquier otro texto (el resumen de un modelo de lenguaje).  La
    etiqueta se reconoce aunque el modelo la haya alterado, y se cambia por la
    forma principal del dato:
      entre corchetes, llaves, < > o corchetes escapados o codificados como
      URL, sin distinguir mayusculas ("[Persona_1]", "{PERSONA_1}",
      "\\[PERSONA\\_1\\]"), con otro separador ("[PERSONA 1]", "[PERSONA-1]",
      "[PERSONA1]") o con el tipo traducido ("[PERSON_1]", "[TELÈFON_2]");
      sin corchetes solo en mayusculas y sin espacio ("PERSONA_1",
      "**PERSONA_1**"): "la persona 1 de la lista" es texto normal.
    Nunca adivina: una etiqueta que no esta en la tabla ("[PERSONA_9]",
    "[PERSONA_DNI_1]") se deja como esta y se avisa.
El resumen de lo hecho lleva etiquetas y formas, nunca valores.
"""
from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from app.services.seudonimo.etiquetas import NOMBRES, sin_tildes

#: Como llama un modelo a cada tipo (castellano, valenciano, ingles), sin tildes.
SINONIMOS = {
    "PERSONA": ("PERSON", "PERSONE", "NOMBRE", "NOM", "NAME"),
    "DNI": ("NIF",),
    "TELEFONO": ("TELEFON", "PHONE", "TELEPHONE", "TEL", "MOVIL", "MOBIL", "PHONENUMBER"),
    "CORREO": ("EMAIL", "MAIL", "CORREU", "EMAILADDRESS"),
    "DIRECCION": ("ADRECA", "ADDRESS", "DOMICILIO", "DOMICILI"),
    "EMPRESA": ("ORGANIZATION", "ORGANIZACION", "ORGANITZACIO", "COMPANY", "ORG"),
    "IMPORTE": ("IMPORT", "AMOUNT", "MONEY"),
    "FECHA": ("DATA", "DATE"),
    "NACIMIENTO": ("NAIXEMENT", "BIRTHDATE", "BIRTH"),
    "LUGAR": ("LLOC", "PLACE"),
    "MATRICULA": ("PLATE", "LICENSEPLATE"),
    "EXPEDIENTE": ("EXPEDIENT", "CASE", "CASENUMBER"),
    "PASAPORTE": ("PASSAPORT", "PASSPORT"),
    "TARJETA": ("TARGETA", "CARD", "CREDITCARD"),
    "WEB": ("URL", "LINK", "ENLACE", "ENLLAC"),
    "CP": ("POSTALCODE", "ZIP", "CODIGOPOSTAL", "CODIPOSTAL"),
    "USUARIO": ("USUARI", "USER"),
    "REGISTRO": ("REGISTRE", "REGISTRY"),
    "CERTIFICADO": ("CERTIFICAT", "CERTIFICATE"),
    "POLIZA": ("POLISSA", "POLICY"),
    "CATASTRO": ("CADASTRE", "CADASTRAL"),
    "SANITARIO": ("SANITARI", "MEDICAL"),
    "IBAN": ("CUENTA", "COMPTE", "ACCOUNT"),
}
_TIPO = {n: n for n in NOMBRES.values()}
for _nombre, _otros in SINONIMOS.items():
    for _o in _otros:
        _TIPO.setdefault(_o, _nombre)

_L = "A-Za-zÀ-ÖØ-öø-ÿ"
_PARES = {"[": "]", "\\[": "\\]", "{": "}", "<": ">", "%5b": "%5d"}
_ETIQUETA = re.compile(
    rf"(?P<a>\\?\[|%5[Bb]|\{{|<)[ ]?(?P<tipo>[{_L}]{{2,20}})(?P<sep>\\?_|[ \-]?)(?P<n>\d{{1,5}})[ ]?(?P<c>\\?\]|%5[Dd]|\}}|>)"
    rf"|(?<![{_L}0-9_\[\\])(?P<tipo2>[A-ZÀ-ÖØ-Þ]{{2,20}})(?P<sep2>\\?_|-|)(?P<n2>\d{{1,5}})(?![{_L}0-9_])")
#: Algo con forma de etiqueta entre corchetes que no se ha resuelto.
_PARECE = re.compile(r"\\?\[[A-ZÀ-ÖØ-Þ]+(?:\\?_[A-ZÀ-ÖØ-Þ]+)*\\?_\d+\\?\]")
#: La etiqueta tal como la escribe el servicio (tambien escapada o en un destino).
_ESTRICTA = re.compile(r"\\\[([A-Z]+_\d+)\\\]|\[([A-Z]+_\d+)\]|%5B([A-Z]+_\d+)%5D")


@dataclass
class Reconstruccion:
    exacta: bool = False
    texto: str | None = None            # texto reconstruido (texto, Markdown o pegado)
    datos: bytes | None = None          # fichero reconstruido
    resueltas: int = 0
    alteradas: list[dict] = field(default_factory=list)      # {"forma", "etiqueta"}
    no_resueltas: list[str] = field(default_factory=list)
    no_usadas: list[str] = field(default_factory=list)
    otro_documento: bool = False        # fichero del mismo formato, pero no el que salio

    def resumen(self) -> dict:
        """Para la respuesta y la pantalla: etiquetas y formas, nunca valores."""
        return {"exact": self.exacta, "resolved": self.resueltas,
                "altered": [{"form": a["forma"], "label": a["etiqueta"]} for a in self.alteradas],
                "unresolved": self.no_resueltas, "unused": self.no_usadas,
                "other_document": self.otro_documento}


class _Tolerante:
    def __init__(self, tabla: dict) -> None:
        self.etiquetas = tabla["etiquetas"]
        self.r = Reconstruccion()
        self.usadas: set[str] = set()
        self._alteradas: set[tuple[str, str]] = set()

    def _cambio(self, m: re.Match) -> str:
        forma = m.group(0)
        con_corchetes = m.group("a") is not None
        if con_corchetes:
            if _PARES.get(m.group("a").lower()) != m.group("c").lower():
                return forma
            tipo, sep, n = m.group("tipo"), m.group("sep"), m.group("n")
        else:
            tipo, sep, n = m.group("tipo2"), m.group("sep2"), m.group("n2")
        nombre = _TIPO.get(sin_tildes(tipo).upper())
        if nombre is None:
            return forma
        etiqueta = f"{nombre}_{int(n)}"
        if etiqueta not in self.etiquetas:
            # Con forma clara de etiqueta, se avisa; "CP46004" es texto normal.
            if (con_corchetes and m.group("a") in ("[", "\\[")) or sep in ("_", "\\_"):
                self._no_resuelta(forma)
            return forma
        self.usadas.add(etiqueta)
        self.r.resueltas += 1
        if forma != f"[{etiqueta}]" and (forma, etiqueta) not in self._alteradas:
            self._alteradas.add((forma, etiqueta))
            self.r.alteradas.append({"forma": forma, "etiqueta": etiqueta})
        return self.etiquetas[etiqueta]["principal"]

    def _no_resuelta(self, forma: str) -> None:
        if forma not in self.r.no_resueltas:
            self.r.no_resueltas.append(forma)

    def cambios(self, texto: str) -> tuple[list[tuple[int, int, str]], str]:
        """(inicio, fin, valor) de cada etiqueta resuelta, y el texto ya cambiado."""
        cambios, piezas, pos = [], [], 0
        for m in _ETIQUETA.finditer(texto):
            valor = self._cambio(m)
            if valor != m.group(0):
                cambios.append((m.start(), m.end(), valor))
            piezas += [texto[pos:m.start()], valor]
            pos = m.end()
        nuevo = "".join(piezas) + texto[pos:]
        for m in _PARECE.finditer(nuevo):
            self._no_resuelta(m.group(0))
        return cambios, nuevo

    def texto(self, texto: str) -> str:
        return self.cambios(texto)[1]

    def cerrar(self) -> Reconstruccion:
        self.r.no_usadas = [e for e in self.etiquetas if e not in self.usadas]
        return self.r


def reconstruir_texto(texto: str, tabla: dict) -> Reconstruccion:
    """Texto pegado (el resumen de un modelo): reconstruccion tolerante."""
    t = _Tolerante(tabla)
    nuevo = t.texto(texto)
    r = t.cerrar()
    r.texto = nuevo
    return r


def _exacta_texto(texto: str, tabla: dict) -> str | None:
    """Cada etiqueta por la variante que habia en su sitio, o None si las
    etiquetas del texto no son, una a una, las que anoto la tabla."""
    orden = tabla.get("orden") or []
    encontradas = list(_ESTRICTA.finditer(texto))
    if [m.group(1) or m.group(2) or m.group(3) for m in encontradas] != [o[0] for o in orden]:
        return None
    piezas, pos = [], 0
    for m, (etiqueta, variante, *reparto) in zip(encontradas, orden):
        valor = tabla["etiquetas"][etiqueta]["variantes"][variante]
        reparto = reparto[0] if reparto else []
        # El dato cruzaba sintaxis: su primer trozo, y cada uno de los demas
        # detras de la sintaxis que se quedo tras la etiqueta.
        primero = len(valor) - sum(len(trozo) for _, trozo in reparto)
        piezas += [texto[pos:m.start()], valor[:primero]]
        pos = m.end()
        for n, trozo in reparto:
            piezas += [texto[pos:pos + n], trozo]
            pos += n
    return "".join(piezas) + texto[pos:]


def _docx(datos: bytes, tabla: dict, exacta: bool) -> Reconstruccion:
    from app.services.office.docx import _PARTES_DE_TEXTO, _sustituir, bloques
    from app.services.office.paquete import Paquete

    with tempfile.TemporaryDirectory() as tmp:
        entrada, salida = Path(tmp) / "e.docx", Path(tmp) / "s.docx"
        entrada.write_bytes(datos)
        paq = Paquete.abrir(entrada)
        todos = [b for nombre in sorted(n for n in paq.nombres("word/") if _PARTES_DE_TEXTO.match(n))
                 for b in bloques(paq.xml(nombre))]
        orden = tabla.get("orden") or []
        if exacta:
            encontradas = [(b, m) for b in todos for m in _ESTRICTA.finditer(b.texto)]
            exacta = [m.group(2) for _, m in encontradas] == [o[0] for o in orden]
        if exacta:
            r = Reconstruccion(exacta=True, resueltas=len(orden))
            for (b, m), (etiqueta, variante, *_) in reversed(list(zip(encontradas, orden))):
                _sustituir(b, m.start(), m.end(), tabla["etiquetas"][etiqueta]["variantes"][variante])
            r.no_usadas = []
        else:
            t = _Tolerante(tabla)
            for b in todos:
                for a, c, valor in reversed(t.cambios(b.texto)[0]):
                    _sustituir(b, a, c, valor)
            r = t.cerrar()
        paq.guardar(salida)
        r.datos = salida.read_bytes()
    return r


def reconstruir_fichero(datos: bytes, formato: str, tabla: dict) -> Reconstruccion:
    """Un fichero con etiquetas (txt, md o docx): exacta si es el documento que
    salio con esa tabla; si no, tolerante."""
    misma = hashlib.sha256(datos).hexdigest() == tabla.get("huella") and formato == tabla.get("formato")
    if formato == "docx":
        r = _docx(datos, tabla, misma)
        r.otro_documento = not misma
        return r
    from app.services.texto.codificacion import leer

    lectura = leer(datos)
    nuevo = _exacta_texto(lectura.texto, tabla) if misma else None
    if nuevo is not None:
        r = Reconstruccion(exacta=True, resueltas=len(tabla.get("orden") or []))
    else:
        t = _Tolerante(tabla)
        nuevo = t.texto(lectura.texto)
        r = t.cerrar()
        r.otro_documento = not misma
    r.texto = nuevo
    # Un valor que no cabe en la codificacion del fichero: la salida va en UTF-8.
    r.datos = lectura.escribir(nuevo) if lectura.cabe(nuevo) else nuevo.encode("utf-8")
    return r
