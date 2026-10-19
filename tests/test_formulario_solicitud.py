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


# --- 5. la etiqueta encima del valor no se muerde ----------------------------------

def test_la_etiqueta_justo_encima_del_valor_sale_entera(tmp_path):
    """Al tachar, PyMuPDF borra todo caracter cuya caja TOQUE la barra, y la caja
    usa el alto completo de la fuente.  Con la etiqueta pequena justo encima del
    valor (formulario real), la barra del valor se llevaba el pie de la etiqueta:
    "BSERVACIONES", "LLIDOS", "ECIBO"."""
    from app.services.pdf.redactor import redact_pdf_digital

    entrada, salida = tmp_path / "in.pdf", tmp_path / "out.pdf"
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    filas = [("NIF/NIE", "69026956T"), ("COGNOMS / APELLIDOS", "MARTIN DELGADO"),
             ("OBSERVACIONS / OBSERVACIONES", "PAULA MARTIN DELGADO"),
             ("NÚM. REBUT / Nº RECIBO", "C071-2-55821")]
    for k, (etiqueta, valor) in enumerate(filas):
        y = 200 + 34 * k
        p.insert_text((70, y), etiqueta, fontsize=6.5)
        p.insert_text((70, y + 9), valor, fontsize=9)
    doc.save(entrada)

    redact_pdf_digital(entrada, salida, "t", analyzer)
    texto = fitz.open(salida)[0].get_text()
    for etiqueta, valor in filas:
        assert etiqueta in texto, f"etiqueta mordida: {etiqueta}"
    for valor in ("69026956T", "MARTIN DELGADO", "C071-2-55821"):
        assert valor not in texto, f"sigue a la vista: {valor}"


# --- texto de OCR: las etiquetas seguidas y los valores despues --------------------
# En la pasada OCR del formulario real (la pagina va al OCR por el escudo de
# fondo) el texto no sigue el orden de la capa digital: las etiquetas pueden ir
# seguidas y los valores despues.  Se tachaban lineas de etiqueta enteras, el
# codigo del curso y el "11/2007" de la ley del pie.

def test_una_etiqueta_no_es_el_valor_de_la_etiqueta_de_encima():
    texto = ("PROVINCIA NAIXEMENT / PROVINCIA NACIMIENTO\n"
             "MUNICIPI NAIXEMENT / MUNICIPIO NACIMIENTO\n"
             "OBSERVACIONS / OBSERVACIONES\n"
             "D. NAIXEMENT / F. NACIMIENTO\n")
    for etiqueta in ("MUNICIPI NAIXEMENT", "OBSERVACIONS", "D. NAIXEMENT"):
        assert "PLACE_OF_BIRTH" not in _tipos(texto, etiqueta), etiqueta


def test_el_registro_no_pesca_un_numero_lejano_ni_una_fecha():
    texto = ("Núm. registre:\nNº registro:\nData de\npresentació:\n"
             "CURS ACADÈMIC/TITULACIÓ\n02262 - Máster Universitario\n"
             "els articles 24 i següents de la Llei 11/2007, les sol·licituds\n")
    assert "REGISTRY_NUMBER" not in _tipos(texto, "02262")
    assert "REGISTRY_NUMBER" not in _tipos(texto, "11/2007")


def test_el_registro_salta_solo_etiquetas_hasta_su_valor():
    texto = "Núm. registre:\nNº registro:\nUV-SEU-0000000731905\n"
    assert "REGISTRY_NUMBER" in _tipos(texto, "UV-SEU-0000000731905")


def test_tras_nom_o_cognoms_otra_etiqueta_no_es_un_nombre():
    """Pasada OCR del formulario real: el detector de campo sin dos puntos tomaba
    por nombre la linea que sigue a "COGNOMS / APELLIDOS", que en el orden del OCR
    era otra etiqueta.  Se tachaban lineas de etiqueta enteras."""
    texto = ("NOM / NOMBRE\nCOGNOMS / APELLIDOS\nD. NAIXEMENT / F. NACIMIENTO\n"
             "PROVINCIA NAIXEMENT / PROVINCIA NACIMIENTO\nDESCRIPCIÓ / DESCRIPCIÓN\n")
    for etiqueta in ("COGNOMS", "D. NAIXEMENT", "PROVINCIA NAIXEMENT", "DESCRIPCIÓ"):
        assert "PERSON" not in _tipos(texto, etiqueta), etiqueta


