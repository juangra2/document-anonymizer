"""En el digital se tachan TODAS las apariciones de un dato detectado.

Un dato se detecta en un sitio y vuelve a aparecer en otros donde por si solo
no se detectaria: tambien ahi se tacha, como en el camino escaneado.  Los tres
casos tipicos:

  - el nombre en el cuerpo ("Nombre y apellidos: <nombre>") y otra vez solo en
    el bloque de firma, en la franja del pie;
  - la misma direccion en la otra columna, partida en dos lineas;
  - el importe de la nomina en la pagina 1 y repetido en la 2.
"""
import fitz

from app.services.pdf.redactor import redact_pdf_digital
from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _documento(ruta):
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((58, 300), "Nombre y apellidos: David Álvarez Suárez DNI: Z4854145T", fontsize=9.5)
    p.insert_text((58, 330), "Total devengado: 4.860,00 euros", fontsize=9.5)
    p.insert_text((79, 770), "David Álvarez Suárez", fontsize=8.5)            # firma, en el pie
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((57, 300), "de Robòtica Aplicada i Dades Sanitàries, Calle de", fontsize=9.5)
    p2.insert_text((57, 314), "Cervantes, 96, 5º C, 28013 Madrid.", fontsize=9.5)
    p2.insert_text((311, 314), "Domicilio: Calle de Cervantes, 96, 5º C, 28013 Madrid.", fontsize=9.5)
    p2.insert_text((58, 400), "Base cotización CC: 4.860,00 € • Base IRPF: 4.860,00 €", fontsize=8)
    doc.save(ruta)


def test_se_tachan_todas_las_apariciones(tmp_path):
    entrada, salida = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _documento(entrada)
    redact_pdf_digital(entrada, salida, "t", analyzer)
    texto = " ".join(" ".join(p.get_text().split()) for p in fitz.open(salida))
    assert "Álvarez Suárez" not in texto, "el nombre del bloque de firma sigue a la vista"
    assert "Cervantes, 96" not in texto, "la direccion partida de la otra columna sigue a la vista"
    assert "4.860,00" not in texto, "el importe repetido en la pagina 2 sigue a la vista"
    assert "Base cotización CC" in texto and "Robòtica Aplicada" in texto, "se tacho de mas"


def test_un_valor_corto_solo_se_tacha_como_palabra_entera(tmp_path):
    """"28013" no se busca dentro de otro numero (un IBAN, un expediente)."""
    from app.services.pdf.redactor import tachar_todas_las_apariciones
    from app.services.pii.models import PiiEntity

    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((58, 300), "Codigo postal 28013 de Madrid", fontsize=10)
    p.insert_text((58, 320), "Expediente 2028013/2024 en tramite", fontsize=10)
    tachar_todas_las_apariciones(doc, [PiiEntity("POSTAL_CODE", 0, 5, 0.9, text="28013")], analyzer)
    texto = " ".join(doc[0].get_text().split())
    assert "Codigo postal de Madrid" in texto or "28013 de" not in texto
    assert "2028013/2024" in texto


def test_un_valor_corto_pegado_a_su_etiqueta_si_se_tacha():
    """"Código postal:41001" (la extraccion pega etiqueta y valor): "41001" es
    una pieza entera de la palabra, no parte de otro numero."""
    from app.services.pdf.redactor import tachar_todas_las_apariciones
    from app.services.pii.models import PiiEntity

    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((58, 300), "Código postal:41001 Municipio:Sevilla", fontsize=10)
    p.insert_text((58, 320), "Expediente 2028013/2024 en tramite", fontsize=10)
    tachar_todas_las_apariciones(doc, [PiiEntity("POSTAL_CODE", 0, 5, 0.9, text="41001"),
                                       PiiEntity("POSTAL_CODE", 0, 5, 0.9, text="28013")], analyzer)
    texto = " ".join(doc[0].get_text().split())
    assert "41001" not in texto
    assert "2028013/2024" in texto


# --- el barrido no multiplica un nombre de institucion mal detectado ----------------
# El analizador toma a veces por PERSON un nombre de institucion o "La persona".
# Donde se detecta se tacha; el barrido no lo multiplica por todo el documento,
# membrete incluido.

def test_el_barrido_no_multiplica_un_nombre_de_institucion():
    from app.services.pdf.redactor import tachar_todas_las_apariciones
    from app.services.pii.models import PiiEntity

    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((58, 120), "Ajuntament de Sueca · Universitat Politècnica de Llevant", fontsize=9)
    p.insert_text((58, 300), "La persona interesada, Vicent Peris Llopis, comparece.", fontsize=10)
    p.insert_text((58, 320), "Firma: Vicent Peris Llopis", fontsize=10)
    tachar_todas_las_apariciones(doc, [
        PiiEntity("PERSON", 0, 1, 0.8, text="Ajuntament de Sueca"),
        PiiEntity("PERSON", 0, 1, 0.8, text="Universitat Politècnica de Llevant"),
        PiiEntity("PERSON", 0, 1, 0.8, text="La persona"),
        PiiEntity("PERSON", 0, 1, 0.8, text="Vicent Peris Llopis"),
    ], analyzer)
    texto = " ".join(doc[0].get_text().split())
    assert "Ajuntament de Sueca" in texto and "Universitat Politècnica de Llevant" in texto
    assert "La persona interesada" in texto
    assert "Vicent Peris Llopis" not in texto
