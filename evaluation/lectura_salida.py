"""Comprueba en el PDF de SALIDA que el dato ya no se lee.

Por que hace falta
------------------
La evaluacion compara la lista de entidades que devuelve el servicio contra las
etiquetas del corpus: mide DETECCION.  Nunca abre el documento anonimizado, asi
que una barra negra pintada a tres centimetros del dato cuenta como acierto.
Ese defecto existe de verdad —el camino hibrido coloca las barras desplazadas—
y podia haber vivido escondido indefinidamente.

Aqui se abre el PDF de salida y se busca el dato.  Es caro (rasterizar mas OCR
por pagina), asi que se pasa sobre una MUESTRA, no sobre los corpus enteros.

Las dos condiciones que hacen fiable la medida
----------------------------------------------
1. **Enderezar antes de leer, por cuenta propia.**  Un OCR no lee texto girado:
   sobre una pagina puesta de lado devuelve basura y "no encuentro el dato"
   pareceria un exito.  Se prueban los cuatro angulos y se toma la mejor
   lectura.  No se usa el enderezador del pipeline: si el que falla es el, se
   heredaria su error y la comprobacion volveria a decir que todo esta bien.

2. **"No lo encuentro" no basta.**  Si el dato tampoco se lee en la ENTRADA, la
   comprobacion no demuestra nada: puede que el OCR simplemente no sepa leerlo.
   Ese caso se marca NO CONCLUYENTE y queda fuera del porcentaje, con su numero
   publicado al lado.  Un numero de no concluyentes que crece significa que la
   que falla es la medida, no el pipeline.
"""
from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import fitz

# Resultados posibles para un dato.
TAPADO = "tapado"
FUGA = "fuga"
NO_CONCLUYENTE = "no_concluyente"

_DPI_LECTURA = 200
_ANGULOS = (0, 90, 180, 270)
# Calidad de una lectura: caracteres por token.  Medido sobre el corpus, una
# pagina bien orientada da 14-35 y una girada 2-3.
_CALIDAD_ACEPTABLE = 8.0
_UMBRAL_OSCURO = 60


# --------------------------------------------------------------------------
# lectura
# --------------------------------------------------------------------------

def _aplanar(texto: str) -> str:
    """Mayusculas sin acentos ni separadores: el OCR degrada tildes y guiones."""
    plegado = unicodedata.normalize("NFKD", texto.upper())
    limpio = "".join(c for c in plegado if not unicodedata.combining(c))
    return "".join(c for c in limpio if c.isalnum())


def _calidad(tokens) -> float:
    if not tokens:
        return 0.0
    caracteres = sum(len(t.text) for t in tokens)
    return caracteres / len(tokens)


def _ocr(imagen_png: bytes, indice: int):
    from app.services.ocr.factory import get_word_ocr_engine
    from app.services.storage.temp_storage import scratch_dir

    with scratch_dir(prefix="lectura_salida_") as td:
        ruta = Path(td) / "p.png"
        ruta.write_bytes(imagen_png)
        return get_word_ocr_engine(None).analyze_image(image_path=ruta, page_index=indice)


def leer_pagina(pdf: Path, indice: int) -> tuple[str, int]:
    """Texto de una pagina, enderezandola si hace falta.

    Devuelve (texto, angulo_aplicado).  Prueba primero sin girar y solo recurre
    a los otros angulos si la lectura sale de mala calidad, que es lo que hace
    barata la comprobacion sobre paginas normales.
    """
    with fitz.open(pdf) as doc:
        pagina = doc[indice]
        base = pagina.get_pixmap(matrix=fitz.Matrix(_DPI_LECTURA / 72.0,
                                                    _DPI_LECTURA / 72.0))
        png = base.tobytes("png")

    mejor_tokens = _ocr(png, indice).tokens
    mejor_angulo = 0
    if _calidad(mejor_tokens) >= _CALIDAD_ACEPTABLE:
        return " ".join(t.text for t in mejor_tokens), 0

    from io import BytesIO

    from PIL import Image

    imagen = Image.open(BytesIO(png))
    for angulo in _ANGULOS[1:]:
        girada = imagen.rotate(angulo, expand=True)
        tampon = BytesIO()
        girada.save(tampon, format="PNG")
        tokens = _ocr(tampon.getvalue(), indice).tokens
        if _calidad(tokens) > _calidad(mejor_tokens):
            mejor_tokens, mejor_angulo = tokens, angulo

    return " ".join(t.text for t in mejor_tokens), mejor_angulo


def leer_documento(pdf: Path) -> tuple[str, list[int]]:
    """Todo el texto legible de un PDF y los angulos que hubo que aplicar."""
    with fitz.open(pdf) as doc:
        paginas = doc.page_count
    textos, angulos = [], []
    for indice in range(paginas):
        texto, angulo = leer_pagina(pdf, indice)
        textos.append(texto)
        angulos.append(angulo)
    return " ".join(textos), angulos