def test_tras_nom_y_cognoms_el_valor_si_es_un_nombre():
    texto = "NOM / NOMBRE\nPAULA\nCOGNOMS / APELLIDOS\nMARTIN DELGADO\n"
    assert "PERSON" in _tipos(texto, "MARTIN DELGADO")


def test_tras_nom_un_valor_sin_nombre_ni_apellido_no_es_persona():
    """Formulario real: "Persona" del epigrafe del pie se tomaba por etiqueta y
    lo que seguia, por un nombre.  Sin un nombre o un apellido del padron no lo
    es (corpus digital: 26 descartes de este detector, ningun nombre real)."""
    texto = "Entrada electrònica de\nPersona y Entidad\n"
    assert "PERSON" not in _tipos(texto, "y Entidad")


def test_un_numero_suelto_corto_no_es_un_registro():
    """En el orden del OCR, tras "Nº RECIBO" iba el bloque del curso: "02262 -
    Máster..." acababa tachado como numero de recibo."""
    texto = ("NÚM. REBUT / Nº RECIBO\nCURS ACADÈMIC/TITULACIÓ / CURSO ACADÉMICO/TITULACIÓN\n"
             "02262 - Máster Universitario en Ciencia de Datos\n")
    assert "REGISTRY_NUMBER" not in _tipos(texto, "02262")


# --- camino escaneado: celdas de tabla de MinerU -------------------------------------
# Expediente escaneado real: MinerU devuelve la casilla como una celda con el
# rotulo y el valor separados por <br>, y el lector de tablas los pegaba sin
# separador ("RECIBO" + numero, "FECHA" + fecha, "IMPORTE" + importe).  El
# numero de recibo no casaba despues con ninguna palabra de la imagen y no se
# tachaba; una fecha pegada a letras ni se detectaba.

def test_el_rotulo_y_el_valor_de_una_celda_no_se_pegan():
    from app.services.ocr.mineru_engine import _parse_html_table_tokens

    html = ("<table><tr><td>NÚM. REBUT / Nº RECIBO<br>C071-2-55821</td>"
            "<td>DATA / FECHA<br>12/03/2026</td><td>IMPORT / IMPORTE<br>148.35 €</td></tr></table>")
    texto = " ".join(t.text for t in _parse_html_table_tokens(html, fitz.Rect(0, 0, 500, 40)))
    assert "RECIBOC071" not in texto and "FECHA12" not in texto and "IMPORTE148" not in texto
    for valor, tipo in (("C071-2-55821", "REGISTRY_NUMBER"), ("12/03/2026", "DATE_TIME"),
                        ("148.35", "MONEY")):
        assert tipo in _tipos(texto, valor), valor


def test_mineru_pega_el_valor_al_rotulo_bilingue_y_se_despega():
    """El propio MinerU devolvia la casilla sin separador: "NOM / NOMBREPEDRO",
    "NIF/NIE69026956T", "Nº RECIBOC071-2-55821".  Se despega solo tras la ultima
    palabra de un rotulo bilingue, nunca dentro de una palabra suelta."""
    from app.services.ocr.mineru_engine import _parse_html_table_tokens

    html = ("<table><tr><td>NIF/NIE69026956T</td></tr><tr><td>NOM / NOMBREPEDRO</td></tr>"
            "<tr><td>COGNOMS / APELLIDOSMARTIN DELGADO</td></tr>"
            "<tr><td>D. NAIXEMENT / F. NACIMIENTO12/03/1990</td></tr>"
            "<tr><td>NÚM. REBUT / N° RECIBOC071-2-55821</td><td>DATA / FECHA12/03/2026</td>"
            "<td>IMPORT / IMPORTE148.35 €</td></tr>"
            "<tr><td>Es IMPORTANTE presentar la DATACION del recibo</td></tr></table>")
    texto = "\n".join(t.text for t in _parse_html_table_tokens(html, fitz.Rect(0, 0, 500, 200)))
    for valor, tipo in (("69026956T", "ES_DNI"), ("PEDRO", "PERSON"), ("MARTIN DELGADO", "PERSON"),
                        ("C071-2-55821", "REGISTRY_NUMBER"), ("148.35", "MONEY")):
        assert tipo in _tipos(texto, valor), valor
    assert "IMPORTANTE" in texto and "DATACION" in texto


