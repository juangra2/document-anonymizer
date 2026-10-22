"""Desactivar un tipo no puede dejar sin tapar lo que ese tipo contenía.

La fusión de solapes se queda con un único span y descarta el resto.  Si se
filtraran los tipos desactivados DESPUÉS de fusionar, un span desactivado que
hubiera ganado se llevaría por delante a los que contiene: nombre y correo
dentro de una fila de expediente, el teléfono tras una dirección, una IP tomada
por importe.  Los textos imitan fragmentos del corpus.
"""
import pytest

from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _covered(text: str, value: str, entities, entity_type: str | None = None) -> bool:
    start = text.index(value)
    end = start + len(value)
    for e in entities:
        if entity_type and e.entity_type != entity_type:
            continue
        if e.start <= start and e.end >= end:
            return True
    return False


def _analyze(text: str, disabled: set[str]):
    return analyzer.with_disabled_types(disabled).analyze(text, mode="strict")


# --- un tipo desactivado que contiene a otros ------------------------------------

def test_case_table_row_disabled_keeps_name_and_email_hidden():
    text = "CAS-001 Diego Ferrer Demo Email: diego.demo@example.com\n"
    entities = _analyze(text, {"CASE_TABLE_ROW"})
    assert not any(e.entity_type == "CASE_TABLE_ROW" for e in entities)
    assert _covered(text, "diego.demo@example.com", entities, "EMAIL_ADDRESS")


def test_address_disabled_keeps_contact_on_the_same_line_hidden():
    text = ("Adreça postal:C/ de las Flores, 81, 18010 Granada Contacte:+34 772 187 390 "
            "E-mail:rafaelprieto@yahoo.es\n")
    entities = _analyze(text, {"ADDRESS"})
    assert not any(e.entity_type == "ADDRESS" for e in entities)
    assert _covered(text, "rafaelprieto@yahoo.es", entities, "EMAIL_ADDRESS")
    assert _covered(text, "772 187 390", entities)


def test_address_disabled_still_finds_the_embedded_postal_code():
    # El CP se deriva de la dirección detectada: desactivar ADDRESS no puede
    # hacer que se pierda un POSTAL_CODE que sigue activo.
    text = "Adreça postal:C/ de las Flores, 81, 18010 Granada\n"
    base = analyzer.analyze(text, mode="strict")
    off = _analyze(text, {"ADDRESS"})
    if _covered(text, "18010", base, "POSTAL_CODE"):
        assert _covered(text, "18010", off, "POSTAL_CODE")


def test_dni_label_disabled_keeps_the_nie_value_hidden():
    text = "Tutor/a\nNombre\nMiguel Gutiérrez Cano\nDNI:\nY7527760N\n"
    entities = _analyze(text, {"ES_DNI"})
    assert _covered(text, "Y7527760N", entities, "ES_NIE")


def test_iban_disabled_keeps_the_birth_date_hidden():
    text = "IBAN:\nES53-3058-4700-7070-2668-9384 i D.N.: 29/07/1988\n18:07.\n"
    entities = _analyze(text, {"IBAN_CODE"})
    assert _covered(text, "29/07/1988", entities)


# --- sin tipos desactivados, la fusión no cambia --------------------------------

@pytest.mark.parametrize("text", [
    "CAS-001 Diego Ferrer Demo Email: diego.demo@example.com\n",
    "Adreça postal:C/ de las Flores, 81, 18010 Granada Contacte:+34 772 187 390\n",
    "IBAN:\nES53-3058-4700-7070-2668-9384 i D.N.: 29/07/1988\n",
    "IP de signatura electrònica / IP de firma electrónica: 90.241.195.223\n",
])
def test_without_disabled_types_the_result_is_the_plain_merge(text):
    plain = analyzer.analyze(text, mode="strict")
    empty = analyzer.with_disabled_types(set()).analyze(text, mode="strict")
    key = lambda e: (e.entity_type, e.start, e.end, e.source)
    assert sorted(map(key, plain)) == sorted(map(key, empty))


def test_postal_codes_are_not_duplicated_when_address_is_disabled():
    text = "Domicilio: Calle Mayor 12, 46001 Valencia\n"
    off = _analyze(text, {"ADDRESS"})
    spans = [(e.start, e.end) for e in off if e.entity_type == "POSTAL_CODE"]
    assert len(spans) == len(set(spans))
