"""El contacto de la institucion emisora no se tacha, tampoco en un escaneado.

filter_institutional aparta el telefono, la direccion y el codigo postal de quien
emite el documento (membrete, o justo despues del nombre de la institucion sin
una persona en medio).  Solo lo aplicaba el camino digital: en el escaneado se
tachaba el membrete entero —"Plaça Major, 1, 46410 Sueca", "96 170 05 00"—, la
sobre-redaccion de la que se quejo el ayuntamiento.  Eran 66 falsos positivos en
los corpus escaneado e hibrido.

Ahora lo aplica el RedactionPlan, el punto unico de salida, en los dos caminos:
un solo filtro, no dos copias que se desincronicen.

Y el riesgo contrario, que pesa mas: el destinatario de una carta va arriba, en
la misma franja que el membrete.  Si el OCR estropea su nombre y no se detecta
ninguna persona, su direccion pareceria la de la institucion y se entregaria a
la vista.  En la duda, se tacha: un tratamiento ("Sr.", "Sra.", "D.", "Dña.",
"A/A"...) entre la institucion y el dato cuenta como persona aunque el nombre no
se haya detectado.
"""
import fitz

from app.services.layout.models import PageZone, ZonedPage, ZoneType
from app.services.layout.segmenters import _build_ocr_zone_text_and_tokens
from app.services.ocr.models import OcrToken
from app.services.pdf.scanned_redactor import _collect_redaction_rects
from app.services.pii.analyzer import PiiAnalyzer
from app.services.pii.institution_filter import filter_institutional
from app.services.pii.models import PiiEntity

analyzer = PiiAnalyzer()


def _zona(tipo: ZoneType, lineas: list[str], y0: int) -> PageZone:
    # Un fragmento por linea, como los bloques que devuelve MinerU.
    palabras = [OcrToken(text=linea, confidence=1.0,
                         rect=fitz.Rect(100, y0 + 40 * i, 100 + 14 * len(linea), y0 + 40 * i + 30))
                for i, linea in enumerate(lineas)]
    texto, tokens = _build_ocr_zone_text_and_tokens(palabras)
    marco = fitz.Rect(palabras[0].rect)
    for p in palabras[1:]:
        marco |= p.rect
    return PageZone(zone_type=tipo, rect=marco, text=texto, tokens=tokens, source="ocr")


def _tachado(zonas: list[PageZone]) -> set[str]:
    _, entidades = _collect_redaction_rects(ZonedPage(page_index=0, width=1654, height=2340,
                                                      zones=zonas), analyzer, "strict")
    return {" ".join(e.text.split()) for e in entidades}


_MEMBRETE = ["Ajuntament de Sueca", "Plaça Major, 1, 46410 Sueca", "Tel. 96 170 05 00"]


def test_el_membrete_de_un_escaneado_no_se_tacha():
    t = _tachado([_zona(ZoneType.BODY, _MEMBRETE, 100)])
    assert "96 170 05 00" not in t
    assert "Plaça Major, 1, 46410 Sueca" not in t


def test_el_telefono_de_un_particular_en_el_cuerpo_si():
    t = _tachado([_zona(ZoneType.BODY, _MEMBRETE, 100),
                  _zona(ZoneType.BODY, ["El interesado, con telefono 612 345 678, solicita"], 600)])
    assert "612 345 678" in t


def test_el_destinatario_de_la_carta_se_tacha():
    t = _tachado([_zona(ZoneType.BODY, _MEMBRETE + ["Sra. Xelo Brufau Ivars",
                                                      "C/ Mayor, 3, 46410 Sueca"], 100)])
    assert "C/ Mayor, 3, 46410 Sueca" in t


def test_el_destinatario_con_el_nombre_estropeado_por_el_ocr_tambien():
    """El OCR no deja detectar el nombre: el tratamiento basta."""
    t = _tachado([_zona(ZoneType.BODY, ["Ajuntament de Sueca", "Sra. Xrlo Brvfau lvars",
                                          "C/ Mayor, 3, 46410 Sueca"], 100)])
    assert "C/ Mayor, 3, 46410 Sueca" in t


