"""Formulario bilingue a dos columnas: se tachan los valores, no los rotulos.

En una solicitud valenciano | castellano a dos columnas, los rotulos de las
dos columnas no se tachan.  Dos cosas lo hacen posible:

  - tras el canal entre columnas, "Nombre y apellidos:" se reconoce como
    etiqueta (lo que queda tras un corte sin etiqueta se tacharia, por
    seguridad);
  - la primera fila ("Nom i cognoms: ... | Nombre y apellidos: ...") no es la
    cabecera de una tabla, que haria tachar como nombres los rotulos de debajo.
    Una cabecera de tabla no termina en dos puntos; una etiqueta de formulario
    si.
"""
import fitz

from app.services.pdf.redactor import redact_pdf_digital
from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()

_COLUMNAS = [
    [("Nom i cognoms:", "Jordi Alapont Ribes"), ("DNI/NIE:", "29184735K"),
     ("Domicili:", "C/ de la Reina, 87, 3r, 6a"), ("Codi postal:", "46011"),
     ("Telèfon:", "655 412 908"), ("Data de naixement:", "07/11/1984")],
    [("Nombre y apellidos:", "Jordi Alapont Ribes"), ("DNI/NIE:", "29184735K"),
     ("Domicilio:", "C/ de la Reina, 87, 3º, 6ª"), ("Código postal:", "46011"),
     ("Teléfono:", "655 412 908"), ("Fecha de nacimiento:", "07/11/1984")],
]


def test_los_rotulos_de_las_dos_columnas_se_ven_y_los_valores_no(tmp_path):
    entrada, salida = tmp_path / "in.pdf", tmp_path / "out.pdf"
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((50, 52), "AJUNTAMENT DE VALÈNCIA", fontsize=13)
    p.draw_line((297, 90), (297, 400))
    for k, campos in enumerate(_COLUMNAS):
        x = 50 + k * 257
        for j, (etiqueta, valor) in enumerate(campos):
            p.insert_text((x, 125 + 20 * j), etiqueta, fontsize=8)
            p.insert_text((x + 95, 125 + 20 * j), valor, fontsize=8)
    doc.save(entrada)

    redact_pdf_digital(entrada, salida, "t", analyzer)
    texto = " ".join(fitz.open(salida)[0].get_text().split())
    for campos in _COLUMNAS:
        for etiqueta, valor in campos:
            assert etiqueta in texto, f"rotulo tachado: {etiqueta}"
    for valor in ("Jordi Alapont Ribes", "29184735K", "C/ de la Reina", "655 412 908", "07/11/1984"):
        assert valor not in texto, f"sigue a la vista: {valor}"
