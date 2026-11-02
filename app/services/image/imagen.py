"""Imagenes sueltas (JPEG, PNG, TIFF): salen en su formato, tapadas y sin metadatos.

Anonimizar: cada imagen (cada pagina de un TIFF) se mete en una pagina de PDF
medida para que el camino escaneado la lea con sus pixeles originales, pasa por
ese camino completo (OCR, tachado, copias de DNI, red de seguridad,
verificacion) y se saca de la salida.  Si el tope de megapixeles del OCR la
redujo, se vuelve a su tamaño.  Antes se aplica el giro que marca el EXIF: sin
el EXIF, una foto de movil saldria tumbada.

Solo quitar metadatos: un JPEG derecho pierde sus segmentos de metadatos sin
recomprimirse (`limpiar_jpeg`); uno girado por EXIF se endereza y se vuelve a
comprimir, porque quitarle la marca de giro lo dejaria tumbado.  Un PNG pierde
los fragmentos de texto, fecha y EXIF con los pixeles intactos.  Un TIFF se
reescribe sin etiquetas.
"""
from __future__ import annotations

import struct
import tempfile
from io import BytesIO
from pathlib import Path
from time import perf_counter
from typing import Callable

import fitz
from PIL import Image, ImageOps, ImageSequence

from app.core.config import settings
from app.core.errors import DocumentTooLargeError, FileValidationError, TooManyPagesError
from app.schemas.report import CONTENT_NOT_REVIEWED, CodedWarning, ProcessingReport
from app.services.pdf.limpieza import jpeg_con_metadatos, limpiar_jpeg
from app.services.pdf.plan_pages import MARCA_IMAGEN_SUELTA


_ORIENTACION = 0x0112
# Fragmentos de PNG que se conservan al quitar metadatos: los de la imagen y los
# de color.  Fuera texto (tEXt, zTXt, iTXt), fecha (tIME), EXIF (eXIf) y el resto.
_PNG_CONSERVAR = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB",
                  b"iCCP", b"sBIT", b"bKGD", b"pHYs"}
# Etiquetas TIFF que delatan metadatos (descripcion, programa, fecha, autor,
# copyright, EXIF, GPS, XMP, IPTC, Photoshop).
_TIFF_METADATOS = {270, 305, 306, 315, 33432, 34665, 34853, 700, 33723, 34377}