def test_el_mismo_filtro_en_digital():
    """La regla del tratamiento vive en filter_institutional: vale para los dos caminos."""
    texto = "Ajuntament de Sueca\nSra. Xrlo Brvfau lvars\nC/ Mayor, 3, 46410 Sueca"
    i = texto.index("C/ Mayor")
    direccion = PiiEntity("ADDRESS", i, len(texto), 0.8, text=texto[i:])
    zp = ZonedPage(page_index=0, width=595, height=842, zones=[])
    assert filter_institutional(texto, zp, [direccion]) == set()


def test_un_parrafo_no_es_membrete_aunque_sea_de_las_primeras_lineas():
    """MinerU devuelve un parrafo como una sola 'linea': las cinco primeras
    lineas de la pagina pueden alcanzar el cuerpo.  Un membrete son lineas
    cortas; un parrafo largo ya no lo es, y su telefono se tacha."""
    t = _tachado([_zona(ZoneType.BODY, _MEMBRETE + [
        "Le comunicamos que su solicitud ha sido admitida a tramite y que, para "
        "cualquier aclaracion sobre el expediente, puede llamar al 612 345 678."], 100)])
    assert "612 345 678" in t


# --- una etiqueta de dato personal entre la institucion y el dato -------------------
# Caso real (nomina_0007): "Centro de trabajo: Universitat Mediterrània d'Elx /
# Antigüedad / 12 de febrer de 2015 / Domicilio fiscal: Paseo del Sol, 75, 2º B,
# 18001 Granada / Tfno.: +34 796 202 675".  Sin ninguna persona detectada en
# medio, el filtro tomaba el domicilio y el telefono del trabajador por los de la
# universidad y los dejaba a la vista.  La etiqueta dice de quien es el dato.

def _suprimidos(texto, valores):
    ents = []
    for tipo, v in valores:
        i = texto.index(v)
        ents.append(PiiEntity(tipo, i, i + len(v), 0.8, text=v))
    zp = ZonedPage(page_index=0, width=595, height=842, zones=[])
    return {ents[i].text for i in filter_institutional(texto, zp, ents)}


def test_el_domicilio_fiscal_del_trabajador_no_es_de_la_universidad():
    texto = ("Centro de trabajo\nUniversitat Mediterrània d'Elx\nAntigüedad\n12 de febrer de 2015\n"
             "Domicilio fiscal:\nPaseo del Sol, 75, 2º B, 18001 Granada\nTfno.:\n+34 796 202 675")
    assert _suprimidos(texto, [("ADDRESS", "Paseo del Sol, 75, 2º B, 18001 Granada"),
                               ("PHONE_NUMBER", "+34 796 202 675")]) == set()


def test_el_domicilio_social_de_la_institucion_si():
    texto = "Universitat Mediterrània d'Elx\nDomicilio social: Avinguda de la Mediterrània, 100, 03202 Elx"
    assert _suprimidos(texto, [("ADDRESS", "Avinguda de la Mediterrània, 100, 03202 Elx")]) == {
        "Avinguda de la Mediterrània, 100, 03202 Elx"}


# --- el codigo postal de una direccion institucional ya apartada ----------------------
# El filtro apartaba la direccion de la institucion, pero el codigo postal de
# dentro va como entidad aparte y se quedaba: se tachaba solo el CP del membrete.

def test_el_codigo_postal_de_una_direccion_institucional_tambien_se_aparta():
    """solicitud_generica_0003: la direccion cae dentro de la ventana de cercania
    al nombre de la institucion, pero su codigo postal, 30 caracteres despues, no."""
    texto = ("Ayuntamiento de Cartagena Sur con la finalidad de gestionar su solicitud. Puede "
             "ejercer sus\nderechos dirigiéndose a Calle del Consistorio, 14, 30201 Cartagena")
    assert _suprimidos(texto, [("ADDRESS", "Calle del Consistorio, 14, 30201 Cartagena"),
                               ("POSTAL_CODE", "30201")]) == {
        "Calle del Consistorio, 14, 30201 Cartagena", "30201"}


def test_el_codigo_postal_de_una_direccion_personal_no():
    texto = "Ajuntament de Sueca\nSra. Xelo Brufau Ivars, C/ Mayor, 3, 46410 Sueca"
    assert _suprimidos(texto, [("ADDRESS", "C/ Mayor, 3, 46410 Sueca"), ("POSTAL_CODE", "46410")]) == set()
