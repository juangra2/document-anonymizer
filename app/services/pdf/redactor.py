"""Digital-PDF redactor — layout-first architecture.

Flow per page
─────────────
1. extract_page_text  → PageText  (word boxes + char offsets)
2. DigitalPageSegmenter.segment → ZonedPage  (HEADER / FOOTER / FORM / BODY)
3. Per zone:
   • FORM  → apply zone.layout_redact_rects directly (structural detection)
   • BODY / HEADER / FOOTER → run PII analyzer on zone.text, translate
     local entity offsets to absolute PageText offsets, get word-level rects
4. Visual QR detection: render page, detect codes, add QR_CODE zones.
5. apply_redactions, scrub metadata, save.
"""
from __future__ import annotations

import re
from collections import defaultdict
from io import BytesIO
from pathlib import Path

import fitz
from PIL import Image

from app.core.config import settings
from app.schemas.report import DetectedEntityInfo, ProcessingLimits, ProcessingReport
from app.services.layout.models import PageZone, ZoneType
from app.services.layout.segmenters import DigitalPageSegmenter
from app.services.layout.zone_config import ALL_PII_TYPES, get_detector_config
from app.services.pdf.extractor import extract_page_text, rects_for_span
from app.services.pdf.inspector import inspect_pdf_for_phase1
from app.services.pdf.full_redaction import classify_pages
from app.services.pdf.plan_pages import is_plan_page, redact_plan_page
from app.services.pii.analyzer import PiiAnalyzer
from app.services import cancellation, progress
from app.services.pdf.partial import append_partial_notice
from app.services.pii.code_policy import code_may_stay_visible
from app.services.pii.label_types import structural_rects_released
from app.services.pdf.redaction_plan import RedactionPlan
from app.services.pii.models import PiiEntity
from app.services.vision.code_detector import detect_redactable_codes, detect_visual_code_rects
from app.services.vision.signature_region import detect_signature_rects

# Render DPI used only for visual QR detection — not full-page OCR.
# 300 DPI gives ~5–6 px/module for small QR codes (contrato_laboral, notificacion)
# which improves decode rate significantly vs 200 DPI (3–4 px/module).
_QR_DETECT_DPI = 300


# Barrido de todas las apariciones (tachar_todas_las_apariciones): longitud
# minima de un valor para buscarlo, y de un trozo de direccion partida.
_MIN_VALOR_BARRIDO = 5
_MIN_TROZO_DIRECCION = 12


def _trozos_de_direccion(valor: str) -> list[str]:
    """El valor y sus trozos con contenido, por si una aparicion va partida en dos lineas.

    "Calle de Cervantes, 96, 5º C, 28013 Madrid." -> tambien "Cervantes, 96, 5º C,
    28013 Madrid." (quitando palabras del principio) y "Calle de Cervantes, 96, 5º
    C," (quitando del final), mientras el trozo conserve al menos el 60 % del valor y
    _MIN_TROZO_DIRECCION caracteres: un trozo corto ("Calle de") no dice nada y
    cazaria cualquier otra direccion.
    """
    palabras = valor.split()
    minimo = max(_MIN_TROZO_DIRECCION, int(len(valor) * 0.6))
    trozos = [valor]
    for i in range(1, len(palabras)):
        for trozo in (" ".join(palabras[i:]), " ".join(palabras[:-i])):
            if len(trozo) >= minimo and trozo not in trozos:
                trozos.append(trozo)
    return trozos


_ETIQUETAS_TRAS_NOMBRE = {"dni", "nif", "nie", "doc", "doc.", "documento", "pasaporte",
                          "passaport", "naf", "nass", "nss", "con", "y", "i", "en"}


