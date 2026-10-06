"""Para tachar no hace falta que el dato sea valido: basta con que tenga su forma.

Si el OCR mete una errata en un IBAN, falla el digito de control y no se
tachaba; si corta el final de un correo, tampoco.  Casos reales del corpus
escaneado (contrato_laboral_0002): "ES8O 00495069 47323516 9178" (un 9 leido
como 5 y un 0 como O) y "pilarmoreno@orange" (sin ".es").

Medido antes de fijarlo sobre el texto del corpus digital y el OCR de las
entradas escaneadas e hibridas (452 documentos):
  IBAN por su forma: 1 dato real recuperado, 0 falsos positivos.
  correo sin terminacion: 20 datos reales; un falso positivo del OCR
  ("pr@sent", que era "present"), que se evita exigiendo 3 caracteres de usuario.
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
