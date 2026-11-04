"""Mini corpus de imagenes con datos inventados y sus etiquetas.

Lo que va a llegar de verdad como imagen suelta: fotos de movil de documentos
(rectas, inclinadas, en perspectiva, giradas, con la mesa y otros objetos
alrededor, con sombras), a resolucion de movil y pequeñas, carnets y tarjetas
con texto claro sobre fondo oscuro, capturas de pantalla, JPEG muy comprimidos,
PNG con transparencia, TIFF de varias paginas y copias de DNI de ejemplo.

Cada documento se dibuja plano (PyMuPDF, fuentes integradas) y se convierte en
foto con una transformacion conocida.  Se guarda esa transformacion: la lectura
de barras (lectura_imagenes.py) la deshace para leer la entrada y la salida en
plano, sin depender del enderezador del sistema.

Etiqueta de cada imagen (etiquetas.json):
  datos     lo que hay que tapar (texto y tipo)
  rotulos   lo que tiene que quedar a la vista (rotulos, titulos)
  entera    si lo correcto es tapar la imagen entera (copia de DNI)
  paginas   por pagina: tamaño del documento plano y matriz documento -> foto

Uso: python -m evaluation.corpus_imagenes [DESTINO]   (por defecto evaluation/imagenes/corpus)
"""
from __future__ import annotations

import io
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import fitz
import numpy as np
import qrcode
from PIL import Image

DESTINO = Path("evaluation/imagenes/corpus")
DPI_DOC = 200

# --- datos inventados -------------------------------------------------------

_LETRAS = "TRWAGMYFPDXBNJZSQVHLCKE"


def dni(numero: int) -> str:
    return f"{numero:08d}{_LETRAS[numero % 23]}"


def dni_puntos(numero: int) -> str:
    d = dni(numero)
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}-{d[8]}"


def iban(banco: str, oficina: str, dc: str, cuenta: str) -> str:
    bban = banco + oficina + dc + cuenta
    control = 98 - int(bban + "142800") % 97
    s = f"ES{control:02d}{bban}"
    return " ".join(s[i:i + 4] for i in range(0, len(s), 4))


PERSONAS = [
    dict(nombre="Lucía Ferrando Climent", dni=dni_puntos(53810247), nacimiento="17/09/1986",
         direccion="Calle de Sueca 42, 3º B, 46006 Valencia", telefono="654 218 937",
         correo="lucia.ferrando@correo.es", iban=iban("2100", "0418", "45", "0200051332")),
    dict(nombre="Andrés Peiró Sanchis", dni=dni_puntos(20947316), nacimiento="03/02/1974",
         direccion="Avenida del Puerto 118, 46023 Valencia", telefono="+34 622 487 105",
         correo="andres.peiro@correo.es", iban=iban("0049", "1500", "05", "2810498763")),
]


@dataclass
class Documento:
    nombre: str
    tipo: str
    paginas: list[np.ndarray]          # RGB, plano
    datos: list[dict] = field(default_factory=list)
    rotulos: list[str] = field(default_factory=list)
    entera: bool = False
    oscuro: bool = False
    # Cajas (pixeles del documento plano) de cada dato y de cada rotulo: la
    # lectura de barras mira por pixeles si la barra cubre el dato entero.
    cajas_rotulos: list[list[list[float]]] = field(default_factory=list)


def _cajas(page: fitz.Page, texto: str, ppp: float) -> list[list[float]]:
    k = ppp / 72
    return [[r.x0 * k, r.y0 * k, r.x1 * k, r.y1 * k] for r in page.search_for(texto)]


def _con_cajas(doc: Documento, page: fitz.Page, ppp: float = DPI_DOC) -> Documento:
    for d in doc.datos:
        d.setdefault("cajas", _cajas(page, d["text"], ppp))
    doc.cajas_rotulos = [_cajas(page, r, ppp) for r in doc.rotulos]
    return doc


