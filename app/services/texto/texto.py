"""Texto plano (.txt) y Markdown (.md): el dato se sustituye por su tipo y el
resultado es el mismo formato, en la misma codificacion.

Como en Word: el texto se analiza en trozos de lineas (hasta unos 4000
caracteres), cada valor detectado se busca despues en todo el documento y se
sustituye donde aparezca, y la salida se relee entera (segunda opinion).  Los
marcadores son los de Word ("[DNI]", "[NOMBRE]").

En Markdown se sustituye sobre el texto original y la sintaxis no se borra
nunca (markdown.zonas): se analizan el texto, el codigo, el HTML y los
destinos de los enlaces y las imagenes.  En un destino el marcador va sin
espacios, para que el enlace siga siendo un enlace; en el texto, un marcador
seguido de "(", "[" o ":" se escapa ("\\[NOMBRE\\]"), porque si no seria un
enlace o una definicion de enlace y desapareceria al verlo.

Un texto no tiene metadatos: "solo quitar metadatos" lo devuelve tal cual y lo
dice.

Seudonimizar (con un `asignador` de etiquetas): lo mismo, pero cada dato pasa
a su etiqueta numerada ("[PERSONA_1]") en vez de al marcador de su tipo, y el
asignador anota las apariciones en el orden del documento, para poder
devolver cada valor a su sitio.  Un enlace automatico conserva sus < >.
"""
from __future__ import annotations

import re
import shutil
import unicodedata
from collections import Counter
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from urllib.parse import quote, quote_plus

from app.schemas.report import (
    CONTENT_NOT_REVIEWED,
    FORMAT_WITHOUT_METADATA,
    TEXT_ENCODING_UNCERTAIN,
    CodedWarning,
    ProcessingReport,
)
from app.services.office.docx import _barrido, _spans, marcador
from app.services.office.paquete import Plazo
from app.services.texto.codificacion import Lectura, leer
from app.services.texto.markdown import _AUTOENLACE, lineas, para_analizar, sin_marcado, zonas

_MAXIMO = 4000


def _trozos(texto: str) -> list[tuple[int, int]]:
    """Lineas seguidas en trozos de hasta unos 4000 caracteres."""
    salida, inicio, fin = [], 0, 0
    for a, b in lineas(texto):
        if b - inicio > _MAXIMO and fin > inicio:
            salida.append((inicio, fin))
            inicio = a
        fin = b
    if fin > inicio:
        salida.append((inicio, fin))
    return salida


def _zonas(texto: str, formato: str) -> str:
    return zonas(texto) if formato == "md" else sin_marcado(texto)


def _cabe(marca: str, lectura: Lectura) -> str:
    """El marcador en la codificacion del fichero: sin tildes si no caben."""
    if lectura.cabe(marca):
        return marca
    sin = "".join(c for c in unicodedata.normalize("NFKD", marca.replace("º", "o"))
                  if not unicodedata.combining(c))
    return sin if lectura.cabe(sin) else sin.encode("ascii", "replace").decode("ascii")


def _sustituir(texto: str, zona: str, a: int, b: int, marca: str, formato: str,
               reversible: bool = False) -> str:
    """texto[a:b] por *marca*, sin borrar la sintaxis que haya dentro."""
    piezas, puesta, donde = [], False, "t"
    for k in range(a, b):
        if zona[k] in "sp":
            piezas.append(texto[k])
        elif not puesta:
            piezas.append("\0")
            puesta, donde = True, zona[k]
    if not puesta:
        return texto
    if formato == "md":
        if donde == "a":
            if reversible:      # se queda entre < >, para volver a ser el enlace
                return texto[:a] + "".join(piezas).replace("\0", marca) + texto[b:]
            return _en_autoenlace(texto, zona, a, b, "".join(piezas), marca)
        if donde == "d":
            marca = _en_url(marca)
        elif donde == "t" and texto[b:b + 1] in ("(", "[", ":"):
            marca = "\\[" + marca[1:-1] + "\\]"
    return texto[:a] + "".join(piezas).replace("\0", marca) + texto[b:]


