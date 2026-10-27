"""Scanned-PDF redactor — layout-first architecture.

Flow per page
─────────────
1. Render page to PNG at configured DPI.
2. EasyOCR → OcrPage  (tokens with pixel-space bboxes + confidence).
3. OcrPageSegmenter.segment(ocr_page, image) → ZonedPage.
4. Per zone:
   • SIGNATURE / QR_CODE → full zone.rect painted black.
   • FORM                 → zone.layout_redact_rects painted black.
   • BODY / HEADER / FOOTER → PII analyzer on zone.text, entity spans
     mapped to token rects via ZoneToken.text_offset.
5. Optional second-pass OCR on the redacted image to check for residuals.
6. Embed redacted images as PDF pages, save clean output.
"""
from __future__ import annotations

import logging
import contextvars
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from io import BytesIO
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

from app.core.config import settings
from app.schemas.report import (
    REVIEW_FULL_REDACTION_PAGES,
    REVIEW_LOW_OCR_CONFIDENCE,
    REVIEW_PAGES_SKIPPED,
    SAFETY_NET_ACTED,
    SAFETY_NET_STILL_READABLE,
    CodedWarning,
    DetectedEntityInfo,
    OcrInfo,
    PageRedactedByContent,
    PageRedactedInFull,
    ProcessingLimits,
    ProcessingReport,
)
from app.services.layout.models import PageZone, ZoneToken, ZoneType
from app.services.layout.segmenters import OcrPageSegmenter
from app.services.layout.zone_config import ALL_PII_TYPES, get_detector_config
from app.services.ocr.base import OcrEngine
from app.services.ocr import ajustes
from app.services.ocr.factory import get_ocr_engine, get_word_ocr_engine
from app.services.ocr.models import OcrPage, OcrToken
from app.services.ocr.orientation import resolve_orientation
from app.services.pdf.inspector import inspect_pdf_for_phase1
from app.services.pdf.limpieza import integrar_anotaciones, limpiar_para_entregar
from app.services.pdf.partial import append_partial_notice
from app.services.pdf.full_redaction import DNI_SCAN, classify_page, classify_pages
from app.services.pdf.plan_pages import is_image_dominant_page, is_plan_page, redact_plan_page
from app.services.pii.analyzer import PiiAnalyzer
from app.services import cancellation, notices, progress
from app.services.storage.temp_storage import scratch_dir
from app.services.pii.code_policy import QR_TYPE, code_may_stay_visible
from app.services.pii.label_types import structural_rects_released
from app.services.pdf.redaction_plan import RedactionPlan
from app.services.pii.gazetteer import normalize as _norm_token
from app.services.pii.models import PiiEntity
from app.services.vision.card_region import card_regions
from app.services.vision.code_detector import detect_redactable_codes, detect_visual_code_rects
from app.services.vision.scanned_marks import detect_stamp_rects, parece_firma
from app.services.pdf.fondos import es_fondo as _es_fondo

logger = logging.getLogger(__name__)


def _run_with_timeout(fn, timeout_s: int):
    """Run *fn* in a worker thread, returning its result or raising on timeout.

    On timeout the worker thread is abandoned (Python cannot force-kill it); the
    caller skips the page so the overall job still completes.
    """
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn)
    try:
        return future.result(timeout=timeout_s)
    finally:
        executor.shutdown(wait=False)


def _restore(image: Image.Image, angle: int) -> Image.Image:
    """Devuelve la imagen a su orientación original tras trabajar rotada."""
    if not angle:
        return image
    return image.rotate(-angle, expand=True)


def _fuera_de_la_hoja(imagen, girada, ajuste, leer, segmenter, analyzer, mode):
    """Con la hoja recortada del fondo, lo de fuera no se ha analizado: se lee
    aparte (la hoja en blanco) y se tapa lo que lleve datos.  Un segundo papel
    sobre la mesa no puede salir a la vista por no ser la hoja elegida."""
    import numpy as np

    region = ajustes.region_de_trabajo(girada, ajuste)
    fuera = np.asarray(imagen.convert("RGB")).copy()
    fuera[region > 0] = 255
    situados = _residuos_situados(segmenter.segment(leer(Image.fromarray(fuera))), analyzer, mode)
    if situados:
        _aplicar_red(imagen, situados, [])
    return imagen


def _blacked_out(image: Image.Image) -> Image.Image:
    """La página entera en negro, con el mismo tamaño y modo que *image*."""
    return Image.new(image.mode, image.size, "black")


# Metadata-only page classifiers shared with the hybrid/digital paths.
_is_image_dominant_page = is_image_dominant_page


