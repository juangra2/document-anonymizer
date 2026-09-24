"""Fabrica los documentos MIXTOS que el corpus no cubre (casos 1-5, 11-13).

El corpus genera cada documento de un solo tipo, en un solo idioma y con una
sola calidad, y nunca concatena: todo lo que sea "dos cosas dentro del mismo
PDF" esta sin probar por construccion.  Estos ficheros llenan ese hueco.

Las paginas escaneadas se rasterizan aqui con fitz, no con el efecto escaneo
del generador: ese efecto depende de poppler y, sin el, copia el PDF limpio
en silencio (por eso el 30 % "escaneado" del corpus conserva capa de texto).

Cada caso trae su CONTROL: el mismo material sin mezclar.  Sin el control no
se puede distinguir "el pipeline no sabe tratar esto" de "el pipeline no
llega a mirarlo".

Reproducible: mismas entradas, mismas salidas.  Sin datos reales: todo sale
del corpus sintetico o es inventado aqui.
"""
from __future__ import annotations

import sys
from pathlib import Path

import fitz

RAIZ = Path(__file__).resolve().parents[1]
CORPUS = RAIZ.parent / "corpus-generator" / "output"
DESTINO = RAIZ / "samples" / "mixtos"

RENDER_DPI = 200

# PII inventada para el texto de las paginas que fabrico, distinta de la del
# corpus: asi se distingue lo tachado en el texto de lo tachado en la imagen.
NOMBRE_TEXTO = "Vicente Alborch Ferrandis"
DNI_TEXTO = "46821357G"


# --------------------------------------------------------------------------
# piezas
# --------------------------------------------------------------------------

def rasterizar(origen: Path, pagina: int = 0,
               caja: tuple[float, float] | None = None) -> fitz.Document:
    """PDF de una pagina con el contenido como imagen y sin capa de texto.

    `caja` fuerza el tamano de pagina en puntos.  Hace falta porque algunos
    PDF del corpus (y muchos escaneres reales) escriben la pagina a un punto
    por pixel: un A4 a 200 DPI queda como una pagina de 58 x 82 cm, y a ese
    tamano el pipeline la toma por un plano de obra.
    """
    src = fitz.open(origen)
    page = src[pagina]
    pix = page.get_pixmap(matrix=fitz.Matrix(RENDER_DPI / 72.0, RENDER_DPI / 72.0))
    out = fitz.open()
    ancho, alto = caja if caja else (page.rect.width, page.rect.height)
    nueva = out.new_page(width=ancho, height=alto)
    nueva.insert_image(nueva.rect, stream=pix.tobytes("jpeg", jpg_quality=88))
    src.close()
    return out


def pagina_de_texto(doc: fitz.Document, texto: str) -> fitz.Page:
    """Anade una pagina A4 con ese texto en la mitad superior."""
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(
        fitz.Rect(60, 60, 535, 400), texto,
        fontname="helv", fontsize=10, lineheight=1.4,
    )
    return page


def pegar_imagen(page: fitz.Page, origen: Path, rect: fitz.Rect) -> None:
    """Pega el contenido de un PDF como imagen dentro de un rectangulo."""
    src = fitz.open(origen)
    pix = src[0].get_pixmap(matrix=fitz.Matrix(RENDER_DPI / 72.0, RENDER_DPI / 72.0))
    page.insert_image(rect, stream=pix.tobytes("jpeg", jpg_quality=88))
    src.close()


def juntar(destino: Path, piezas: list[tuple[str, Path, int]]) -> Path:
    """piezas: (modo, fichero, pagina) con modo en {"texto", "escaneo"}."""
    doc = fitz.open()
    for modo, fichero, pagina in piezas:
        if modo == "texto":
            doc.insert_file(fichero, from_page=pagina, to_page=pagina)
        elif modo == "escaneo":
            img = rasterizar(fichero, pagina)
            doc.insert_pdf(img)
            img.close()
        else:
            raise ValueError(modo)
    destino.parent.mkdir(parents=True, exist_ok=True)
    doc.save(destino)
    doc.close()
    return destino


def guardar(destino: Path, doc: fitz.Document) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    doc.save(destino)
    doc.close()
    return destino


