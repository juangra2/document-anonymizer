"""Imagenes pegadas en una pagina con texto: se leen recortadas y se tachan en su sitio.

Casos dificiles con un DNI real (no van al repositorio; ver
test_casos_dificiles_reales.py): a mitad de escala salia entero (la pagina no
iba al OCR), girado 90 grados tambien (el OCR leia la pagina derecha), y a
tamano real se tapaba la pagina entera, oficio incluido.  Aqui, con un OCR
falso y datos inventados: lee la imagen apaisada y da ruido si esta de lado,
como el OCR real con una tarjeta girada.
"""
import fitz
from PIL import Image, ImageDraw

from app.services.ocr.models import OcrPage, OcrToken
from app.services.pdf.imagenes_pegadas import _deshacer_giro, revisar_imagenes
from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()

_COPIA = ["REINO DE ESPAÑA", "DOCUMENTO NACIONAL DE IDENTIDAD", "APELLIDOS", "MARTIN DELGADO",
          "NOMBRE", "PAULA", "DNI 69026956T"]
_CAPTURA = ["Persona de contacto: Vicent Peris Llopis", "Teléfono de contacto: 655 123 456"]


def _lector(lineas):
    """OCR falso: en apaisado lee `lineas`, una por fila; de lado, ruido."""
    def leer(img: Image.Image) -> OcrPage:
        w, h = img.size
        if w >= h:
            alto = h / (len(lineas) + 1)
            tokens = [OcrToken(t, 0.9, fitz.Rect(10, alto * k + 5, 10 + 9 * len(t), alto * (k + 1)))
                      for k, t in enumerate(lineas)]
        else:
            tokens = [OcrToken(c, 0.2, fitz.Rect(5, 12 * k, 15, 12 * k + 10))
                      for k, c in enumerate("x/;I:l.,|'")]
        return OcrPage(page_index=0, width=w, height=h, tokens=tokens)
    return leer


def _tarjeta(girada=False) -> bytes:
    img = Image.new("RGB", (600, 380), (225, 215, 200))
    ImageDraw.Draw(img).rectangle([20, 20, 160, 200], fill=(120, 110, 100))
    if girada:
        img = img.rotate(90, expand=True)
    import io
    b = io.BytesIO()
    img.save(b, format="PNG")
    return b.getvalue()


def _pagina(rect, girada=False):
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_textbox(fitz.Rect(60, 60, 535, 200),
                     "Se incorpora al expediente la copia del documento aportada por la persona "
                     "interesada, a efectos de su cotejo con el original. " * 2, fontsize=10)
    p.insert_image(rect, stream=_tarjeta(girada))
    return doc, p


def _caja(p):
    """Donde ha quedado la imagen (insert_image conserva la proporcion)."""
    return fitz.Rect(p.get_image_info()[0]["bbox"])


def test_una_copia_a_mitad_de_escala_se_tapa_entera():
    """Zona gris: 1,85 % de la pagina, por debajo del 5 % que manda la pagina al OCR."""
    rect = fitz.Rect(240, 300, 361, 376)
    _, p = _pagina(rect)
    r = revisar_imagenes(p, analyzer, leer=_lector(_COPIA))
    assert len(r) == 1 and r[0].copia and r[0].entera
    assert r[0].rects == [_caja(p)]


def test_una_copia_girada_se_endereza_y_se_tapa_entera():
    rect = fitz.Rect(220, 280, 373, 523)          # de lado en la pagina
    _, p = _pagina(rect, girada=True)
    r = revisar_imagenes(p, analyzer, leer=_lector(_COPIA))
    assert r[0].giro in (90, 270)
    assert r[0].copia and r[0].rects == [_caja(p)]


def test_una_imagen_que_no_es_copia_solo_tacha_sus_datos_en_su_sitio():
    rect = fitz.Rect(100, 300, 500, 400)
    _, p = _pagina(rect)
    r = revisar_imagenes(p, analyzer, leer=_lector(_CAPTURA))
    assert len(r) == 1 and not r[0].copia and not r[0].entera
    tipos = {e.entity_type for e in r[0].entidades}
    assert {"PERSON", "PHONE_NUMBER"} <= tipos
    assert r[0].rects and all(rect.contains(x) or rect.intersects(x) for x in r[0].rects)
    # La barra cubre una parte de la imagen, no la imagen entera.
    assert all(x.get_area() < 0.5 * rect.get_area() for x in r[0].rects)


def test_un_logo_no_se_lee():
    _, p = _pagina(fitz.Rect(60, 30, 100, 55))    # 0,2 % de la pagina
    assert revisar_imagenes(p, analyzer, leer=_lector(_COPIA)) == []


def test_deshacer_el_giro_devuelve_las_coordenadas_originales():
    ancho, alto = 600, 380
    original = fitz.Rect(50, 30, 200, 90)
    img = Image.new("L", (ancho, alto), 255)
    ImageDraw.Draw(img).rectangle([original.x0, original.y0, original.x1 - 1, original.y1 - 1], fill=0)
    for giro in (90, 180, 270):
        girada = img.rotate(giro, expand=True)
        caja = fitz.Rect(Image.eval(girada, lambda v: 255 - v).getbbox())
        vuelta = _deshacer_giro(caja, giro, ancho, alto)
        assert all(abs(a - b) <= 1.5 for a, b in zip(vuelta, original)), (giro, vuelta)


def test_una_copia_tapada_entera_informa_de_los_datos_que_lleva():
    """Medicion c4, corpus hibrido: la copia de DNI pegada se tapaba entera (bien)
    pero el informe solo decia "una copia", sin los datos de dentro.  Quien lea el
    informe, y el evaluador, tienen que ver que se taparon un nombre y un DNI."""
    _, p = _pagina(fitz.Rect(240, 300, 361, 376))
    r = revisar_imagenes(p, analyzer, leer=_lector(_COPIA))
    tipos = {e.entity_type for e in r[0].entidades}
    assert r[0].copia and {"PERSON", "ES_DNI"} <= tipos
    assert any(e.text == "69026956T" for e in r[0].entidades)



def test_una_copia_con_texto_encima_se_tapa_entera(tmp_path, monkeypatch):
    """Casos dificiles con un DNI real: la copia con un sello encima, o cruzada por
    una linea del oficio, salia tapada solo entre un 9 y un 19 %.  El ajuste de las
    barras a las letras (para no morder etiquetas) reducia la marca de la copia
    entera a la altura de esa linea de texto.  La marca de una imagen no es una
    barra de texto y no se ajusta."""
    from app.services.pdf import imagenes_pegadas
    from app.services.pdf.redactor import redact_pdf_digital

    monkeypatch.setattr(imagenes_pegadas, "lector_por_defecto", lambda: _lector(_COPIA))
    entrada, salida = tmp_path / "in.pdf", tmp_path / "out.pdf"
    doc, p = _pagina(fitz.Rect(150, 300, 450, 490))
    p.insert_text((160, 420), "ES COPIA FIEL DEL ORIGINAL - COMPULSADO", fontsize=8)
    doc.save(entrada)
    redact_pdf_digital(entrada, salida, "t", analyzer)
    out = fitz.open(salida)[0]
    caja = fitz.Rect(out.get_image_info()[0]["bbox"]) if out.get_image_info() else fitz.Rect(150, 300, 450, 490)
    pix = out.get_pixmap(dpi=40, clip=caja, colorspace=fitz.csGRAY)
    negro = sum(1 for v in pix.samples if v < 45) / len(pix.samples)
    assert negro > 0.9, f"la copia solo esta tapada al {negro:.0%}"
