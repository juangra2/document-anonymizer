"""Direcciones en prosa y repartidas en varias lineas.

"reside en la calle Ruzafa número 12, piso tercero", o una direccion con el
codigo postal en la linea siguiente, no llevan un codigo postal en la misma
linea, que es lo que piden los patrones de direccion habituales.

En prosa se exige la palabra "número" (o "núm.", "nº") y un nombre de calle con
mayuscula: es lo que separa "la calle Ruzafa número 12" de "la calle estaba
cortada" o de un año ("el Paseo de Gracia, 2023").
"""
import pytest

from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _direcciones(texto):
    return [" ".join(texto[e.start:e.end].split())
            for e in analyzer.analyze(texto, mode="strict", zone_type="body")
            if e.entity_type == "ADDRESS"]


@pytest.mark.parametrize("texto, esperado", [
    ("El interesado reside en la calle Ruzafa número 12, piso tercero, de esta ciudad.",
     "calle Ruzafa número 12, piso tercero"),
    ("con domicilio en la avenida del Puerto núm. 45, puerta 8, según el padrón",
     "avenida del Puerto núm. 45, puerta 8"),
    ("La persona interessada té el domicili en el carrer de Cuba, número 9, porta 7, de València.",
     "carrer de Cuba, número 9, porta 7"),
    ("vive en la plaza de la Reina nº 3, 2º B, desde 2019", "plaza de la Reina nº 3, 2º B"),
])
def test_direccion_en_prosa(texto, esperado):
    assert any(esperado in d for d in _direcciones(texto)), _direcciones(texto)


def test_direccion_repartida_en_dos_lineas():
    texto = "Calle del Doctor Sumsi, 326, 2º\n46005 Valencia\n(Valencia)"
    assert any("Doctor Sumsi, 326" in d and "46005" in d for d in _direcciones(texto))


@pytest.mark.parametrize("texto", [
    "La calle estaba cortada por obras durante la mañana.",
    "La calle Mayor está cortada por obras desde el lunes.",
    "Se celebró en la Plaza de Toros de Valencia.",
    "Recorrió el Camino de Santiago en 2019, etapa número 12.",
    "El Paseo de Gracia, 2023, fue el lugar elegido.",
    "Según la avenida número 4 del plan urbanístico",
])
def test_trampas_de_direccion_en_prosa(texto):
    assert not _direcciones(texto), _direcciones(texto)


# --- "Pg." y "Partida" sin etiqueta --------------------------------------------------
# "Pg." tambien es "pagina" ("ver Pg. 12"): solo cuenta como via si le sigue un
# nombre de calle (con mayuscula) y luego numero y codigo postal.  "Partida"
# tambien es la partida presupuestaria.

@pytest.mark.parametrize("texto, esperado", [
    ("Pg. de l'Albereda, 23, 46022 València", "Pg. de l'Albereda, 23, 46022"),
    ("Passeig de l'Albereda, 41, 46022 València", "Passeig de l'Albereda, 41, 46022"),
    ("Partida de la Torre, 12, 46017 València", "Partida de la Torre, 12, 46017"),
    ("Ptda. Benimàmet, 5, 46035 València", "Ptda. Benimàmet, 5, 46035"),
])
def test_pg_y_partida_sin_etiqueta(texto, esperado):
    assert any(esperado in d for d in _direcciones(texto)), _direcciones(texto)


@pytest.mark.parametrize("texto", [
    "Para el detalle, ver Pg. 12 del informe adjunto.",
    "como se indica en la Pg. 46, 46002 ejemplares impresos",
    "Partida presupuestaria 2024/4410/22699",
    "Partida 3 del presupuesto: 46100 euros",
])
def test_trampas_de_pg_y_partida(texto):
    assert not _direcciones(texto), _direcciones(texto)


# --- direccion repartida en una pagina con lineas largas (formulario real) -----------
# El reagrupado de lineas digital solo une una linea con la siguiente si la
# primera "llena la columna" (se partio por falta de sitio).  Con un parrafo
# largo en la pagina la columna es ancha, "Calle X, 12, 2º" no la llena y el
# codigo postal de la linea siguiente quedaria en otra zona: la direccion no se
# veria entera.

def test_direccion_en_dos_lineas_en_una_pagina_con_lineas_largas(tmp_path):
    import fitz

    from app.services.pdf.redactor import redact_pdf_digital

    entrada, salida = tmp_path / "f.pdf", tmp_path / "f_out.pdf"
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    lineas = ["El interesado presenta la presente solicitud en tiempo y forma ante este organismo publico.",
              "Domicilio: Calle de Colón, 319",
              "Calle del Doctor Sumsi, 326, 2º", "46005 Valencia", "(Valencia)",
              "Correo: interesado@ejemplo.es"]
    for i, linea in enumerate(lineas):
        p.insert_text((50, 300 + 15 * i), linea, fontsize=10)
    doc.save(entrada)
    redact_pdf_digital(entrada, salida, "t", analyzer)
    queda = " ".join(fitz.open(salida)[0].get_text().split())
    assert "Doctor Sumsi" not in queda and "46005" not in queda


def test_direccion_con_el_salto_de_linea_a_mitad():
    """El salto de linea cae entre el numero y el piso, no antes del codigo
    postal."""
    texto = ("NASS: 41/3546801989, domiciliado/a en Ctra. de España, 60,\n"
             "Entresuelo Centro, 08002 Barcelona, Tel.: 617 485 476")
    assert any("España, 60" in d and "08002" in d for d in _direcciones(texto)), _direcciones(texto)
