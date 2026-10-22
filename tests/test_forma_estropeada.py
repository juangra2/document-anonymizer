"""Para tachar no hace falta que el dato sea valido: basta con que tenga su forma.

Si el OCR mete una errata en un IBAN, falla el digito de control; si corta el
final de un correo, falta el dominio.  Ninguna de las dos cosas puede dejar el
dato a la vista: un IBAN con una cifra leida como otra y una O en lugar de un 0,
o un correo sin ".es", se tachan igual.

Un correo sin terminacion exige al menos 3 caracteres de usuario, para no tomar
por correo un "pr@sent" del OCR.
"""
from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _tipos(texto, tipo):
    return [texto[e.start:e.end] for e in analyzer.analyze(texto, mode="strict", zone_type="body")
            if e.entity_type == tipo]


# --- IBAN ---------------------------------------------------------------------

def test_un_iban_con_errata_se_tacha_por_su_forma():
    texto = "CiC:ES8O 00495069 47323516 9178"
    assert any("00495069" in v for v in _tipos(texto, "IBAN_CODE"))


def test_una_cifra_de_menos_no_es_un_iban():
    assert not _tipos("Cuenta: ES80 0049906 47323516 9178", "IBAN_CODE")


def test_un_texto_lleno_de_letras_no_cuela_como_iban():
    # 22 caracteres que el OCR podria confundir con cifras, pero casi sin cifras.
    assert not _tipos("es lo sobo silos lobo libio sol 1234", "IBAN_CODE")


# --- correo ---------------------------------------------------------------------

def test_un_correo_sin_terminacion_se_tacha():
    assert "pilarmoreno@orange" in _tipos("Correu: pilarmoreno@orange", "EMAIL_ADDRESS")


def test_un_correo_con_tilde_en_el_dominio_se_tacha_entero():
    assert "contacto@formació.com" in _tipos("correu contacto@formació.com, representada",
                                              "EMAIL_ADDRESS")


def test_una_palabra_con_una_e_leida_como_arroba_no():
    assert not _tipos("compareixen i pr@sent el document", "EMAIL_ADDRESS")


def test_una_mencion_sin_usuario_no():
    assert not _tipos("Siguenos en @ajuntamentvalencia", "EMAIL_ADDRESS")
