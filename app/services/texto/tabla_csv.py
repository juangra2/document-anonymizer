"""CSV: se anonimiza por columnas, con la misma logica que un Excel, y sale el
mismo CSV.

Lectura: la codificacion como en un texto (codificacion.leer) y el separador
por el contenido: de punto y coma (el habitual en Espana), coma, tabulador o
barra, el que da el mismo numero de campos en mas filas.  Las comillas siguen
la norma de los CSV: un campo entre comillas puede llevar el separador, saltos
de linea y comillas dobladas (dos comillas seguidas son una).

Clasificacion: la del Excel (office/xlsx.py), sin copiarla: el CSV se presenta
como una hoja de una sola tabla (`HojaCsv`, con la misma interfaz que la hoja
de un Excel) y pasa por las mismas funciones: la fila de cabecera, las tablas
de la hoja, la ficha en vertical, la clasificacion de cada columna por su
cabecera o por su contenido, la cuenta repartida, los importes de personas y
el presupuesto de obra, y las casillas desmarcadas.  Una columna personal pasa
entera al marcador de su tipo; una de texto libre, o una celda larga suelta,
se analiza y solo cambia el dato.  En un CSV no hay formulas: un campo que
empieza por "=" es texto y se escribe tal cual, sin interpretarlo.

Escritura: se sustituye sobre el texto original, campo a campo; lo que no
cambia se queda byte a byte (separador, comillas, saltos de linea, la
cabecera).  Un campo que iba entre comillas sigue entre comillas.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from app.core.config import settings
from app.core.errors import OfficeTooLargeError
from app.schemas.report import TEXT_ENCODING_UNCERTAIN, CodedWarning, ProcessingReport
from app.services.office.docx import marcador
from app.services.office.paquete import Plazo
from app.services.texto.codificacion import leer

SEPARADORES = (";", ",", "\t", "|")
NOMBRE_HOJA = "CSV"
_NUMERO = re.compile(r"[-+]?\d[\d.,]*")


@dataclass
class Campo:
    inicio: int             # posicion en el texto, con las comillas
    fin: int
    valor: str              # sin comillas y con las comillas dobladas deshechas
    entrecomillado: bool

    # La interfaz de una celda de Excel que usa la clasificacion: no hay formulas.
    def find(self, _tag):
        return None

    def get(self, _atributo, defecto=None):
        return defecto


def _registros(texto: str, separador: str, limite: int | None = None) -> list[list[Campo]]:
    """Los registros del CSV con la posicion de cada campo en *texto*."""
    filas: list[list[Campo]] = []
    fila: list[Campo] = []
    n, i = len(texto), 0
    while i < n or (fila and i == n):
        if limite is not None and len(filas) >= limite:
            break
        inicio = i
        if i < n and texto[i] == '"':
            partes, i = [], i + 1
            while i < n:
                j = texto.find('"', i)
                if j < 0:
                    partes.append(texto[i:])
                    i = n
                    break
                partes.append(texto[i:j])
                if j + 1 < n and texto[j + 1] == '"':
                    partes.append('"')
                    i = j + 2
                    continue
                i = j + 1
                break
            valor, comillas = "".join(partes), True
            # Lo que vaya pegado tras la comilla de cierre (mal formado) va con el campo.
            while i < n and texto[i] not in (separador, "\r", "\n"):
                valor += texto[i]
                i += 1
        else:
            while i < n and texto[i] not in (separador, "\r", "\n"):
                i += 1
            valor, comillas = texto[inicio:i], False
        fila.append(Campo(inicio, i, valor, comillas))
        if i < n and texto[i] == separador:
            i += 1
            continue            # al final del texto, el bucle anade el campo vacio
        # Fin de registro: \r\n, \n o \r.
        if i < n and texto[i] == "\r":
            i += 1
        if i < n and texto[i] == "\n":
            i += 1
        filas.append(fila)
        fila = []
        if i >= n:
            break
    if fila:
        filas.append(fila)
    return filas


def separador_de(texto: str) -> str:
    """El separador que da el mismo numero de campos (mas de uno) en mas filas."""
    mejor, puntos = SEPARADORES[0], -1.0
    for sep in SEPARADORES:
        filas = [f for f in _registros(texto, sep, limite=50) if any(c.valor for c in f)]
        if not filas:
            continue
        cuentas = Counter(len(f) for f in filas)
        campos, veces = cuentas.most_common(1)[0]
        if campos < 2:
            continue
        p = veces / len(filas) + 0.001 * campos
        if p > puntos + 1e-9:
            mejor, puntos = sep, p
    return mejor


def leer_csv(texto: str) -> tuple[str, list[list[Campo]]]:
    separador = separador_de(texto)
    return separador, _registros(texto, separador)


class HojaCsv:
    """El CSV con la interfaz de una hoja de Excel (office/xlsx.Hoja)."""

    def __init__(self, filas: list[list[Campo]]) -> None:
        self.nombre = NOMBRE_HOJA
        self.celdas: dict[tuple[int, int], Campo] = {}
        for f, fila in enumerate(filas, 1):
            for col, campo in enumerate(fila, 1):
                if campo.valor.strip():
                    self.celdas[(f, col)] = campo

    def texto(self, c: Campo) -> str | None:
        return c.valor if c.valor != "" else None

    def es_texto(self, c: Campo) -> bool:
        return not _NUMERO.fullmatch(c.valor.strip())


def _escribir_campo(campo: Campo, valor: str, separador: str) -> str:
    if campo.entrecomillado or any(x in valor for x in (separador, '"', "\r", "\n")):
        return '"' + valor.replace('"', '""') + '"'
    return valor


def _segunda_opinion(filas: list[list[str]], analyzer, mode: str) -> Counter:
    """Posibles datos que se siguen leyendo, fila a fila (celdas con tabuladores),
    en bloques de unos 4000 caracteres, como la segunda opinion del Excel."""
    from app.services.office.verificador import _tipos_en

    cuenta: Counter = Counter()
    bloque, largo = [], 0
    for texto in dict.fromkeys("\t".join(v for v in fila if v) for fila in filas):
        if largo + len(texto) > 4000 and bloque:
            _tipos_en("\n".join(bloque), analyzer, mode, cuenta)
            bloque, largo = [], 0
        bloque.append(texto)
        largo += len(texto) + 1
    if bloque:
        _tipos_en("\n".join(bloque), analyzer, mode, cuenta)
    return cuenta


def comprobar_csv(ruta: Path) -> None:
    """Al subirlo: demasiadas celdas para tratarlo de una vez."""
    _, filas = leer_csv(leer(Path(ruta).read_bytes()).texto)
    n = sum(len(f) for f in filas)
    if n > settings.max_office_cells:
        raise OfficeTooLargeError(f"CSV file with {n} cells; the maximum is {settings.max_office_cells}.")


def anonimizar_csv(entrada: Path, salida: Path, analyzer, job_id: str, mode: str = "strict") -> ProcessingReport:
    from app.schemas.report import REVIEW_RESIDUAL_PII
    from app.services.office import xlsx

    empezado = perf_counter()
    plazo = Plazo()
    lectura = leer(Path(entrada).read_bytes())
    texto = lectura.texto
    separador, filas = leer_csv(texto)
    hoja = HojaCsv(filas)

    # Clasificar, como una hoja de Excel.
    personales: dict = {}
    por_filas: dict[int, str] = {}
    columnas_info: list[dict] = []
    info_de: dict = {}
    cabeceras = {0}
    vertical = xlsx._ficha_vertical(hoja)
    if vertical:
        for f, (tipo, rotulo) in vertical.items():
            if tipo not in analyzer.disabled_types:
                por_filas[f] = tipo
                if tipo not in info_de:
                    info_de[tipo] = {"sheet": NOMBRE_HOJA, "header": rotulo, "type": tipo, "cells": 0}
                    columnas_info.append(info_de[tipo])
    else:
        por_columna: dict[int, list] = {}
        for (f, col), c in hoja.celdas.items():
            por_columna.setdefault(col, []).append((f, c))
        tramos = xlsx._tablas(hoja, xlsx._fila_cabecera(hoja))
        cabeceras = {cab for cab, _ in tramos}
        for cab, fin in tramos:
            plazo.comprobar()
            xlsx._clasificar_tabla(hoja, cab, fin, por_columna, analyzer, personales, info_de, columnas_info)

    # Sustituir: el valor nuevo de cada campo que cambia.
    tipos: Counter = Counter()
    nuevos: dict[tuple[int, int], str] = {}
    quitados: set[str] = set()
    for n, ((f, col), c) in enumerate(hoja.celdas.items()):
        if n % 2000 == 0:
            plazo.comprobar()
        valor = c.valor
        if vertical:
            if col > 1 and f in por_filas:
                tipo = por_filas[f]
                if not re.fullmatch(r"\d{1,5}", valor.strip()):
                    quitados.add(valor)
                nuevos[(f, col)] = marcador(tipo)
                tipos[tipo] += 1
                info_de[tipo]["cells"] += 1
            continue
        if f in cabeceras or f <= min(cabeceras):
            continue
        tipo, cab = xlsx._tipo_en(personales, NOMBRE_HOJA, col, f)
        if tipo and tipo != xlsx.TEXTO_LIBRE:
            if tipo not in ("MONEY", "BUDGET_AMOUNT") and not re.fullmatch(r"\d{1,5}", valor.strip()):
                quitados.add(valor)
            nuevos[(f, col)] = marcador(tipo)
            tipos[tipo] += 1
            info_de[(NOMBRE_HOJA, col, cab)]["cells"] += 1
        elif hoja.es_texto(c) and (tipo == xlsx.TEXTO_LIBRE or (len(valor) >= 25 and " " in valor)):
            cambiado = xlsx._sustituir_en_texto(valor, analyzer, mode, tipos)
            if cambiado != valor:
                quitados.add(valor)
                nuevos[(f, col)] = cambiado

    # Escribir sobre el texto original, de atras adelante.
    salida_texto = texto
    for (f, col), valor in sorted(nuevos.items(), key=lambda x: hoja.celdas[x[0]].inicio, reverse=True):
        campo = hoja.celdas[(f, col)]
        salida_texto = salida_texto[:campo.inicio] + _escribir_campo(campo, valor, separador) + salida_texto[campo.fin:]
    datos = lectura.escribir(salida_texto) if lectura.cabe(salida_texto) else salida_texto.encode("utf-8")
    Path(salida).write_bytes(datos)

    # Comprobar la salida: los valores quitados y la segunda opinion.
    _, filas_salida = leer_csv(salida_texto)
    plano = "\n".join("\t".join(c.valor for c in fila) for fila in filas_salida).casefold()
    quedan = [v for v in quitados if len(v.strip()) >= 4 and v.strip().casefold() in plano]
    sospechas = _segunda_opinion([[c.valor for c in fila] for fila in filas_salida], analyzer, mode)
    avisos, codificados = [], []
    if quedan:
        avisos.append(f"{len(quedan)} replaced values still found in the output.")
    if sospechas:
        detalle = ", ".join(f"{t}: {k}" for t, k in sorted(sospechas.items()))
        avisos.append(f"Independent check: {sum(sospechas.values())} possible personal data still "
                      f"readable in the output ({detalle}).")
    residuales = len(quedan) + sum(sospechas.values())
    if lectura.dudosa:
        avisos.append(f"The text encoding was not clear; it was read as {lectura.nombre}. "
                      "Check that accents and special letters look right.")
        codificados.append(CodedWarning(code=TEXT_ENCODING_UNCERTAIN))
    return ProcessingReport(
        job_id=job_id,
        status="manual_review_required" if residuales else ("completed_with_warnings" if avisos else "completed"),
        document_type="csv", pages=1,
        entities_detected=sum(tipos.values()), entities_redacted=sum(tipos.values()),
        entities_by_type=dict(tipos), residual_entities=residuales,
        manual_review_required=bool(residuales), manual_review_reasons=[REVIEW_RESIDUAL_PII] if residuales else [],
        warnings=avisos, coded_warnings=codificados,
        replaced_columns=[i for i in columnas_info if i["cells"]],
        text_encoding=lectura.codificacion,
        redaction_strategy="csv_column_replacement",
        processing_ms=int((perf_counter() - empezado) * 1000),
    )
