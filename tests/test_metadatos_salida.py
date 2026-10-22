"""En el PDF entregado no queda nada oculto ni interactivo.

Notas de comentario con su autor, marcadores, JavaScript, la apariencia de un
campo con su valor y el EXIF/GPS de las fotos pegadas no pueden llegar a la
salida, en ningun camino (tampoco en las paginas que el hibrido copia ni en las
de plano del escaneado).  Principio: todo lo que se ve ha pasado por el
analisis; lo demas, fuera.

  - Lo que no se ve (notas, adjuntos, JavaScript, marcadores, metadatos): fuera.
  - Lo que se ve (texto libre, sellos, dibujos, campos rellenos): se integra en la
    pagina ANTES del analisis, asi que lo que lleve pasa por el tachado.
  - Las fotos que se conservan pierden EXIF, GPS, XMP, IPTC y marcas del
    codificador sin volver a comprimirse: solo quedan el perfil de color y la
    marca de Adobe.

Los cuatro casos de la auditoria, por los tres caminos, con OCR simulado.
"""
from __future__ import annotations

import io
import re

import fitz
import pytest
from PIL import Image

from app.services.ocr.models import OcrPage
from app.services.pdf.limpieza import limpiar_jpeg, quitar_copias_de_texto
from app.services.pii.analyzer import PiiAnalyzer
from tests.casos_metadatos import CASOS, OCULTOS, VISIBLE_CAMPO, VISIBLE_LIBRE, foto_movil, presentes


@pytest.fixture(scope="module")
def analyzer():
    return PiiAnalyzer()


class _OcrVacio:
    """OCR simulado que no lee nada: el test mira la estructura, no el OCR."""

    engine_name = "fake"
    provides_layout = False
    supports_pdf_input = False

    def analyze_image(self, image_path, page_index=0):
        with Image.open(image_path) as img:
            ancho, alto = img.size
        return OcrPage(page_index=page_index, width=ancho, height=alto, tokens=[])


@pytest.fixture
def caminos(monkeypatch, analyzer):
    import app.services.pdf.imagenes_pegadas as ip
    import app.services.pdf.scanned_redactor as sr
    from app.services.pdf.hybrid_redactor import redact_pdf_hybrid_secure
    from app.services.pdf.redactor import redact_pdf_digital

    motor = _OcrVacio()
    monkeypatch.setattr(sr, "get_ocr_engine", lambda: motor)
    monkeypatch.setattr(sr, "get_word_ocr_engine", lambda *_a, **_k: motor)
    monkeypatch.setattr(ip, "lector_por_defecto",
                        lambda: (lambda img: OcrPage(page_index=0, width=img.width,
                                                     height=img.height, tokens=[])))
    return {
        "digital": lambda e, s: redact_pdf_digital(e, s, "t", analyzer),
        "escaneado": lambda e, s: sr.redact_pdf_scanned(e, s, "t", analyzer,
                                                         document_type="pdf_scanned"),
        "hibrido": lambda e, s: redact_pdf_hybrid_secure(e, s, "t", analyzer),
    }


def _sin_nada_oculto(ruta):
    """Estructura del fichero entregado: nada oculto ni interactivo."""
    crudo = ruta.read_bytes()
    doc = fitz.open(ruta)
    fallos = []
    if crudo.count(b"%%EOF") != 1:
        fallos.append("mas de una revision")
    if any(v for k, v in doc.metadata.items() if k not in ("format", "encryption")):
        fallos.append("diccionario de informacion")
    if doc.get_xml_metadata().strip():
        fallos.append("XMP del documento")
    if doc.get_toc():
        fallos.append("marcadores")
    if doc.embfile_count():
        fallos.append("adjuntos")
    cat = doc.xref_object(doc.pdf_catalog())
    for clave in ("/OpenAction", "/AA", "/AcroForm", "/Names", "/Outlines", "/Metadata"):
        if clave in cat:
            fallos.append(f"catalogo {clave}")
    for p in doc:
        if list(p.annots()) or list(p.widgets()):
            fallos.append(f"anotaciones o campos en la pagina {p.number + 1}")
        obj = doc.xref_object(p.xref)
        for clave in ("/Metadata", "/Thumb", "/AA", "/PieceInfo"):
            if clave in obj:
                fallos.append(f"pagina {p.number + 1} {clave}")
    for x in range(1, doc.xref_length()):
        obj = doc.xref_object(x)
        if "/JS" in obj or "/JavaScript" in obj:
            fallos.append("JavaScript")
        if "/Subtype /Image" in obj and "/Metadata" in obj:
            fallos.append("XMP de imagen")
    doc.close()
    return fallos


