"""Informe en PDF, en castellano, para quien no sabe informatica.

Se construye SOLO con recuentos, paginas y codigos del informe: nunca con
`detected_entities`, `descartes_padron` ni las frases de `warnings`, que pueden
llevar valores.  El informe se descarga y puede acabar en cualquier sitio.
Tampoco lleva el nombre del fichero (suele llevar el del interesado): fecha,
numero de paginas e identificador del trabajo.

Orden: primero si hay que revisar algo y que, despues las cifras.
"""
from __future__ import annotations

import html
import io
from datetime import datetime
from zoneinfo import ZoneInfo

import fitz

from app.schemas.report import ProcessingReport
from app.services.pii.entity_catalog import entity_catalog

ZONA = ZoneInfo("Europe/Madrid")

# Colores del mapa de paginas: tambien se distinguen impresos en gris.
SIN_DATOS, CON_DATOS, REVISAR, ENTERA = "#E3E6EA", "#3A6EA5", "#E0A030", "#1E1E1E"

_REVISION = {
    "residual_pii": "La comprobación final encontró posibles datos que siguen legibles.",
    "verification_risk_high": "La comprobación final da un riesgo alto de que quede algún dato.",
    "plan_without_titleblock": "Hay páginas de plano sin cajetín reconocible: se han dejado sin tachar.",
    "full_redaction_pages": "Hay páginas tachadas enteras por su contenido: compruebe que eran "
                            "copias de documentos de identidad o autoliquidaciones.",
    "low_ocr_confidence": "Hay páginas escaneadas que se han leído con poca seguridad.",
    "pages_skipped": "Hay páginas que no se han podido procesar.",
    "no_text_detected": "No se ha encontrado texto que analizar.",
}

_TACHADA_ENTERA = {
    "ocr_unreadable": "no se pudo leer, ni girándola, enderezándola o invirtiendo los colores",
    "ocr_timeout": "su lectura tardó demasiado y se tachó por seguridad",
    "dni_scan": "es una copia de un documento de identidad",
    "tax_form": "es una autoliquidación de impuestos",
}

_METADATOS = {
    "document_info": "Datos del documento (autor, programa, título, fechas)",
    "xmp": "Metadatos XMP",
    "comments": "Comentarios y notas",
    "form_fields": "Campos de formulario (lo que se veía pasa a la página)",
    "bookmarks": "Marcadores",
    "javascript": "Código JavaScript",
    "attachments": "Ficheros adjuntos",
    "photo_metadata": "Fotos con datos de la cámara o de ubicación (GPS)",
    "thumbnails": "Miniaturas de página",
    "links": "Enlaces",
    "accessibility_tags": "Estructura de etiquetas y textos alternativos",
    "text_copies": "Copias ocultas del texto",
    "previous_versions": "Versiones anteriores guardadas dentro del fichero",
    "hidden_text": "Fragmentos de texto que no se veían",
    "tracked_changes": "Cambios con control de cambios (lo borrado se quita, lo añadido queda)",
    "field_codes": "Códigos de campo",
    "custom_xml": "Propiedades y datos personalizados",
    "alt_text": "Textos alternativos de imágenes",
    "embedded_objects": "Objetos incrustados (hojas de cálculo, gráficos...)",
    "images_removed": "Imágenes en formatos que no se pueden revisar",
}

_CSS = """
body { font-family: sans-serif; font-size: 10pt; color: #1E1E1E; }
h1 { font-size: 17pt; margin: 0 0 2pt 0; }
h2 { font-size: 12pt; margin: 16pt 0 5pt 0; color: #1F3B57; }
p { margin: 0 0 5pt 0; }
.org { font-size: 10pt; color: #4A5560; margin: 0 0 4pt 0; }
.meta { font-size: 9pt; color: #4A5560; margin: 0 0 12pt 0; }
.caja { padding: 8pt 10pt; margin: 0 0 6pt 0; }
.revisar { background-color: #FFF1DA; border: 1pt solid #C77700; }
.bien { background-color: #E8F3EA; border: 1pt solid #3C7F4A; }
.titulo { font-size: 12pt; font-weight: bold; margin: 0 0 4pt 0; }
.grande { font-size: 20pt; font-weight: bold; }
.nota { font-size: 8.5pt; color: #4A5560; }
table { border-collapse: collapse; }
td, th { padding: 2pt 6pt 2pt 0; text-align: left; vertical-align: top; }
th { font-size: 9pt; color: #4A5560; font-weight: normal; border-bottom: 0.5pt solid #9AA3AD; }
td.n { text-align: right; padding-right: 14pt; }
td.c { width: 18pt; height: 15pt; font-size: 7pt; text-align: center; padding: 0;
       border: 0.6pt solid #FFFFFF; }
td.ley { font-size: 8.5pt; color: #4A5560; padding: 0 12pt 0 4pt; white-space: nowrap; }
table.leyenda { margin: 4pt 0 8pt 0; }
"""


def _e(texto) -> str:
    return html.escape(str(texto))