def test_el_rotulo_no_entra_en_el_nombre():
    """Con el valor despegado, la fila llega como "NOM / NOMBRE PEDRO", y el nombre
    salia como "NOMBRE PEDRO": la barra se llevaba el rotulo."""
    texto = "NOM / NOMBRE PEDRO\nCOGNOMS / APELLIDOS MARTIN DELGADO\n"
    personas = [texto[e.start:e.end] for e in analyzer.analyze(texto, mode="strict", context_text=texto)
                if e.entity_type == "PERSON"]
    assert personas and not any(p.split()[0] in ("NOMBRE", "APELLIDOS", "NOM", "COGNOMS") for p in personas)
    assert any("PEDRO" in p for p in personas) and any("MARTIN DELGADO" in p for p in personas)


# --- camino escaneado: la barra dentro de una linea larga ------------------------
# Oficio escaneado real: el OCR leyo la frase entera como una sola caja, y la
# barra del dato se calculaba repartiendo el ancho a partes iguales entre los
# caracteres.  Mayusculas y cifras son mas anchas que la media: la barra empezaba
# tarde y dejaba a la vista el nombre de pila y las dos primeras cifras del NIF.

def test_la_barra_de_un_dato_dentro_de_una_linea_larga_lo_cubre_desde_su_inicio():
    from types import SimpleNamespace
    from PIL import ImageFont
    from app.services.pdf.scanned_redactor import _subrect_for_line_token, _content_tokens

    fuente = ImageFont.load_default(size=30)
    linea = ("Vista la solicitud de expedición de título presentada por PEDRO MARTIN DELGADO, "
             "con NIF 69026956T y examinada la documentación aportada")
    caja = fitz.Rect(100, 50, 100 + fuente.getlength(linea), 85)
    palabra = SimpleNamespace(text=linea, rect=caja)
    for dato in ("PEDRO MARTIN DELGADO", "69026956T"):
        inicio = caja.x0 + fuente.getlength(linea[:linea.index(dato)])
        fin = caja.x0 + fuente.getlength(linea[:linea.index(dato) + len(dato)])
        barra = _subrect_for_line_token(_content_tokens(dato), palabra)
        assert barra is not None and barra.x0 <= inicio and barra.x1 >= fin, (dato, barra, inicio, fin)


# --- registro de entrada o salida sin "Nº" delante -----------------------------------
# Caso dificil (oficio con la copia del DNI): "Registre d'entrada: 2026/118430" en
# la cabecera no se tachaba; la etiqueta del registro exigia "Nº" o "Núm.".

def test_registro_de_entrada_o_salida_sin_numero_delante():
    for etiqueta in ("Registre d'entrada:", "Registre d’eixida:", "Registro de entrada:",
                     "Registro de salida:", "Registro General de Entrada:", "Reg. entrada:",
                     "Registre general d'entrada:", "Registre de sortida:"):
        texto = f"AJUNTAMENT DE VALÈNCIA\n{etiqueta} 2026/118430\n"
        assert "REGISTRY_NUMBER" in _tipos(texto, "2026/118430"), etiqueta


def test_el_registro_en_prosa_sigue_sin_disparar():
    texto = "Se presentó en el registro de entrada del Ayuntamiento el día 12 de marzo de 2026.\n"
    assert "REGISTRY_NUMBER" not in _tipos(texto, "2026")


def test_en_evaluacion_el_informe_dice_que_nombres_descarto_la_regla_del_padron(tmp_path):
    """Para medir el riesgo de la regla en el escaneado y el hibrido: con
    include_entities (solo evaluacion y auditoria) el informe lista lo que el
    detector de nombres sin dos puntos descarto por no llevar nombre del padron."""
    from app.services.pdf.pipeline import PdfAnonymizationPipeline

    entrada = tmp_path / "in.pdf"
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((60, 100), "Entrada electrònica de", fontsize=10)
    p.insert_text((60, 114), "Persona y Entidad", fontsize=10)
    p.insert_text((60, 160), "Texto corriente del documento, sin datos personales de nadie.", fontsize=10)
    doc.save(entrada)
    pipe = PdfAnonymizationPipeline()
    con = pipe.process(input_path=entrada, output_path=tmp_path / "a.pdf", job_id="t", collect_entities=True)
    sin = pipe.process(input_path=entrada, output_path=tmp_path / "b.pdf", job_id="t")
    assert any("Entidad" in v for v in con.descartes_padron)
    assert sin.descartes_padron == []