@pytest.mark.parametrize("camino", ["digital", "escaneado", "hibrido"])
@pytest.mark.parametrize("caso", list(CASOS))
def test_nada_oculto_en_la_salida(caso, camino, caminos, tmp_path):
    entrada = CASOS[caso](tmp_path / f"{caso}.pdf")
    salida = tmp_path / f"{caso}_{camino}.pdf"
    caminos[camino](entrada, salida)
    assert presentes(salida, OCULTOS) == []
    assert _sin_nada_oculto(salida) == []


@pytest.mark.parametrize("camino", ["digital", "hibrido"])
def test_lo_visible_pasa_a_la_pagina_y_se_analiza(camino, caminos, tmp_path):
    """El texto libre y el campo relleno se ven: se quedan, pero tachados donde
    llevan un dato (el telefono, el DNI)."""
    entrada = CASOS["digital_cargado"](tmp_path / "d.pdf")
    salida = tmp_path / "s.pdf"
    caminos[camino](entrada, salida)
    texto = " ".join(fitz.open(salida)[0].get_text().split())
    assert "ZZLIBRETEXTO" in texto and "ZZCAMPO" in texto
    assert "655 123 456" not in texto and "48291736K" not in texto


def test_el_rotulo_dibujado_en_el_campo_no_se_pierde(caminos, tmp_path):
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((60, 90), "Solicitud de licencia", fontsize=11)
    campo = fitz.Widget()
    campo.field_name, campo.field_type = "dni", fitz.PDF_WIDGET_TYPE_TEXT
    campo.rect, campo.field_value = fitz.Rect(60, 120, 400, 150), "48291736K"
    campo.text_fontsize = 9
    p.add_widget(campo)
    # Un rotulo pintado dentro de la apariencia del propio campo, como los de
    # algunos formularios de la sede.
    w = next(p.widgets())
    ap = int(doc.xref_get_key(w.xref, "AP/N")[1].split()[0])
    doc.update_stream(ap, b"BT /Helv 6 Tf 2 25 Td (Rellenar en mayusculas) Tj ET\n" + doc.xref_stream(ap))
    entrada, salida = tmp_path / "f.pdf", tmp_path / "f_out.pdf"
    doc.save(entrada)
    caminos["digital"](entrada, salida)
    texto = " ".join(fitz.open(salida)[0].get_text().split())
    assert texto.count("Rellenar en mayusculas") == 1
    assert "48291736K" not in texto


def test_la_foto_conservada_pierde_sus_metadatos_sin_recomprimirse(caminos, tmp_path):
    entrada = CASOS["digital_cargado"](tmp_path / "d.pdf")
    salida = tmp_path / "s.pdf"
    caminos["digital"](entrada, salida)
    original = foto_movil()
    doc = fitz.open(salida)
    jpegs = [doc.xref_stream_raw(x) for x in range(1, doc.xref_length())
             if "/DCTDecode" in doc.xref_object(x)]
    assert len(jpegs) == 1
    jpeg = jpegs[0]
    # mismos datos de imagen: lo que va desde el primer SOS no ha cambiado
    assert jpeg[jpeg.index(b"\xff\xda"):] == original[original.index(b"\xff\xda"):]
    assert b"Exif" not in jpeg and b"http://ns.adobe.com/xap" not in jpeg
    assert b"ICC_PROFILE" in jpeg, "el perfil de color se conserva"