def redact_pdf_scanned(
    input_path: Path,
    output_path: Path,
    job_id: str,
    analyzer: PiiAnalyzer,
    mode: str = "strict",
    document_type: str = "pdf_scanned",
    collect_entities: bool = False,
    paginas_ocr: set[int] | None = None,
    textos_previos: dict[int, str] | None = None,
    redactadas_previas: dict[int, str] | None = None,
) -> ProcessingReport:
    """Redacta un PDF pasando por OCR las paginas que lo necesitan.

    `paginas_ocr` dice cuales.  Con None, todas: es el documento escaneado.  El
    camino hibrido pasa solo las que su criterio de superficie senala, y las
    demas se copian tal cual —con su capa de texto ya redactada por el pase
    digital— para que conserven el texto seleccionable.

    Que un solo bucle recorra el documento entero en orden, pasen o no por
    OCR, es lo que hace que la clasificacion de redaccion total, la parada
    parcial, los numeros de pagina de los avisos y el avance funcionen igual en
    un documento escaneado que en uno mixto.  Antes el camino hibrido tenia su
    propio bucle de OCR, sin enderezado, sin clasificacion y con una geometria
    de barras distinta.

    `textos_previos` es el texto ORIGINAL de cada pagina, antes del pase
    digital.  Hace falta para propagar entre tipos de pagina: una
    autoliquidacion digital que el pase digital ya dejo en negro no conserva
    su justificante en el intermedio, y sin su texto original la clasificacion
    global no podria arrastrar el anexo escaneado que lo repite.
    `redactadas_previas` son las paginas que ese pase ya tacho enteras.
    """
    inspection = inspect_pdf_for_phase1(input_path)

    ocr_engine = get_ocr_engine()
    # Redaction geometry uses word/line-level OCR for tight, aligned boxes; the
    # primary engine (e.g. MinerU) is used only for zone classification when it
    # provides layout.  When the primary already is EasyOCR, the same instance is
    # reused (single OCR pass).
    word_engine = get_word_ocr_engine(ocr_engine)
    provides_layout = ocr_engine.provides_layout
    segmenter = OcrPageSegmenter()

    source_doc = fitz.open(input_path)
    # Las anotaciones visibles se pintan en la pagina, como en el camino digital:
    # asi entran en lo que se rasteriza y en las paginas que se copian (planos).
    integrar_anotaciones(source_doc)
    output_doc = fitz.open()

    all_entities: list[PiiEntity] = []
    # (pagina, posicion en all_entities al empezarla): recuento por pagina.
    marcas: list[tuple[int, int]] = []
    all_residual_entities: list[PiiEntity] = []
    redacted_count = 0
    confidence_values: list[float] = []
    low_confidence_pages: list[int] = []
    skipped_pages: list[int] = []
    plan_pages_done: list[int] = []
    full_redaction_pages: dict[int, str] = {}
    # El texto de cada pagina, para clasificar UNA VEZ sobre el documento entero
    # cuando termina el recorrido (ver _propagar_redaccion_total).
    textos_por_pagina: dict[int, str] = {}
    # Paginas que el camino hibrido pide dejar pasar sin OCR.
    paginas_copiadas = 0
    # Lo que la red de seguridad hace en este documento.
    red_documento = {"imagenes_tachadas": 0, "zonas_repasadas": 0,
                     "persisten": 0, "paginas": 0}
    # En que paginas (desde 0): donde actuo, y donde algo siguio legible.
    paginas_red: list[int] = []
    paginas_persisten: list[int] = []
    rotated_pages: dict[int, int] = {}
    cropped_pages: list[int] = []
    crop_fallbacks: dict[int, str] = {}
    poor_ocr_pages: list[int] = []
    # Páginas tachadas enteras porque el OCR no pudo leerlas: motivo por página.
    unreadable_pages: dict[int, str] = {}
    warnings: list[str] = []
    pages_total_original = inspection.pages
    pages_done = inspection.pages
    partial = False

    try:
        with scratch_dir(prefix="pdf_ocr_") as tmp_dir_name:
            tmp_dir = Path(tmp_dir_name)

            # Engines that process the full PDF natively (MinerU) do one pass here.
            # Raster-based engines (EasyOCR) process page-by-page below.
            native_ocr_pages: list[OcrPage] | None = None
            if ocr_engine.supports_pdf_input:
                try:
                    native_ocr_pages = ocr_engine.analyze_pdf(input_path)
                except Exception as exc:
                    logger.warning(
                        "Native PDF analysis failed (%s); falling back to raster OCR", exc
                    )

            # El testigo de parada se toma AQUÍ, en el hilo del trabajo: el hilo
            # auxiliar que corre cada página con presupuesto de tiempo no hereda
            # las variables de hilo de quien lo lanza, pero sí ve este objeto.
            stop = cancellation.current()

            def _process_page_heavy(
                page_index: int, image: Image.Image, imagenes_px=(),
            ) -> dict:
                """OCR + segment + collect + draw + second pass for one page.

                Runs under a per-page timeout; draws on a copy so the caller can
                fall back to the un-redacted original if it times out.

                Se comprueba la parada entre pasos pesados, no solo entre
                páginas: en un escaneado cada paso es una llamada al OCR y
                esperar a la página entera puede ser varios minutos.
                """
                stop.check()
                # Orientación: un escaneo con el contenido girado devuelve
                # RUIDO, no vacío, y sin corregirlo no se detecta ni se avisa de
                # nada.  Se decide ANTES que MinerU para que el motor de layout
                # también vea la página derecha.  Se trabaja todo en el espacio
                # rotado y la imagen redactada se gira de vuelta al final: así
                # no hay conversión de coordenadas que pueda fallar.
                def _ocr_word(img: Image.Image) -> OcrPage:
                    path = tmp_dir / f"page_{page_index + 1}_w{img.width}x{img.height}.png"
                    img.save(path)
                    return word_engine.analyze_image(
                        image_path=path, page_index=page_index,
                    )

                orientation = resolve_orientation(image, _ocr_word)
                stop.check()
                if orientation.angle:
                    image = image.rotate(orientation.angle, expand=True)
                # Ajuste para leer una pagina que tal cual no se leia (enderezar,
                # recortar la hoja, invertir; ocr/ajustes.py).  Se trabaja en el
                # espacio ajustado y lo tapado vuelve a la original al final.
                ajuste = orientation.ajuste
                girada = image
                image = ajustes.aplicar(image, ajuste)
                limpia = image.copy()

                def _leer(img: Image.Image) -> OcrPage:
                    return _ocr_word(ajustes.para_leer(img, ajuste))

                def _entregar(trabajo: Image.Image) -> Image.Image:
                    vuelta = ajustes.deshacer(trabajo, limpia, girada, ajuste)
                    if ajuste is not None and ajuste.tipo == "perspectiva":
                        vuelta = _fuera_de_la_hoja(vuelta, girada, ajuste, _leer, segmenter, analyzer, mode)
                    return _restore(vuelta, orientation.angle)

                ocr_page = orientation.ocr_page
                work_image = image.copy()

                # Zone classification from the layout engine (MinerU), if any.
                # Con la página rotada no se puede reutilizar el pase nativo,
                # que se hizo sobre la orientación original.
                layout_page: OcrPage | None = None
                if provides_layout:
                    if (
                        native_ocr_pages is not None
                        and page_index < len(native_ocr_pages)
                        and not orientation.angle
                        and ajuste is None
                    ):
                        layout_page = native_ocr_pages[page_index]
                    else:
                        layout_path = tmp_dir / f"page_{page_index + 1}_layout.png"
                        ajustes.para_leer(image, ajuste).save(layout_path)
                        layout_page = ocr_engine.analyze_image(
                            image_path=layout_path, page_index=page_index,
                        )
                stop.check()

                # A1: detect PII on the layout engine's clean, ordered text
                # (MinerU) when available — its blocks keep IBANs/CSV/names
                # contiguous, unlike the reordered word stream — and map the
                # spans to the word-level boxes for tight geometry.  Without a
                # layout engine, detect and place on the word OCR directly.
                # `layout_page.tokens` puede venir VACÍO aunque el motor haya
                # respondido: MinerU no devuelve nada, por ejemplo, en una
                # tarjeta de identidad pequeña sobre un A4 casi en blanco.  Sin
                # este repliegue la página se quedaba sin zonas y no se redactaba
                # nada, otro fallo silencioso; EasyOCR sí lee esas páginas.
                if layout_page is not None and layout_page.tokens:
                    zoned_page = segmenter.segment(layout_page, image=work_image)
                    easy_words = ocr_page.tokens
                    # Lo que MinerU no leyo, lo lee EasyOCR (ver
                    # _zona_de_lo_no_leido).  El repliegue de arriba cubre solo
                    # el caso de todo-o-nada; este cubre la region que falta.
                    complemento = _zona_de_lo_no_leido(
                        layout_page.tokens, ocr_page.tokens,
                    )
                    if complemento is not None:
                        zoned_page.zones.append(complemento)
                        _contar_no_leido(len(complemento.tokens))
                else:
                    if layout_page is not None and not layout_page.tokens:
                        logger.info(
                            "Page %d: layout engine returned no text; falling "
                            "back to word OCR for zoning.", page_index,
                        )
                    zoned_page = segmenter.segment(ocr_page, image=work_image)
                    easy_words = None

                # Redacción total (copia de DNI, autoliquidación): se decide
                # sobre el texto que acaba de producir el OCR.  Aquí la
                # clasificación es POR PÁGINA — el path escaneado procesa
                # página a página y no dispone del texto de las vecinas para
                # propagar; en la práctica cada ejemplar de un impreso repite su
                # cabecera, así que puntúa por sí solo.
                page_ocr_text = "\n".join(z.text for z in zoned_page.zones if z.text)
                full_reason = classify_page(page_ocr_text)
                if full_reason:
                    # Copia de DNI: se intenta redactar solo la tarjeta, que
                    # ocupa una fraccion pequena de un A4 casi vacio.  El
                    # recorte NO se aplica por confianza en el detector: se
                    # pinta, se vuelve a leer con OCR y se exige que no quede
                    # texto legible fuera.  Si queda algo, pagina entera.
                    crop_rects: list[fitz.Rect] = []
                    crop_reason: str | None = None
                    if full_reason == DNI_SCAN and settings.dni_crop_enabled:
                        crop_rects, crop_reason = card_regions(
                            work_image,
                            [t.rect for t in ocr_page.tokens if t.text.strip()],
                            dpi=settings.ocr_pdf_render_dpi,
                        )
                        if crop_rects:
                            probe = work_image.copy()
                            probe_draw = ImageDraw.Draw(probe)
                            for rect in crop_rects:
                                probe_draw.rectangle(
                                    [int(rect.x0), int(rect.y0),
                                     int(rect.x1), int(rect.y1)],
                                    fill="black",
                                )
                            leftover = [
                                t for t in _leer(probe).tokens if t.text.strip()
                            ]
                            if leftover:
                                crop_rects = []
                                crop_reason = (
                                    f"quedaba texto legible fuera "
                                    f"({len(leftover)} fragmentos)"
                                )

                    draw = ImageDraw.Draw(work_image)
                    if crop_rects:
                        for rect in crop_rects:
                            draw.rectangle(
                                [int(rect.x0), int(rect.y0),
                                 int(rect.x1), int(rect.y1)],
                                fill="black",
                            )
                    else:
                        draw.rectangle([0, 0, work_image.width, work_image.height],
                                       fill="black")
                    # La página se pinta entera, pero se analiza igualmente para
                    # el informe: una página en negro que reporta cero entidades
                    # es engañosa para la auditoría y haría figurar como no
                    # detectado lo que está redactado al 100 %.
                    audit_entities = [
                        PiiEntity(
                            entity_type=e.entity_type, start=e.start, end=e.end,
                            score=e.score, source=e.source,
                            text=page_ocr_text[e.start:e.end],
                        )
                        for e in analyzer.analyze(
                            page_ocr_text, mode=mode, zone_type=None,
                            context_text=page_ocr_text,
                        )
                        if e.entity_type in set(ALL_PII_TYPES)
                    ]
                    return {
                        "image": _entregar(work_image),
                        "entities": audit_entities,
                        "residuals": [],
                        "rect_count": len(crop_rects) or 1,
                        "confidence": ocr_page.avg_confidence,
                        "full_redaction": full_reason,
                        "cropped": bool(crop_rects),
                        "crop_fallback_reason": crop_reason if not crop_rects else None,
                        "rotated": orientation.angle,
                        "poor_ocr": orientation.poor_quality,
                        "texto": page_ocr_text,
                    }

                import numpy as np

                marca_tinta = _TINTA.set(np.asarray(work_image.convert("L")))
                lector = getattr(word_engine, "palabras", None)

                def _palabras(rect: fitz.Rect) -> list:
                    margen = 4
                    caja = (max(0, int(rect.x0) - margen), max(0, int(rect.y0) - margen),
                            min(work_image.width, int(rect.x1) + margen),
                            min(work_image.height, int(rect.y1) + margen))
                    recorte = ajustes.para_leer(work_image.crop(caja), ajuste)
                    return [OcrToken(text=t.text, confidence=t.confidence,
                                     rect=fitz.Rect(t.rect) + (caja[0], caja[1], caja[0], caja[1]))
                            for t in lector(recorte)]

                marca_palabras = _PALABRAS.set(_palabras if callable(lector) else None)
                try:
                    page_rects, page_entities = _collect_redaction_rects(
                        zoned_page=zoned_page,
                        analyzer=analyzer,
                        mode=mode,
                        collect_entities=collect_entities,
                        easy_words=easy_words,
                    )
                finally:
                    _TINTA.reset(marca_tinta)
                    _PALABRAS.reset(marca_palabras)

                page_rects = _sin_pisar_rotulos(page_rects, ocr_page.tokens)
                draw = ImageDraw.Draw(work_image)
                for rect in page_rects:
                    draw.rectangle(
                        [int(rect.x0), int(rect.y0), int(rect.x1), int(rect.y1)],
                        fill="black",
                    )

                residuals: list[PiiEntity] = []
                red = {"imagenes": 0, "zonas": 0, "persisten": 0}
                stop.check()
                if settings.ocr_second_pass_verification:
                    residual_ocr = _leer(work_image)
                    situados = _residuos_situados(
                        segmenter.segment(residual_ocr), analyzer, mode,
                    )
                    if situados:
                        # Red de seguridad (ver TIPOS_FIABLES_RED).  Las imagenes
                        # pegadas se situan en coordenadas del render sin girar:
                        # si la pagina se enderezo, no casan y se usa solo la
                        # regla de la zona, que es la prudente.
                        marcos = (list(imagenes_px)
                                  if not orientation.angle and ajuste is None else [])
                        red["imagenes"], red["zonas"], _ = _aplicar_red(
                            work_image, situados, marcos,
                        )
                        # Tercera lectura: ¿se sigue viendo algo tras repasar?
                        stop.check()
                        tercera = _leer(work_image)
                        persisten = _residuos_situados(
                            segmenter.segment(tercera), analyzer, mode,
                        )
                        residuals = [e for e, _ in persisten]
                        red["persisten"] = len(residuals)

                return {
                    "image": _entregar(work_image),
                    "entities": page_entities,
                    "residuals": residuals,
                    "red": red,
                    "rect_count": len(page_rects),
                    "confidence": ocr_page.avg_confidence,
                    "rotated": orientation.angle,
                    "poor_ocr": orientation.poor_quality,
                    "texto": page_ocr_text,
                }

            on_page = progress.claim(source_doc.page_count)

            try:
                for page_index, page in enumerate(source_doc):
                    on_page(page_index)
                    marcas.append((page_index, len(all_entities)))
                    cancellation.check()

                    # Pagina que no necesita OCR (documento mixto): se copia tal
                    # cual.  Viene del intermedio que ya redacto el pase digital,
                    # asi que su capa de texto esta limpia y se conserva
                    # seleccionable.  Su texto se apunta igualmente, porque la
                    # clasificacion de redaccion total mira el documento entero.
                    if paginas_ocr is not None and page_index not in paginas_ocr:
                        textos_por_pagina[page_index] = (
                            textos_previos.get(page_index, "")
                            if textos_previos is not None
                            else page.get_text("text")
                        )
                        output_doc.insert_pdf(
                            source_doc, from_page=page_index, to_page=page_index,
                        )
                        paginas_copiadas += 1
                        continue

                    # Plan pages (A1/A0 drawings) are detected from metadata BEFORE
                    # rendering: rasterising them is the expensive step the page
                    # budget cannot cover.  Only the title block is analysed and
                    # redacted; without one, the page is kept as-is and flagged.
                    if is_plan_page(page):
                        found, plan_entities, plan_rects = redact_plan_page(
                            page, analyzer, mode=mode,
                        )
                        if found:
                            logger.info(
                                "Page %d: construction plan, title block redacted "
                                "(%d rects).", page_index, plan_rects,
                            )
                            all_entities.extend(plan_entities)
                            redacted_count += plan_rects
                            plan_pages_done.append(page_index)
                        else:
                            logger.warning(
                                "Page %d: construction plan without detectable "
                                "title block; kept as-is (manual review).",
                                page_index,
                            )
                            skipped_pages.append(page_index)
                        output_doc.insert_pdf(
                            source_doc, from_page=page_index, to_page=page_index,
                        )
                        continue

                    # El render respeta un tope de pixeles (ocr_max_megapixels),
                    # asi que una pagina enorme no es cara: es una pagina normal
                    # reducida.  Por eso una pagina con una imagen dominante no
                    # se salta: un A4 escaneado a 600 DPI o una foto de movil
                    # moderna son paginas corrientes y pasan por el mismo
                    # tratamiento que las demas.
                    image = _render_page_to_image(page, dpi=settings.ocr_pdf_render_dpi)
                    # Donde estan las imagenes pegadas, para la red de seguridad.
                    imagenes_px = _imagenes_pegadas_px(page, _zoom_con_tope(
                        page, settings.ocr_pdf_render_dpi, settings.ocr_max_megapixels,
                    ))

                    # Per-page timeout: a stalled page must not hang the whole job.
                    # Sin leerla no se puede descartar que lleve datos, así que se
                    # tacha entera en vez de entregarse intacta.
                    try:
                        result = _run_with_timeout(
                            lambda: _process_page_heavy(page_index, image, imagenes_px),
                            settings.ocr_page_timeout_s,
                        )
                    except FuturesTimeout:
                        if settings.redact_unreadable_pages:
                            logger.warning(
                                "Page %d exceeded the %ds OCR budget; redacted in full.",
                                page_index, settings.ocr_page_timeout_s,
                            )
                            unreadable_pages[page_index] = notices.OCR_TIMEOUT
                            # En vivo: quien esté mirando la pantalla se entera
                            # ahora, no al leer el informe media hora despues.
                            notices.page_redacted_in_full(page_index, notices.OCR_TIMEOUT)
                            redacted_count += 1
                            _append_image_as_pdf_page(
                                output_doc, _blacked_out(image), page.rect,
                            )
                        else:
                            logger.warning(
                                "Page %d exceeded the %ds OCR budget; skipping (manual review).",
                                page_index, settings.ocr_page_timeout_s,
                            )
                            skipped_pages.append(page_index)
                            _append_image_as_pdf_page(output_doc, image, page.rect)
                        continue

                    if result.get("rotated"):
                        rotated_pages[page_index] = result["rotated"]
                    if result.get("poor_ocr"):
                        if settings.redact_unreadable_pages and not result.get("full_redaction"):
                            # Ilegible en todos los giros: lo que el OCR no ha leído
                            # tampoco se ha podido tachar.  Las entidades se conservan
                            # para el informe; los residuos del segundo pase ya no
                            # aplican sobre una página en negro.
                            unreadable_pages[page_index] = notices.OCR_UNREADABLE
                            notices.page_redacted_in_full(page_index, notices.OCR_UNREADABLE)
                            result["image"] = _blacked_out(result["image"])
                            result["rect_count"] = 1
                            result["residuals"] = []
                        else:
                            poor_ocr_pages.append(page_index)

                    # En una pagina mixta el OCR lee la imagen del intermedio,
                    # donde el pase digital ya tacho la capa de texto: el texto
                    # original de esa capa se suma para no perder sus
                    # referencias al clasificar el documento entero.
                    textos_por_pagina[page_index] = (
                        (textos_previos.get(page_index, "") + "\n" if textos_previos else "")
                        + (result.get("texto") or "")
                    )

                    if result.get("full_redaction"):
                        full_redaction_pages[page_index] = result["full_redaction"]
                        if result.get("cropped"):
                            cropped_pages.append(page_index)
                        elif result.get("crop_fallback_reason"):
                            crop_fallbacks[page_index] = result["crop_fallback_reason"]
                        logger.info("Page %d fully redacted (%s).", page_index,
                                    result["full_redaction"])

                    all_entities.extend(result["entities"])
                    all_residual_entities.extend(result["residuals"])
                    red_pagina = result.get("red") or {}
                    if red_pagina.get("imagenes") or red_pagina.get("zonas"):
                        red_documento["paginas"] += 1
                        paginas_red.append(page_index)
                    if any(e.entity_type in TIPOS_AVISO_RED for e in result["residuals"]):
                        paginas_persisten.append(page_index)
                        RED_SEGURIDAD["paginas"] += 1
                    for clave_red, clave_doc in (("imagenes", "imagenes_tachadas"),
                                                 ("zonas", "zonas_repasadas"),
                                                 ("persisten", "persisten")):
                        valor = red_pagina.get(clave_red, 0)
                        red_documento[clave_doc] += valor
                        RED_SEGURIDAD[clave_doc] += valor
                    redacted_count += result["rect_count"]
                    if result["confidence"] is not None:
                        confidence_values.append(result["confidence"])
                        if result["confidence"] < 0.75:
                            low_confidence_pages.append(page_index)

                    _append_image_as_pdf_page(output_doc, result["image"], page.rect)

            except cancellation.JobStopped:
                # PARAR: el PDF de salida ya contiene SOLO las páginas terminadas,
                # porque cada una se añade al acabarla.  La que estaba en curso no
                # llegó a añadirse, así que no hay nada que quitar.
                stop.honor()
                partial = True
                pages_done = output_doc.page_count
                append_partial_notice(output_doc, pages_done, pages_total_original)
                warnings.append(
                    f"Partial document: pages 1-{pages_done} of "
                    f"{pages_total_original} were processed before the stop request."
                )

            # Clasificacion del documento ENTERO, una sola vez y con el texto de
            # todas las paginas.  Solo cuando el recorrido ha terminado: sobre una
            # entrega parcial faltarian paginas y la propagacion decidiria con
            # medio documento delante.
            if not partial:
                propagadas = _propagar_redaccion_total(
                    output_doc, textos_por_pagina,
                    {**(redactadas_previas or {}), **full_redaction_pages},
                )
                if propagadas:
                    full_redaction_pages.update(propagadas)
                    redacted_count += len(propagadas)

        limpiar_para_entregar(output_doc)

        output_doc.save(
            output_path,
            garbage=4,
            deflate=True,
            clean=True,
            incremental=False,
        )

    finally:
        source_doc.close()
        output_doc.close()

    residual_by_type = analyzer.count_by_type(all_residual_entities)
    residual_count = len(all_residual_entities)

    if low_confidence_pages:
        warnings.append("Low OCR confidence detected in one or more pages.")
    frases_red, coded_warnings, revision_por_residuos = avisos_de_red(
        red_documento, paginas_red, paginas_persisten, all_residual_entities,
    )
    warnings.extend(frases_red)
    if rotated_pages:
        detail = ", ".join(
            f"{idx + 1}: {angle}°" for idx, angle in sorted(rotated_pages.items())
        )
        warnings.append(
            f"Scanned pages auto-rotated before OCR ({detail})."
        )
    if poor_ocr_pages:
        # Fallo silencioso: sin este aviso el usuario creería que el documento
        # está limpio cuando en realidad el OCR no ha podido leerlo.
        warnings.append(
            "Low-quality OCR text on pages "
            f"{[i + 1 for i in sorted(poor_ocr_pages)]}: content may not have "
            "been detected. Manual review required."
        )
    if unreadable_pages:
        by_reason: dict[str, list[int]] = {}
        for idx, why in sorted(unreadable_pages.items()):
            by_reason.setdefault(why, []).append(idx + 1)
        detail = "; ".join(f"{why}: {pages}" for why, pages in sorted(by_reason.items()))
        warnings.append(
            "Pages redacted in full because OCR could not read them "
            f"({detail}). Manual review required."
        )

    if cropped_pages:
        warnings.append(
            "Identity-document pages redacted by cropping the card area: "
            f"{[i + 1 for i in sorted(cropped_pages)]}."
        )
    if crop_fallbacks:
        detail = "; ".join(
            f"{idx + 1}: {why}" for idx, why in sorted(crop_fallbacks.items())
        )
        warnings.append(
            f"Identity-document pages redacted in full instead of cropped ({detail})."
        )

    if full_redaction_pages:
        by_reason: dict[str, list[int]] = {}
        for idx, why in sorted(full_redaction_pages.items()):
            by_reason.setdefault(why, []).append(idx + 1)
        detail = "; ".join(f"{why}: {pages}" for why, pages in sorted(by_reason.items()))
        warnings.append(
            "Pages fully redacted as identity-document copies or tax "
            f"self-assessment forms ({detail}). Manual review required."
        )
    if skipped_pages:
        warnings.append(
            "Pages skipped (image-dominant or OCR timeout) and not fully redacted: "
            f"{[i + 1 for i in sorted(skipped_pages)]}. Manual review required."
        )

    confidence_avg = (
        sum(confidence_values) / len(confidence_values) if confidence_values else None
    )

    return ProcessingReport(
        job_id=job_id,
        status="completed_with_warnings" if warnings else "completed",
        document_type=document_type,
        pages=pages_done if partial else inspection.pages,
        partial=partial,
        pages_total_original=pages_total_original if partial else None,
        selectable_text_chars=inspection.selectable_text_chars,
        entities_detected=len(all_entities),
        entities_redacted=redacted_count,
        entities_by_type=analyzer.count_by_type(all_entities),
        entities_by_page=analyzer.count_by_page(marcas, all_entities),
        residual_entities=residual_count,
        residual_entities_by_type=residual_by_type,
        manual_review_required=(
            bool(low_confidence_pages) or revision_por_residuos or bool(skipped_pages)
            or bool(full_redaction_pages) or bool(poor_ocr_pages)
        ),
        manual_review_reasons=(
            [REVIEW_LOW_OCR_CONFIDENCE] * bool(low_confidence_pages or poor_ocr_pages)
            + [SAFETY_NET_STILL_READABLE] * bool(revision_por_residuos)
            + [REVIEW_PAGES_SKIPPED] * bool(skipped_pages)
            + [REVIEW_FULL_REDACTION_PAGES] * bool(full_redaction_pages)
        ),
        warnings=warnings,
        coded_warnings=coded_warnings,
        pages_redacted_in_full=[
            PageRedactedInFull(page=idx + 1, reason=why)
            for idx, why in sorted(unreadable_pages.items())
        ],
        pages_redacted_by_content=[
            PageRedactedByContent(page=idx + 1, reason=why)
            for idx, why in sorted(full_redaction_pages.items())
        ],
        redaction_mode=mode,
        limits=ProcessingLimits(
            max_upload_mb=settings.max_upload_mb,
            max_pdf_pages=settings.max_pdf_pages,
            min_digital_text_chars=settings.min_digital_text_chars,
        ),
        ocr=OcrInfo(
            # Las paginas copiadas sin OCR (documento mixto) no cuentan: el
            # informe tiene que decir cuantas se miraron de verdad.
            enabled=(pages_done if partial else inspection.pages) - paginas_copiadas > 0,
            engine=ocr_engine.engine_name,
            pages_ocr_processed=max(
                0, (pages_done if partial else inspection.pages) - paginas_copiadas,
            ),
            images_processed=0,
            ocr_confidence_avg=confidence_avg,
            low_confidence_pages=low_confidence_pages,
            second_pass_verification=settings.ocr_second_pass_verification,
            safety_net_images_redacted=red_documento["imagenes_tachadas"],
            safety_net_zones_repainted=red_documento["zonas_repasadas"],
            safety_net_still_readable=red_documento["persisten"],
        ),
        detected_entities=(
            [
                DetectedEntityInfo(text=e.text, type=e.entity_type, score=e.score, source=e.source)
                for e in all_entities
                if e.text
            ]
            if collect_entities
            else []
        ),
    )


