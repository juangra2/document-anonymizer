"""Imagenes sueltas (JPEG, PNG, TIFF): salen en su formato, tapadas y sin metadatos.

Pasan por el camino escaneado completo (aqui con OCR simulado: rapido y
determinista) y se comprueba leyendo el fichero de salida: mismo formato y
mismas dimensiones, sin EXIF/GPS/XMP/textos, la zona del dato oscura y el resto
igual.  "Solo quitar metadatos" no recomprime un JPEG derecho y endereza uno
girado por EXIF antes de quitarle la marca de giro.
"""
import io
import time
from io import BytesIO
from pathlib import Path

import fitz
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin

from app.main import app
from app.services.image.imagen import (
    anonimizar_imagen,
    formato_de,
    solo_metadatos_imagen,
)
from app.services.ocr.models import OcrPage, OcrToken
from app.services.pdf.limpieza import jpeg_con_metadatos
from app.services.pii.analyzer import PiiAnalyzer
from tests.casos_metadatos import foto_movil

# Donde el OCR simulado "lee" el dato, en pixeles de la imagen original.
RENGLON = (40, 40, 560, 75)
DATO = "Interesado: Marcos Villalba Tena con DNI 12345678Z"


class _OcrSimulado:
    engine_name = "fake"
    provides_layout = False
    supports_pdf_input = False

    def analyze_image(self, image_path, page_index=0):
        with Image.open(image_path) as img:
            ancho, alto = img.size
        escala = ancho / 600          # las imagenes de prueba miden 600 de ancho
        caja = fitz.Rect(*RENGLON) * escala
        palabras = DATO.split()
        paso = caja.width / len(palabras)
        tokens = [OcrToken(text=p, confidence=0.95,
                           rect=fitz.Rect(caja.x0 + i * paso, caja.y0, caja.x0 + (i + 1) * paso - 4, caja.y1))
                  for i, p in enumerate(palabras)]
        return OcrPage(page_index=page_index, width=ancho, height=alto, tokens=tokens)


@pytest.fixture(scope="module")
def analyzer():
    return PiiAnalyzer()


@pytest.fixture
def procesar_pdf(monkeypatch, analyzer):
    import app.services.pdf.scanned_redactor as sr

    motor = _OcrSimulado()
    monkeypatch.setattr(sr, "get_ocr_engine", lambda: motor)
    monkeypatch.setattr(sr, "get_word_ocr_engine", lambda *_a, **_k: motor)
    return lambda entrada, salida: sr.redact_pdf_scanned(entrada, salida, "t", analyzer)


def _jpeg_movil(tmp_path, orientacion=1) -> Path:
    img = Image.open(io.BytesIO(foto_movil(600, 800)))
    exif = img.getexif()
    exif[0x0112] = orientacion
    destino = tmp_path / "foto.jpg"
    img.save(destino, format="JPEG", quality=90, exif=exif,
             icc_profile=img.info.get("icc_profile"))
    return destino


def _png_con_textos(tmp_path) -> Path:
    img = Image.open(io.BytesIO(foto_movil(600, 800)))
    info = PngImagePlugin.PngInfo()
    info.add_text("Author", "ZZAUTORPNG Marta Ferrer")
    info.add_itxt("Comment", "ZZCOMENTARIOPNG 48291736K")
    destino = tmp_path / "escaneo.png"
    img.save(destino, format="PNG", pnginfo=info)
    return destino


def _oscuridad(img: Image.Image, caja) -> float:
    return 255 - float(np.asarray(img.convert("L").crop(caja)).mean())


def _segmentos_jpeg(datos: bytes) -> list[int]:
    """Marcas de los segmentos antes de los datos de imagen (no se mira dentro de
    los datos comprimidos: ahi cualquier secuencia de bytes es posible)."""
    marcas, i = [], 2
    while i + 4 <= len(datos) and datos[i] == 0xFF:
        marca = datos[i + 1]
        if marca == 0xDA:
            break
        marcas.append(marca)
        i += 2 + int.from_bytes(datos[i + 2:i + 4], "big")
    return marcas


