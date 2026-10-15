"""Una pagina girada (anexo en A3 apaisado) dentro de un documento vertical.

Caso dificil 13: la extraccion de texto da las coordenadas de la pagina SIN
girar (alto 1191) y el segmentador medía las franjas con el tamano de la pagina
girada (alto 842).  Todo el texto caia por debajo del "pie" y se analizaba con
los tipos del pie, que no buscan correos: el correo de la firma, junto al borde
inferior, salia entero, y el nombre a medias.
"""
import fitz

from app.services.pdf.redactor import redact_pdf_digital
from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _documento(ruta):
    doc = fitz.open()
    doc.new_page(width=595, height=842).insert_text((50, 60), "PROYECTO DE REFORMA", fontsize=12)
    p = doc.new_page(width=842, height=1191)
    p.set_rotation(90)
    m = p.derotation_matrix

    def texto(x, y, s, fs=8):
        p.insert_text(fitz.Point(x, y) * m, s, fontsize=fs, rotate=90)

    texto(40, 30, "Promotor: Vicent Garcia Llorens · NIF 73015628G · Tel. 678 214 093")
    texto(40, 60, "ANEXO II · MEDICIONES Y PRESUPUESTO", 12)
    for k, fila in enumerate(["01.01  Demolición de tabiquería de ladrillo hueco  m2  48,20",
                              "01.02  Levantado de solado cerámico  m2  96,50"]):
        texto(40, 100 + 22 * k, fila)
    texto(40, 817, "Firmado: Laura Pastor Gil, arquitecta · laura.pastor@arquitectes.example")
    doc.save(ruta)


def test_los_datos_junto_a_los_bordes_de_una_pagina_girada_se_tachan(tmp_path):
    entrada, salida = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _documento(entrada)
    redact_pdf_digital(entrada, salida, "t", analyzer)
    texto = " ".join(fitz.open(salida)[1].get_text().split())
    for dato in ("laura.pastor@arquitectes.example", "Laura Pastor Gil", "Vicent Garcia Llorens",
                 "73015628G", "678 214 093"):
        assert dato not in texto, f"sigue a la vista: {dato}"
    assert "MEDICIONES Y PRESUPUESTO" in texto and "Demolición de tabiquería" in texto