# ---------------------------------------------------------------------------
# Per-page redaction logic
# ---------------------------------------------------------------------------

def _collect_redaction_rects(
    zoned_page,
    analyzer: PiiAnalyzer,
    mode: str,
    collect_entities: bool = False,
    easy_words: list | None = None,
) -> tuple[list[fitz.Rect], list[PiiEntity]]:
    """Return (pixel_rects_to_redact, pii_entities_found).

    When *easy_words* is provided (A1), PII is detected on the layout engine's
    clean text but redaction rects are taken from those word-level OCR boxes for
    tight, aligned geometry; otherwise rects come from the zone's own tokens.

    Todo pasa por un RedactionPlan, igual que en el camino digital: el filtro de
    tipos desactivados en un único sitio y el recuento sin depender de
    `collect_entities`.
    """
    plan = RedactionPlan(disabled_types=frozenset(analyzer.disabled_types))
    easy_line_h = _median_height(easy_words) if easy_words else 0.0

    # Contexto de pagina para el gate de importes (ver PiiAnalyzer.analyze).
    page_context = chr(10).join(z.text for z in zoned_page.zones if z.text)
    # Donde empieza cada zona en ese texto: el filtro institucional mira el
    # membrete y lo que hay antes de cada dato en la PAGINA, no en la zona.  El
    # segmentador de OCR no lo fija (lo deja a 0).
    desplazamiento = 0
    for zone in zoned_page.zones:
        if zone.text:
            zone.page_text_offset = desplazamiento
            desplazamiento += len(zone.text) + 1
    contexto_formulario: list[PiiEntity] = []

    def _en_pagina(entity: PiiEntity, zone) -> PiiEntity:
        return PiiEntity(entity_type=entity.entity_type,
                         start=zone.page_text_offset + entity.start,
                         end=zone.page_text_offset + entity.end,
                         score=entity.score, source=entity.source,
                         text=zone.text[entity.start:entity.end])

    for zone in zoned_page.zones:

        # SIGNATURE / QR_CODE → full zone rect
        if zone.is_auto_redact:
            if zone.zone_type == ZoneType.QR_CODE:
                if code_may_stay_visible(zone.text, analyzer):
                    continue
                plan.add_entity(
                    PiiEntity(
                        entity_type="QR_CODE", start=0, end=len(zone.text or ""),
                        score=0.99, source="visual_qr", text=zone.text,
                    ),
                    [zone.rect],
                    always=True,
                )
            else:
                plan.add_rect(zone.rect, None)
            continue

        # FORM / TABLE with pre-computed structural rects (heuristic segmenter).
        if zone.zone_type in (ZoneType.FORM, ZoneType.TABLE) and zone.layout_redact_rects:
            types = zone.layout_redact_types or []
            for index, rect in enumerate(zone.layout_redact_rects):
                plan.add_rect(rect, types[index] if index < len(types) else None, structural=True)
            if structural_rects_released(types, analyzer.disabled_types):
                # Lo que cubría el rect descartado ya no está tapado: se reconstruye
                # el texto de la zona y se tapan sus entidades activas.
                for entity, rects in _released_form_entities(
                    zone, analyzer, mode, page_context, easy_words, easy_line_h,
                ):
                    plan.add_entity(entity, rects)
                continue
            cfg = get_detector_config(zone.zone_type)
            if zone.text.strip() and cfg.entity_types is not None:
                allowed = set(cfg.entity_types)
                for entity in analyzer.analyze(
                    zone.text, mode=mode, zone_type=zone.zone_type.value,
                    context_text=page_context,
                ):
                    if entity.entity_type in allowed:
                        contexto_formulario.append(_en_pagina(entity, zone))
                        # Valor ya tapado por el rect estructural: se cuenta, no se pinta.
                        plan.add_entity(PiiEntity(
                            entity_type=entity.entity_type, start=entity.start, end=entity.end,
                            score=entity.score, source=entity.source,
                            text=zone.text[entity.start:entity.end],
                        ), [])
            continue

        # BODY / HEADER / FOOTER → PII analysis
        cfg = get_detector_config(zone.zone_type)
        if not zone.text.strip() or cfg.entity_types is None:
            continue

        allowed = set(cfg.entity_types)
        for entity in analyzer.analyze(
            zone.text,
            mode=mode,
            zone_type=zone.zone_type.value,
            context_text=page_context,
        ):
            if entity.entity_type not in allowed:
                continue
            if easy_words is not None:
                entity_rects = _entity_rects_via_easy(entity, zone, easy_words, easy_line_h)
            else:
                entity_rects = _entity_rects_from_zone(entity, zone)
            if entity_rects:
                plan.add_candidate(PiiEntity(
                    entity_type=entity.entity_type, start=entity.start, end=entity.end,
                    score=entity.score, source=entity.source,
                    text=zone.text[entity.start:entity.end],
                ), entity_rects, en_pagina=_en_pagina(entity, zone))

    # El contacto de la institucion emisora no se tacha: el MISMO filtro que en
    # el camino digital, en el punto unico de salida (RedactionPlan).
    plan.resolve_candidates(page_context, zoned_page, contexto=contexto_formulario)
    return plan.rects, plan.entities