def _nombre_sin_cola(valor: str) -> str:
    """Quita del final de un nombre lo que no es nombre: "David Álvarez Suárez DNI".

    El analizador a veces alarga el nombre hasta la etiqueta siguiente (es la
    sobre-redaccion que mide la evaluacion).  Para buscar las demas apariciones
    del nombre hay que buscar el nombre, no "nombre + etiqueta", que no se repite.
    """
    palabras = valor.split()
    while len(palabras) > 2:
        ultima = palabras[-1]
        if (ultima.lower().strip(":.,;/|") in _ETIQUETAS_TRAS_NOMBRE or not ultima[0].isupper()
                or (ultima.isupper() and len(ultima) > 1) or not any(c.isalpha() for c in ultima)):
            palabras.pop()
        else:
            break
    return " ".join(palabras)


# Por debajo de esta longitud un valor solo se busca como palabra entera:
# search_for encuentra subcadenas, y "28013" estaria dentro de "2028013/2024".
_MIN_SUBCADENA = 8


def _buscar(page: fitz.Page, valor: str) -> list[fitz.Rect]:
    """Rects de las apariciones de `valor`.  Un valor corto solo cuenta si es una
    pieza entera de su palabra: "41001" en "postal:41001" si, en "2028013/2024" no
    ("28013" seria solo parte de "2028013")."""
    if len(valor) >= _MIN_SUBCADENA:
        return page.search_for(valor)
    limpio = valor.strip(".,;:()")
    encontrados = page.search_for(limpio)
    palabras = page.get_text("words") if encontrados else []
    rects = []
    for r in encontrados:
        for w in palabras:
            if fitz.Rect(w[:4]).intersects(r):
                piezas = re.split(r"[^0-9A-Za-zÀ-ÿ]+", w[4])
                if limpio in piezas:
                    rects.append(r)
                break
    return rects


def _clave_de_busqueda(texto: str) -> str:
    """Texto sin espacios, saltos ni guiones y en minusculas: si search_for
    encuentra un valor, su clave esta dentro de la clave de la pagina."""
    return re.sub(r"[\s\-\u00ad]+", "", texto.lower())


_ENLACES_DE_NOMBRE = {"de", "del", "la", "las", "los", "y", "i", "da", "dos", "san", "santa"}


def _tiene_forma_de_nombre(valor: str) -> bool:
    """Si un PERSON detectado se puede barrer por todo el documento.

    El analizador toma a veces por persona un nombre de institucion ("Ajuntament
    de Sueca", "Universitat Politècnica de Llevant") o una frase ("La persona").
    Tachado donde se detecto es un falso positivo; barrido, se tachaba tambien en
    el membrete y en todas partes (c3: 163 barras de mas en 72 documentos).  No
    se barre lo que nombra una institucion ni lo que lleva palabras en minuscula
    que no son de enlace.  Donde se detecto se sigue tachando igual.
    """
    from app.services.pii.institution_filter import INSTITUTION_NAME_RE, PUBLIC_INSTITUTION_RE

    if INSTITUTION_NAME_RE.search(valor) or PUBLIC_INSTITUTION_RE.search(valor):
        return False
    palabras = valor.split()
    if palabras[0].lower() in _ENLACES_DE_NOMBRE:
        return False
    return all(p[0].isupper() or p.lower() in _ENLACES_DE_NOMBRE for p in palabras)


def _fusionar_rects(rects: list[fitz.Rect]) -> list[fitz.Rect]:
    """Une los rects contiguos de una misma linea.  Aplicar marcas cuesta por
    marca, y en una pagina con decenas de miles de trazos cada una son ~10 ms."""
    fusionados: list[fitz.Rect] = []
    for r in sorted(rects, key=lambda r: (round(r.y0), r.x0)):
        u = fusionados[-1] if fusionados else None
        if (u is not None and abs(u.y0 - r.y0) <= 0.5 and abs(u.y1 - r.y1) <= 0.5
                and r.x0 <= u.x1 + 1):
            u.include_rect(r)
        else:
            fusionados.append(fitz.Rect(r))
    return fusionados