def _segmentos(jpeg: bytes) -> list[bytes]:
    out, i = [], 2
    while jpeg[i + 1] != 0xDA:
        n = int.from_bytes(jpeg[i + 2:i + 4], "big")
        out.append(jpeg[i + 1:i + 2] + jpeg[i + 4:i + 4 + min(n - 2, 11)])
        i += 2 + n
    return out


def test_limpiar_jpeg_conserva_solo_color_y_adobe():
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x0110] = "ZZMODELO"
    Image.new("CMYK", (64, 48), (10, 200, 30, 5)).save(
        buf, format="JPEG", quality=90, exif=exif,
        icc_profile=b"\x00" * 128)
    jpeg = buf.getvalue()
    # Ducky (APP12), IPTC de Photoshop (APP13) y un comentario, como los de Photoshop
    extra = (b"\xff\xec\x00\x10Ducky\x00\x01\x00\x04\x00\x00\x00\x50\x00"
             b"\xff\xed\x00\x10Photoshop 3.0\x00"
             b"\xff\xfe\x00\x0bZZCOMENTA")
    jpeg = jpeg[:2] + extra + jpeg[2:]
    limpio = limpiar_jpeg(jpeg)
    marcas = [s[:1] for s in _segmentos(limpio)]
    assert b"\xe1" not in marcas and b"\xec" not in marcas and b"\xed" not in marcas
    assert b"\xfe" not in marcas and b"\xe0" not in marcas
    assert any(s.startswith(b"\xeeAdobe") for s in _segmentos(limpio)), "Adobe (CMYK) se queda"
    assert any(s.startswith(b"\xe2ICC_PROFILE") for s in _segmentos(limpio)), "ICC se queda"
    assert limpio[limpio.index(b"\xff\xda"):] == jpeg[jpeg.index(b"\xff\xda"):]
    assert Image.open(io.BytesIO(limpio)).tobytes() == Image.open(io.BytesIO(jpeg)).tobytes()


def test_un_jpeg_raro_no_se_toca():
    assert limpiar_jpeg(b"no es un jpeg") is None


def test_pagina_de_plano_en_el_camino_escaneado_sale_limpia(caminos, tmp_path):
    from tests.test_plan_pages import _plan_pdf

    doc, page = _plan_pdf()
    page.insert_text((2100, 1600), "ARQUITECTO: LOLA ROMERA MARTINEZ", fontsize=13)
    nota = page.add_text_annot((100, 100), "ZZANOTTEXTO revisar")
    nota.set_info(title="ZZANOTAUTOR Jose Peris")
    nota.update()
    doc.set_toc([[1, "ZZMARCADOR plano de Lola Romera", 1]])
    entrada, salida = tmp_path / "plano.pdf", tmp_path / "plano_out.pdf"
    doc.save(entrada)
    caminos["escaneado"](entrada, salida)
    assert presentes(salida, ["ZZANOTTEXTO", "ZZANOTAUTOR", "ZZMARCADOR"]) == []
    assert _sin_nada_oculto(salida) == []


def test_el_contenido_visible_conserva_su_forma(caminos, tmp_path):
    """El texto libre integrado sigue en su sitio: no se ha ido con las anotaciones."""
    entrada = CASOS["digital_cargado"](tmp_path / "d.pdf")
    salida = tmp_path / "s.pdf"
    caminos["digital"](entrada, salida)
    pagina = fitz.open(salida)[0]
    cajas = pagina.search_for("ZZLIBRETEXTO")
    assert cajas and fitz.Rect(330, 690, 580, 715).intersects(cajas[0])
    assert re.search(r"ZZCAMPO", pagina.get_text())