_WORD_TOK_RE = re.compile(r"[0-9A-Za-zÁÉÍÓÚÑÜáéíóúñüÀÈÒÏÇàèòïç]+")

# OCR letter↔digit confusions, applied to digit-bearing tokens only so an
# identifier read slightly differently by the layout engine (MinerU) and the
# word engine (EasyOCR) still matches — e.g. "ES13…" vs "E513…" (S↔5).  Plain
# alphabetic words are left untouched to avoid cross-word collisions.
_OCR_FOLD = str.maketrans("SOILB", "50118")


def _canon_tok(raw: str) -> str | None:
    """Canonical form of a raw token for OCR-tolerant matching, or None.

    Normalises, and folds common OCR confusions on digit-bearing tokens so a
    misread identifier matches across the two OCR engines; plain alphabetic
    words are left untouched to avoid cross-word collisions.
    """
    if len(raw) < 2:
        return None
    norm = _norm_token(raw)
    if not norm:
        return None
    if any(ch.isdigit() for ch in norm):
        norm = norm.translate(_OCR_FOLD)
    return norm


#: Parecido minimo para dar por igual un token del dato y uno del OCR.
#:
#: No se copia del 0.85 de la lectura de salidas por analogia: alli se comparan
#: cadenas para VERIFICAR, aqui se decide DONDE se pinta una barra y emparejar
#: mal tapa el sitio equivocado.  El umbral acepta un identificador con una
#: letra mal leida por el OCR (un NIE con la Z leida como 2) y rechaza dos
#: datos distintos que solo difieren en la ultima cifra (dos codigos postales
#: vecinos).  Y solo se aplica a tokens de 5 caracteres o mas —identificadores—:
#: en uno corto cualquier parecido es casualidad.
PARECIDO_TOKEN = 0.85
_LARGO_MINIMO_PARECIDO = 5


def _casan(token_dato: str, token_ocr: str) -> bool:
    """¿Son el mismo token, admitiendo que el OCR lo haya leido con erratas?"""
    if token_dato == token_ocr:
        return True
    # El OCR pega tokens vecinos cuando se come un separador: "PAD/2023/6391"
    # leido como "PADI2023/6391" deja las piezas dentro de una palabra mas larga.
    # Se exige que la palabra del OCR lleve digitos —es un identificador pegado,
    # no prosa— para que "Ana" no case con "Anastasia".
    if any(c.isdigit() for c in token_ocr):
        if len(token_dato) >= 4 and token_dato in token_ocr:
            return True
        if len(token_dato) >= 3 and token_ocr.startswith(token_dato):
            return True
    if len(token_dato) < _LARGO_MINIMO_PARECIDO or len(token_ocr) < _LARGO_MINIMO_PARECIDO:
        return False
    from difflib import SequenceMatcher
    return SequenceMatcher(None, token_dato, token_ocr).ratio() >= PARECIDO_TOKEN


def _compartidos(tokens_dato: set[str], tokens_ocr: set[str]) -> set[str]:
    """Tokens del dato que aparecen en el OCR, tolerando sus erratas."""
    iguales = tokens_dato & tokens_ocr
    restantes = tokens_dato - iguales
    if not restantes:
        return iguales
    return iguales | {
        td for td in restantes if any(_casan(td, to) for to in tokens_ocr)
    }


def _content_tokens(text: str) -> set[str]:
    """Set of canonical tokens (≥2 chars) of *text* for content matching."""
    return {c for raw in _WORD_TOK_RE.findall(text) if (c := _canon_tok(raw))}


def _is_id_like(token: str) -> bool:
    """True for a single token specific enough to anchor on its own (a DNI/NIE/
    NSS/CP/IBAN-fragment): at least 5 chars and containing a digit."""
    return len(token) >= 5 and any(ch.isdigit() for ch in token)


#: Imagen de la pagina en curso (escala de grises), para ajustar a la tinta las
#: barras que tapan un tramo de una pieza.  La pone _process_page_heavy.
_TINTA: contextvars.ContextVar = contextvars.ContextVar("tinta_pagina", default=None)
#: Lector de palabras sueltas de la pagina en curso: rect -> [OcrToken] en
#: coordenadas de la pagina.  Lo pone _process_page_heavy si el motor lo admite.
_PALABRAS: contextvars.ContextVar = contextvars.ContextVar("palabras_pagina", default=None)


def _tramo_por_palabras(ent_tokens: set[str], word) -> fitz.Rect | None:
    """La barra exacta de un dato dentro de una pieza con mas texto: se vuelve
    a leer la pieza palabra a palabra y se toma el tramo seguido de palabras del
    dato que lo cubre entero (si hay varios, el de mas a la derecha: el valor va
    detras de su etiqueta, y la etiqueta puede repetir una palabra del valor).
    None si no se puede (sin lector, o no casa el dato entero)."""
    leer = _PALABRAS.get()
    if leer is None:
        return None
    try:
        palabras = sorted(leer(word.rect), key=lambda t: t.rect.x0)
    except Exception:  # el ajuste fino no puede tumbar la pagina
        logger.debug("lectura por palabras fallida", exc_info=True)
        return None
    if not palabras:
        return None

    def del_dato(t) -> tuple[set[str], int]:
        """(palabras del dato, caracteres de etiqueta delante).  El detector de
        EasyOCR pega a veces la etiqueta a la primera palabra del valor aunque
        haya hueco ("DIRECCION:Avenida", "TELEFONO:+34"): se admite un prefijo
        que no es del dato si todo lo que sigue si lo es."""
        hits, prefijo = set(), 0
        for m in _WORD_TOK_RE.finditer(t.text):
            canon = _canon_tok(m.group())
            hit = next((td for td in ent_tokens if canon and _casan(td, canon)), None)
            if hit is None:
                if hits:
                    return set(), 0   # algo que no es del dato DESPUES del dato
                prefijo = m.end()
                continue
            hits.add(hit)
        return hits, prefijo

    tramos, actual, cubre, inicio = [], [], set(), 0
    for t in palabras + [None]:
        hits, prefijo = del_dato(t) if t is not None else (set(), 0)
        if hits and prefijo and actual:
            tramos.append((actual, cubre, inicio))     # una etiqueta corta el tramo
            actual, cubre = [], set()
        if hits:
            if not actual:
                inicio = prefijo
            actual.append(t)
            cubre |= hits
        elif actual:
            tramos.append((actual, cubre, inicio))
            actual, cubre = [], set()
    completos = [x for x in tramos if x[1] >= ent_tokens]
    if not completos:
        return None
    elegidas, _, prefijo = completos[-1]
    # La comparacion admite erratas: un trozo del dato ("53.810.2" de
    # "53.810.247-E", que la relectura partio en dos) podia pasar por el dato
    # entero y la barra dejaba el resto a la vista.  El tramo tiene que tener
    # casi todos los caracteres del dato; si no, se usa la estimacion prudente.
    leidos = sum(len(_canon_tok(m.group()) or "") for t in elegidas
                 for m in _WORD_TOK_RE.finditer(t.text[prefijo:] if t is elegidas[0] else t.text))
    if leidos < 0.9 * sum(len(tok) for tok in ent_tokens):
        return None
    # Las piezas de una o dos letras no cuentan como palabras del dato (la letra
    # de un DNI: "53.810.247" + "-E"): si van pegadas detras del tramo, son suyas.
    alto = word.rect.y1 - word.rect.y0
    siguientes = [t for t in palabras if t.rect.x0 >= elegidas[-1].rect.x1 - 1]
    for t in siguientes:
        cortas = [len(m.group()) for m in _WORD_TOK_RE.finditer(t.text)]
        if t.rect.x0 - elegidas[-1].rect.x1 > 0.6 * alto or not cortas or max(cortas) > 2:
            break
        elegidas = elegidas + [t]
    x0 = elegidas[0].rect.x0
    if prefijo:
        x0 = _inicio_tras_prefijo(elegidas[0], prefijo)
    return _padded(fitz.Rect(x0, word.rect.y0,
                             max(t.rect.x1 for t in elegidas), word.rect.y1))