def tachar_todas_las_apariciones(doc: fitz.Document, entidades: list[PiiEntity],
                                 analyzer: PiiAnalyzer, excluir: set[int] = frozenset()) -> int:
    """Tacha en TODO el documento cada aparicion de un valor ya detectado.

    El dato se detecta en un sitio y vuelve a aparecer en otro donde no se
    detecta: el nombre solo en el bloque de firma (en el pie), la direccion en la
    otra columna partida en dos lineas, el importe de la nomina repetido en la
    pagina 2.  El camino escaneado ya tachaba todas las apariciones; el digital
    solo la detectada, y la lectura de barras de c2 lo encontro.  Solo valores que
    el plan admitio (lo que el filtro institucional aparto no entra) y que no son
    de un tipo desactivado.  Las paginas de `excluir` (planos) no se tocan: su
    tratamiento es el del cajetin.  Devuelve cuantos tachados añadio.
    """
    from app.services.pii.label_types import NON_REDACTABLE_LABEL_TYPES

    valores: set[tuple[str, str]] = set()
    for e in entidades:
        if (e.entity_type in analyzer.disabled_types
                or e.entity_type in NON_REDACTABLE_LABEL_TYPES or not e.text):
            continue
        v = " ".join(e.text.split())
        if e.entity_type == "PERSON":
            v = _nombre_sin_cola(v)
            if len(v.split()) < 2 or not _tiene_forma_de_nombre(v):
                continue          # un nombre suelto cazaria a cualquier otro
        if len(v) >= _MIN_VALOR_BARRIDO and any(c.isalnum() for c in v):
            valores.add((e.entity_type, v))
    if not valores:
        return 0

    añadidos = 0
    for page in doc:
        if page.number in excluir:
            continue
        # Filtro barato antes de search_for (~0,1 s por valor en una pagina de
        # plano): si el valor no esta en el texto de la pagina, ni se busca.
        texto = _clave_de_busqueda(page.get_text())
        rects: list[fitz.Rect] = []
        for tipo, v in valores:
            trozos = _trozos_de_direccion(v) if tipo == "ADDRESS" else [v]
            for trozo in trozos:
                if _clave_de_busqueda(trozo.strip(".,;:()")) in texto:
                    rects.extend(_buscar(page, trozo))
        # Una sola aplicacion por pagina.  Sin tocar los trazos: la barra negra
        # ya tapa lo que haya debajo, y en una pagina con decenas de miles de
        # trazos recortarlos duplica el coste.
        for r in _fusionar_rects(rects):
            page.add_redact_annot(r, fill=(0, 0, 0))
        if rects:
            _apply_redactions_safe(page, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
            añadidos += len(rects)
    return añadidos


# Por debajo de este tamaño (en puntos) se busca texto oculto para quitarlo.
TAMANO_TEXTO_DIMINUTO = 2.0


def _es_blanco(color) -> bool:
    if not color:
        return False
    if len(color) == 4:                                  # CMYK
        return all(c <= 0.05 for c in color)
    return all(c >= 0.95 for c in color)                 # gris o RGB


def _texto_diminuto_oculto(page: fitz.Page) -> list[fitz.Rect]:
    """Rects del texto de menos de TAMANO_TEXTO_DIMINUTO puntos que no se ve: en
    blanco, invisible (o transparente) o tapado por una imagen dibujada despues.
    El numero de orden de cada trozo de texto (seqno) es su posicion en la lista
    de dibujos de la pagina, asi que una imagen posterior que lo cubre lo tapa."""
    trazas = [t for t in page.get_texttrace()
              if t["size"] < TAMANO_TEXTO_DIMINUTO and t["chars"]]
    if not trazas:
        return []
    imagenes = [(i, fitz.Rect(caja)) for i, (tipo, caja) in enumerate(page.get_bboxlog())
                if tipo == "fill-image"]
    rects = []
    for t in trazas:
        caja = fitz.Rect(t["bbox"])
        tapado = any(i > t["seqno"] and abs(caja & img) >= 0.5 * abs(caja)
                     for i, img in imagenes)
        if t["type"] == 3 or t["opacity"] == 0 or _es_blanco(t["color"]) or tapado:
            rects.append(caja)
    return rects


def quitar_texto_diminuto(doc: fitz.Document, excluir: set[int] = frozenset()) -> int:
    """Quita de la salida el texto diminuto que no se ve, sin pintar nada.

    El caso que lo motiva: el contenido del QR en blanco de 1 punto debajo de la
    imagen ("Nombre | DNI"), que sobrevivia al tachado y se podia copiar.
    scrub(hidden_text=True) solo quita el texto marcado como invisible.  El
    texto diminuto que SI se ve se queda: las cotas de un plano se leen con zoom,
    y un A3 no se detecta como plano.  Las paginas de `excluir` (planos) no se
    tocan.  Devuelve cuantos fragmentos se quitaron.
    """
    quitados = 0
    for page in doc:
        if page.number in excluir:
            continue
        rects = _texto_diminuto_oculto(page)
        for r in _fusionar_rects(rects):
            page.add_redact_annot(r, fill=None)
        if rects:
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                                  graphics=fitz.PDF_REDACT_LINE_ART_NONE)
            quitados += len(rects)
    return quitados


