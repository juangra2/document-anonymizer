"""Imagenes pegadas en una pagina con texto: se leen recortadas y se tachan en su sitio.

Antes, una pagina con texto e imagenes o pasaba ENTERA por OCR (si sus
imagenes sumaban el 5 %) o sus imagenes no se miraban.  Casos dificiles con un
DNI real:

  - a mitad de escala (las dos caras suman un 3,7 %): no se miraba y salia
    entero;
  - girado 90 grados: la pagina iba al OCR, que la lee derecha; la tarjeta de
    lado no la leia nadie y salia entera;
  - a tamano real: la pagina entera se clasificaba como copia de DNI y se
    tapaba, oficio incluido.

Ahora cada imagen que cubre al menos `ocr_imagen_minima` se lee sola, con sus
propios pixeles (sin el texto digital que pueda ir encima) y probando los giros
como el camino escaneado.  Si su texto es el de una copia de identidad, se tapa
la imagen entera; si no, solo los datos que se leen en ella.  Despues se vuelve
a leer el recorte ya tachado: si todavia se lee un dato fiable, la imagen
entera, salvo que parezca una firma (la rubrica se conserva).  Todo se tacha en
la propia pagina digital: el texto de la pagina sigue siendo seleccionable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import fitz
from PIL import Image, ImageDraw

from app.core.config import settings
from app.services.ocr.models import OcrPage
from app.services.pii.models import PiiEntity

# Lector: imagen PIL -> OcrPage con cajas en pixeles de esa imagen.
Lector = Callable[[Image.Image], OcrPage]


@dataclass
class ImagenRevisada:
    bbox: fitz.Rect                         # en la pagina
    copia: bool = False                     # copia de identidad: se tapa entera
    entera: bool = False                    # se tapa entera (copia o red)
    rects: list[fitz.Rect] = field(default_factory=list)   # en la pagina
    entidades: list[PiiEntity] = field(default_factory=list)
    giro: int = 0


def lector_por_defecto() -> Lector:
    """EasyOCR de palabras, el motor rapido (ver ocr/factory)."""
    from app.services.ocr.factory import get_word_ocr_engine
    from app.services.storage.temp_storage import scratch_dir

    motor = get_word_ocr_engine(None)

    def leer(imagen: Image.Image) -> OcrPage:
        with scratch_dir(prefix="imagen_pegada_") as tmp:
            ruta = Path(tmp) / "recorte.png"
            imagen.save(ruta)
            return motor.analyze_image(image_path=ruta, page_index=0)

    return leer


def imagenes_de_la_pagina(page: fitz.Page) -> list[dict]:
    """Imagenes que cubren al menos `ocr_imagen_minima` de la pagina."""
    area = page.rect.width * page.rect.height
    if area <= 0:
        return []
    out = []
    for info in page.get_image_info(xrefs=True):
        if not info.get("xref"):
            continue
        visible = fitz.Rect(info["bbox"]) & page.rect
        if visible.is_empty or visible.get_area() / area < settings.ocr_imagen_minima:
            continue
        out.append(info)
    return out


def _pixeles(doc: fitz.Document, xref: int) -> Image.Image:
    pix = fitz.Pixmap(doc, xref)
    if pix.alpha or pix.colorspace is None or pix.colorspace.n != 3:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _deshacer_giro(r: fitz.Rect, giro: int, ancho: int, alto: int) -> fitz.Rect:
    """Rect en la imagen girada `giro` grados (PIL, antihorario, expand) -> imagen original."""
    def punto(u, v):
        if giro == 90:
            return ancho - v, u
        if giro == 180:
            return ancho - u, alto - v
        if giro == 270:
            return v, alto - u
        return u, v
    a, b = punto(r.x0, r.y0), punto(r.x1, r.y1)
    return fitz.Rect(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))


def _a_pagina(r: fitz.Rect, ancho: int, alto: int, transform) -> fitz.Rect:
    m = fitz.Matrix(transform)
    esquinas = [fitz.Point(x / ancho, y / alto) * m for x in (r.x0, r.x1) for y in (r.y0, r.y1)]
    return fitz.Rect(min(p.x for p in esquinas), min(p.y for p in esquinas),
                     max(p.x for p in esquinas), max(p.y for p in esquinas))


def _datos(ocr: OcrPage, analyzer, mode: str) -> list[tuple[PiiEntity, list[fitz.Rect]]]:
    """Entidades del texto leido, con sus cajas en pixeles de la imagen leida."""
    from app.services.layout.models import PageZone, ZoneType
    from app.services.layout.segmenters import _build_ocr_zone_text_and_tokens
    from app.services.pdf.redaction_plan import RedactionPlan
    from app.services.pdf.scanned_redactor import _entity_rects_from_zone

    if not ocr.tokens:
        return []
    texto, tokens = _build_ocr_zone_text_and_tokens(ocr.tokens)
    zona = PageZone(zone_type=ZoneType.BODY, rect=fitz.Rect(0, 0, ocr.width, ocr.height),
                    text=texto, tokens=tokens, source="ocr")
    plan = RedactionPlan(disabled_types=frozenset(analyzer.disabled_types))
    out = []
    for e in analyzer.analyze(texto, mode=mode, context_text=texto):
        if not plan.admits(e.entity_type):
            continue
        rects = _entity_rects_from_zone(e, zona)
        if rects:
            out.append((PiiEntity(e.entity_type, e.start, e.end, e.score, source="imagen_pegada",
                                  text=texto[e.start:e.end]), rects))
    return out


def revisar_imagenes(page: fitz.Page, analyzer, mode: str = "strict",
                     leer: Lector | None = None) -> list[ImagenRevisada]:
    """Lee cada imagen pegada de la pagina y decide que tachar en ella."""
    from app.services.ocr.orientation import resolve_orientation
    from app.services.pdf.full_redaction import DNI_SCAN, classify_page
    from app.services.pdf.scanned_redactor import TIPOS_FIABLES_RED
    from app.services.vision.scanned_marks import parece_firma

    infos = imagenes_de_la_pagina(page)
    if not infos:
        return []
    leer = leer or lector_por_defecto()
    revisadas = []
    for info in infos:
        try:
            original = _pixeles(page.parent, info["xref"])
        except Exception:
            continue
        # Tope de pixeles, como en el camino escaneado.
        escala = 1.0
        tope = settings.ocr_max_megapixels * 1e6
        if tope > 0 and original.width * original.height > tope:
            escala = (tope / (original.width * original.height)) ** 0.5
            original = original.resize((max(1, int(original.width * escala)),
                                        max(1, int(original.height * escala))))
        ancho, alto = original.size
        bbox = fitz.Rect(info["bbox"])
        orient = resolve_orientation(original, leer)
        leida = original.rotate(orient.angle, expand=True) if orient.angle else original
        ocr = orient.ocr_page
        r = ImagenRevisada(bbox=bbox, giro=orient.angle)

        def a_pagina(rect_leido: fitz.Rect) -> fitz.Rect:
            return _a_pagina(_deshacer_giro(rect_leido, orient.angle, ancho, alto),
                             ancho, alto, info["transform"])

        if classify_page(ocr.text) == DNI_SCAN:
            r.copia = r.entera = True
            r.rects = [bbox]
            r.entidades = [PiiEntity("ES_DNI", 0, 0, 0.99, source="imagen_pegada_copia")]
            revisadas.append(r)
            continue

        datos = _datos(ocr, analyzer, mode)
        if not datos:
            if ocr.tokens:
                revisadas.append(r)
            continue
        r.entidades = [e for e, _ in datos]
        # Segunda lectura del recorte ya tachado (la red de seguridad, en pequeno).
        tachada = leida.copy()
        dibujo = ImageDraw.Draw(tachada)
        for _, rects in datos:
            for x in rects:
                dibujo.rectangle([int(x.x0) - 2, int(x.y0) - 2, int(x.x1) + 2, int(x.y1) + 2], fill="black")
        residuos = _datos(leer(tachada), analyzer, mode)
        fiable = any(e.entity_type in TIPOS_FIABLES_RED for e, _ in residuos)
        if residuos and (fiable or len(residuos) >= 2) and not parece_firma(
                leida, fitz.Rect(0, 0, *leida.size)):
            r.entera = True
            r.rects = [bbox]
        else:
            r.rects = [a_pagina(x) for _, rects in datos + residuos for x in rects]
        revisadas.append(r)
    return revisadas
