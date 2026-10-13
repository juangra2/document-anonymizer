"""En el digital se tachan TODAS las apariciones de un dato detectado.

La lectura de barras de c2 sobre 100 documentos digitales encontro datos que el
sistema ya habia detectado y seguian a la vista: el dato se detectaba en un sitio
y sus otras apariciones se quedaban.  El camino escaneado ya tacha todas las
apariciones de un valor detectado; el digital solo tachaba la que habia detectado.
Los tres casos reales:

  - el nombre en el cuerpo ("Nombre y apellidos: David Álvarez Suárez") y otra
    vez solo en el bloque de firma, en la franja del pie;
  - la misma direccion en la otra columna, partida en dos lineas ("…Sanitàries,
    Calle de" / "Cervantes, 96, 5º C, 28013 Madrid.");
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
    """solicitud_generica_0016: "Código postal:41001" (la extraccion pega
    etiqueta y valor).  "41001" es una pieza entera de la palabra, no parte de
    otro numero."""
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