def negrura(pdf: Path) -> list[float]:
    """Fraccion de pixeles oscuros por pagina.

    Distingue una redaccion TOTAL (cerca de 1.0) de una selectiva (unas
    centesimas).  Sin esto, una pagina entera en negro y una pagina intacta se
    parecen demasiado: en las dos el OCR no encuentra el dato.
    """
    valores = []
    with fitz.open(pdf) as doc:
        for pagina in doc:
            pix = pagina.get_pixmap(matrix=fitz.Matrix(0.5, 0.5),
                                    colorspace=fitz.csGRAY)
            datos = pix.samples
            oscuros = sum(1 for b in datos if b < _UMBRAL_OSCURO)
            valores.append(round(oscuros / max(1, len(datos)), 4))
    return valores


# --------------------------------------------------------------------------
# comprobacion
# --------------------------------------------------------------------------

@dataclass
class ResultadoDocumento:
    documento: str
    tapados: int = 0
    fugas: int = 0
    no_concluyentes: int = 0
    detalle: list[dict] = field(default_factory=list)
    negrura_por_pagina: list[float] = field(default_factory=list)
    angulos_entrada: list[int] = field(default_factory=list)
    angulos_salida: list[int] = field(default_factory=list)

    @property
    def concluyentes(self) -> int:
        return self.tapados + self.fugas

    @property
    def porcentaje_tapado(self) -> float | None:
        """Solo sobre los concluyentes.  None cuando no hay ninguno."""
        if self.concluyentes == 0:
            return None
        return round(100.0 * self.tapados / self.concluyentes, 2)


def comprobar_documento(entrada: Path, salida: Path, etiqueta: dict) -> ResultadoDocumento:
    """Busca cada dato de la etiqueta en la entrada y en la salida."""
    resultado = ResultadoDocumento(documento=entrada.stem)

    texto_entrada, resultado.angulos_entrada = leer_documento(entrada)
    texto_salida, resultado.angulos_salida = leer_documento(salida)
    resultado.negrura_por_pagina = negrura(salida)

    plano_entrada = _aplanar(texto_entrada)
    plano_salida = _aplanar(texto_salida)

    vistos: set[tuple[str, str]] = set()
    for entidad in etiqueta.get("entities", []):
        valor = entidad.get("text", "")
        tipo = entidad.get("type", "")
        if not valor or (valor, tipo) in vistos:
            continue
        vistos.add((valor, tipo))

        aguja = _aplanar(valor)
        if len(aguja) < 4:
            # Cadenas muy cortas casan con cualquier cosa: no se miden.
            continue

        en_entrada = aguja in plano_entrada
        en_salida = aguja in plano_salida

        if not en_entrada:
            estado = NO_CONCLUYENTE
            resultado.no_concluyentes += 1
        elif en_salida:
            estado = FUGA
            resultado.fugas += 1
        else:
            estado = TAPADO
            resultado.tapados += 1

        resultado.detalle.append({"texto": valor, "tipo": tipo, "estado": estado})

    return resultado


def resumir(resultados: list[ResultadoDocumento]) -> dict:
    tapados = sum(r.tapados for r in resultados)
    fugas = sum(r.fugas for r in resultados)
    no_concluyentes = sum(r.no_concluyentes for r in resultados)
    concluyentes = tapados + fugas
    return {
        "documentos": len(resultados),
        "tapados": tapados,
        "fugas": fugas,
        "no_concluyentes": no_concluyentes,
        "concluyentes": concluyentes,
        "porcentaje_tapado": (
            round(100.0 * tapados / concluyentes, 2) if concluyentes else None
        ),
        "documentos_con_fuga": sorted(r.documento for r in resultados if r.fugas),
    }


def imprimir(resumen: dict) -> None:
    porcentaje = resumen["porcentaje_tapado"]
    print("\n" + "=" * 66)
    print("LECTURA DE LAS SALIDAS")
    print("=" * 66)
    print(f"  documentos comprobados : {resumen['documentos']}")
    print(f"  tapados                : {resumen['tapados']}")
    print(f"  FUGAS                  : {resumen['fugas']}")
    print(f"  no concluyentes        : {resumen['no_concluyentes']}"
          "   (el dato tampoco se leia en la entrada)")
    if porcentaje is None:
        print("  porcentaje tapado      : sin datos concluyentes")
    else:
        print(f"  porcentaje tapado      : {porcentaje} % "
              f"sobre {resumen['concluyentes']} concluyentes")
    if resumen["documentos_con_fuga"]:
        print("\n  documentos con fuga:")
        for nombre in resumen["documentos_con_fuga"]:
            print(f"    - {nombre}")


def guardar(resultados: list[ResultadoDocumento], resumen: dict, destino: Path) -> None:
    import dataclasses
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps({
        "resumen": resumen,
        "documentos": [dataclasses.asdict(r) for r in resultados],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