def _paginas(numeros) -> str:
    n = sorted(set(numeros))
    if not n:
        return ""
    if len(n) == 1:
        return f"la página {n[0]}"
    return "las páginas " + ", ".join(map(str, n[:-1])) + f" y {n[-1]}"


def _avisos(r: ProcessingReport, etiquetas: dict[str, str]) -> list[str]:
    """Que revisar, en lenguaje llano, con sus paginas (desde 1)."""
    lineas: list[str] = []
    codigos = {a.code for a in r.coded_warnings}
    for a in r.coded_warnings:
        donde = _paginas(a.pages)
        tipos = ", ".join(etiquetas.get(t, t).lower() for t in a.types)
        if a.code == "safety_net_still_readable":
            lineas.append(f"Puede que siga legible algún dato ({tipos or 'sin tipo'}) en {donde}.")
        elif a.code == "pages_not_verified":
            lineas.append(f"La comprobación final no ha podido revisar {donde}.")
        elif a.code == "safety_net_acted":
            lineas.append(f"La comprobación final aún leía datos en {donde} y se han tapado; "
                          "conviene echarles un vistazo.")
        elif a.code == "plan_titleblock_only_verified":
            lineas.append(f"En {donde}, que son planos, solo se ha comprobado el cajetín.")
        elif a.code == "digital_signature_invalidated":
            lineas.append("El documento llevaba firma digital: al modificarlo deja de ser válida. "
                          "Si necesita la firma, use el original.")
    for motivo in r.manual_review_reasons:
        if motivo in codigos or motivo not in _REVISION:
            continue
        texto = _REVISION[motivo]
        if motivo == "low_ocr_confidence" and r.ocr.low_confidence_pages:
            texto = texto[:-1] + f": {_paginas(p + 1 for p in r.ocr.low_confidence_pages)}."
        lineas.append(texto)
    return lineas


def _mapa(r: ProcessingReport, revisar: set[int], enteras: set[int]) -> str:
    """Una casilla por pagina: sin datos, con datos, a revisar, tachada entera."""
    celdas = []
    for n in range(1, r.pages + 1):
        if n in enteras:
            color, letra = ENTERA, "#FFFFFF"
        elif n in revisar:
            color, letra = REVISAR, "#1E1E1E"
        elif n in r.entities_by_page:
            color, letra = CON_DATOS, "#FFFFFF"
        else:
            color, letra = SIN_DATOS, "#4A5560"
        celdas.append(f'<td class="c" style="background-color:{color};color:{letra}">{n}</td>')
    filas = ["<tr>" + "".join(celdas[i:i + 25]) + "</tr>" for i in range(0, len(celdas), 25)]
    leyenda = "".join(
        f'<td class="c" style="background-color:{c}">&#160;</td><td class="ley">{t}</td>'
        for c, t in ((CON_DATOS, "con datos ocultados"), (SIN_DATOS, "sin datos"),
                     (REVISAR, "conviene revisarla"), (ENTERA, "tachada entera")))
    return f"<table>{''.join(filas)}</table><table class='leyenda'><tr>{leyenda}</tr></table>"


def _unidad(r: ProcessingReport) -> str:
    if r.document_type == "docx":
        return "documento de Word"
    if r.document_type == "image":
        return "imagen" if r.pages == 1 else "imágenes"
    return "página" if r.pages == 1 else "páginas"


