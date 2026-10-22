"""Comprueba en el PDF de SALIDA que el dato ya no se lee.

Por que hace falta
------------------
La evaluacion compara la lista de entidades que devuelve el servicio contra las
etiquetas del corpus: mide DETECCION.  Nunca abre el documento anonimizado, asi
que una barra negra pintada a tres centimetros del dato cuenta como acierto, y
un defecto asi (barras desplazadas) puede vivir escondido indefinidamente.

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

3. **Comparar tolerando las erratas del OCR.**  Exigir identidad exacta
   convierte cualquier errata en un falso exito: un DNI leido con una cifra de
   menos, perfectamente visible, contaria como tapado.  Se busca la ventana mas
   parecida, con un umbral que no llega a casar dos datos distintos (dos codigos
   postales que solo difieren en la ultima cifra se quedan cortos).

4. **Juzgar pagina a pagina.**  Uniendo el texto de todo el documento no se
   sabe si la lectura de la pagina donde vive el dato es de fiar.  Un "no esta"
   solo cuenta si esa pagina se leyo comparablemente bien que en la entrada, o
   si esta practicamente en negro, que es una redaccion total.

QUE VALE COMO PRUEBA Y QUE NO
-----------------------------
Una FUGA es una prueba: el dato se lee en la salida.

Un TAPADO es de fiar salvo por una reserva, la unica que queda: que el dato
siga en la pagina y no lo haya leido la lectura de la salida pese a que esa
pagina se lee bien y la comparacion admite erratas.  Es poco probable, pero no
imposible, y por eso queda escrito.

Los dos fallos que este modulo tuvo —los dos diciendo "tapado" de datos que
estaban a la vista— estan cerrados y fijados en tests/test_lectura_salida.py.
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
# Calidad de una lectura: caracteres por token.  Una pagina bien orientada da
# muchos; una girada, muy pocos.
_CALIDAD_ACEPTABLE = 8.0
_UMBRAL_OSCURO = 60
# Por encima de esto la pagina esta tachada entera: que no se lea nada es lo
# correcto, no un fallo de lectura.
_NEGRURA_REDACCION_TOTAL = 0.5
# La lectura de la salida tiene que recuperar al menos esta parte de los
# caracteres que recupero la de la entrada para poder fiarse de un "no esta".
_FRACCION_MINIMA = 0.5
# Parecido minimo para dar un dato por encontrado pese a las erratas del OCR.
# Por debajo de esto ya casarian datos distintos (18001 contra 18002 sale 0.8).
_PARECIDO_MINIMO = 0.85


# --------------------------------------------------------------------------
# lectura
# --------------------------------------------------------------------------

def _aplanar(texto: str) -> str:
    """Mayusculas sin acentos ni separadores: el OCR degrada tildes y guiones."""
    plegado = unicodedata.normalize("NFKD", texto.upper())
    limpio = "".join(c for c in plegado if not unicodedata.combining(c))
    return "".join(c for c in limpio if c.isalnum())


def aparece(aguja: str, pajar: str, umbral: float = _PARECIDO_MINIMO) -> bool:
    """¿Esta el dato en el texto, aunque el OCR lo haya leido con erratas?

    Una comparacion exacta contaria como tapado un dato perfectamente visible
    que el OCR de la salida ha leido con una cifra de menos.  Exigir identidad
    convierte cualquier errata del OCR en un falso exito, que es la peor
    direccion posible en la que equivocarse.

    Se busca la ventana mas parecida del mismo largo.  Barato: el texto de una
    pagina son un par de miles de caracteres y el dato, una decena.
    """
    if aguja in pajar:
        return True
    if len(aguja) < 4 or len(pajar) < len(aguja):
        return False
    from difflib import SequenceMatcher
    for inicio in range(len(pajar) - len(aguja) + 1):
        ventana = pajar[inicio:inicio + len(aguja)]
        if SequenceMatcher(None, aguja, ventana).ratio() >= umbral:
            return True
    return False


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


@dataclass
class LecturaPagina:
    """Lo que se pudo leer de una pagina, y con cuanto esfuerzo."""
    indice: int
    texto: str
    angulo: int

    @property
    def plano(self) -> str:
        return _aplanar(self.texto)

    @property
    def caracteres(self) -> int:
        return len(self.plano)


def leer_documento(pdf: Path) -> list[LecturaPagina]:
    """El texto legible PAGINA A PAGINA, no unido.

    Unirlo fue el error de la primera version: sin saber de que pagina salio
    cada cosa no se puede juzgar si la lectura de esa pagina es fiable, y un
    dato perdido por un OCR que leyo mal se contaba como tapado.
    """
    with fitz.open(pdf) as doc:
        paginas = doc.page_count
    lecturas = []
    for indice in range(paginas):
        texto, angulo = leer_pagina(pdf, indice)
        lecturas.append(LecturaPagina(indice=indice, texto=texto, angulo=angulo))
    return lecturas


def negrura(pdf: "Path | bytes") -> list[float]:
    """Fraccion de pixeles oscuros por pagina (de un fichero o de sus bytes).

    Distingue una redaccion TOTAL (cerca de 1.0) de una selectiva (unas
    centesimas).  Sin esto, una pagina entera en negro y una pagina intacta se
    parecen demasiado: en las dos el OCR no encuentra el dato.
    """
    valores = []
    abierto = (fitz.open(stream=pdf, filetype="pdf") if isinstance(pdf, bytes)
               else fitz.open(pdf))
    with abierto as doc:
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
    # Datos de tipos que no se anonimizan (LOCATION...): se leen y se listan,
    # pero no son fugas ni tapados.  Ver anotar().
    fuera_de_alcance: int = 0
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


def pagina_fiable(
    entrada: LecturaPagina,
    salida: LecturaPagina | None,
    negrura_salida: float,
    fraccion_minima: float = _FRACCION_MINIMA,
) -> bool:
    """¿Se puede creer un "no lo encuentro" en esta pagina de la salida?

    Dos formas de que si:

    - La pagina esta practicamente en negro: es una redaccion TOTAL y que no
      se lea nada es exactamente lo que tiene que pasar.
    - La lectura de la salida recupero una fraccion razonable de los
      caracteres que recupero la de la entrada: el OCR sigue funcionando sobre
      esa pagina, asi que lo que falta, falta porque esta tapado.

    Si no se da ninguna, el OCR leyo la salida peor que la entrada y un dato
    que no aparece puede seguir estando a la vista.
    """
    if negrura_salida >= _NEGRURA_REDACCION_TOTAL:
        return True
    if salida is None:
        return False
    if entrada.caracteres == 0:
        return False
    return salida.caracteres >= fraccion_minima * entrada.caracteres


def decidir(en_entrada: bool, en_salida: bool, fiable: bool) -> str:
    """La tabla de decision, aparte para poder fijarla con pruebas."""
    if not en_entrada:
        return NO_CONCLUYENTE      # no se leia ya de origen: no demuestra nada
    if en_salida:
        return FUGA                # se lee en las dos: prueba
    if not fiable:
        return NO_CONCLUYENTE      # no aparece, pero la lectura no es de fiar
    return TAPADO


def comprobar_documento(entrada: Path, salida: Path, etiqueta: dict) -> ResultadoDocumento:
    """Busca cada dato de la etiqueta en la entrada y en la salida."""
    resultado = ResultadoDocumento(documento=entrada.stem)

    lecturas_entrada = leer_documento(entrada)
    lecturas_salida = leer_documento(salida)
    resultado.negrura_por_pagina = negrura(salida)
    resultado.angulos_entrada = [l.angulo for l in lecturas_entrada]
    resultado.angulos_salida = [l.angulo for l in lecturas_salida]

    por_indice = {l.indice: l for l in lecturas_salida}
    plano_salida = " ".join(l.plano for l in lecturas_salida)

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

        paginas_origen = [l for l in lecturas_entrada if aparece(aguja, l.plano)]
        en_entrada = bool(paginas_origen)
        en_salida = aparece(aguja, plano_salida)

        # Fiable si lo es la lectura de ALGUNA de las paginas donde vivia el
        # dato: basta con poder creerse una para creerse que no esta.
        fiable = any(
            pagina_fiable(
                pagina,
                por_indice.get(pagina.indice),
                (resultado.negrura_por_pagina[pagina.indice]
                 if pagina.indice < len(resultado.negrura_por_pagina) else 0.0),
            )
            for pagina in paginas_origen
        )

        anotar(resultado, valor, tipo, decidir(en_entrada, en_salida, fiable),
               paginas_en_entrada=[p.indice + 1 for p in paginas_origen],
               lectura_fiable=fiable)

    return resultado


def anotar(resultado: ResultadoDocumento, valor: str, tipo: str, estado: str,
           **extra) -> None:
    """Cuenta un dato en su casilla.

    Los tipos que el sistema no anonimiza por politica (un municipio) quedan
    aparte: contarlos como fuga inflaria el numero.  Es el mismo criterio que
    aplica el evaluador.
    """
    from evaluation.metrics import fuera_de_alcance

    fuera = fuera_de_alcance(tipo, valor)
    if fuera:
        resultado.fuera_de_alcance += 1
    elif estado == TAPADO:
        resultado.tapados += 1
    elif estado == FUGA:
        resultado.fugas += 1
    else:
        resultado.no_concluyentes += 1
    resultado.detalle.append({"texto": valor, "tipo": tipo, "estado": estado,
                              "fuera_de_alcance": fuera, **extra})


def resumir(resultados: list[ResultadoDocumento]) -> dict:
    tapados = sum(r.tapados for r in resultados)
    fugas = sum(r.fugas for r in resultados)
    no_concluyentes = sum(r.no_concluyentes for r in resultados)
    fuera_de_alcance = sum(r.fuera_de_alcance for r in resultados)
    concluyentes = tapados + fugas
    return {
        "documentos": len(resultados),
        "tapados": tapados,
        "fugas": fugas,
        "no_concluyentes": no_concluyentes,
        "fuera_de_alcance": fuera_de_alcance,
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
    print(f"  fuera de alcance       : {resumen['fuera_de_alcance']}"
          "   (tipos que no se anonimizan, p. ej. municipio)")
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