DNI_ESCANEADO = CORPUS / "ext_pdfs" / "dni_escaneado_0003.pdf"       # derecho
DNI_GIRADO = CORPUS / "ext_pdfs" / "dni_escaneado_0001.pdf"          # girado 90
EXPEDIENTE = CORPUS / "pdfs" / "acuerdo_adscripcion_0033.pdf"        # texto denso
NOTIFICACION = CORPUS / "pdfs" / "notificacion_ayuntamiento_0004.pdf"
AUTOLIQUIDACION = CORPUS / "ext_pdfs" / "autoliquidacion_0002.pdf"   # modelo 600

NOTA_CORTA = (
    f"Se adjunta copia del documento de identidad de {NOMBRE_TEXTO}, "
    f"con DNI {DNI_TEXTO}."
)

NOTA_LARGA = NOTA_CORTA + "\n\n" + (
    "En relacion con el expediente de referencia, y a los efectos previstos en "
    "el articulo 68 de la Ley 39/2015, de 1 de octubre, del Procedimiento "
    "Administrativo Comun de las Administraciones Publicas, se requiere al "
    "interesado para que aporte la documentacion que se relaciona a "
    "continuacion, advirtiendole de que, de no hacerlo en el plazo de diez "
    "dias habiles contados a partir del siguiente al de la recepcion de la "
    "presente notificacion, se le tendra por desistido de su peticion, previa "
    "resolucion que debera ser dictada en los terminos previstos en el "
    "articulo 21 de la citada Ley. La documentacion podra presentarse en el "
    "registro general de esta corporacion o por cualquiera de los medios "
    "previstos en el articulo 16.4 de la misma norma. Transcurrido dicho plazo "
    "sin que se haya aportado la documentacion requerida, se procedera al "
    "archivo de las actuaciones sin mas tramite. Contra el presente acto, por "
    "ser de tramite, no cabe recurso alguno, sin perjuicio de que pueda "
    "alegarse lo que se estime procedente para su consideracion en la "
    "resolucion que ponga fin al procedimiento."
)


# --------------------------------------------------------------------------
# casos
# --------------------------------------------------------------------------

def caso1() -> Path:
    """Expediente digital con una pagina escaneada dentro."""
    return juntar(DESTINO / "caso1_expediente_con_dni.pdf", [
        ("texto",   EXPEDIENTE, 0),
        ("texto",   EXPEDIENTE, 1),
        ("escaneo", DNI_ESCANEADO, 0),
    ])


def caso2a() -> Path:
    """Una pagina con texto ABUNDANTE y una imagen escaneada dentro."""
    doc = fitz.open()
    page = pagina_de_texto(doc, NOTA_LARGA)
    pegar_imagen(page, DNI_ESCANEADO, fitz.Rect(90, 420, 505, 800))
    return guardar(DESTINO / "caso2a_pagina_mixta_con_texto.pdf", doc)


def caso2b() -> Path:
    """La MISMA pagina con texto ESCASO: el documento si entra al hibrido."""
    doc = fitz.open()
    page = pagina_de_texto(doc, NOTA_CORTA)
    pegar_imagen(page, DNI_ESCANEADO, fitz.Rect(90, 420, 505, 800))
    return guardar(DESTINO / "caso2b_pagina_mixta_sin_texto.pdf", doc)


def caso3() -> Path:
    """Documento que SI entra al hibrido, con una pagina girada dentro."""
    doc = fitz.open()
    pagina_de_texto(doc, NOTA_CORTA)
    for origen in (DNI_GIRADO, DNI_ESCANEADO):
        img = rasterizar(origen, 0)
        doc.insert_pdf(img)
        img.close()
    return guardar(DESTINO / "caso3_girado_en_hibrido.pdf", doc)


def caso3_control() -> Path:
    """El mismo DNI girado, solo: camino escaneado."""
    return guardar(DESTINO / "control_dni_girado_solo.pdf", rasterizar(DNI_GIRADO, 0))


A4 = (595.0, 842.0)


def caso3b() -> Path:
    """Girado en hibrido, con la pagina a tamano A4 de verdad.

    El caso3 se desviaba por el tamano de pagina: 1654 x 2338 pt entra por la
    puerta de "plano de obra" y ni se mira.  Aqui el giro se prueba solo.
    """
    doc = fitz.open()
    pagina_de_texto(doc, NOTA_CORTA)
    img = rasterizar(DNI_GIRADO, 0, caja=A4)
    doc.insert_pdf(img)
    img.close()
    return guardar(DESTINO / "caso3b_girado_a4_en_hibrido.pdf", doc)


def caso3b_control() -> Path:
    """El mismo DNI girado a A4, solo: camino escaneado."""
    return guardar(DESTINO / "control_dni_girado_a4.pdf",
                   rasterizar(DNI_GIRADO, 0, caja=A4))