def _html(r: ProcessingReport, fecha: datetime) -> str:
    etiquetas = {t.id: t.label for t in entity_catalog()}
    solo_metadatos = r.treatment == "metadata_only"
    local = fecha.astimezone(ZONA) if fecha.tzinfo else fecha
    partes = [
        "<p class='org'>Universitat de València</p>",
        "<h1>Informe de anonimización</h1>" if not solo_metadatos
        else "<h1>Informe de limpieza de metadatos</h1>",
        f"<p class='meta'>{local:%d/%m/%Y %H:%M} &#183; "
        f"{'' if r.document_type == 'docx' else f'{r.pages} '}{_unidad(r)} "
        f"&#183; identificador {_e(r.job_id)}</p>",
    ]

    # 1. Antes que nada: hay que revisar o no.
    avisos = _avisos(r, etiquetas)
    if solo_metadatos:
        partes.append(
            "<div class='caja revisar'><p class='titulo'>El contenido NO se ha revisado</p>"
            "<p>Solo se han quitado los metadatos y lo que no se veía. Los datos personales que "
            "se vean en el documento siguen ahí: no lo publique como anonimizado.</p></div>")
    elif r.manual_review_required or avisos:
        partes.append("<div class='caja revisar'><p class='titulo'>Revise el documento antes "
                      "de usarlo</p>" + "".join(f"<p>&#8226; {_e(a)}</p>" for a in avisos) + "</div>")
    else:
        partes.append("<div class='caja bien'><p class='titulo'>El proceso no ha señalado nada que "
                      "revisar</p><p>Aun así, una lectura rápida antes de publicarlo es buena "
                      "práctica: la detección automática puede fallar.</p></div>")
    if solo_metadatos and avisos:
        partes.append("".join(f"<p>&#8226; {_e(a)}</p>" for a in avisos))
    if r.partial and r.pages_total_original:
        partes.append(f"<div class='caja revisar'><p>Documento parcial: contiene solo las páginas "
                      f"1 a {r.pages} de {r.pages_total_original}. El resto no se ha "
                      "anonimizado y no se entrega.</p></div>")

    # 2. Que se ha ocultado.
    if not solo_metadatos:
        total = sum(r.entities_by_type.values())
        partes.append("<h2>Qué se ha ocultado</h2>")
        partes.append(f"<p><span class='grande'>{total}</span> "
                      f"{'dato personal' if total == 1 else 'datos personales'}</p>")
        if r.entities_by_type:
            filas = "".join(
                f"<tr><td>{_e(etiquetas.get(t, t))}</td><td class='n'>{n}</td></tr>"
                for t, n in sorted(r.entities_by_type.items(), key=lambda x: (-x[1], x[0])))
            partes.append(f"<table><tr><th>Tipo de dato</th><th>Cuántos</th></tr>{filas}</table>")
        if r.structural_redactions_untyped:
            partes.append(f"<p class='nota'>Además, {r.structural_redactions_untyped} "
                          f"{'zona tachada' if r.structural_redactions_untyped == 1 else 'zonas tachadas'} "
                          "por la forma del documento (por ejemplo, la casilla de un formulario), "
                          "sin tipo de dato.</p>")

    # 3. En que paginas.
    enteras = {p.page: p.reason for p in r.pages_redacted_by_content}
    enteras.update({p.page: p.reason for p in r.pages_redacted_in_full})
    revisar = {n for a in r.coded_warnings for n in a.pages} | {
        p + 1 for p in r.ocr.low_confidence_pages}
    if r.pages and not solo_metadatos and r.document_type != "docx":
        partes.append("<h2>En qué páginas</h2>")
        partes.append(_mapa(r, revisar, set(enteras)))
        if r.entities_by_page:
            filas = "".join(
                f"<tr><td>Página {n}</td><td>"
                + _e(", ".join(f"{etiquetas.get(t, t)} ({c})"
                               for t, c in sorted(tipos.items(), key=lambda x: (-x[1], x[0]))))
                + "</td></tr>"
                for n, tipos in sorted(r.entities_by_page.items()))
            partes.append(f"<table><tr><th>Página</th><th>Datos ocultados</th></tr>{filas}</table>")

    # 4. Paginas tachadas enteras.
    if enteras:
        partes.append("<h2>Páginas tachadas enteras</h2>")
        partes.append("".join(
            f"<p>&#8226; Página {n}: {_e(_TACHADA_ENTERA.get(why, 'se tachó entera por seguridad'))}.</p>"
            for n, why in sorted(enteras.items())))

    # 5. Metadatos.
    partes.append("<h2>Metadatos y contenido oculto eliminados</h2>")
    quitado = {k: v for k, v in r.metadata_removed.items() if v}
    if quitado:
        filas = "".join(f"<tr><td>{_e(_METADATOS.get(k, k))}</td><td class='n'>{n}</td></tr>"
                        for k, n in quitado.items())
        partes.append(f"<table><tr><th>Qué</th><th>Cuántos</th></tr>{filas}</table>")
    else:
        partes.append("<p>El documento no llevaba metadatos ni contenido oculto que quitar.</p>")
    partes.append("<p class='nota'>Siempre se eliminan, si los hay: autor y programa, comentarios, "
                  "marcadores, adjuntos, código, datos de las fotos, miniaturas y versiones "
                  "anteriores. El documento entregado solo lleva lo que se ve en sus páginas.</p>")
    return "".join(partes)


def generar_informe_pdf(r: ProcessingReport, fecha: datetime) -> bytes:
    """El informe como PDF.  Solo recuentos, paginas y codigos: ningun valor."""
    story = fitz.Story(html=_html(r, fecha), user_css=_CSS)
    destino = io.BytesIO()
    writer = fitz.DocumentWriter(destino)
    pagina = fitz.paper_rect("a4")
    caja = pagina + (50, 50, -50, -60)
    mas = 1
    while mas:
        dev = writer.begin_page(pagina)
        mas, _ = story.place(caja)
        story.draw(dev)
        writer.end_page()
    writer.close()

    doc = fitz.open(stream=destino.getvalue(), filetype="pdf")
    total = len(doc)
    for i, p in enumerate(doc):
        pie = (f"Universitat de València · Informe {r.job_id} · página {i + 1} de {total} · "
               "no contiene ninguno de los datos ocultados")
        p.insert_text((50, pagina.height - 30), pie, fontsize=7.5, color=(0.35, 0.38, 0.42))
    doc.set_metadata({"title": "Informe de anonimización", "creator": "", "producer": ""})
    datos = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return datos
