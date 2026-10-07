"""Telefono, direccion y codigo postal en la cabecera y el pie: decide el filtro, no la zona.

Las zonas de cabecera y pie excluian por diseño estos tres tipos, para no
tachar el telefono de centralita del membrete.  La consecuencia, medida con el
banco de pruebas (scripts/banco_contacto.py): un telefono o una direccion
PERSONAL en el 12 % superior o inferior de una pagina digital no se tachaba
nunca, con cualquier formato.  El destinatario de una carta va justo ahi.

Ahora el filtro institucional —el mismo en los dos caminos— ya distingue el
membrete del destinatario, asi que la zona deja de excluir: en cabecera y pie se
buscan como en el cuerpo y el filtro aparta solo el contacto de la institucion.
Entre tachar un telefono de centralita y dejar a la vista el de un particular,
se prefiere lo primero.
"""
import fitz

from app.services.layout.models import ZoneType
from app.services.pdf.redactor import redact_pdf_digital
from app.services.pii.analyzer import PiiAnalyzer

from tests.test_institucional_escaneado import _tachado, _zona

analyzer = PiiAnalyzer()


def _carta(tmp_path, lineas_arriba, lineas_abajo=()):
    entrada, salida = tmp_path / "carta.pdf", tmp_path / "carta_out.pdf"
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    y = 30
    for linea in lineas_arriba:                    # dentro del 12 % superior (< 101 pt)
        p.insert_text((50, y), linea, fontsize=8); y += 11
    y = 300
    for linea in ["Por la presente se le comunica la resolucion del expediente de",
                  "referencia, que se adjunta a esta notificacion para su conocimiento."]:
        p.insert_text((50, y), linea, fontsize=10); y += 14
    y = 770
    for linea in lineas_abajo:                     # dentro del 12 % inferior (> 741 pt)
        p.insert_text((50, y), linea, fontsize=8); y += 11
    doc.save(entrada)
    redact_pdf_digital(entrada, salida, "t", analyzer)
    return " ".join(fitz.open(salida)[0].get_text().split())


_MEMBRETE = ["Ajuntament de Sueca", "Plaça Major, 1, 46410 Sueca", "Tel. 96 170 05 00"]


def test_digital_el_membrete_sigue_a_la_vista(tmp_path):
    texto = _carta(tmp_path, _MEMBRETE)
    assert "96 170 05 00" in texto
    assert "Plaça Major, 1" in texto


def test_digital_el_destinatario_arriba_se_tacha(tmp_path):
    texto = _carta(tmp_path, _MEMBRETE + ["Sra. Xelo Brufau Ivars", "C/ Mayor, 3, 46410 Sueca",
                                          "Tel. 612 345 678"])
    assert "C/ Mayor, 3" not in texto
    assert "612 345 678" not in texto


def test_digital_un_telefono_personal_en_el_pie_se_tacha(tmp_path):
    texto = _carta(tmp_path, _MEMBRETE, ["Persona de contacto: Vicent Peris Llopis, movil 655 123 456"])
    assert "655 123 456" not in texto


def test_escaneado_el_destinatario_en_una_cabecera_de_mineru_se_tacha():
    t = _tachado([_zona(ZoneType.HEADER, _MEMBRETE + ["Sra. Xelo Brufau Ivars",
                                                      "C/ Mayor, 3, 46410 Sueca"], 100)])
    assert "C/ Mayor, 3, 46410 Sueca" in t
    assert "96 170 05 00" not in t


def test_escaneado_un_telefono_personal_en_un_pie_se_tacha():
    t = _tachado([_zona(ZoneType.FOOTER, ["Persona de contacto: Vicent Peris Llopis, movil 655 123 456"],
                        2200)])
    assert "655 123 456" in t