def _trozos_de(texto: str, zona: str, a: int, b: int) -> list[tuple[int, str]]:
    """texto[a:b] repartido: (caracteres de sintaxis que lleva delante, trozo de texto)."""
    salida: list[list] = []
    sintaxis, anterior = 0, True
    for k in range(a, b):
        if zona[k] in "sp":
            sintaxis, anterior = sintaxis + 1, True
        elif anterior:
            salida.append([sintaxis, texto[k]])
            sintaxis, anterior = 0, False
        else:
            salida[-1][1] += texto[k]
    return [(n, t) for n, t in salida]


def _en_url(marca: str) -> str:
    """El marcador dentro de un destino: "[CORREO]" codificado como URL, para que
    el enlace siga siendo un enlace y al abrirlo se lea "[CORREO]"."""
    return quote(marca, safe="")


def _en_autoenlace(texto: str, zona: str, a: int, b: int, piezas: str, marca: str) -> str:
    """En un enlace automatico (<correo@dominio.es>) el contenido es a la vez el
    destino y lo que se ve.  Si con el marcador codificado sigue siendo un
    enlace (<https://sede.es/?dni=%5BDNI%5D>), se queda asi; si no (el correo
    entero era el dato), se quitan los < > y queda el marcador como texto."""
    izq, der = a, b
    while izq > 0 and zona[izq - 1] == "a":
        izq -= 1
    while der < len(zona) and zona[der] == "a":
        der += 1
    dentro = texto[izq:a] + piezas.replace("\0", _en_url(marca)) + texto[b:der]
    if _AUTOENLACE.fullmatch(f"<{dentro}>") or not (texto[izq - 1:izq] == "<" and texto[der:der + 1] == ">"):
        return texto[:izq] + dentro + texto[der:]
    return texto[:izq - 1] + texto[izq:a] + piezas.replace("\0", marca) + texto[b:der] + texto[der + 1:]


def _variantes(valor: str) -> list[str]:
    """El valor y como va escrito dentro de una URL (Teresa%20Navarro)."""
    salida = [valor]
    for v in (quote(valor, safe="@"), quote_plus(valor, safe="@"),
              valor.replace(" ", "%20"), valor.replace(" ", "+")):
        if v not in salida:
            salida.append(v)
    return salida


