"""Formulario de solicitud: codigo de verificacion, registro, recibo, importe y
lugar de nacimiento.

Documento real (solicitud de titulo de una universidad): con la red ya
arreglada, seguian a la vista cuatro campos que el detector no reconocia, y la
provincia y el municipio de nacimiento, que no se tachaban nunca:

  - "Cód. Verificación" / "Cod. Verificació": el CSV solo reconocia "CSV" y
    "Código seguro de verificación";
  - "Núm. registre / Nº registro" y "Núm. rebut / Nº recibo": ninguna regla;
    el registro ademas partido entre lineas ("UV-" / "SEU-0000000446721");
  - "212.07 €": punto decimal, que el patron de importes no aceptaba.  Solo con
    el simbolo de euro detras, para no confundirlo con un separador de miles;
  - lugar de nacimiento: un lugar solo se tacha si su etiqueta dice que es el
    de nacimiento.  Fuera de esa etiqueta los lugares siguen a la vista.

La disposicion es la del real: etiqueta bilingue en una linea y valor debajo.
Todos los valores son inventados.
"""
import fitz

from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _tipos(texto, valor):
    ini = texto.index(valor)
    fin = ini + len(valor)
    return {e.entity_type for e in analyzer.analyze(texto, mode="strict", context_text=texto)
            if e.start < fin and e.end > ini}


# --- 1. codigo de verificacion -------------------------------------------------------

def test_cod_verificacion_en_castellano_y_valenciano():
    texto = "Cod. Verificació / Cód. Verificación:\nKQW7ZT0B4RA851MX\nhttps://sede.example.es/\n"
    assert "CSV" in _tipos(texto, "KQW7ZT0B4RA851MX")


def test_cod_verificacion_con_las_etiquetas_en_lineas_separadas():
    texto = "Cod.\nVerificació:\nCód.\nVerificación:\nKQW7ZT0B4RA851MX\nData de\n"
    assert "CSV" in _tipos(texto, "KQW7ZT0B4RA851MX")


# --- 2. numero de registro y de recibo -------------------------------------------------

def test_numero_de_registro_partido_entre_lineas():
    texto = "Núm. registre / Nº registro: UV-\nSEU-0000000731905\nVerificació:\n"
    tipos = _tipos(texto, "SEU-0000000731905")
    assert "REGISTRY_NUMBER" in tipos
    assert "REGISTRY_NUMBER" in _tipos(texto, "UV-")


def test_numero_de_registro_con_las_dos_etiquetas_seguidas():
    texto = "Núm. registre:\nNº registro:\nUV-SEU-0000000731905\nDestinació:\n"
    assert "REGISTRY_NUMBER" in _tipos(texto, "UV-SEU-0000000731905")


def test_numero_de_recibo():
    texto = "NÚM. REBUT / Nº RECIBO\nC071-2-55821\nDescripció / Descripción\n"
    assert "REGISTRY_NUMBER" in _tipos(texto, "C071-2-55821")


def test_la_palabra_registro_en_prosa_no_dispara_nada():
    texto = ("De conformidad con el Reglamento del Registro Electrónico, las solicitudes "
             "presentadas no requieren su presentación en un registro presencial. Ley 11/2007.\n")
    assert "REGISTRY_NUMBER" not in _tipos(texto, "11/2007")


# --- 3. importe con punto decimal ----------------------------------------------------

def test_importe_con_punto_decimal_y_euro():
    texto = "DADES PAGAMENT / DATOS PAGO\nIMPORT / IMPORTE\n148.35 €\n"
    assert "MONEY" in _tipos(texto, "148.35")


def test_punto_decimal_sin_euro_no_es_importe():
    """Sin el simbolo, "148.35" puede ser cualquier cosa (una version, una cota)."""
    texto = "IMPORT / IMPORTE\nversion 148.35 del formulario\n"
    assert "MONEY" not in _tipos(texto, "148.35")


# --- 4. lugar de nacimiento ----------------------------------------------------------

def test_provincia_y_municipio_de_nacimiento_se_tachan():
    texto = ("PROVINCIA NAIXEMENT / PROVINCIA NACIMIENTO\nTERUEL\n"
             "MUNICIPI NAIXEMENT / MUNICIPIO NACIMIENTO\nALCAÑIZ\n"
             "OBSERVACIONS / OBSERVACIONES\n")
    assert "PLACE_OF_BIRTH" in _tipos(texto, "TERUEL")
    assert "PLACE_OF_BIRTH" in _tipos(texto, "ALCAÑIZ")


def test_lugar_de_nacimiento_en_la_misma_linea():
    for texto, valor in (("Lugar de nacimiento: Cuenca\n", "Cuenca"),
                         ("Lloc de naixement: Gandia\n", "Gandia")):
        assert "PLACE_OF_BIRTH" in _tipos(texto, valor)


def test_un_lugar_fuera_de_esa_etiqueta_sigue_a_la_vista():
    texto = "La sesión se celebró en Teruel, en la sede de la Diputación.\n"
    assert "PLACE_OF_BIRTH" not in _tipos(texto, "Teruel")


# --- el formulario entero ------------------------------------------------------------

def test_el_formulario_sale_con_los_valores_tachados_y_las_etiquetas_a_la_vista(tmp_path):
    from app.services.pdf.redactor import redact_pdf_digital

    entrada, salida = tmp_path / "solicitud.pdf", tmp_path / "out.pdf"
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    lineas = [
        (420, 50, "Cod. Verificació / Cód. Verificación:"), (420, 62, "KQW7ZT0B4RA851MX"),
        (420, 80, "Núm. registre / Nº registro: UV-"), (420, 92, "SEU-0000000731905"),
        (60, 140, "DADES PERSONALS / DATOS PERSONALES"),
        (60, 170, "PROVINCIA NAIXEMENT / PROVINCIA NACIMIENTO"), (60, 182, "TERUEL"),
        (60, 210, "MUNICIPI NAIXEMENT / MUNICIPIO NACIMIENTO"), (60, 222, "ALCAÑIZ"),
        (60, 270, "DADES PAGAMENT / DATOS PAGO"),
        (60, 300, "NÚM. REBUT / Nº RECIBO"), (60, 312, "C071-2-55821"),
        (400, 300, "IMPORT / IMPORTE"), (400, 312, "148.35 EUR"),
    ]
    for x, y, t in lineas:
        p.insert_text((x, y), t, fontsize=8)
    doc.save(entrada)

    redact_pdf_digital(entrada, salida, "t", analyzer)
    texto = " ".join(fitz.open(salida)[0].get_text().split())
    for valor in ("KQW7ZT0B4RA851MX", "0000000731905", "TERUEL", "ALCAÑIZ", "C071-2-55821", "148.35"):
        assert valor not in texto, f"sigue a la vista: {valor}"
    for etiqueta in ("Cód. Verificación", "Nº registro", "PROVINCIA NACIMIENTO",
                     "MUNICIPIO NACIMIENTO", "Nº RECIBO", "IMPORTE", "DATOS PAGO"):
        assert etiqueta in texto, f"se tacho la etiqueta: {etiqueta}"