def redact_pdf_digital(
    input_path: Path,
    output_path: Path,
    job_id: str,
    analyzer: PiiAnalyzer,
    mode: str = "strict",
    collect_entities: bool = False,
) -> ProcessingReport:
    inspection = inspect_pdf_for_phase1(input_path)
    segmenter = DigitalPageSegmenter()

    doc = fitz.open(input_path)

    try:
        all_entities: list[PiiEntity] = []
        layout_rect_count = 0
        redacted_count = 0
        warnings: list[str] = []

        plan_pages_skipped: list[int] = []
        plan_pages_done: list[int] = []

        # Redacción total (copia de DNI, autoliquidación): se clasifica sobre el
        # texto de TODAS las páginas de una vez, porque la propagación a una
        # página contigua exige comparar sus referencias de impreso.
        full_redaction_pages = classify_pages(
            [page.get_text() for page in doc]
        )

        on_page = progress.claim(doc.page_count)
        pages_total_original = doc.page_count
        pages_done = 0
        partial = False
        stop = cancellation.current()

        try:
            for page_index, page in enumerate(doc):
                on_page(page_index)
                # Parada pedida: el camino digital procesa varias páginas por
                # segundo, así que aquí se corta practicamente al instante.
                # Las páginas completas son las ANTERIORES a esta.
                cancellation.check()
                reason = full_redaction_pages.get(page_index)
                if reason:
                    # La página se pinta entera, pero SÍ se analiza para el informe:
                    # una página en negro que reporta cero entidades es engañosa
                    # para la auditoría, y además haría contar como no detectado lo
                    # que en realidad está redactado al 100 %.  El análisis no
                    # decide aquí qué se tacha — eso ya está decidido.
                    all_entities.extend(
                        _entities_for_audit(analyzer, page.get_text(), mode)
                    )
                    _redact_whole_page(page)
                    redacted_count += 1
                    continue

                # Plan pages (A1/A0 drawings) never enter the normal pipeline: NER
                # on fragment soup yields noise and the 300-DPI QR render alone
                # exceeds 70 MP.  Only the title block (cajetín) carries PII, so it
                # is located and redacted on its own; without one, the page is kept
                # as-is and flagged for manual review.
                if is_plan_page(page):
                    found, plan_entities, plan_rects = redact_plan_page(
                        page, analyzer, mode=mode,
                    )
                    if found:
                        plan_pages_done.append(page_index)
                        all_entities.extend(plan_entities)
                        redacted_count += plan_rects
                    else:
                        plan_pages_skipped.append(page_index)
                    continue

                page_text = extract_page_text(page, page_index)
                zoned_page = segmenter.segment(page_text, page.rect)

                # Add QR_CODE zones from visual detection.
                # DigitalPageSegmenter works on text only and cannot detect visual codes;
                # we render the page at low DPI and scan for QR codes separately.
                _add_visual_qr_zones(page, zoned_page)

                # Add SIGNATURE zones for handwritten signatures (images / ink).
                if settings.detect_visual_signatures:
                    _add_signature_zones(page, zoned_page, page_text)

                page_rects, page_entities, page_layout_count = _collect_redaction_rects(
                    zoned_page=zoned_page,
                    page_text=page_text,
                    analyzer=analyzer,
                    mode=mode,
                    collect_entities=collect_entities,
                )

                all_entities.extend(page_entities)
                layout_rect_count += page_layout_count
                redacted_count += len(page_rects)

                for rect in page_rects:
                    page.add_redact_annot(rect, fill=(0, 0, 0))

                _apply_redactions_safe(page)

            redacted_count += tachar_todas_las_apariciones(
                doc, all_entities, analyzer, excluir=set(plan_pages_done + plan_pages_skipped))

        except cancellation.JobStopped:
            # PARAR: se entrega lo ya procesado.  Las páginas completas son las
            # ANTERIORES a la que estaba en curso, así que se corta ahí.  Las no
            # procesadas NO se incluyen: pintarlas de negro no borraría su texto
            # —seguiría siendo seleccionable debajo— y una sola que se colara
            # saldría con todos sus datos a la vista.
            stop.honor()
            partial = True
            pages_done = page_index
            if pages_done < doc.page_count:
                doc.delete_pages(from_page=pages_done, to_page=doc.page_count - 1)
            append_partial_notice(doc, pages_done, pages_total_original)
            warnings.append(
                f"Partial document: pages 1-{pages_done} of "
                f"{pages_total_original} were processed before the stop request."
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

        if plan_pages_done:
            warnings.append(
                "Construction-plan pages: only the title block was analysed "
                f"and redacted: {sorted(plan_pages_done)}."
            )
        if plan_pages_skipped:
            warnings.append(
                "Construction-plan pages without a detectable title block, "
                f"kept unredacted: {sorted(plan_pages_skipped)}. "
                "Manual review required."
            )

        quitar_texto_diminuto(doc, excluir=set(plan_pages_done + plan_pages_skipped))

        try:
            doc.scrub(
                attached_files=True,
                clean_pages=True,
                embedded_files=True,
                hidden_text=True,
                javascript=True,
                metadata=True,
                redactions=True,
                remove_links=True,
                reset_fields=True,
                reset_responses=True,
                thumbnails=True,
                xml_metadata=True,
            )
        except Exception:
            warnings.append(
                "PDF scrub cleanup could not be fully completed. "
                "Metadata and auxiliary object cleanup may be partial."
            )

        doc.set_metadata({})

        try:
            doc.del_xml_metadata()
        except Exception:
            pass

        doc.save(
            output_path,
            garbage=4,
            deflate=True,
            clean=True,
            incremental=False,
        )

    finally:
        doc.close()

    entities_by_type = analyzer.count_by_type(all_entities)

    return ProcessingReport(
        job_id=job_id,
        status="completed" if not warnings else "completed_with_warnings",
        document_type="pdf_digital",
        pages=pages_done if partial else inspection.pages,
        partial=partial,
        pages_total_original=pages_total_original if partial else None,
        selectable_text_chars=inspection.selectable_text_chars,
        entities_detected=len(all_entities) + layout_rect_count,
        entities_redacted=redacted_count,
        entities_by_type=entities_by_type,
        structural_redactions_untyped=layout_rect_count,
        residual_entities=0,
        manual_review_required=bool(plan_pages_skipped) or bool(full_redaction_pages),
        warnings=warnings,
        redaction_mode=mode,
        limits=ProcessingLimits(
            max_upload_mb=settings.max_upload_mb,
            max_pdf_pages=settings.max_pdf_pages,
            min_digital_text_chars=settings.min_digital_text_chars,
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

_NAME_CELL_TOKEN_RE = re.compile(r"^[A-ZÁÉÍÓÚÑÜÀÈÒÏÇ][\wÀ-ÿ'’\-]*$")


def _standalone_name_cell(text: str) -> tuple[int, int] | None:
    """Span (start, end) si la zona entera es un nombre propio aislado.

    En formularios de rejilla el segmentador deja cada celda en su propia zona,
    así que un nombre sin etiqueta en la misma celda ("ROBERTO FERNANDEZ
    MORAN") no tiene contexto para el NER ni para los patrones etiquetados.
    Se acepta solo con aval fuerte del INE: 2–4 palabras alfabéticas
    capitalizadas, ≥2 en el gazetteer con al menos un apellido, y que el
    conjunto no sea un topónimo (celdas de municipio).
    """
    from app.services.pii import gazetteer

    stripped = text.strip()
    if not stripped or "\n" in stripped or len(stripped) > 48:
        return None
    tokens = stripped.split()
    if not 2 <= len(tokens) <= 4:
        return None
    if not all(_NAME_CELL_TOKEN_RE.match(t) for t in tokens):
        return None
    if gazetteer.is_toponym(stripped):
        return None
    norm = [gazetteer.normalize(t) for t in tokens]
    ine_hits = sum(1 for t in norm if gazetteer.is_person_token(t))
    has_surname = any(t in gazetteer.surnames() for t in norm)
    if ine_hits < 2 or not has_surname:
        return None
    start = text.index(stripped)
    return start, start + len(stripped)


def _collect_redaction_rects(
    zoned_page,
    page_text,
    analyzer: PiiAnalyzer,
    mode: str,
    collect_entities: bool = False,
) -> tuple[list[fitz.Rect], list[PiiEntity], int]:
    """Return (rects_to_redact, pii_entities_found, layout_rect_count).

    Todo lo que se tacha o se cuenta pasa por un RedactionPlan: el filtro de
    tipos desactivados vive en un único sitio, y los rects pintados y las
    entidades contadas salen de la misma lista, pida o no la petición la lista
    de entidades (`collect_entities` ya no cambia el recuento).
    """
    plan = RedactionPlan(disabled_types=frozenset(analyzer.disabled_types))
    all_protected = [r for zone in zoned_page.zones for r in zone.layout_protected_rects]

    # Entidades de formulario: cuentan y sirven de prueba para la supresión
    # institucional, pero sus valores ya los tapa el rect estructural.
    form_entities: list[PiiEntity] = []
    # BODY/HEADER/FOOTER: sus rects esperan a la supresión institucional.
    candidates: list[tuple[PiiEntity, list[fitz.Rect]]] = []

    for zone in zoned_page.zones:

        # SIGNATURE / QR_CODE → full zone rect
        if zone.is_auto_redact:
            if zone.zone_type == ZoneType.QR_CODE:
                if code_may_stay_visible(zone.text, analyzer):
                    continue
                # Si llega aquí con QR_CODE desactivado es porque su contenido lleva
                # un obligatorio o no se puede leer: se tacha igual (excepción).
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
        # MinerU creates TABLE/FORM zones without pre-computed rects, so those
        # fall through to PII analysis.
        if zone.zone_type in (ZoneType.FORM, ZoneType.TABLE) and zone.layout_redact_rects:
            types = zone.layout_redact_types or []
            for index, rect in enumerate(zone.layout_redact_rects):
                plan.add_rect(rect, types[index] if index < len(types) else None, structural=True)
            # Si un rect se ha dejado de pintar por un tipo desactivado, lo que
            # cubría se tapa por entidad.  Si no, el valor ya está tapado y la
            # entidad solo se cuenta.
            released = structural_rects_released(types, analyzer.disabled_types)
            cfg = get_detector_config(zone.zone_type)
            if zone.text.strip() and cfg.entity_types is not None:
                allowed = set(cfg.entity_types)
                for entity in analyzer.analyze(
                    zone.text, mode=mode, zone_type=zone.zone_type.value,
                    context_text=page_text.text,
                ):
                    if entity.entity_type not in allowed:
                        continue
                    absolute = PiiEntity(
                        entity_type=entity.entity_type,
                        start=zone.page_text_offset + entity.start,
                        end=zone.page_text_offset + entity.end,
                        score=entity.score,
                        source=entity.source,
                        text=zone.text[entity.start:entity.end],
                    )
                    form_entities.append(absolute)
                    rects = [
                        r for r in rects_for_span(page_text, absolute.start, absolute.end)
                        if not _center_in_any(r, all_protected)
                    ] if released else []
                    plan.add_entity(absolute, rects)
            continue

        # BODY / HEADER / FOOTER → PII analysis with zone-specific types
        cfg = get_detector_config(zone.zone_type)
        if not zone.text.strip() or cfg.entity_types is None:
            continue

        allowed = set(cfg.entity_types)
        local_entities = [
            e for e in analyzer.analyze(
                zone.text,
                mode=mode,
                zone_type=zone.zone_type.value,
                # El gate de importes necesita la pagina: cada fila de tabla es una
                # zona propia y ahi no se distingue nomina de presupuesto de obra.
                context_text=page_text.text,
            )
            if e.entity_type in allowed
        ]

        # Celda-nombre aislada (valor de formulario sin etiqueta en la zona).  Pasa
        # por el plan como cualquier otra: con PERSON desactivado no se tacha (F10).
        if "PERSON" in allowed and not any(e.entity_type == "PERSON" for e in local_entities):
            span = _standalone_name_cell(zone.text)
            if span is not None:
                local_entities.append(PiiEntity(
                    entity_type="PERSON", start=span[0], end=span[1],
                    score=0.85, source="standalone_name_cell",
                ))

        for entity in local_entities:
            abs_start = zone.page_text_offset + entity.start
            abs_end = zone.page_text_offset + entity.end
            candidates.append((
                PiiEntity(
                    entity_type=entity.entity_type, start=abs_start, end=abs_end,
                    score=entity.score, source=entity.source,
                    text=zone.text[entity.start:entity.end],
                ),
                [
                    r for r in rects_for_span(page_text, abs_start, abs_end)
                    if not _center_in_any(r, all_protected)
                ],
            ))

    # Suppress institutional contact data (issuer's own phone/address/CP) before
    # emitting BODY/HEADER/FOOTER entity rects: it is not personal data under RGPD.
    evidence = None
    if "PERSON" in analyzer.disabled_types:
        evidence = _person_evidence(zoned_page, page_text, analyzer, mode)
    for entity, rects in candidates:
        plan.add_candidate(entity, rects)
    plan.resolve_candidates(page_text.text, zoned_page, contexto=form_entities,
                            person_spans=evidence)

    return plan.rects, plan.entities, plan.untyped_structural


def _person_evidence(zoned_page, page_text, analyzer: PiiAnalyzer, mode: str) -> list[tuple[int, int]]:
    """Spans de PERSON de la página SIN filtrar tipos desactivados.

    Solo se calcula cuando PERSON está desactivado (hoy no alcanzable por la API:
    PERSON es obligatorio).  La supresión institucional necesita saber si hay una
    persona entre el nombre de una institución y un teléfono aunque no se vaya a
    tachar.
    """
    unfiltered = analyzer.with_disabled_types(frozenset())
    spans: list[tuple[int, int]] = []
    for zone in zoned_page.zones:
        if zone.is_auto_redact or not zone.text.strip():
            continue
        cfg = get_detector_config(zone.zone_type)
        if cfg.entity_types is None or "PERSON" not in set(cfg.entity_types):
            continue
        found = [
            e for e in unfiltered.analyze(
                zone.text, mode=mode, zone_type=zone.zone_type.value, context_text=page_text.text,
            )
            if e.entity_type == "PERSON"
        ]
        if not found and zone.zone_type not in (ZoneType.FORM, ZoneType.TABLE):
            span = _standalone_name_cell(zone.text)
            if span is not None:
                spans.append((zone.page_text_offset + span[0], zone.page_text_offset + span[1]))
        spans.extend((zone.page_text_offset + e.start, zone.page_text_offset + e.end) for e in found)
    return spans


def _center_in_any(rect: fitz.Rect, containers: list[fitz.Rect]) -> bool:
    cx = (rect.x0 + rect.x1) / 2
    cy = (rect.y0 + rect.y1) / 2
    for c in containers:
        if c.x0 - 1.5 <= cx <= c.x1 + 1.5 and c.y0 - 1.5 <= cy <= c.y1 + 1.5:
            return True
    return False


def _entities_for_audit(
    analyzer: PiiAnalyzer, text: str, mode: str,
) -> list[PiiEntity]:
    """Entidades de una página de redacción total, solo para el informe.

    No influye en la redacción (la página entera ya se tacha): sirve para que el
    informe diga QUÉ datos contenía y para que una página completamente
    protegida no figure como PII no detectada.
    """
    if not text or not text.strip():
        return []
    allowed = set(ALL_PII_TYPES)
    return [
        PiiEntity(
            entity_type=e.entity_type, start=e.start, end=e.end,
            score=e.score, source=e.source, text=text[e.start:e.end],
        )
        for e in analyzer.analyze(text, mode=mode, zone_type=None, context_text=text)
        if e.entity_type in allowed
    ]


def _redact_whole_page(page: fitz.Page) -> None:
    """Pinta la página entera: copia de DNI o impreso de autoliquidación.

    No se ejecuta análisis selectivo de entidades — todo el contenido de estos
    documentos es identificativo.  Se redacta el rectángulo completo de la
    página, eliminando también el contenido de las imágenes embebidas.
    """
    page.add_redact_annot(page.rect, fill=(0, 0, 0))
    _apply_redactions_safe(page)


def _apply_redactions_safe(page: fitz.Page, **opciones) -> None:
    """apply_redactions con fallback de colorspace.

    El modo por defecto (PIXELS) tacha el contenido de las imágenes, pero MuPDF
    solo lo soporta en Gray/RGB/CMYK; una imagen embebida con colorspace
    exótico (indexado/ICC, p.ej. la de un código de barras real) lanza
    FzErrorArgument.  En ese caso se reintenta ELIMINANDO las imágenes que
    solapan la redacción — mismo resultado de privacidad.
    """
    try:
        page.apply_redactions(**opciones)
    except Exception:
        page.apply_redactions(**{**opciones, "images": fitz.PDF_REDACT_IMAGE_REMOVE})


def _add_signature_zones(page: fitz.Page, zoned_page, page_text) -> None:
    """Append SIGNATURE zones for handwritten signatures (in-place).

    SIGNATURE zones are auto-redacted (whole rect), like QR_CODE zones.
    """
    for rect in detect_signature_rects(page, page_text):
        zoned_page.zones.append(PageZone(
            zone_type=ZoneType.SIGNATURE,
            rect=rect,
            text="",
            source="digital",
        ))


def _add_visual_qr_zones(page: fitz.Page, zoned_page) -> None:
    """Render *page* at low DPI, detect QR codes, append QR_CODE zones (in-place).

    Coordinate conversion: image pixels → PDF pts via 1/scale (scale = DPI/72).
    """
    scale = _QR_DETECT_DPI / 72.0
    matrix = fitz.Matrix(scale, scale)
    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
    image = Image.open(BytesIO(pixmap.tobytes("png"))).convert("RGB")

    # Shared guard (see code_detector.detect_redactable_codes): only codes that
    # decode to a non-empty payload and are not implausibly large are redacted.
    for qr_rect, decoded_text in detect_redactable_codes(image):
        pdf_rect = fitz.Rect(
            qr_rect.x0 / scale,
            qr_rect.y0 / scale,
            qr_rect.x1 / scale,
            qr_rect.y1 / scale,
        )
        zoned_page.zones.append(PageZone(
            zone_type=ZoneType.QR_CODE,
            rect=pdf_rect,
            text=decoded_text,
            source="digital",
        ))