def _inicio_tras_prefijo(palabra, prefijo: int) -> float:
    """Donde empieza el dato dentro de una pieza con la etiqueta pegada: el hueco
    de tinta mas cercano a la estimacion por anchura de caracter.  En una pieza
    tan corta no hay huecos dentro del dato con que confundirlo; sin hueco, la
    estimacion con un caracter de margen hacia la etiqueta."""
    texto = palabra.text
    pesos = [_anchura_relativa(c) for c in texto]
    ancho = palabra.rect.x1 - palabra.rect.x0
    estimado = palabra.rect.x0 + ancho * sum(pesos[:prefijo]) / max(1e-6, sum(pesos))
    gris = _TINTA.get()
    bloques = _bloques_de_tinta(gris, palabra.rect) if gris is not None else []
    huecos = [(bloques[i][1], bloques[i + 1][0]) for i in range(len(bloques) - 1)]
    prudente = max(palabra.rect.x0, estimado - ancho / max(1, len(texto)))
    if huecos:
        _, fin = min(huecos, key=lambda h: abs((h[0] + h[1]) / 2 - estimado))
        # El hueco tiene que dejar a cada lado una anchura acorde con lo que
        # hay escrito (etiqueta delante, dato detras); si no, es un hueco de
        # otra cosa (dentro del dato: "+34 622") y se usa la estimacion.
        parte = (fin - palabra.rect.x0) / ancho
        esperada = sum(pesos[:prefijo]) / max(1e-6, sum(pesos))
        if abs(parte - esperada) <= 0.25 * max(esperada, 1 - esperada) and fin <= estimado + 0.15 * ancho:
            return min(fin, max(prudente, fin))
    return prudente


def _otsu(zona) -> float:
    import numpy as np

    hist, bordes = np.histogram(zona, bins=64, range=(0, 255))
    total, suma = hist.sum(), (hist * bordes[:-1]).sum()
    mejor, umbral, peso, acumulado = -1.0, 128.0, 0.0, 0.0
    for i in range(64):
        peso += hist[i]
        if peso == 0 or peso == total:
            continue
        acumulado += hist[i] * bordes[i]
        m0, m1 = acumulado / peso, (suma - acumulado) / (total - peso)
        varianza = peso * (total - peso) * (m0 - m1) ** 2
        if varianza > mejor:
            mejor, umbral = varianza, bordes[i + 1]
    return float(umbral)


