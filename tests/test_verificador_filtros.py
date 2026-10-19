"""El verificador: la pagina entera y todos los tipos, con los filtros del tachado.

Lee sin limitar tipos por zona (las fugas de estas semanas estaban en esas
limitaciones: telefonos y direcciones en cabecera y pie, nombres en el pie), pero
con el mismo texto y el mismo filtro institucional que el tachado.  Antes daba
falsas alarmas: el contacto del membrete, y palabras de celdas distintas que la
pagina ordenada juntaba en un "nombre" ("Acero Guantes").
"""
import fitz

from app.services.pii.analyzer import PiiAnalyzer
from app.services.verification.layers import check_textual

analyzer = PiiAnalyzer()


def _tipos(ruta):
    return [e.entity_type for e in check_textual(ruta, 0, analyzer, "strict").entities_found]


def _pagina(tmp_path, lineas):
    ruta = tmp_path / "salida.pdf"
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    for x, y, texto, fs in lineas:
        p.insert_text((x, y), texto, fontsize=fs)
    doc.save(ruta)
    return ruta


_MEMBRETE = [(50, 30, "Ajuntament de Sueca", 9), (50, 42, "Plaça Major, 1, 46410 Sueca", 8),
             (50, 54, "Tel. 96 170 05 00", 8)]
_CUERPO = [(50, 300, "Por la presente se le comunica la resolucion del expediente de", 10),
           (50, 314, "referencia, que se adjunta a esta notificacion para su conocimiento.", 10)]


def test_el_contacto_del_membrete_no_es_un_residuo(tmp_path):
    tipos = _tipos(_pagina(tmp_path, _MEMBRETE + _CUERPO))
    assert not {"PHONE_NUMBER", "ADDRESS", "POSTAL_CODE"} & set(tipos), tipos


def test_un_telefono_personal_en_el_pie_si_lo_es(tmp_path):
    """Sin limitar tipos por zona: el pie se mira con todos los tipos."""
    pie = [(50, 800, "Persona de contacto: Vicent Peris Llopis, movil 655 123 456", 8)]
    assert "PHONE_NUMBER" in _tipos(_pagina(tmp_path, _MEMBRETE + _CUERPO + pie))


def test_palabras_de_celdas_distintas_no_forman_un_nombre(tmp_path):
    """Presupuesto de seguridad y salud: una columna de partidas y otra de
    materiales.  Ordenada, la pagina juntaba "Acero" y "Guantes"."""
    filas = [(50, 120 + 16 * k, a, 9) for k, a in enumerate(["Casco", "Guantes", "Calzado"])]
    filas += [(300, 120 + 16 * k, b, 9) for k, b in enumerate(["Acero", "Cuero", "Goma"])]
    assert "PERSON" not in _tipos(_pagina(tmp_path, _CUERPO + filas))


def test_una_frase_tras_una_etiqueta_vacia_no_es_un_numero_de_expediente():
    """En la salida, "Expediente:" se queda sin valor (se tacho) y la linea
    siguiente es una frase: no es un numero de expediente, que siempre lleva cifras."""
    texto = "Expediente:\nEste documento contiene datos personales ficticios para probar la anonimización.\n"
    tipos = [e.entity_type for e in analyzer.analyze(texto, mode="strict")]
    assert "CASE_NUMBER" not in tipos



def test_en_una_carta_corta_el_telefono_personal_del_pie_tambien(tmp_path):
    """Con una sola linea de cuerpo, el filtro tomaba el pie por membrete (cabia en
    las primeras lineas de la pagina) y el verificador, que comparte el filtro,
    tampoco veia el movil del particular."""
    cuerpo = [(50, 300, "Por la presente se le comunica la resolucion del expediente de referencia.", 10)]
    pie = [(50, 800, "Persona de contacto: Vicent Peris Llopis, movil 655 123 456", 8)]
    assert "PHONE_NUMBER" in _tipos(_pagina(tmp_path, _MEMBRETE + cuerpo + pie))