def control_notificacion() -> Path:
    """Notificacion rasterizada SOLA: camino escaneado, redaccion selectiva.

    Es el control que separa "el hibrido coloca mal las barras" de "la
    geometria del OCR esta mal en los dos caminos": la misma pagina va dentro
    del caso 4 por el hibrido y aqui por el escaneado.
    """
    return guardar(DESTINO / "control_notificacion_escaneada.pdf",
                   rasterizar(NOTIFICACION, 0))


def caso4() -> Path:
    """DNI derecho dentro de un documento que SI entra al hibrido."""
    doc = fitz.open()
    pagina_de_texto(doc, NOTA_CORTA)
    img = rasterizar(DNI_ESCANEADO, 0)
    doc.insert_pdf(img)
    img.close()
    otra = rasterizar(NOTIFICACION, 0)
    doc.insert_pdf(otra)
    otra.close()
    return guardar(DESTINO / "caso4_dni_en_hibrido.pdf", doc)


def _pagina_con_mencion(doc: fitz.Document, texto: str) -> None:
    pagina_de_texto(doc, texto)


NOTIF_SIN_RELACION = (
    "AYUNTAMIENTO DE MONTSERRAT\nServicio de Urbanismo\n\n"
    "Notificacion de inicio de expediente\n\n"
    f"Interesado: {NOMBRE_TEXTO}, con DNI {DNI_TEXTO}, con domicilio a efectos "
    "de notificaciones en Carrer de Sant Vicent, 18, 46117 Betera (Valencia).\n\n"
    "Se pone en su conocimiento que con fecha de hoy se ha iniciado expediente "
    "de restauracion de la legalidad urbanistica en relacion con las obras "
    "ejecutadas en el inmueble de referencia, concediendosele un plazo de "
    "quince dias para formular alegaciones y aportar los documentos que estime "
    "convenientes."
)

NOTIF_CON_MENCION = (
    "AYUNTAMIENTO DE MONTSERRAT\nServicio de Gestion Tributaria\n\n"
    "Comunicacion al interesado\n\n"
    f"Interesado: {NOMBRE_TEXTO}, con DNI {DNI_TEXTO}.\n\n"
    "En relacion con el modelo 600 presentado en esta administracion, se le "
    "comunica que la autoliquidacion ha sido revisada y que no procede "
    "practicar liquidacion complementaria alguna. Se archiva el expediente sin "
    "mas tramite, quedando a su disposicion el justificante de ingreso en las "
    "oficinas del servicio."
)


def caso5a() -> Path:
    """Autoliquidacion + notificacion SIN referencia compartida."""
    doc = fitz.open()
    doc.insert_file(AUTOLIQUIDACION, from_page=0, to_page=0)
    _pagina_con_mencion(doc, NOTIF_SIN_RELACION)
    return guardar(DESTINO / "caso5a_autoliquidacion_mas_ajena.pdf", doc)


def caso5b() -> Path:
    """Autoliquidacion + notificacion que MENCIONA el modelo 600 en prosa."""
    doc = fitz.open()
    doc.insert_file(AUTOLIQUIDACION, from_page=0, to_page=0)
    _pagina_con_mencion(doc, NOTIF_CON_MENCION)
    return guardar(DESTINO / "caso5b_autoliquidacion_mas_mencion.pdf", doc)


CASOS = {
    "1": caso1,
    "2a": caso2a, "2b": caso2b,
    "3": caso3, "3c": caso3_control,
    "3b": caso3b, "3bc": caso3b_control,
    "cn": control_notificacion,
    "4": caso4,
    "5a": caso5a, "5b": caso5b,
}


if __name__ == "__main__":
    from app.services.pdf.inspector import inspect_pdf_for_phase1

    pedidos = sys.argv[1:] or list(CASOS)
    for clave in pedidos:
        ruta = CASOS[clave]()
        ins = inspect_pdf_for_phase1(ruta)
        media = ins.selectable_text_chars / ins.pages
        camino = "DIGITAL (sin OCR)" if (
            ins.document_type == "pdf_digital" or
            (ins.document_type == "pdf_hybrid" and media >= 200)
        ) else ("ESCANEADO" if ins.document_type == "pdf_scanned" else "HIBRIDO")
        print(f"caso {clave:3s} {ruta.name:44s} pags={ins.pages} "
              f"tipos={[p.page_type for p in ins.page_inspections]} "
              f"media={media:7.1f} -> {camino}")