def _bloques_de_tinta(gris, rect: fitz.Rect) -> list[tuple[float, float]]:
    """Tramos horizontales con tinta dentro de *rect*: las palabras de la pieza.

    Columnas con algun pixel de tinta (umbral de Otsu en el recorte; si el fondo
    es oscuro, la tinta es lo claro), agrupadas cuando el hueco entre ellas es
    menor que una fraccion de la altura: el espacio entre letras no separa, el
    espacio entre palabras si.
    """
    import numpy as np

    x0, y0 = max(0, int(rect.x0)), max(0, int(rect.y0))
    x1, y1 = min(gris.shape[1], int(rect.x1) + 1), min(gris.shape[0], int(rect.y1) + 1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return []
    zona = gris[y0:y1, x0:x1].astype(np.float32)
    umbral = _otsu(zona)
    tinta = zona > umbral if np.median(zona) < umbral else zona < umbral
    # Dos pixeles de tinta por columna: un punto suelto del ruido de un JPEG muy
    # comprimido no cuenta, y si contara rellenaria los huecos entre palabras.
    columnas = tinta.sum(axis=0) >= 2
    hueco_min = max(2, int(0.22 * (y1 - y0)))
    bloques, inicio, fin, vacias = [], None, 0, 0
    for i, c in enumerate(columnas):
        if c:
            if inicio is None:
                inicio = i
            vacias, fin = 0, i
        elif inicio is not None:
            vacias += 1
            if vacias >= hueco_min:
                bloques.append((x0 + inicio, x0 + fin + 1))
                inicio = None
    if inicio is not None:
        bloques.append((x0 + inicio, x0 + fin + 1))
    return bloques


def _ajustar_a_tinta(estimado: fitz.Rect, word, c0: int, c1: int) -> fitz.Rect:
    """Lleva cada extremo de la barra a un hueco de verdad entre palabras, sin
    meterse nunca dentro del dato.

    La estimacion por anchura de caracter se desvia (una etiqueta en negrita es
    mas ancha que la media: la barra empezaba en la etiqueta y dejaba el final
    del DNI a la vista).  En la imagen se ven los huecos entre palabras, pero el
    dato tambien tiene huecos dentro ("+34 622 487 105"): acercarse al mas
    proximo podia dejar su primer grupo a la vista.  Por eso el comienzo solo
    retrocede (al ultimo hueco que no pasa de la estimacion, con medio caracter
    de tolerancia) y el final solo avanza (al primer hueco que no queda antes);
    si el dato acaba la pieza, hasta el final de la tinta.  Si la estimacion se
    desvia, la barra se come algo de la etiqueta: nunca deja parte del dato.
    No se empareja por numero de palabras: el texto del OCR no separa igual que
    la imagen ("DIRECCION:Avenida", "andres. peiro").
    """
    gris = _TINTA.get()
    if gris is None:
        return estimado
    bloques = _bloques_de_tinta(gris, word.rect)
    if not bloques:
        return estimado
    huecos = [(bloques[i][1], bloques[i + 1][0]) for i in range(len(bloques) - 1)]
    if not huecos:
        # Sin huecos que ver (letras pegadas, ruido): la tinta no informa y
        # estirar la barra hasta el principio de la linea tapaba frases enteras.
        return estimado
    tolerancia = 0.5 * (word.rect.x1 - word.rect.x0) / max(1, len(word.text))
    x0, x1 = estimado.x0, estimado.x1
    antes = [h for h in huecos if (h[0] + h[1]) / 2 <= x0 + tolerancia]
    x0 = antes[-1][1] if c0 > 0 and antes else min(x0, bloques[0][0])
    despues = [h for h in huecos if (h[0] + h[1]) / 2 >= x1 - tolerancia]
    if c1 >= len(word.text.rstrip()) or not despues:
        x1 = max(x1, bloques[-1][1])
    else:
        x1 = despues[0][0]
    if x1 <= x0:
        return estimado
    return _padded(fitz.Rect(x0, word.rect.y0, x1, word.rect.y1))


def _tramo(word, matches: list[tuple[int, int]]) -> fitz.Rect | None:
    """Sub-rect de *word* entre el primer y el ultimo caracter casado, por
    anchura de caracter y con un caracter medio de margen a cada lado, ajustado
    despues a la tinta de la imagen si esta disponible."""
    c0 = min(s for s, _ in matches)
    c1 = max(e for _, e in matches)
    n = len(word.text)
    if n <= 0:
        return None
    pesos = [_anchura_relativa(c) for c in word.text]
    total = sum(pesos)
    width = word.rect.x1 - word.rect.x0
    x0 = word.rect.x0 + width * (sum(pesos[:c0]) / total)
    x1 = word.rect.x0 + width * (sum(pesos[:c1]) / total)
    margen = width / n
    estimado = _padded(fitz.Rect(max(word.rect.x0, x0 - margen), word.rect.y0,
                                 min(word.rect.x1, x1 + margen), word.rect.y1))
    return _ajustar_a_tinta(estimado, word, c0, c1)


def _subrect_tramo(ent_tokens: set[str], word) -> fitz.Rect | None:
    """Tramo del dato dentro de una pieza donde es MAYORIA (con etiqueta delante
    o detras).  Exige que case todo el dato: si falta alguna palabra, la barra
    cubre la pieza entera, que es lo prudente."""
    # La ULTIMA aparicion de cada palabra del dato: el valor va detras de su
    # etiqueta, y la etiqueta puede repetir una palabra del valor ("CORREO
    # ELECTRONICO: lucia.ferrando@correo.es").
    ultima: dict[str, tuple[int, int]] = {}
    for m in _WORD_TOK_RE.finditer(word.text):
        canon = _canon_tok(m.group())
        hit = next((td for td in ent_tokens if canon and _casan(td, canon)), None)
        if hit is not None:
            ultima[hit] = (m.start(), m.end())
    if set(ultima) != ent_tokens:
        return None
    return _tramo_por_palabras(ent_tokens, word) or _tramo(word, list(ultima.values()))


def _subrect_for_line_token(ent_tokens: set[str], word) -> fitz.Rect | None:
    """Sub-rect of *word* spanning the entity's tokens when they are a minority
    of a long EasyOCR line token ("con IBAN ES13 … 6633 y SWIFT …",
    "con DNI número 47019611K.").

    The matched words' char offsets within the token are interpolated over the
    token rect, giving a tight box for the entity inside the line.  A multi-token
    entity needs at least half its tokens matched; a single-token entity is only
    anchored when that token is identifier-like (so an ID such as a DNI is
    covered, but a lone common word is not).
    """
    # La busqueda dentro del token admite las mismas erratas que el emparejado
    # de fuera.  Sin esto, un NIE leido "24422899G" en vez de "Z4422899G" se
    # reconocia al elegir el candidato y luego no se le sabia construir el
    # rect, asi que su segunda aparicion se quedaba sin tapar.
    matches = []
    for m in _WORD_TOK_RE.finditer(word.text):
        canon = _canon_tok(m.group())
        if canon and any(_casan(td, canon) for td in ent_tokens):
            matches.append((m.start(), m.end()))

    if len(ent_tokens) == 1:
        if not _is_id_like(next(iter(ent_tokens))):
            return None
        need = 1
    else:
        need = max(2, (len(ent_tokens) + 1) // 2)
    # Un dato partido entre dos piezas de linea ("...con DNI 53.810." y
    # ".247-E y domicilio"): en la pieza donde solo esta un trozo, ese trozo va
    # pegado a un borde.  Se acepta aunque no llegue al minimo, y la barra llega
    # hasta ese borde; si no, la segunda mitad del dato se quedaba sin tapar.
    alnum = [i for i, c in enumerate(word.text) if c.isalnum()]
    al_principio = bool(matches) and bool(alnum) and matches[0][0] == alnum[0]
    al_final = bool(matches) and bool(alnum) and matches[-1][1] == alnum[-1] + 1
    trozo_en_borde = (al_principio or al_final) and any(
        len(word.text[a:b]) >= 3 for a, b in matches)
    if len(matches) < need and not trozo_en_borde:
        return None
    if len(matches) < need:
        c0 = 0 if al_principio else min(a for a, _ in matches)
        c1 = len(word.text) if al_final else max(b for _, b in matches)
        if al_principio:
            # Lo que sigue pegado al trozo (la letra de un DNI: ".247-E") es suyo.
            while c1 < len(word.text) and not word.text[c1].isspace():
                c1 += 1
        return _tramo(word, [(c0, c1)])
    exacto = _tramo_por_palabras(ent_tokens, word)
    if exacto is not None:
        return exacto
    c0 = min(s for s, _ in matches)
    c1 = max(e for _, e in matches)
    n = len(word.text)
    if n <= 0:
        return None
    # Posicion por ANCHURA de caracter, no a partes iguales: mayusculas y cifras
    # son mas anchas que la media, y con el reparto uniforme la barra empezaba
    # tarde (oficio escaneado real: nombre de pila y dos cifras del NIF a la
    # vista).  Y un caracter medio de margen a cada lado: es una estimacion.
    pesos = [_anchura_relativa(c) for c in word.text]
    total = sum(pesos)
    width = word.rect.x1 - word.rect.x0
    x0 = word.rect.x0 + width * (sum(pesos[:c0]) / total)
    x1 = word.rect.x0 + width * (sum(pesos[:c1]) / total)
    margen = width / n
    estimado = _padded(fitz.Rect(max(word.rect.x0, x0 - margen), word.rect.y0,
                                 min(word.rect.x1, x1 + margen), word.rect.y1))
    return _ajustar_a_tinta(estimado, word, c0, c1)


def _anchura_relativa(c: str) -> float:
    """Anchura aproximada de un caracter en una fuente proporcional."""
    if c in "iljtfrI.,:;'|!" or c == "·":
        return 0.45
    if c == " ":
        return 0.5
    if c in "mwMW":
        return 1.35
    if c.isupper() or c.isdigit():
        return 1.15
    return 0.95


def _region_for_entity(entity: PiiEntity, zone: PageZone) -> fitz.Rect:
    """Bounding rect of the layout tokens overlapping the entity span."""
    region: fitz.Rect | None = None
    for token in zone.tokens:
        if token.text_offset is None:
            continue
        t_start = token.text_offset
        t_end = t_start + len(token.text)
        if t_end <= entity.start or t_start >= entity.end:
            continue
        region = fitz.Rect(token.rect) if region is None else (region | token.rect)
    return region if region is not None else fitz.Rect(zone.rect)


def _median_height(words: list) -> float:
    heights = sorted(w.rect.y1 - w.rect.y0 for w in words if w.rect.height > 0)
    return heights[len(heights) // 2] if heights else 0.0


class _CajaSuelta:
    """Envuelve un rect suelto para que se agrupe igual que una palabra.

    `_cluster_by_line` trabaja sobre objetos con `.rect`; los sub-rects que salen
    de un token de linea larga no lo eran, y por eso quedaban fuera del agrupado.
    """

    __slots__ = ("rect", "text")

    def __init__(self, rect: fitz.Rect):
        self.rect = rect
        self.text = ""


def _cluster_by_line(candidates: list, line_h: float) -> list[list]:
    """Group (word, shared_tokens) candidates into visual lines by y-proximity."""
    gap = max(8.0, line_h * 0.7) if line_h > 0 else 10.0
    items = sorted(candidates, key=lambda c: (c[0].rect.y0 + c[0].rect.y1) / 2)
    clusters: list[list] = []
    current: list = []
    last_cy: float | None = None
    for word, shared in items:
        cy = (word.rect.y0 + word.rect.y1) / 2
        if last_cy is None or abs(cy - last_cy) <= gap:
            current.append((word, shared))
        else:
            clusters.append(current)
            current = [(word, shared)]
        last_cy = cy
    if current:
        clusters.append(current)
    return clusters


#: Cuantas veces cae el emparejamiento en cada caso.  Si "cobertura_parcial" o
#: "sin_emparejar" crecen, el emparejamiento no esta funcionando y hay que verlo
#: en un numero, no descubrirlo dentro de tres semanas mirando un documento.
CASOS_EMPAREJADO: dict[str, int] = {}


#: Fraccion de los tokens que una linea debe cubrir para tomarla por OTRA
#: aparicion del mismo dato.  Solo se aplica cuando ya hay una aparicion
#: COMPLETA en la pagina: sin esa ancla, una cobertura parcial puede ser
#: simplemente que el OCR leyo mal un trozo, y entonces lo seguro es tapar la
#: region entera en vez de pintar solo lo emparejado y dejar el resto a la
#: vista.
#:
#: Por que 2/3 y no cobertura total: el OCR parte y degrada.  "Elena Ramos
#: Munoz" aparecia dos veces en la pagina y la del parrafo llegaba partida entre
#: dos cajas, cubriendo 2 de 3 tokens; exigir el total la descartaba y el nombre
#: se entregaba legible.  Y por que no menos: con 1 de 3 bastaria una mencion
#: suelta del apellido en otro parrafo para tacharlo, que es sobre-redaccion.
#:
#: Un dato de un solo token —un DNI, un IBAN— exige ese token, asi que se pinta
#: dondequiera que aparezca, que es justo lo que se busca.
FRACCION_APARICION = 2 / 3


def _cubre_bastante(cubiertos: set, del_dato: set) -> bool:
    if not del_dato:
        return False
    return len(cubiertos & del_dato) / len(del_dato) >= FRACCION_APARICION


def _contar(caso: str) -> None:
    CASOS_EMPAREJADO[caso] = CASOS_EMPAREJADO.get(caso, 0) + 1


def reiniciar_casos_emparejado() -> None:
    CASOS_EMPAREJADO.clear()


def _entity_rects_via_easy(
    entity: PiiEntity,
    zone: PageZone,
    easy_words: list,
    line_h: float = 0.0,
) -> list[fitz.Rect]:
    """Map an entity (detected in clean layout text) to word-level OCR boxes.

    Matching is by content across the page — NOT bounded to the layout token's
    rect, which for MinerU table rows is badly misaligned with the real text.
    Word-level matches are grouped into visual lines; an entity buried inside a
    single long EasyOCR line token contributes a proportional sub-rect.  The
    groups that add new entity-token coverage are kept (handles wrapped
    entities while ignoring same-token collisions on unrelated lines).  When
    nothing matches, only a SMALL layout region is used as fallback; a large
    multi-line block is left untouched (the paragraph over-redaction bug).
    """
    region = _region_for_entity(entity, zone)
    ent_tokens = _content_tokens(zone.text[entity.start:entity.end])
    if not ent_tokens:
        return [_padded(region)] if _small(region, line_h) else []

    whole: list = []           # (word, shared) — word is mostly the entity
    line_groups: list = []     # (sub_rect, shared) — entity inside a line token
    for word in easy_words:
        w_tokens = _content_tokens(word.text)
        if not w_tokens:
            continue
        shared = _compartidos(ent_tokens, w_tokens)
        if not shared:
            continue
        if len(shared) / len(w_tokens) > 0.5:
            # El dato es mayoria en la pieza, pero si la pieza lleva ademas
            # otras palabras (la etiqueta de un formulario: "NOMBRE COMPLETO:
            # Alejandro Martin Gomez"), la barra se recorta a su tramo.  Sin
            # esto se tachaba la linea entera, etiqueta incluida.
            sub = _subrect_tramo(ent_tokens, word) if w_tokens - shared else None
            if sub is not None:
                line_groups.append((sub, shared))
            else:
                whole.append((word, shared))
        else:
            # Word shares tokens but is mostly other text (a long line token):
            # take a proportional sub-rect of the entity's span within it.
            sub = _subrect_for_line_token(ent_tokens, word)
            if sub is not None:
                line_groups.append((sub, shared))

    # Los candidatos de los dos tipos se agrupan JUNTOS por linea visual.
    #
    # Si los sub-rects fueran sueltos, una aparicion repartida entre una palabra
    # propia y un token de linea larga quedaria partida en dos grupos
    # incompletos y perderia frente a otra aparicion entera en un solo token:
    # de un dato que sale DOS veces en la pagina, se taparia una y se
    # entregaria la otra.
    _Falsa = _CajaSuelta
    candidatos = list(whole) + [(_Falsa(sub), shared) for sub, shared in line_groups]

    groups: list = []
    for cluster in _cluster_by_line(candidatos, line_h):
        cov: set = set()
        for _, shared in cluster:
            cov |= shared
        cx = sum((w.rect.x0 + w.rect.x1) / 2 for w, _ in cluster) / len(cluster)
        cy = sum((w.rect.y0 + w.rect.y1) / 2 for w, _ in cluster) / len(cluster)
        groups.append(([_padded(fitz.Rect(w.rect)) for w, _ in cluster], cov, cx, cy))

    if not groups:
        # Nothing matched: fall back only for a small region; never paint a big
        # multi-line block (almost always an NER false positive on a paragraph).
        return [_padded(region)] if _small(region, line_h) else []

    # TODAS las apariciones completas, no solo una: un nombre que aparece en la
    # fila del formulario Y en el parrafo de abajo se tapa las dos veces.  Parar
    # al cubrir los tokens del dato una vez entregaria la otra aparicion a la
    # vista, y la evaluacion por entidades no lo veria, porque no mira el PDF
    # de salida.
    completos = [g for g in groups if g[1] >= ent_tokens]
    if completos:
        # Hay al menos un sitio donde el dato esta entero: se sabe que el
        # emparejamiento funciona en esta pagina.  Entonces las demas lineas con
        # solapamiento sustancial son OTRAS APARICIONES del mismo dato, aunque
        # el OCR las haya partido o degradado, y tambien hay que taparlas.
        otras = [g for g in groups
                 if g not in completos and _cubre_bastante(g[1], ent_tokens)]
        _contar("todas_las_apariciones")
        rects: list[fitz.Rect] = []
        for group_rects, _, _, _ in completos + otras:
            rects.extend(group_rects)
        return rects

    # Ningun grupo cubre el dato entero: se acumula lo que haya, ordenado por
    # cobertura y cercania a donde el motor de layout creia que estaba.
    rcx = (region.x0 + region.x1) / 2
    rcy = (region.y0 + region.y1) / 2
    groups.sort(key=lambda g: (-len(g[1]), abs(g[3] - rcy) + abs(g[2] - rcx)))

    rects = []
    covered: set = set()
    for group_rects, cov, _, _ in groups:
        if cov - covered:
            rects.extend(group_rects)
            covered |= cov

    if _small(region, line_h):
        # Region pequena: se tapa entera, que no deja fuga.
        _contar("region_completa")
        return [_padded(region)]

    if rects:
        _contar("cobertura_parcial")
        return rects

    # Nada que pintar y la region es un bloque grande: pintarla entera seria
    # tachar parrafos enteros por un falso positivo del NER.  Se declara.
    _contar("sin_emparejar")
    logger.warning(
        "No se pudo situar una entidad %s en la pagina: no hay ninguna palabra "
        "del OCR que la contenga y la region del motor de layout es un bloque "
        "de %.0f px. La pagina necesita revision manual.",
        entity.entity_type, region.height,
    )
    return []


def _small(region: fitz.Rect, line_h: float) -> bool:
    """True when a region is at most ~2 text lines tall (safe to fully redact)."""
    if line_h <= 0:
        return region.height <= 60.0
    return region.height <= line_h * 2.5


def _entity_rects_from_zone(
    entity: PiiEntity,
    zone: PageZone,
) -> list[fitz.Rect]:
    """Map a PII entity span (in zone.text) to pixel-space rects via ZoneTokens.

    For MinerU form-row tokens that carry a value_start offset: when the entity
    falls in the value portion (local_start >= value_start), we map the entity to
    the full token rect instead of using character-proportion interpolation.
    This corrects the systematic rightward drift caused by long label prefixes
    in "Label: Value" row tokens.
    """
    result: list[fitz.Rect] = []

    for token in zone.tokens:
        if token.text_offset is None:
            continue

        t_start = token.text_offset
        t_end = t_start + len(token.text)

        if t_end <= entity.start or t_start >= entity.end:
            continue

        local_start = max(entity.start, t_start) - t_start
        local_end = min(entity.end, t_end) - t_start

        # Form-row remapping: entity in value portion → map to full token rect.
        # Heuristic: token.value_start marks the boundary between label chars and
        # value chars. Characters < value_start are the label (outside token rect);
        # characters ≥ value_start are the value (fill the full token rect).
        # LEARNABLE: value_start is estimated from the row's HTML structure; exact
        # cell-level bboxes from the MinerU API would make this unnecessary.
        if (
            token.value_start is not None
            and local_start >= token.value_start
        ):
            value_len = len(token.text) - token.value_start
            if value_len > 0:
                vs = local_start - token.value_start
                ve = local_end - token.value_start
                rect = _rect_for_value_range(token.rect, vs, ve, value_len)
                if rect is not None:
                    result.append(rect)
            continue

        rect = _rect_for_token_range(token, local_start, local_end)
        if rect is not None:
            result.append(rect)

    return result


def _rect_for_value_range(
    token_rect: fitz.Rect,
    vs: int,
    ve: int,
    value_len: int,
) -> fitz.Rect | None:
    """Map a value-portion entity to pixel coords within the token rect.

    The token rect covers only the value column; vs/ve are offsets within
    the value text (0 = start of value, value_len = end of value).
    """
    vs = max(0, min(vs, value_len))
    ve = max(0, min(ve, value_len))
    if vs >= ve:
        return None
    rect = fitz.Rect(token_rect)
    if vs == 0 and ve == value_len:
        return _padded(rect)
    width = rect.x1 - rect.x0
    x0 = rect.x0 + width * (vs / value_len)
    x1 = rect.x0 + width * (ve / value_len)
    return _padded(fitz.Rect(x0, rect.y0, x1, rect.y1))


def _rect_for_token_range(
    token: ZoneToken,
    local_start: int,
    local_end: int,
) -> fitz.Rect | None:
    text = token.text
    if not text:
        return None

    token_len = len(text)
    local_start = max(0, min(local_start, token_len))
    local_end = max(0, min(local_end, token_len))

    if local_start >= local_end:
        return None

    rect = fitz.Rect(token.rect)

    if local_start == 0 and local_end == token_len:
        return _padded(rect)

    width = rect.x1 - rect.x0
    x0 = rect.x0 + width * (local_start / token_len)
    x1 = rect.x0 + width * (local_end / token_len)

    return _padded(fitz.Rect(x0, rect.y0, x1, rect.y1))


def _padded(rect: fitz.Rect, px: float = 2.0, py: float = 2.0) -> fitz.Rect:
    return fitz.Rect(rect.x0 - px, rect.y0 - py, rect.x1 + px, rect.y1 + py)


def _released_form_entities(zone, analyzer, mode, page_context, easy_words, easy_line_h):
    """Entidades activas de una zona FORM cuyo rect estructural se ha liberado.

    Devuelve pares (entidad, rects).  La zona FORM del OCR no tiene texto propio:
    se reconstruye desde sus tokens.
    """
    from app.services.layout.models import PageZone, ZoneType as _ZT
    from app.services.layout.segmenters import _build_ocr_zone_text_and_tokens
    from app.services.ocr.models import OcrToken

    if zone.text.strip():
        work = zone
    else:
        ocr_tokens = [
            OcrToken(text=t.text, confidence=t.confidence or 1.0, rect=t.rect)
            for t in zone.tokens if t.text.strip()
        ]
        if not ocr_tokens:
            return []
        text, tokens = _build_ocr_zone_text_and_tokens(ocr_tokens)
        work = PageZone(zone_type=_ZT.BODY, rect=zone.rect, text=text, tokens=tokens, source="ocr")

    cfg = get_detector_config(zone.zone_type)
    if cfg.entity_types is None or not work.text.strip():
        return []
    allowed = set(cfg.entity_types)
    out = []
    for entity in analyzer.analyze(work.text, mode=mode, zone_type=zone.zone_type.value,
                                   context_text=page_context):
        if entity.entity_type not in allowed:
            continue
        if easy_words is not None:
            entity_rects = _entity_rects_via_easy(entity, work, easy_words, easy_line_h)
        else:
            entity_rects = _entity_rects_from_zone(entity, work)
        out.append((PiiEntity(
            entity_type=entity.entity_type, start=entity.start, end=entity.end,
            score=entity.score, source=entity.source,
            text=work.text[entity.start:entity.end],
        ), entity_rects))
    return out


def _mostly_same(a: fitz.Rect, b: fitz.Rect) -> bool:
    """Dos rects que señalan el mismo objeto (solapan >= 50 % del menor)."""
    inter = fitz.Rect(a) & b
    if inter.is_empty:
        return False
    smaller = min(a.get_area(), b.get_area()) or 1.0
    return inter.get_area() >= 0.5 * smaller


def _center_in_any(rect: fitz.Rect, containers: list[fitz.Rect]) -> bool:
    cx = (rect.x0 + rect.x1) / 2
    cy = (rect.y0 + rect.y1) / 2
    for c in containers:
        if c.x0 - 3 <= cx <= c.x1 + 3 and c.y0 - 3 <= cy <= c.y1 + 3:
            return True
    return False


# ---------------------------------------------------------------------------
# Shared PDF/image utilities (also used by hybrid_redactor)
# ---------------------------------------------------------------------------

def _zoom_con_tope(page: fitz.Page, dpi: int, tope_mp: float) -> float:
    """Zoom de render que no pasa del tope de pixeles.

    Una pagina declarada a un punto por pixel —lo que escriben muchos
    conversores de imagen a PDF— dice medir 58 x 82 cm, y a 200 DPI daria 30 MP.
    Reducir es barato y no cuesta calidad de lectura (ver ocr_max_megapixels);
    saltarse la pagina si cuesta: saldria sin mirar.
    """
    zoom = dpi / 72
    if tope_mp <= 0:
        return zoom
    megapixeles = (page.rect.width * zoom) * (page.rect.height * zoom) / 1e6
    if megapixeles <= tope_mp:
        return zoom
    return zoom * (tope_mp / megapixeles) ** 0.5


#: Holgura al decidir si una palabra de EasyOCR cae dentro de un bloque leido
#: por MinerU: sus recuadros son de bloque y las palabras del borde pueden
#: asomar unos pixeles.
_HOLGURA_BLOQUE_PX = 6

#: Palabras de EasyOCR que han entrado por la zona de lo no leido, en total.
#: Si crece mucho, MinerU esta dejando de leer mas de lo que creiamos.
NO_LEIDO_POR_MINERU: dict[str, int] = {"paginas": 0, "palabras": 0}


def _contar_no_leido(palabras: int) -> None:
    NO_LEIDO_POR_MINERU["paginas"] += 1
    NO_LEIDO_POR_MINERU["palabras"] += palabras


def _zona_de_lo_no_leido(tokens_mineru: list, tokens_easy: list) -> "PageZone | None":
    """Zona con las palabras de EasyOCR que caen fuera de todo bloque leido por MinerU.

    Por que hace falta
    ------------------
    El texto que se analiza en una pagina escaneada es el de MinerU; EasyOCR
    coloca las barras.  Lo que MinerU no lee, no se analiza: ni se detecta, ni
    se tacha, ni llega a la clasificacion de documento de identidad.  Y deja
    cosas sin leer por dos vias:

    1. A proposito.  No se le pide el contenido de los bloques de imagen y
       grafico (ocr_mineru_skip_block_types), que es carisimo.  El repliegue al
       OCR de palabras solo actua si la pagina ENTERA sale vacia, asi que una
       copia de DNI pegada en una pagina con texto no la leeria nadie.
    2. Sin decirlo.  Hay texto corriente, fuera de cualquier figura, que MinerU
       tampoco devuelve.

    Por eso se complementa con TODO lo que MinerU no leyo, no solo con sus
    figuras: recupera muchos mas datos, y lo que anade tiene una precision
    parecida a la del propio camino escaneado (el ruido de bordes de tabla y
    texturas de sello es poco).

    El VLM sigue sin leer figuras, que es lo caro.  Las lee EasyOCR, que ya ha
    leido la pagina entera para colocar las barras.
    """
    if not tokens_easy:
        return None
    from app.services.layout.segmenters import _build_ocr_zone_text_and_tokens

    # Un bloque CORTADO (MinerU cayo en un bucle a mitad, ver
    # mineru_engine.recortar_bucle) no cubre lo que no llego a leer: sus
    # palabras de EasyOCR no se dan por leidas por estar dentro de su caja, solo
    # por contenido: si no, lo que viene despues del bucle (un IBAN, un correo)
    # se perderia.
    leidos = [
        fitz.Rect(t.rect.x0 - _HOLGURA_BLOQUE_PX, t.rect.y0 - _HOLGURA_BLOQUE_PX,
                  t.rect.x1 + _HOLGURA_BLOQUE_PX, t.rect.y1 + _HOLGURA_BLOQUE_PX)
        for t in tokens_mineru
        if not getattr(t, "cortado", False)
    ]
    # Lo que MinerU SI leyo, por su contenido.  Una palabra cuya caja asoma
    # fuera de un bloque pero cuyo texto ya esta aqui no es texto sin leer: es
    # la misma palabra vista por otro motor.  Dejarla entrar detectaria el dato
    # dos veces, con falsos positivos.  Y quitarla es seguro: la geometria tapa todas las apariciones de un dato,
    # asi que si se detecta en el texto de MinerU, la barra cae tambien donde
    # asome.
    leido_por_contenido: set[str] = set()
    for t in tokens_mineru:
        leido_por_contenido |= _content_tokens(t.text or "")

    fuera = []
    for palabra in tokens_easy:
        centro = fitz.Point((palabra.rect.x0 + palabra.rect.x1) / 2,
                            (palabra.rect.y0 + palabra.rect.y1) / 2)
        if any(r.contains(centro) for r in leidos):
            continue
        propios = _content_tokens(palabra.text or "")
        if propios and _compartidos(propios, leido_por_contenido) >= propios:
            continue
        fuera.append(palabra)
    if not fuera:
        return None

    texto, tokens = _build_ocr_zone_text_and_tokens(fuera)
    if not texto.strip():
        return None
    marco = fitz.Rect(fuera[0].rect)
    for palabra in fuera[1:]:
        marco |= palabra.rect
    return PageZone(
        zone_type=ZoneType.BODY,
        rect=marco,
        text=texto,
        tokens=tokens,
        source="ocr",
    )


# ---------------------------------------------------------------------------
# Red de seguridad: lo que la segunda pasada todavia lee, no se entrega
# ---------------------------------------------------------------------------
#
# La segunda pasada vuelve a leer la pagina ya tachada con EasyOCR.  Si lo que
# encuentra solo se contara como "residuo" y se pidiera revision, el sistema
# SABRIA que un dato sigue a la vista y lo entregaria igual.  Como con las
# paginas ilegibles y los tiempos agotados, la respuesta es tachar, no solo
# avisar.  Pero de forma proporcional:
#
#   - Dentro de una IMAGEN PEGADA en una pagina con texto (una copia de DNI en
#     un expediente) se tacha la imagen entera: no tiene nada que conservar.
#   - En cualquier otro sitio se repasa solo la zona del residuo.  Tachar la
#     pagina seria sobre-redaccion.
#
# Dos cuidados:
#
#   - En una pagina ESCANEADA la "imagen" es la pagina entera.  Si la regla de
#     imagen se le aplicara, un solo residuo tacharia la pagina completa.  Por
#     eso solo cuentan las imagenes de paginas que tienen capa de texto.
#   - La segunda pasada usa el mismo analizador que la primera, con sus falsos
#     positivos (en el camino escaneado, sobre todo en direccion, codigo postal
#     y telefono).  Tachar una imagen entera exige un residuo de tipo
#     FIABLE o dos residuos en la misma imagen: un codigo postal suelto no basta
#     para borrar la foto de un anexo.
#
# Limite: solo actua sobre lo que la segunda pasada VE.  Cubre "lo detecte y
# lo pinte en el sitio equivocado"; no cubre "nunca lo detecte", porque la
# segunda pasada no ve mas que lo que lee EasyOCR.  Es la ultima defensa, no la
# solucion.

#: Tipos que bastan solos para tachar una imagen pegada entera.  Se validan con
#: digito de control (DNI, NIE, IBAN, CIF) o son el dato que mas importa (PERSON).
TIPOS_FIABLES_RED = frozenset({"ES_DNI", "ES_NIE", "IBAN_CODE", "ES_CIF", "PERSON"})

# Lo que, si SIGUE legible tras la red, pide revision manual.  Sin PERSON: la
# segunda pasada da muchos falsos positivos de nombre, y el aviso saltaria casi
# siempre sin motivo.  Un aviso que casi nunca acierta acaba ignorado.  Los demas residuos
# se tapan igual y se siguen contando en el informe; solo no piden revision.
TIPOS_AVISO_RED = frozenset({"ES_DNI", "ES_NIE", "IBAN_CODE", "ES_CIF"})


def aviso_de_residuos(residuos: list[PiiEntity]) -> tuple[str | None, bool]:
    """(aviso, pide revision manual) segun lo que sigue legible tras la red."""
    fiables = sorted({e.entity_type for e in residuos if e.entity_type in TIPOS_AVISO_RED})
    if not fiables:
        return None, False
    return (
        f"Residual {', '.join(fiables)} STILL readable after the safety net "
        "repainted it. Manual review required.",
        True,
    )

def avisos_de_red(
    red: dict[str, int],
    paginas_red: list[int],
    paginas_persisten: list[int],
    residuos: list[PiiEntity],
) -> tuple[list[str], list[CodedWarning], bool]:
    """(frases, avisos con codigo, pide revision manual) de lo que hizo la red.

    Las paginas entran desde 0 y salen desde 1, como en el resto de avisos."""
    frases: list[str] = []
    codigos: list[CodedWarning] = []
    if red["imagenes_tachadas"] or red["zonas_repasadas"]:
        # La red ha actuado: se dice, porque significa que la primera pasada
        # dejo algo a la vista y quien revise el documento tiene que saberlo.
        frases.append(
            f"Safety net: the post-redaction check still read personal data on "
            f"{red['paginas']} page(s) and covered it "
            f"({red['imagenes_tachadas']} pasted image(s) redacted in full, "
            f"{red['zonas_repasadas']} zone(s) repainted)."
        )
        codigos.append(CodedWarning(
            code=SAFETY_NET_ACTED, pages=sorted(i + 1 for i in set(paginas_red)),
            counts={"images": red["imagenes_tachadas"], "zones": red["zonas_repasadas"]},
        ))
    aviso, revisar = aviso_de_residuos(residuos)
    if aviso:
        frases.append(aviso)
        codigos.append(CodedWarning(
            code=SAFETY_NET_STILL_READABLE,
            pages=sorted(i + 1 for i in set(paginas_persisten)),
            types=sorted({e.entity_type for e in residuos if e.entity_type in TIPOS_AVISO_RED}),
        ))
    return frases, codigos, revisar


#: Recuento de lo que hace la red, para ver en un numero si actua y como.
RED_SEGURIDAD: dict[str, int] = {
    "imagenes_tachadas": 0, "zonas_repasadas": 0, "persisten": 0, "paginas": 0,
}
#: Detalle por residuo (tipo, si cae en imagen pegada y que se hizo), sin
#: valores: es lo que permite medir si la regla de tipos fiables es la buena.
RED_DETALLE: list[dict] = []


def reiniciar_red() -> None:
    for clave in RED_SEGURIDAD:
        RED_SEGURIDAD[clave] = 0
    RED_DETALLE.clear()


def _residuos_situados(zoned_page, analyzer: PiiAnalyzer, mode: str) -> list[tuple]:
    """Residuos de la segunda pasada CON su posicion: (entidad, [rects]).

    `_collect_redaction_rects` devuelve las barras y las entidades por separado;
    para decidir si un residuo cae dentro de una imagen pegada hace falta saber
    que barra es de que entidad.
    """
    plan = RedactionPlan(disabled_types=frozenset(analyzer.disabled_types))
    situados = []
    for zone in zoned_page.zones:
        if zone.is_auto_redact:
            continue
        cfg = get_detector_config(zone.zone_type)
        if not zone.text.strip() or cfg.entity_types is None:
            continue
        permitidos = set(cfg.entity_types)
        for entidad in analyzer.analyze(zone.text, mode=mode,
                                        zone_type=zone.zone_type.value):
            if entidad.entity_type not in permitidos:
                continue
            if not plan.admits(entidad.entity_type):
                continue
            rects = _entity_rects_from_zone(entidad, zone)
            if rects:
                situados.append((PiiEntity(
                    entity_type=entidad.entity_type, start=entidad.start,
                    end=entidad.end, score=entidad.score, source="segunda_pasada",
                    text=zone.text[entidad.start:entidad.end],
                ), rects))
    return situados


def _imagenes_pegadas_px(page: fitz.Page, zoom: float) -> list[fitz.Rect]:
    """Recuadros, en pixeles del render, de las imagenes pegadas en una pagina con texto.

    Solo paginas con capa de texto: en una pagina escaneada la unica imagen es
    la pagina, y tacharla por un residuo seria tachar el documento.  Y solo
    imagenes que cubren al menos `ocr_imagen_minima`: un logo no es una copia.
    Tampoco un FONDO (el escudo de un formulario, con los campos escritos
    encima): ver _es_fondo.
    """
    try:
        if len(page.get_text("text").strip()) < settings.min_digital_text_chars:
            return []
        area = page.rect.width * page.rect.height
        if area <= 0:
            return []
        rects = []
        dibujo = None
        for imagen in page.get_images(full=True):
            for r in page.get_image_rects(imagen[0]):
                visible = r & page.rect
                if visible.is_empty or visible.get_area() / area < settings.ocr_imagen_minima:
                    continue
                if dibujo is None:
                    dibujo = (page.get_bboxlog(), page.get_texttrace())
                if _es_fondo(visible, *dibujo):
                    continue
                rects.append(fitz.Rect(visible.x0 * zoom, visible.y0 * zoom,
                                       visible.x1 * zoom, visible.y1 * zoom))
        return rects
    except Exception:
        return []


# Solape maximo (fraccion de la altura del rotulo) para considerar que la barra
# solo MUERDE su borde.  Mas solape: el rotulo y el valor van en la misma caja del
# OCR, y recortar la barra destaparia el valor.
_MORDISCO_MAXIMO = 0.40


def _sin_pisar_rotulos(rects: list[fitz.Rect], tokens: list) -> list[fitz.Rect]:
    """La barra de un valor empieza debajo del rotulo de encima si solo le muerde
    el pie (formulario escaneado real: "COGNOMS", "NOM", "Nº RECIBO" mordidos)."""
    from app.services.pii.analyzer import _parece_etiqueta

    rotulos = [t.rect for t in tokens if t.text and t.text.strip() and _parece_etiqueta(t.text)]
    if not rotulos:
        return rects
    salida = []
    for r in rects:
        r = fitz.Rect(r)
        alto = r.y1 - r.y0
        for w in rotulos:
            if w.x1 <= r.x0 or w.x0 >= r.x1:
                continue                                  # no se tocan en horizontal
            if (w.y0 + w.y1) / 2 >= (r.y0 + r.y1) / 2 or w.y1 <= r.y0:
                continue                                  # no esta encima, o no la toca
            if (w.y1 - r.y0) > _MORDISCO_MAXIMO * (w.y1 - w.y0):
                continue                                  # mas que un mordisco: misma caja
            if r.y1 - (w.y1 + 1) >= 0.5 * alto:
                r.y0 = w.y1 + 1
        salida.append(r)
    return salida


def _aplicar_red(
    imagen: "Image.Image",
    situados: list[tuple],
    imagenes_px: list[fitz.Rect],
) -> tuple[int, int, list]:
    """Tapa lo que la segunda pasada todavia lee.  Devuelve (imagenes, zonas, residuos).

    Los residuos devueltos son los que quedan sin una accion de la red: ninguno,
    salvo que no traigan posicion.
    """
    dibujo = ImageDraw.Draw(imagen)
    tachadas: list[fitz.Rect] = []

    for marco in imagenes_px:
        dentro = [
            (e, rs) for e, rs in situados
            if any(marco.contains(fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
                   for r in rs)
        ]
        if not dentro:
            continue
        # Una firma pegada no se tacha entera (requisito: la rubrica se
        # conserva).  Sus residuos se repasan solo en su zona, mas abajo.
        if parece_firma(imagen, marco):
            continue
        fiable = any(e.entity_type in TIPOS_FIABLES_RED for e, _ in dentro)
        if fiable or len(dentro) >= 2:
            dibujo.rectangle([int(marco.x0), int(marco.y0), int(marco.x1), int(marco.y1)],
                             fill="black")
            tachadas.append(marco)
            for e, _ in dentro:
                RED_DETALLE.append({"tipo": e.entity_type, "en_imagen": True,
                                    "accion": "imagen_tachada"})

    zonas = 0
    for e, rs in situados:
        centro_en_tachada = any(
            m.contains(fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
            for m in tachadas for r in rs
        )
        if centro_en_tachada:
            continue
        en_imagen = any(
            m.contains(fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
            for m in imagenes_px for r in rs
        )
        for r in rs:
            dibujo.rectangle([int(r.x0) - 2, int(r.y0) - 2, int(r.x1) + 2, int(r.y1) + 2],
                             fill="black")
        zonas += 1
        RED_DETALLE.append({"tipo": e.entity_type, "en_imagen": en_imagen,
                            "accion": "zona_repasada"})

    return len(tachadas), zonas, []


def _propagar_redaccion_total(
    output_doc: "fitz.Document",
    textos_por_pagina: dict[int, str],
    ya_redactadas: dict[int, str],
) -> dict[int, str]:
    """Clasifica el documento ENTERO y tacha las paginas que arrastre la propagacion.

    El camino escaneado decide pagina a pagina, y cuando decide aun no conoce a
    las vecinas: no puede propagar.  Asi, el segundo ejemplar de una
    autoliquidacion —el que repite el justificante pero no la cabecera— se
    entregaba con redaccion selectiva mientras el primero salia entero en negro.

    La clasificacion se hace aqui una sola vez, sobre el texto de todas las
    paginas, igual que en el camino digital.  Y la propagacion ya esta apretada:
    hace falta compartir justificante o referencia, nunca el numero de modelo.

    El tachado es una redaccion de verdad —se eliminan los pixeles— y no un
    rectangulo negro encima, que dejaria la imagen original dentro del PDF.

    Devuelve las paginas que ha anadido, para el informe.
    """
    if not textos_por_pagina:
        return {}

    paginas = max(textos_por_pagina) + 1
    textos = [textos_por_pagina.get(i, "") for i in range(paginas)]
    clasificadas = classify_pages(textos)

    anadidas: dict[int, str] = {}
    for indice, motivo in sorted(clasificadas.items()):
        if indice in ya_redactadas or indice >= output_doc.page_count:
            continue
        pagina = output_doc[indice]
        pagina.add_redact_annot(pagina.rect, fill=(0, 0, 0))
        try:
            pagina.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)
        except Exception:
            pagina.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE)
        anadidas[indice] = motivo
        logger.info(
            "Pagina %d tachada entera por propagacion (%s): comparte "
            "justificante con otra pagina del mismo impreso.", indice, motivo,
        )
    return anadidas


def _render_page_to_image(page: fitz.Page, dpi: int) -> Image.Image:
    zoom = _zoom_con_tope(page, dpi, settings.ocr_max_megapixels)
    if zoom < dpi / 72:
        logger.info(
            "Pagina %d rasterizada por debajo de %d DPI para no pasar de %.0f MP: "
            "antes una pagina asi se saltaba entera.",
            (page.number or 0) + 1, dpi, settings.ocr_max_megapixels,
        )
    matrix = fitz.Matrix(zoom, zoom)
    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
    return Image.open(BytesIO(pixmap.tobytes("png"))).convert("RGB")


def _append_image_as_pdf_page(
    output_doc: fitz.Document,
    image: Image.Image,
    source_page_rect: fitz.Rect,
) -> None:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    page = output_doc.new_page(
        width=source_page_rect.width,
        height=source_page_rect.height,
    )
    page.insert_image(page.rect, stream=buffer.getvalue())