def _normal(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip().casefold()


def _revisar(texto: str, formato: str, analyzer, mode: str, valores: list[str]):
    """Valores sustituidos que siguen y segunda opinion sobre la salida."""
    from app.schemas.report import REVIEW_RESIDUAL_PII
    from app.services.office.verificador import _tipos_en

    analizado, _ = para_analizar(texto, _zonas(texto, formato))
    plano = _normal(analizado)
    quedan = [v for v in valores if len(_normal(v)) >= 4 and _normal(v) in plano]
    sospechas: Counter = Counter()
    for a, b in _trozos(analizado):
        _tipos_en(analizado[a:b], analyzer, mode, sospechas)
    avisos = []
    if quedan:
        avisos.append(f"{len(quedan)} replaced values still found in the output.")
    if sospechas:
        detalle = ", ".join(f"{t}: {n}" for t, n in sorted(sospechas.items()))
        avisos.append(f"Independent check: {sum(sospechas.values())} possible personal data still "
                      f"readable in the output ({detalle}).")
    residuales = len(quedan) + sum(sospechas.values())
    return residuales, avisos, [REVIEW_RESIDUAL_PII] if residuales else []


def anonimizar_texto(entrada: Path, salida: Path, analyzer, job_id: str, mode: str = "strict",
                     formato: str = "txt", asignador=None) -> ProcessingReport:
    empezado = perf_counter()
    plazo = Plazo()
    lectura = leer(Path(entrada).read_bytes())
    texto = lectura.texto
    zona = _zonas(texto, formato)
    analizado, origen = para_analizar(texto, zona)

    por_trozo = []
    valores: dict[str, str] = {}
    for n, (a, b) in enumerate(_trozos(analizado)):
        if n % 50 == 0:
            plazo.comprobar()
        bloque = SimpleNamespace(texto=analizado[a:b])
        spans = _spans(bloque, analyzer, mode)
        por_trozo.append((a, bloque, spans))
        for i, j, tipo in spans:
            valor = bloque.texto[i:j].strip()
            if len(valor) >= 4:
                for v in _variantes(valor):
                    valores.setdefault(v, tipo)

    todos = []
    for a, bloque, spans in por_trozo:
        # De la posicion en lo analizado a la del original.
        todos += [(origen[a + i], origen[a + j - 1] + 1, tipo)
                  for i, j, tipo in _barrido(bloque, valores, spans) if j > i]
    tipos: Counter = Counter()
    todos = [(i, j, tipo) for i, j, tipo in sorted(todos) if any(zona[k] not in "sp" for k in range(i, j))]
    marcas = {}
    if asignador is not None:
        # Las etiquetas se reparten y se anotan en el orden del documento; el
        # valor es el texto sin la sintaxis que tenga dentro.
        for i, j, tipo in todos:
            trozos = _trozos_de(texto, zona, i, j)
            marcas[(i, j)] = asignador.anotar(tipo, "".join(t for _, t in trozos),
                                              [[n, t] for n, t in trozos[1:]])
    for i, j, tipo in reversed(todos):
        marca = marcas.get((i, j)) or _cabe(marcador(tipo), lectura)
        texto = _sustituir(texto, zona, i, j, marca, formato, reversible=asignador is not None)
        tipos[tipo] += 1

    Path(salida).write_bytes(lectura.escribir(texto))
    residuales, avisos, motivos = _revisar(texto, formato, analyzer, mode,
                                           [v for v, _ in valores.items()])
    codificados = []
    if lectura.dudosa:
        avisos.append(f"The text encoding was not clear; it was read as {lectura.nombre}. "
                      "Check that accents and special letters look right.")
        codificados.append(CodedWarning(code=TEXT_ENCODING_UNCERTAIN))
    return ProcessingReport(
        job_id=job_id,
        status="manual_review_required" if residuales else ("completed_with_warnings" if avisos else "completed"),
        document_type=formato,
        pages=1,
        entities_detected=sum(tipos.values()),
        entities_redacted=sum(tipos.values()),
        entities_by_type=dict(tipos),
        residual_entities=residuales,
        manual_review_required=bool(residuales),
        manual_review_reasons=motivos,
        warnings=avisos,
        coded_warnings=codificados,
        text_encoding=lectura.codificacion,
        treatment="pseudonymize" if asignador is not None else "anonymize",
        redaction_strategy="text_replacement",
        processing_ms=int((perf_counter() - empezado) * 1000),
    )


def solo_metadatos_texto(entrada: Path, salida: Path, job_id: str = "", formato: str = "txt"
                         ) -> ProcessingReport:
    """Un texto no tiene metadatos: sale tal cual, y el informe lo dice."""
    empezado = perf_counter()
    lectura = leer(Path(entrada).read_bytes())
    shutil.copyfile(entrada, salida)
    return ProcessingReport(
        job_id=job_id, status="completed_with_warnings", document_type=formato, treatment="metadata_only",
        pages=1,
        warnings=["Metadata removed only: the content has NOT been reviewed or anonymized.",
                  "This format has no metadata: the file is returned unchanged."],
        coded_warnings=[CodedWarning(code=CONTENT_NOT_REVIEWED), CodedWarning(code=FORMAT_WITHOUT_METADATA)],
        text_encoding=lectura.codificacion,
        redaction_strategy="metadata_only", processing_ms=int((perf_counter() - empezado) * 1000),
    )