def test_un_iban_partido_en_dos_lineas_de_una_celda_se_tacha(tmp_path):
    """Medicion c4: con las cajas de letra ajustadas tambien al EXTRAER, el IBAN de
    una nomina, partido en dos lineas de su celda y desplazado respecto a la
    etiqueta, quedaba en dos zonas y no se detectaba.  Las cajas ajustadas solo
    se usan al aplicar las tachaduras."""
    from app.services.pdf.redactor import redact_pdf_digital

    entrada, salida = tmp_path / "in.pdf", tmp_path / "out.pdf"
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    # Geometria de la nomina del corpus: etiqueta y valor en columnas, y el
    # IBAN partido en dos lineas desplazadas respecto a la de la etiqueta.
    p.insert_text((67.2, 376.9), "Móvil/Tel.:", fontsize=9.5)
    p.insert_text((164.0, 376.9), "(667) 248-578", fontsize=9.5)
    p.insert_text((337.0, 376.9), "Núm. cuenta:", fontsize=9.5)
    p.insert_text((423.8, 370.0), "ES71 3058 2097 1034", fontsize=9.5)
    p.insert_text((423.8, 383.8), "7979 0013", fontsize=9.5)
    p.insert_text((67, 420), "Por la presente se comunica el abono de la nomina del mes.", fontsize=9.5)
    doc.save(entrada)

    redact_pdf_digital(entrada, salida, "t", analyzer)
    texto = fitz.open(salida)[0].get_text()
    assert "3058" not in texto and "7979" not in texto


# --- telefono partido entre dos lineas ----------------------------------------------
# Medicion c4, hibrido: "Nº teléfono:60" en una linea y "885 718 1" en la siguiente,
# en zonas distintas.  Cada zona se analiza sola y ninguna veia el numero entero.
# En c3 se tachaba porque la pagina iba entera al OCR; ahora sigue siendo digital.

def test_un_telefono_partido_tras_su_etiqueta_se_detecta_en_cada_zona():
    """Cada linea es una zona: la primera solo lleva "65" y la segunda el resto.
    Con la pagina como contexto, cada zona recibe su trozo del telefono, y las
    cifras de las dos quedan dentro de una barra (en la primera, la direccion
    etiquetada llega hasta el final de la linea y se lo lleva)."""
    primera = "Domicilio fiscal:Ronda de Valencia, 116, 28003 Madrid Nº teléfono:65"
    segunda = "412 908 7 E-mail:paula.mendez@example.com"
    pagina = "INFORME\n" + primera + "\n" + segunda + "\nTexto corriente.\n"
    for zona, trozo in ((primera, "65"), (segunda, "412 908 7")):
        cubierto = set()
        for e in analyzer.analyze(zona, mode="strict", context_text=pagina):
            cubierto |= set(range(e.start, e.end))
        ini = zona.rindex(trozo) if trozo == "65" else zona.index(trozo)
        assert set(range(ini, ini + len(trozo))) <= cubierto, trozo
    segunda_tipos = {e.entity_type for e in analyzer.analyze(segunda, mode="strict", context_text=pagina)}
    assert "PHONE_NUMBER" in segunda_tipos


def test_el_telefono_partido_tras_movil_tel():
    """Lectura de barras c4 (hibrido): "Móvil/Tel.:+34 950 314" / "671"."""
    primera = "Domicilio:Pza. de la Cruz, 120, 3º C, 29010 Málaga Móvil/Tel.:+34 950 314"
    segunda = "671 Email:sergio.morales@example.com Cuenta bancaria:"
    pagina = "INFORME\n" + primera + "\n" + segunda + "\nTexto corriente.\n"
    tipos = {e.entity_type for e in analyzer.analyze(segunda, mode="strict", context_text=pagina)
             if segunda[e.start:e.end].strip() == "671"}
    assert "PHONE_NUMBER" in tipos