def _render(page: fitz.Page) -> np.ndarray:
    pix = page.get_pixmap(dpi=DPI_DOC, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()


def formulario(p: dict, n: int) -> Documento:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((150, 90), "FORMULARIO DE SOLICITUD - SERVICIOS GENERALES", fontsize=13, fontname="hebo")
    filas = [("NOMBRE COMPLETO:", p["nombre"], "PERSON"), ("D.N.I.:", p["dni"], "ES_DNI"),
             ("FECHA DE NACIMIENTO:", p["nacimiento"], "DATE_OF_BIRTH"),
             ("DIRECCIÓN:", p["direccion"], "ADDRESS"), ("TELÉFONO:", p["telefono"], "PHONE_NUMBER"),
             ("CORREO ELECTRÓNICO:", p["correo"], "EMAIL_ADDRESS"), ("IBAN:", p["iban"], "IBAN_CODE")]
    y = 160
    for rotulo, valor, _ in filas:
        page.insert_text((70, y), rotulo, fontsize=11, fontname="hebo")
        x = 70 + fitz.get_text_length(rotulo, "hebo", 11) + 8
        page.insert_text((x, y), valor, fontsize=11, fontname="helv")
        y += 34
    page.insert_text((70, y + 40), "Motivo de la solicitud: renovación de la tarjeta de aparcamiento.",
                     fontsize=10)
    page.insert_text((70, y + 120), "FIRMA:", fontsize=11, fontname="hebo")
    page.draw_bezier((130, y + 120), (170, y + 80), (210, y + 150), (260, y + 105),
                     color=(0.1, 0.15, 0.5), width=1.6)
    return _con_cajas(Documento(f"formulario_{n}", "formulario", [_render(page)],
                     datos=[{"text": v, "type": t} for _, v, t in filas],
                     rotulos=[r for r, _, _ in filas] + ["FORMULARIO DE SOLICITUD", "Motivo de la solicitud"]), page)


def carta(p: dict) -> Documento:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((70, 90), "RESOLUCIÓN DE LA ALCALDÍA", fontsize=14, fontname="hebo")
    texto = (f"Vista la solicitud presentada por {p['nombre']}, con DNI {p['dni']} y domicilio en "
             f"{p['direccion']}, relativa a la concesión de una ayuda para el alquiler de vivienda, "
             "y de conformidad con las bases de la convocatoria, se resuelve conceder la ayuda "
             "solicitada por el importe máximo previsto. Contra esta resolución puede interponerse "
             "recurso de reposición en el plazo de un mes.")
    page.insert_textbox(fitz.Rect(70, 130, 525, 420), texto, fontsize=11, lineheight=1.5)
    page.insert_text((70, 460), "Lo que se comunica para su conocimiento y efectos.", fontsize=11)
    return _con_cajas(Documento("carta", "carta", [_render(page)],
                     datos=[{"text": p["nombre"], "type": "PERSON"}, {"text": p["dni"], "type": "ES_DNI"},
                            {"text": p["direccion"], "type": "ADDRESS"}],
                     rotulos=["RESOLUCIÓN DE LA ALCALDÍA", "recurso de reposición",
                              "Lo que se comunica para su conocimiento"]), page)


def _qr(contenido: str) -> bytes:
    buf = io.BytesIO()
    qrcode.make(contenido, border=2).save(buf, format="PNG")
    return buf.getvalue()


def carnet() -> Documento:
    """Carnet de socio: fondo negro, texto blanco, escudos, nombre y QR."""
    doc = fitz.open()
    page = doc.new_page(width=340, height=215)
    page.draw_rect(page.rect, color=None, fill=(0.05, 0.05, 0.07))
    for cx in (40, 300):
        page.draw_circle((cx, 42), 24, color=(0.85, 0.7, 0.2), fill=(0.6, 0.1, 0.1), width=2)
    page.insert_text((80, 40), "PEÑA EL CARAJILLO", fontsize=17, fontname="tibo", color=(1, 1, 1))
    page.insert_text((105, 62), "TEMPORADA 2026/27", fontsize=10, fontname="helv", color=(0.9, 0.85, 0.6))
    page.insert_text((22, 120), "SOCIO", fontsize=9, fontname="helv", color=(0.8, 0.8, 0.8))
    page.insert_text((22, 140), "Ramón Escrivá Peris", fontsize=14, fontname="hebo", color=(1, 1, 1))
    page.insert_text((22, 162), "Nº 0127", fontsize=10, fontname="helv", color=(0.8, 0.8, 0.8))
    caja_qr = fitz.Rect(240, 105, 320, 185)
    page.insert_image(caja_qr, stream=_qr("SOCIO 0127 Ramón Escrivá Peris"))
    k = DPI_DOC / 72
    return _con_cajas(Documento("carnet", "carnet", [_render(page)],
                     datos=[{"text": "Ramón Escrivá Peris", "type": "PERSON"},
                            {"text": "QR", "type": "QR_CODE",
                             "cajas": [[caja_qr.x0 * k, caja_qr.y0 * k, caja_qr.x1 * k, caja_qr.y1 * k]]}],
                     rotulos=["PEÑA EL CARAJILLO", "TEMPORADA 2026/27"], oscuro=True), page)


def tarjeta(p: dict) -> Documento:
    doc = fitz.open()
    page = doc.new_page(width=340, height=200)
    page.draw_rect(page.rect, color=None, fill=(0.96, 0.95, 0.92))
    page.draw_rect(fitz.Rect(0, 0, 340, 14), color=None, fill=(0.15, 0.3, 0.5))
    page.insert_text((24, 70), p["nombre"], fontsize=15, fontname="hebo")
    page.insert_text((24, 92), "Atención al cliente", fontsize=10, fontname="helv", color=(0.3, 0.3, 0.3))
    page.insert_text((24, 140), f"Tel. {p['telefono']}", fontsize=10, fontname="helv")
    page.insert_text((24, 160), p["correo"], fontsize=10, fontname="helv")
    return _con_cajas(Documento("tarjeta", "tarjeta", [_render(page)],
                     datos=[{"text": p["nombre"], "type": "PERSON"},
                            {"text": p["telefono"], "type": "PHONE_NUMBER"},
                            {"text": p["correo"], "type": "EMAIL_ADDRESS"}],
                     rotulos=["Atención al cliente"]), page)


def dni_ejemplo() -> Documento:
    """Anverso de un DNI de ejemplo, con ESPECIMEN y una MRZ inventada."""
    doc = fitz.open()
    page = doc.new_page(width=340, height=215)
    page.draw_rect(page.rect, color=(0.5, 0.5, 0.5), fill=(0.86, 0.9, 0.93), radius=0.05)
    page.insert_text((14, 24), "ESPAÑA", fontsize=11, fontname="hebo", color=(0.6, 0.1, 0.1))
    page.insert_text((90, 24), "DOCUMENTO NACIONAL DE IDENTIDAD", fontsize=10, fontname="hebo")
    page.draw_rect(fitz.Rect(14, 40, 92, 140), color=None, fill=(0.62, 0.66, 0.7))
    campos = [("APELLIDOS", "ESPAÑOL ESPAÑOL"), ("NOMBRE", "CARMEN"), ("SEXO  F   NACIONALIDAD  ESP", ""),
              ("FECHA DE NACIMIENTO", "01 01 1980"), ("VALIDO HASTA", "01 01 2031")]
    y = 46
    for rotulo, valor in campos:
        page.insert_text((104, y), rotulo, fontsize=6.5, fontname="helv", color=(0.3, 0.3, 0.3))
        if valor:
            page.insert_text((104, y + 10), valor, fontsize=9, fontname="hebo")
        y += 22
    page.insert_text((14, 160), "DNI 99999999R", fontsize=10, fontname="hebo")
    page.insert_text((60, 130), "ESPECIMEN", fontsize=30, fontname="hebo", color=(0.8, 0.3, 0.3),
                     morph=(fitz.Point(170, 110), fitz.Matrix(1, 0, 0, 1, 0, 0).prerotate(-15)))
    for i, linea in enumerate(["IDESPBHV122738999999999R<<<<<<", "8001014F3101018ESP<<<<<<<<<<<6",
                               "ESPANOL<ESPANOL<<CARMEN<<<<<<<"]):
        page.insert_text((14, 182 + i * 10), linea, fontsize=8, fontname="cour")
    return Documento("dni_ejemplo", "dni", [_render(page)], entera=True)


def dni_reverso() -> Documento:
    """Reverso de un DNI inventado: domicilio y la zona de lectura mecanica
    (MRZ) de tres lineas, sin rotulo ni otra señal de documento de identidad."""
    doc = fitz.open()
    page = doc.new_page(width=340, height=215)
    page.draw_rect(page.rect, color=(0.5, 0.5, 0.5), fill=(0.88, 0.91, 0.93), radius=0.05)
    page.insert_text((14, 24), "DOMICILIO", fontsize=6.5, fontname="helv", color=(0.3, 0.3, 0.3))
    page.insert_text((14, 36), "C. DE LA PRUEBA 12 PISO 3", fontsize=9, fontname="hebo")
    page.insert_text((14, 48), "VALENCIA", fontsize=9, fontname="hebo")
    page.insert_text((14, 72), "EQUIPO", fontsize=6.5, fontname="helv", color=(0.3, 0.3, 0.3))
    page.insert_text((14, 84), "46001A1D1", fontsize=9, fontname="hebo")
    for i, linea in enumerate(["IDESPBAA000123412345678Z<<<<<<", "9001014F3001014ESP<<<<<<<<<<<4",
                               "PRUEBA<INVENTADA<<ANA<<<<<<<<<"]):
        page.insert_text((14, 160 + i * 16), linea, fontsize=12, fontname="cour")
    return Documento("dni_reverso", "dni", [_render(page)], entera=True)


def captura() -> Documento:
    doc = fitz.open()
    page = doc.new_page(width=800, height=500)
    page.draw_rect(fitz.Rect(0, 0, 800, 40), color=None, fill=(0.17, 0.2, 0.25))
    page.insert_text((20, 26), "Sede electrónica - Perfil de usuario", fontsize=13, color=(1, 1, 1))
    for i, (rot, val) in enumerate([("Nombre", "Marta Soler Gimeno"), ("Correo", "marta.soler@correo.es"),
                                    ("Teléfono", "611 904 382")]):
        y = 100 + i * 60
        page.insert_text((40, y), rot, fontsize=11, color=(0.4, 0.4, 0.4))
        page.draw_rect(fitz.Rect(160, y - 18, 520, y + 8), color=(0.7, 0.7, 0.7), width=1)
        page.insert_text((170, y), val, fontsize=12)
    page.draw_rect(fitz.Rect(160, 300, 260, 330), color=None, fill=(0.2, 0.45, 0.8))
    page.insert_text((185, 320), "Guardar", fontsize=12, color=(1, 1, 1))
    pix = page.get_pixmap(dpi=96, alpha=False)     # una captura: resolucion de pantalla
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()
    return _con_cajas(Documento("captura", "captura", [img],
                     datos=[{"text": "Marta Soler Gimeno", "type": "PERSON"},
                            {"text": "marta.soler@correo.es", "type": "EMAIL_ADDRESS"},
                            {"text": "611 904 382", "type": "PHONE_NUMBER"}],
                     rotulos=["Perfil de usuario", "Guardar", "Correo"]), page, ppp=96)


# --- de documento a foto ------------------------------------------------------

@dataclass
class Foto:
    angulo: float = 0.0
    perspectiva: float = 0.0
    fondo: bool = False
    luz: bool = False
    giro90: bool = False
    ancho: int | None = None          # ancho final de la foto (None: el del documento)
    ocupa: float = 0.78               # fraccion del alto de la foto que ocupa el documento
    jpeg: int | None = 90
    formato: str = "jpeg"             # jpeg, png, png_alfa, tiff


def _fondo(alto: int, ancho: int, rng: random.Random) -> np.ndarray:
    """Mesa de madera con un teclado y una taza."""
    y, x = np.mgrid[0:alto, 0:ancho]
    vetas = (np.sin(x / 37.0 + np.sin(y / 90.0) * 3) * 18).astype(np.int16)
    base = np.stack([150 + vetas, 105 + vetas // 2, 65 + vetas // 3], -1)
    base = np.clip(base + rng.randint(-5, 5), 0, 255).astype(np.uint8)
    teclado_y = int(alto * 0.02)
    cv2.rectangle(base, (int(ancho * 0.1), -20), (int(ancho * 0.85), teclado_y + int(alto * 0.09)),
                  (235, 235, 238), -1)
    for i in range(12):
        x0 = int(ancho * (0.12 + i * 0.06))
        cv2.rectangle(base, (x0, teclado_y), (x0 + int(ancho * 0.05), teclado_y + int(alto * 0.06)),
                      (205, 205, 210), 2)
    cv2.circle(base, (int(ancho * 0.9), int(alto * 0.75)), int(alto * 0.09), (40, 40, 45), -1)
    cv2.circle(base, (int(ancho * 0.9), int(alto * 0.75)), int(alto * 0.07), (90, 60, 40), -1)
    return base


def fotografiar(pagina: np.ndarray, f: Foto, semilla: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Devuelve (foto RGB, alfa, matriz documento -> foto)."""
    rng = random.Random(semilla)
    h, w = pagina.shape[:2]
    origen = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    if not (f.fondo or f.angulo or f.perspectiva):
        foto, alfa, m = pagina.copy(), np.full((h, w), 255, np.uint8), np.eye(3)
        if f.formato == "png_alfa":
            # Documento sobre lienzo transparente con margen.
            mh, mw = int(h * 0.08), int(w * 0.08)
            lienzo = np.zeros((h + 2 * mh, w + 2 * mw, 3), np.uint8)
            lienzo[mh:mh + h, mw:mw + w] = pagina
            alfa = np.zeros(lienzo.shape[:2], np.uint8)
            alfa[mh:mh + h, mw:mw + w] = 255
            foto, m = lienzo, np.array([[1, 0, mw], [0, 1, mh], [0, 0, 1]], float)
    else:
        alto = int(h / f.ocupa)
        ancho = int(alto * 4 / 3) if w <= h else int(w / f.ocupa)
        alto = max(alto, int(ancho * 3 / 4)) if w > h else alto
        escala = alto * f.ocupa / h
        cx, cy = ancho / 2, alto / 2 + alto * 0.03
        esquinas = (origen - [w / 2, h / 2]) * escala
        t = np.radians(f.angulo)
        rot = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
        esquinas = esquinas @ rot.T
        if f.perspectiva:
            # Arriba mas estrecho: el movil mira la hoja desde abajo.
            dx = f.perspectiva * w * escala / 2
            esquinas[0, 0] += dx
            esquinas[1, 0] -= dx
            esquinas[0, 1] += dx * 0.3
            esquinas[1, 1] += dx * 0.3
        destino = np.float32(esquinas + [cx, cy])
        m = cv2.getPerspectiveTransform(origen, destino).astype(float)
        fondo = _fondo(alto, ancho, rng) if f.fondo else np.full((alto, ancho, 3), 245, np.uint8)
        hoja = cv2.warpPerspective(pagina, m, (ancho, alto), flags=cv2.INTER_LINEAR)
        mascara = cv2.warpPerspective(np.full((h, w), 255, np.uint8), m, (ancho, alto))
        foto = np.where(mascara[..., None] > 127, hoja, fondo)
        if f.fondo:
            # Sombra suave de la hoja sobre la mesa.
            sombra = cv2.GaussianBlur(cv2.warpPerspective(np.full((h, w), 255, np.uint8),
                                                          np.array([[1, 0, 12], [0, 1, 16], [0, 0, 1]]) @ m,
                                                          (ancho, alto)), (0, 0), 15)
            foto = np.where(mascara[..., None] > 127, foto,
                            (foto * (1 - 0.35 * sombra[..., None] / 255)).astype(np.uint8))
        alfa = np.full((alto, ancho), 255, np.uint8)
    if f.luz:
        hh, ww = foto.shape[:2]
        y, x = np.mgrid[0:hh, 0:ww]
        luz = 0.62 + 0.45 * (x / ww) * (1 - 0.3 * y / hh)
        sombra = np.exp(-(((x - ww * 0.3) / (ww * 0.18)) ** 2 + ((y - hh * 0.55) / (hh * 0.3)) ** 2))
        luz = luz * (1 - 0.35 * sombra)
        foto = np.clip(foto * luz[..., None], 0, 255).astype(np.uint8)
    if f.fondo or f.luz or f.angulo:
        ruido = np.random.default_rng(semilla).normal(0, 4, foto.shape)
        foto = np.clip(cv2.GaussianBlur(foto, (0, 0), 0.8) + ruido, 0, 255).astype(np.uint8)
    if f.giro90:
        hh = foto.shape[0]
        foto, alfa = np.rot90(foto, k=-1).copy(), np.rot90(alfa, k=-1).copy()
        m = np.array([[0, -1, hh - 1], [1, 0, 0], [0, 0, 1]], float) @ m
    if f.ancho and f.ancho != foto.shape[1]:
        k = f.ancho / foto.shape[1]
        foto = cv2.resize(foto, (f.ancho, round(foto.shape[0] * k)), interpolation=cv2.INTER_AREA)
        alfa = cv2.resize(alfa, (foto.shape[1], foto.shape[0]), interpolation=cv2.INTER_NEAREST)
        m = np.diag([k, k, 1.0]) @ m
    return foto, alfa, m


def guardar(paginas: list[tuple[np.ndarray, np.ndarray]], destino: Path, f: Foto) -> Path:
    if f.formato == "tiff":
        ruta = destino.with_suffix(".tif")
        marcos = [Image.fromarray(p) for p, _ in paginas]
        marcos[0].save(ruta, format="TIFF", save_all=True, append_images=marcos[1:], compression="tiff_deflate")
    elif f.formato == "png_alfa":
        ruta = destino.with_suffix(".png")
        foto, alfa = paginas[0]
        Image.fromarray(np.dstack([foto, alfa])).save(ruta, format="PNG")
    elif f.formato == "png":
        ruta = destino.with_suffix(".png")
        Image.fromarray(paginas[0][0]).save(ruta, format="PNG")
    else:
        ruta = destino.with_suffix(".jpg")
        Image.fromarray(paginas[0][0]).save(ruta, format="JPEG", quality=f.jpeg or 90)
    return ruta


# --- el corpus -----------------------------------------------------------------

RECTA = Foto()
VARIANTES = {
    "recta": RECTA,
    "inclinada_4": Foto(angulo=4, fondo=True),
    "inclinada_10": Foto(angulo=-10, fondo=True),
    "perspectiva": Foto(angulo=3, perspectiva=0.12, fondo=True),
    "girada_90": Foto(giro90=True),
    "mesa_sombra_luz": Foto(angulo=-2, fondo=True, luz=True),
    "movil_4000": Foto(angulo=5, perspectiva=0.06, fondo=True, ancho=4000),
    "pequena_800": Foto(angulo=3, fondo=True, ancho=800),
    "jpeg_q12": Foto(angulo=2, fondo=True, jpeg=12),
    "png_transparente": Foto(formato="png_alfa"),
}


def corpus() -> list[tuple[str, list[Documento], Foto]]:
    casos = []
    for i, p in enumerate(PERSONAS, 1):
        for nombre, f in VARIANTES.items():
            casos.append((f"formulario{i}_{nombre}", [formulario(p, i)], f))
    c = carta(PERSONAS[0])
    for nombre in ("recta", "inclinada_10", "perspectiva", "mesa_sombra_luz", "pequena_800", "jpeg_q12"):
        casos.append((f"carta_{nombre}", [c], VARIANTES[nombre]))
    k = carnet()
    for nombre, f in [("recta", RECTA), ("inclinada", Foto(angulo=7, fondo=True, ocupa=0.55)),
                      ("pequena", Foto(ancho=500)), ("mesa_luz", Foto(angulo=-4, fondo=True, luz=True, ocupa=0.5))]:
        casos.append((f"carnet_{nombre}", [k], f))
    t = tarjeta(PERSONAS[1])
    casos.append(("tarjeta_recta", [t], RECTA))
    casos.append(("tarjeta_perspectiva", [t], Foto(angulo=-6, perspectiva=0.1, fondo=True, ocupa=0.5)))
    d = dni_ejemplo()
    for nombre, f in [("recta", RECTA), ("mesa", Foto(angulo=8, fondo=True, ocupa=0.5)),
                      ("pequena", Foto(ancho=420))]:
        casos.append((f"dni_{nombre}", [d], f))
    cap = captura()
    casos.append(("captura_png", [cap], Foto(formato="png")))
    casos.append(("captura_jpeg_q15", [cap], Foto(jpeg=15)))
    casos.append(("tiff_dos_paginas", [formulario(PERSONAS[0], 1), carta(PERSONAS[1])], Foto(formato="tiff")))
    return casos


def generar(destino: Path = DESTINO) -> list[dict]:
    destino.mkdir(parents=True, exist_ok=True)
    etiquetas = []
    for n, (nombre, docs, f) in enumerate(corpus()):
        fotos, paginas, datos, rotulos, cajas_rotulos = [], [], [], [], []
        for doc in docs:
            indice = len(paginas)
            for pagina in doc.paginas:
                foto, alfa, m = fotografiar(pagina, f, semilla=n)
                fotos.append((foto, alfa))
                paginas.append({"ancho": pagina.shape[1], "alto": pagina.shape[0], "matriz": m.tolist()})
            datos += [{**d, "pagina": indice} for d in doc.datos]
            rotulos += doc.rotulos
            cajas_rotulos += [{"pagina": indice, "cajas": c} for c in doc.cajas_rotulos]
        ruta = guardar(fotos, destino / nombre, f)
        etiquetas.append({"imagen": ruta.name, "tipo": docs[0].tipo, "variante": nombre.split("_", 1)[1],
                          "datos": datos, "rotulos": rotulos, "cajas_rotulos": cajas_rotulos,
                          "entera": any(d.entera for d in docs),
                          "oscuro": any(d.oscuro for d in docs), "paginas": paginas})
    (destino / "etiquetas.json").write_text(json.dumps(etiquetas, ensure_ascii=False, indent=1), encoding="utf-8")
    return etiquetas


if __name__ == "__main__":
    salida = generar(Path(sys.argv[1]) if len(sys.argv) > 1 else DESTINO)
    print(f"{len(salida)} imagenes en {DESTINO if len(sys.argv) < 2 else sys.argv[1]}")