def _sin_metadatos_jpeg(datos: bytes) -> bool:
    """Ni EXIF/GPS/XMP (APP1), ni JFIF (APP0), ni IPTC (APP13), ni comentarios.
    El perfil de color (APP2) y la marca de Adobe (APP14) se conservan, como en
    los JPEG de los PDF: no llevan datos y quitarlos puede cambiar los colores."""
    return not jpeg_con_metadatos(datos) and not [
        m for m in _segmentos_jpeg(datos) if m in (0xE0, 0xE1, 0xED, 0xFE)]


def _fragmentos_png(datos: bytes) -> list[bytes]:
    tipos, i = [], 8
    while i + 8 <= len(datos):
        largo = int.from_bytes(datos[i:i + 4], "big")
        tipos.append(datos[i + 4:i + 8])
        i += 12 + largo
    return tipos


# --- formato de entrada --------------------------------------------------------

def test_reconoce_el_formato_por_su_contenido():
    assert formato_de(b"%PDF-1.7") == "pdf"
    assert formato_de(b"\xff\xd8\xff\xe0") == "jpeg"
    assert formato_de(b"\x89PNG\r\n\x1a\n") == "png"
    assert formato_de(b"II*\x00") == "tiff" and formato_de(b"MM\x00*") == "tiff"
    assert formato_de(b"PK\x03\x04") is None


# --- anonimizar ----------------------------------------------------------------

def test_jpeg_sale_jpeg_tapado_y_sin_metadatos(tmp_path, procesar_pdf):
    entrada = _jpeg_movil(tmp_path)
    salida = tmp_path / "salida.jpg"
    informe = anonimizar_imagen(entrada, salida, "jpeg", procesar_pdf)
    datos = salida.read_bytes()
    assert datos[:3] == b"\xff\xd8\xff"
    assert _sin_metadatos_jpeg(datos)
    original, tapada = Image.open(entrada), Image.open(salida)
    assert tapada.size == original.size
    assert _oscuridad(tapada, RENGLON) > _oscuridad(original, RENGLON) + 60, "el dato no se ha tapado"
    lejos = (40, 600, 560, 780)
    assert abs(_oscuridad(tapada, lejos) - _oscuridad(original, lejos)) < 6, "ha cambiado lo que no era dato"
    assert informe.document_type == "image" and informe.pages == 1
    assert informe.metadata_removed.get("photo_metadata") == 1
    assert informe.entities_by_type


def test_una_foto_grande_no_se_toma_por_un_plano(tmp_path, procesar_pdf):
    """Una foto de 5000x6000 (30 megapixeles) da una pagina por encima de A2 a
    200 ppp y con una imagen dominante: el detector de planos de obra la tomaba
    por un plano sin cajetin y la dejaba sin tachar.  El tamaño de pagina de una
    imagen suelta es inventado."""
    entrada = tmp_path / "grande.png"
    Image.new("RGB", (5000, 6000), "white").save(entrada)
    salida = tmp_path / "salida.png"
    informe = anonimizar_imagen(entrada, salida, "png", procesar_pdf)
    renglon = tuple(int(v * 5000 / 600) for v in RENGLON)
    assert _oscuridad(Image.open(salida), renglon) > 60, "la foto grande ha salido sin tachar"
    assert informe.entities_by_type


def test_una_foto_girada_por_exif_sale_derecha(tmp_path, procesar_pdf):
    entrada = _jpeg_movil(tmp_path, orientacion=6)          # girada 90 grados
    salida = tmp_path / "salida.jpg"
    anonimizar_imagen(entrada, salida, "jpeg", procesar_pdf)
    assert Image.open(salida).size == (800, 600)
    assert 0x0112 not in Image.open(salida).getexif()


def test_png_sale_png_sin_textos(tmp_path, procesar_pdf):
    entrada = _png_con_textos(tmp_path)
    salida = tmp_path / "salida.png"
    anonimizar_imagen(entrada, salida, "png", procesar_pdf)
    datos = salida.read_bytes()
    assert datos[:8] == b"\x89PNG\r\n\x1a\n"
    assert not {b"tEXt", b"iTXt", b"zTXt", b"eXIf", b"tIME"} & set(_fragmentos_png(datos))
    assert Image.open(salida).size == (600, 800)


