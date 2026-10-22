"""La capa binaria no lee datos donde solo hay bytes codificados.

El /ID del trailer son 64 cifras hexadecimales aleatorias en cada salida, y a
menudo llevan nueve cifras seguidas que empiezan por 6, 7 o 9: un "telefono".
Y en latin-1 casi todo byte alto es "imprimible", asi que un flujo comprimido
escaneado como texto da datos falsos.

Por eso se apartan las cadenas hexadecimales y los flujos con /Filter (su
contenido lo leen ya descomprimido la capa textual y la de OCR).  Los flujos sin
comprimir y el resto del fichero se siguen escaneando enteros.
"""
from app.services.pii.analyzer import PiiAnalyzer
from app.services.verification.layers import check_binary

analyzer = PiiAnalyzer()

_RUIDO = bytes(range(0xA0, 0x100)) * 3          # bytes "imprimibles" en latin-1


def _pdf(tmp_path, cuerpo: bytes):
    ruta = tmp_path / "f.pdf"
    ruta.write_bytes(b"%PDF-1.7\n" + cuerpo + b"\n%%EOF\n")
    return ruta


def test_un_dni_aparente_en_un_flujo_comprimido_no_cuenta(tmp_path):
    flujo = _RUIDO + b" 69026956T " + _RUIDO
    ruta = _pdf(tmp_path, b"4 0 obj\n<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(flujo)
                + flujo + b"\nendstream\nendobj")
    assert check_binary(ruta, analyzer).entities_found == []


def test_un_dni_en_un_flujo_sin_comprimir_se_detecta(tmp_path):
    flujo = b"BT /F1 10 Tf 72 700 Td (" + b"x" * 300 + b" DNI 69026956T) Tj ET"
    ruta = _pdf(tmp_path, b"4 0 obj\n<< /Length %d >>\nstream\n" % len(flujo)
                + flujo + b"\nendstream\nendobj")
    assert check_binary(ruta, analyzer).entities_found


def test_un_dni_fuera_de_los_flujos_se_detecta(tmp_path):
    ruta = _pdf(tmp_path, b"1 0 obj\n<< /Title (Expediente 69026956T) >>\nendobj")
    assert check_binary(ruta, analyzer).entities_found


def test_los_anchos_de_una_fuente_no_son_telefonos(tmp_path):
    """Un plano lleva arrays numericos (/Widths, /W) que parecen telefonos."""
    ruta = _pdf(tmp_path, b"5 0 obj\n<< /Type /Font /FirstChar 32 /Widths [278 667 722 611 556 278]"
                b" /W [1 [667 722 611] 10 20 556] >>\nendobj")
    assert check_binary(ruta, analyzer).entities_found == []


def test_el_id_del_trailer_no_es_un_telefono(tmp_path):
    ruta = _pdf(tmp_path, b"trailer\n<</Size 6/Root 1 0 R/ID[<8A0DF9E730113959CAA401072D98B2F5>"
                b"<C3BC6D7D51C387C3A7C284C3B7C29970>]>>")
    assert check_binary(ruta, analyzer).entities_found == []