def test_la_imagen_suelta_sale_sin_segmentos_de_metadatos(tmp_path):
    """Camino de imagen (JPG/PNG subido tal cual): al recodificar, PIL escribia
    un JFIF; tambien pasa por la limpieza."""
    from app.services.image.pipeline import ImageAnonymizationPipeline

    salida = tmp_path / "foto.pdf"
    ImageAnonymizationPipeline._save_image_as_pdf(Image.open(io.BytesIO(foto_movil())), salida)
    doc = fitz.open(salida)
    jpegs = [doc.xref_stream_raw(x) for x in range(1, doc.xref_length())
             if "/DCTDecode" in doc.xref_object(x)]
    assert jpegs and all(limpiar_jpeg(j) == j for j in jpegs)
    assert _sin_nada_oculto(salida) == []


def _pdf_etiquetado(destino):
    """Como lo escribe un generador etiquetado: la linea del DNI va marcada con un
    /ActualText (copia del texto) y la estructura lleva un texto alternativo."""
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((72, 100), "Interesada: Marta Ferrer Soler, vecina de Valencia", fontsize=12)
    p.insert_text((72, 120), "Resolucion del expediente de licencia de obra menor", fontsize=12)
    contenido = p.get_contents()[0]
    flujo = doc.xref_stream(contenido)
    fuente = re.search(rb"/(\w+) [\d.]+ Tf", flujo).group(1)
    doc.update_stream(contenido, flujo + b"\n/Span <</ActualText (DNI: 48291736K) /Alt <48291736> /Lang (es)>> BDC BT /"
                      + fuente + b" 12 Tf 72 692 Td (DNI: 48291736K) Tj ET EMC\n")
    elemento = doc.get_new_xref()
    doc.update_object(elemento, "<</Type/StructElem/S/Figure/Alt (Foto del DNI 48291736K)>>")
    raiz = doc.get_new_xref()
    doc.update_object(raiz, f"<</Type/StructTreeRoot/K {elemento} 0 R>>")
    doc.xref_set_key(doc.pdf_catalog(), "StructTreeRoot", f"{raiz} 0 R")
    doc.save(destino)
    return destino


@pytest.mark.parametrize("camino", ["digital", "hibrido"])
def test_el_actualtext_no_guarda_una_copia_del_dato_tachado(camino, caminos, tmp_path):
    """El /ActualText del contenido marcado sobrevivia al tachado: los glifos se
    iban, pero copiar el texto o un lector de pantalla devolvian el DNI."""
    entrada = _pdf_etiquetado(tmp_path / "etiquetado.pdf")
    salida = tmp_path / "salida.pdf"
    caminos[camino](entrada, salida)
    assert "48291736K" not in fitz.open(salida)[0].get_text()
    todo = presentes(salida, ["48291736K", "ActualText", "StructTreeRoot"])
    assert todo == []
    assert "Resolucion del expediente" in fitz.open(salida)[0].get_text()



def test_quitar_copias_de_texto_respeta_el_resto_del_flujo():
    flujo = (b"q /Span <</Lang (es) /ActualText (DNI \\) (anidado) 48291736K) /MCID 3>> BDC "
             b"BT (x) Tj ET EMC /P <</Alt <FEFF0034>>> BDC EMC "
             b"/E 12 Tf <</ActualText (no es BDC)>> Q")
    limpio = quitar_copias_de_texto(flujo)
    assert b"48291736K" not in limpio and b"FEFF0034" not in limpio
    assert b"/Lang (es)" in limpio and b"/MCID 3" in limpio
    assert b"/E 12 Tf" in limpio, "un nombre de recurso /E fuera de un BDC no se toca"
    assert b"(no es BDC)" in limpio, "un diccionario que no es de contenido marcado no se toca"
    assert quitar_copias_de_texto(b"BT (sin marcado) Tj ET") == b"BT (sin marcado) Tj ET"