def formato_de(cabecera: bytes) -> str | None:
    """Formato por los primeros bytes, no por la extension ni el tipo declarado."""
    if cabecera.startswith(b"%PDF-"):
        return "pdf"
    if cabecera.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if cabecera.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if cabecera[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    return None


def comprobar_imagen(ruta: Path) -> int:
    """Antes de encolar: que se abre, que no es desmesurada.  Devuelve las paginas."""
    try:
        with Image.open(ruta) as img:
            paginas = getattr(img, "n_frames", 1)
            for marco in ImageSequence.Iterator(img):
                if marco.width * marco.height > settings.max_image_megapixels * 1e6:
                    raise DocumentTooLargeError(
                        f"Image of {marco.width}x{marco.height} px exceeds "
                        f"{settings.max_image_megapixels} megapixels.")
            img.seek(0)
            img.load()
    except (DocumentTooLargeError, TooManyPagesError):
        raise
    except Exception as exc:
        raise FileValidationError("Uploaded image is damaged or not supported.") from exc
    if paginas > settings.max_pdf_pages:
        raise TooManyPagesError(
            f"Image has {paginas} pages. Maximum allowed is {settings.max_pdf_pages}.")
    return paginas


def _marcos(ruta: Path) -> list[Image.Image]:
    """Cada pagina, derecha segun su EXIF y en RGB (la transparencia, sobre blanco)."""
    salida = []
    with Image.open(ruta) as img:
        for marco in ImageSequence.Iterator(img):
            m = ImageOps.exif_transpose(marco.copy())
            if m.mode in ("RGBA", "LA", "P"):
                m = m.convert("RGBA")
                fondo = Image.new("RGB", m.size, "white")
                fondo.paste(m, mask=m.getchannel("A"))
                m = fondo
            salida.append(m.convert("RGB"))
    return salida


def _fragmentos_png(datos: bytes):
    i = 8
    while i + 8 <= len(datos):
        largo, tipo = struct.unpack(">I4s", datos[i:i + 8])
        yield tipo, datos[i:i + 12 + largo]
        i += 12 + largo


def inventario_imagen(ruta: Path, formato: str) -> dict[str, int]:
    """Si la imagen llevaba metadatos (camara, GPS, fechas, textos, autor)."""
    datos = Path(ruta).read_bytes()
    if formato == "jpeg":
        hay = jpeg_con_metadatos(datos)
    elif formato == "png":
        hay = any(t not in _PNG_CONSERVAR for t, _ in _fragmentos_png(datos))
    else:
        with Image.open(ruta) as img:
            hay = bool(_TIFF_METADATOS & set(getattr(img, "tag_v2", {}).keys()))
    return {"photo_metadata": 1} if hay else {}


def _guardar(marcos: list[Image.Image], salida: Path, formato: str) -> None:
    if formato == "jpeg":
        buffer = BytesIO()
        marcos[0].save(buffer, format="JPEG", quality=92, optimize=True)
        datos = buffer.getvalue()
        # PIL escribe un JFIF (APP0): fuera, como en los JPEG de los PDF.
        salida.write_bytes(limpiar_jpeg(datos) or datos)
    elif formato == "png":
        marcos[0].save(salida, format="PNG", optimize=True)
    else:
        marcos[0].save(salida, format="TIFF", save_all=True, append_images=marcos[1:],
                       compression="tiff_deflate")


def _imagen_de_pagina(doc: fitz.Document, page: fitz.Page, tamaño: tuple[int, int]) -> Image.Image:
    """La imagen que el camino escaneado dejo en la pagina, a su tamaño original."""
    imagenes = page.get_images(full=True)
    if imagenes:
        xref = max(imagenes, key=lambda i: i[2] * i[3])[0]
        img = Image.open(BytesIO(doc.extract_image(xref)["image"])).convert("RGB")
    else:
        zoom = tamaño[0] / page.rect.width
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    if img.size != tamaño:
        img = img.resize(tamaño, Image.LANCZOS)
    return img


def anonimizar_imagen(
    entrada: Path,
    salida: Path,
    formato: str,
    procesar_pdf: Callable[[Path, Path], ProcessingReport],
) -> ProcessingReport:
    """`procesar_pdf(pdf_entrada, pdf_salida)` es el camino de PDF (el pipeline)."""
    quitado = inventario_imagen(entrada, formato)
    marcos = _marcos(entrada)
    ppp = settings.ocr_pdf_render_dpi
    with tempfile.TemporaryDirectory(dir=Path(salida).parent) as tmp:
        pdf_entrada, pdf_salida = Path(tmp) / "imagen.pdf", Path(tmp) / "tapada.pdf"
        doc = fitz.open()
        for m in marcos:
            buffer = BytesIO()
            m.save(buffer, format="PNG")
            page = doc.new_page(width=m.width * 72 / ppp, height=m.height * 72 / ppp)
            page.insert_image(page.rect, stream=buffer.getvalue())
        doc.set_metadata({"keywords": MARCA_IMAGEN_SUELTA})
        doc.save(pdf_entrada)
        doc.close()

        informe = procesar_pdf(pdf_entrada, pdf_salida)

        with fitz.open(pdf_salida) as tapado:
            # Un parcial lleva al final la hoja de aviso: no es una pagina de la imagen.
            n = min(len(marcos), informe.pages or len(marcos), len(tapado))
            tapadas = [_imagen_de_pagina(tapado, tapado[i], marcos[i].size) for i in range(n)]
    _guardar(tapadas, Path(salida), formato)
    informe.document_type = "image"
    informe.metadata_removed = quitado
    return informe


def solo_metadatos_imagen(entrada: Path, salida: Path, formato: str, job_id: str = "") -> ProcessingReport:
    empezado = perf_counter()
    entrada, salida = Path(entrada), Path(salida)
    quitado = inventario_imagen(entrada, formato)
    datos = entrada.read_bytes()
    with Image.open(entrada) as img:
        girada = img.getexif().get(_ORIENTACION, 1) not in (0, 1)
        paginas = getattr(img, "n_frames", 1)
    if formato == "jpeg" and not girada and limpiar_jpeg(datos) is not None:
        salida.write_bytes(limpiar_jpeg(datos))
    elif formato == "png" and not girada:
        salida.write_bytes(datos[:8] + b"".join(
            f for t, f in _fragmentos_png(datos) if t in _PNG_CONSERVAR))
    else:
        marcos = []
        with Image.open(entrada) as img:
            for marco in ImageSequence.Iterator(img):
                marcos.append(ImageOps.exif_transpose(marco.copy()))
        if formato == "jpeg":
            buffer = BytesIO()
            marcos[0].convert("RGB").save(buffer, format="JPEG", quality=95)
            salida.write_bytes(limpiar_jpeg(buffer.getvalue()) or buffer.getvalue())
        else:
            _guardar([m.convert("RGB") if m.mode not in ("RGB", "L") else m for m in marcos],
                     salida, formato)
    return ProcessingReport(
        job_id=job_id,
        status="completed_with_warnings",
        document_type="image",
        treatment="metadata_only",
        pages=paginas,
        warnings=["Metadata removed only: the content has NOT been reviewed or anonymized."],
        coded_warnings=[CodedWarning(code=CONTENT_NOT_REVIEWED)],
        metadata_removed=quitado,
        redaction_strategy="metadata_only",
        processing_ms=int((perf_counter() - empezado) * 1000),
    )

