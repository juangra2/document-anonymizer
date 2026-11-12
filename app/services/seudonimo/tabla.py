"""La tabla de seudonimos, cifrada con la contrasena del usuario.

El servidor no guarda la tabla en claro: se cifra en cuanto se genera, se
entrega con el documento y caduca y se borra con el.  La contrasena no se
guarda ni se registra: al subir el documento se deriva la clave (scrypt, lento
a proposito para frenar los intentos a ciegas) y solo la clave vive en memoria
hasta que el trabajo termina.

Fichero (.tabla): un JSON de texto con la version, los parametros de la
derivacion, la sal, el nonce y los datos cifrados con AES-256-GCM (que ademas
detecta cualquier cambio).  La cabecera con version permite cambiar de
algoritmo mas adelante.  Contrasena equivocada y fichero danado dan el mismo
error, para no dar pistas.
"""
from __future__ import annotations

import base64
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from app.core.errors import ContrasenaCortaError, TablaIlegibleError

FORMATO = "tabla-seudonimos"
VERSION = 1
MINIMO = 12
_N, _R, _P = 2 ** 15, 8, 1


__all__ = ["Cifrador", "ContrasenaCortaError", "TablaIlegibleError", "abrir_tabla", "comprobar_contrasena"]

_ILEGIBLE = "The table cannot be opened: wrong password or damaged file."


def comprobar_contrasena(contrasena: str | None) -> str:
    if not contrasena or len(contrasena) < MINIMO:
        raise ContrasenaCortaError(f"The password must have at least {MINIMO} characters.")
    return contrasena


def _clave(contrasena: str, sal: bytes, n: int = _N, r: int = _R, p: int = _P) -> bytes:
    return Scrypt(salt=sal, length=32, n=n, r=r, p=p).derive(contrasena.encode("utf-8"))


class Cifrador:
    """La clave derivada de la contrasena, lista para cifrar una tabla.  La
    contrasena no se conserva."""

    def __init__(self, contrasena: str) -> None:
        comprobar_contrasena(contrasena)
        self._sal = os.urandom(16)
        self._clave = _clave(contrasena, self._sal)

    def cifrar(self, tabla: dict) -> bytes:
        nonce = os.urandom(12)
        claro = json.dumps(tabla, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        datos = AESGCM(self._clave).encrypt(nonce, claro, FORMATO.encode())
        sobre = {
            "formato": FORMATO, "version": VERSION, "cifrado": "AES-256-GCM",
            "kdf": {"nombre": "scrypt", "n": _N, "r": _R, "p": _P,
                    "sal": base64.b64encode(self._sal).decode()},
            "nonce": base64.b64encode(nonce).decode(),
            "datos": base64.b64encode(datos).decode(),
        }
        return (json.dumps(sobre, indent=1) + "\n").encode("ascii")


def abrir_tabla(cifrada: bytes, contrasena: str) -> dict:
    """La tabla en claro.  TablaIlegibleError si la contrasena no es o el fichero esta danado."""
    try:
        sobre = json.loads(cifrada)
        if sobre["formato"] != FORMATO or sobre["version"] != VERSION or sobre["kdf"]["nombre"] != "scrypt":
            raise ValueError("formato")
        kdf = sobre["kdf"]
        if not (2 ** 14 <= int(kdf["n"]) <= 2 ** 17 and int(kdf["r"]) == 8 and 1 <= int(kdf["p"]) <= 4):
            raise ValueError("kdf")
        clave = _clave(contrasena or "", base64.b64decode(kdf["sal"]), int(kdf["n"]), int(kdf["r"]), int(kdf["p"]))
        claro = AESGCM(clave).decrypt(base64.b64decode(sobre["nonce"]), base64.b64decode(sobre["datos"]),
                                      FORMATO.encode())
        tabla = json.loads(claro)
        if not isinstance(tabla.get("etiquetas"), dict):
            raise ValueError("tabla")
        return tabla
    except (InvalidTag, ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise TablaIlegibleError(_ILEGIBLE) from exc