def test_tiff_de_varias_paginas_sale_tiff_con_las_mismas(tmp_path, procesar_pdf):
    marcos = [Image.open(io.BytesIO(foto_movil(600, 800))).convert("RGB") for _ in range(2)]
    entrada = tmp_path / "escaneo.tif"
    marcos[0].save(entrada, format="TIFF", save_all=True, append_images=marcos[1:],
                   tiffinfo={315: "ZZARTISTATIFF"})
    salida = tmp_path / "salida.tif"
    informe = anonimizar_imagen(entrada, salida, "tiff", procesar_pdf)
    tif = Image.open(salida)
    assert tif.n_frames == 2 and informe.pages == 2
    assert b"ZZARTISTATIFF" not in salida.read_bytes()


# --- solo quitar metadatos -------------------------------------------------------

def _datos_de_imagen_jpeg(datos: bytes) -> bytes:
    return datos[datos.index(b"\xff\xda"):]


def test_solo_metadatos_jpeg_derecho_no_recomprime(tmp_path):
    entrada = _jpeg_movil(tmp_path)
    salida = tmp_path / "salida.jpg"
    informe = solo_metadatos_imagen(entrada, salida, "jpeg")
    assert _sin_metadatos_jpeg(salida.read_bytes())
    assert _datos_de_imagen_jpeg(salida.read_bytes()) == _datos_de_imagen_jpeg(entrada.read_bytes())
    assert informe.treatment == "metadata_only"
    assert "content_not_reviewed" in {c.code for c in informe.coded_warnings}


def test_solo_metadatos_jpeg_girado_se_endereza(tmp_path):
    entrada = _jpeg_movil(tmp_path, orientacion=6)
    salida = tmp_path / "salida.jpg"
    solo_metadatos_imagen(entrada, salida, "jpeg")
    assert Image.open(salida).size == (800, 600)
    assert _sin_metadatos_jpeg(salida.read_bytes())


def test_solo_metadatos_png_conserva_los_pixeles(tmp_path):
    entrada = _png_con_textos(tmp_path)
    salida = tmp_path / "salida.png"
    solo_metadatos_imagen(entrada, salida, "png")
    assert not {b"tEXt", b"iTXt", b"zTXt"} & set(_fragmentos_png(salida.read_bytes()))
    assert np.array_equal(np.asarray(Image.open(entrada)), np.asarray(Image.open(salida)))


# --- por la cola -------------------------------------------------------------------

client = TestClient(app)


def _esperar(job_id):
    fin = time.time() + 180
    while time.time() < fin:
        estado = client.get(f"/jobs/{job_id}").json()
        if estado["status"] in ("completed", "failed"):
            return estado
        time.sleep(0.3)
    raise AssertionError("no termino")


@pytest.mark.parametrize("tratamiento", ["anonymize", "metadata_only"])
def test_por_la_cola_un_jpeg_vuelve_jpeg(tmp_path, tratamiento):
    entrada = _jpeg_movil(tmp_path)
    r = client.post(f"/jobs?treatment={tratamiento}",
                    files={"file": ("foto.jpg", BytesIO(entrada.read_bytes()), "image/jpeg")})
    assert r.status_code == 202
    estado = _esperar(r.json()["job_id"])
    assert estado["status"] == "completed", estado
    descarga = client.get(f"/jobs/{r.json()['job_id']}/result")
    assert descarga.headers["content-type"] == "image/jpeg"
    assert ".jpg" in descarga.headers["content-disposition"]
    assert _sin_metadatos_jpeg(descarga.content)
    assert Image.open(BytesIO(descarga.content)).size == (600, 800)
    informe = client.get(f"/jobs/{r.json()['job_id']}/report.pdf")
    assert informe.status_code == 200


def test_una_imagen_rota_se_rechaza_y_un_tipo_desconocido_tambien():
    assert client.post("/jobs", files={"file": ("x.png", BytesIO(b"\x89PNG\r\n\x1a\nroto"),
                                                "image/png")}).status_code == 400
    assert client.post("/jobs", files={"file": ("x.txt", BytesIO(b"hola"),
                                                "text/plain")}).status_code == 415


def test_la_ruta_sincrona_devuelve_el_mismo_formato(tmp_path):
    entrada = _png_con_textos(tmp_path)
    r = client.post("/anonymize", files={"file": ("e.png", BytesIO(entrada.read_bytes()), "image/png")})
    assert r.status_code == 200, r.text[:300]
    assert r.headers["content-type"] == "image/png"
    assert not {b"tEXt", b"iTXt", b"zTXt"} & set(_fragmentos_png(r.content))
