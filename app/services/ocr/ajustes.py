"""Lo que se intenta antes de dar una pagina por ilegible.

Una foto de movil o un escaneo con la hoja torcida se leen bien una vez
corregidos.  Tres ajustes, que se prueban solo cuando la pagina, girada en
multiplos de 90 grados, sigue sin leerse:

* inclinacion: el angulo de las lineas del documento (Hough sobre los bordes),
  si es de unos grados;
* perspectiva: la hoja, recortada del fondo (la mesa, un teclado) y llevada a
  un rectangulo;
* invertida: texto claro sobre fondo oscuro (carnets, tarjetas).  La inversion
  es solo PARA LEER: las barras se pintan sobre la imagen original.

Cada ajuste geometrico es una matriz 3x3 de la imagen (ya girada) a la imagen
de trabajo.  Se trabaja en ese espacio y lo tapado se devuelve a la imagen
original con la matriz inversa (`deshacer`).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image, ImageOps

# Inclinacion que merece la pena corregir: por debajo el OCR la tolera, por
# encima de 20 grados ya no es una hoja torcida sino otra cosa.
_INCLINACION_MIN, _INCLINACION_MAX = 0.7, 20.0
# La hoja tiene que ocupar una parte razonable de la foto, y no toda.
_HOJA_MIN, _HOJA_MAX = 0.15, 0.95
# Mediana de luminosidad por debajo de la cual se prueba invertir.
_OSCURA = 110


@dataclass(frozen=True)
class Ajuste:
    tipo: str                                   # "inclinacion", "perspectiva", "invertida"...
    matriz: np.ndarray | None = None            # imagen girada -> imagen de trabajo
    tamaño: tuple[int, int] | None = None       # (ancho, alto) de la imagen de trabajo
    invertir: bool = False                      # leer con los colores invertidos
    detalle: dict = field(default_factory=dict, compare=False)


def aplicar(imagen: Image.Image, ajuste: Ajuste | None) -> Image.Image:
    """La imagen de trabajo (sin invertir: la inversion es solo para leer)."""
    if ajuste is None or ajuste.matriz is None:
        return imagen
    rgb = np.asarray(imagen.convert("RGB"))
    trabajo = cv2.warpPerspective(rgb, ajuste.matriz, ajuste.tamaño, flags=cv2.INTER_LINEAR,
                                  borderValue=(255, 255, 255))
    return Image.fromarray(trabajo)


def para_leer(imagen: Image.Image, ajuste: Ajuste | None) -> Image.Image:
    return ImageOps.invert(imagen.convert("RGB")) if ajuste is not None and ajuste.invertir else imagen


def deshacer(trabajo_tapado: Image.Image, trabajo_limpio: Image.Image, original: Image.Image,
             ajuste: Ajuste | None) -> Image.Image:
    """Lleva lo tapado en el espacio de trabajo a la imagen original.

    Lo tapado es lo que cambio entre la imagen de trabajo limpia y la tapada;
    se devuelve con la matriz inversa, con un margen de un par de pixeles por el
    remuestreo, y se pinta en negro sobre la original.
    """
    if ajuste is None or ajuste.matriz is None:
        return trabajo_tapado
    a = np.asarray(trabajo_limpio.convert("RGB")).astype(np.int16)
    b = np.asarray(trabajo_tapado.convert("RGB")).astype(np.int16)
    cambio = (np.abs(a - b).max(axis=2) > 24).astype(np.uint8) * 255
    ancho, alto = original.size
    atras = cv2.warpPerspective(cambio, ajuste.matriz, (ancho, alto),
                                flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP)
    atras = cv2.dilate(atras, np.ones((5, 5), np.uint8))
    salida = np.asarray(original.convert("RGB")).copy()
    salida[atras > 0] = 0
    return Image.fromarray(salida)


def region_de_trabajo(original: Image.Image, ajuste: Ajuste | None) -> np.ndarray | None:
    """Mascara (en la original) de lo que cubre la imagen de trabajo: lo de fuera
    (el fondo de una foto recortada) se lee aparte para no dejarlo sin mirar."""
    if ajuste is None or ajuste.matriz is None:
        return None
    ancho, alto = original.size
    dentro = np.full((ajuste.tamaño[1], ajuste.tamaño[0]), 255, np.uint8)
    return cv2.warpPerspective(dentro, ajuste.matriz, (ancho, alto),
                               flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP)


def inclinacion(gris: np.ndarray) -> float:
    """Angulo de las lineas del documento, en grados (positivo: antihorario)."""
    bordes = cv2.Canny(gris, 50, 150)
    lineas = cv2.HoughLinesP(bordes, 1, np.pi / 720, 150,
                             minLineLength=max(40, gris.shape[1] // 10), maxLineGap=20)
    if lineas is None:
        return 0.0
    angulos = [np.degrees(np.arctan2(y2 - y1, x2 - x1)) for x1, y1, x2, y2 in lineas[:, 0]]
    angulos = [a for a in angulos if abs(a) < _INCLINACION_MAX + 5]
    return float(np.median(angulos)) if len(angulos) >= 3 else 0.0


def _rotacion(ancho: int, alto: int, grados: float) -> tuple[np.ndarray, tuple[int, int]]:
    """Matriz que endereza una inclinacion de *grados*, con el lienzo ampliado."""
    m = cv2.getRotationMatrix2D((ancho / 2, alto / 2), grados, 1.0)
    cos, sen = abs(m[0, 0]), abs(m[0, 1])
    nuevo = (int(alto * sen + ancho * cos), int(alto * cos + ancho * sen))
    m[0, 2] += nuevo[0] / 2 - ancho / 2
    m[1, 2] += nuevo[1] / 2 - alto / 2
    return np.vstack([m, [0, 0, 1]]), nuevo


def hoja(rgb: np.ndarray) -> tuple[np.ndarray, tuple[int, int]] | None:
    """La hoja recortada del fondo y llevada a un rectangulo: (matriz, tamaño)."""
    gris = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    suave = cv2.GaussianBlur(gris, (9, 9), 0)
    candidatos = []
    for binaria in (cv2.threshold(suave, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
                    cv2.dilate(cv2.Canny(suave, 30, 100), np.ones((5, 5), np.uint8))):
        contornos, _ = cv2.findContours(binaria, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidatos += contornos
    area_total = gris.shape[0] * gris.shape[1]
    mejor = None
    for c in sorted(candidatos, key=cv2.contourArea, reverse=True)[:6]:
        area = cv2.contourArea(c)
        if not (_HOJA_MIN * area_total <= area <= _HOJA_MAX * area_total):
            continue
        aprox = cv2.approxPolyDP(cv2.convexHull(c), 0.02 * cv2.arcLength(c, True), True)
        if len(aprox) == 4:
            mejor = aprox.reshape(4, 2).astype(np.float32)
            break
    if mejor is None:
        return None
    s, d = mejor.sum(1), np.diff(mejor, axis=1).ravel()
    orden = np.array([mejor[s.argmin()], mejor[d.argmin()], mejor[s.argmax()], mejor[d.argmax()]])
    ancho = int(max(np.linalg.norm(orden[0] - orden[1]), np.linalg.norm(orden[3] - orden[2])))
    alto = int(max(np.linalg.norm(orden[0] - orden[3]), np.linalg.norm(orden[1] - orden[2])))
    if ancho < 50 or alto < 50:
        return None
    destino = np.float32([[0, 0], [ancho, 0], [ancho, alto], [0, alto]])
    return cv2.getPerspectiveTransform(orden, destino).astype(float), (ancho, alto)


def _con_tope(m: np.ndarray, tamaño: tuple[int, int]) -> tuple[np.ndarray, tuple[int, int]]:
    """La imagen de trabajo no pasa del tope de megapixeles del OCR: enderezar
    amplia el lienzo, y con el motor de layout cargado una pagina mayor agota la
    memoria de la GPU.  Es el mismo tope que el de las paginas."""
    from app.core.config import settings

    tope = settings.ocr_max_megapixels * 1e6
    area = tamaño[0] * tamaño[1]
    if tope <= 0 or area <= tope:
        return m, tamaño
    k = (tope / area) ** 0.5
    return np.diag([k, k, 1.0]) @ m, (int(tamaño[0] * k), int(tamaño[1] * k))


#: Inclinacion que puede quedar tras un ajuste geometrico.  Mas, y las barras
#: (rectas en el espacio de trabajo) caerian torcidas sobre el texto real: un
#: recorte de hoja mal detectado (la hoja con su sombra, un trozo de mesa) deja
#: el texto inclinado y se descarta.
_INCLINACION_RESIDUAL_MAX = 2.0


def _queda_derecho(gris: np.ndarray, m: np.ndarray, tamaño: tuple[int, int]) -> bool:
    trabajo = cv2.warpPerspective(gris, m, tamaño, flags=cv2.INTER_LINEAR, borderValue=255)
    return abs(inclinacion(trabajo)) <= _INCLINACION_RESIDUAL_MAX


def candidatos(imagen: Image.Image) -> list[Ajuste]:
    """Los ajustes que merece la pena probar en esta imagen, por orden."""
    rgb = np.asarray(imagen.convert("RGB"))
    gris = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    oscura = float(np.median(gris)) < _OSCURA
    salida: list[Ajuste] = []
    recorte = hoja(rgb)
    if recorte is not None:
        m, tamaño = _con_tope(*recorte)
        if _queda_derecho(gris, m, tamaño):
            salida.append(Ajuste("perspectiva", m, tamaño, invertir=oscura))
    grados = inclinacion(gris)
    if _INCLINACION_MIN <= abs(grados) <= _INCLINACION_MAX:
        m, tamaño = _con_tope(*_rotacion(imagen.width, imagen.height, grados))
        if _queda_derecho(gris, m, tamaño):
            salida.append(Ajuste("inclinacion", m, tamaño, invertir=oscura,
                                 detalle={"grados": round(grados, 1)}))
    if oscura:
        salida.append(Ajuste("invertida", invertir=True))
    return salida
