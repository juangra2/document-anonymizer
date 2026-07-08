"""Textual signature-block PERSON attribution (Fdo. / Signat per / Firmado por)."""
from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _persons(text):
    return [
        text[e.start:e.end]
        for e in analyzer.analyze(text, mode="strict", zone_type="body")
        if e.entity_type == "PERSON"
    ]


def test_fdo_prefix_name():
    assert "LOLA ROMERA MARTINEZ" in _persons("Fdo.: LOLA ROMERA MARTINEZ")


def test_signat_electronicament_per():
    out = _persons("Signat electrònicament per LUIS PRADOS DE LA GUARDIA, Arquitecte")
    assert "LUIS PRADOS DE LA GUARDIA" in out


def test_firmado_por():
    assert "Lara Maria Perez Bessone" in _persons(
        "Firmado electrónicamente por Lara Maria Perez Bessone"
    )


def test_role_title_not_person():
    assert _persons("Fdo.: El Secretario General") == []


def test_institution_not_person():
    assert _persons("Firmado por la Universitat Politècnica de Llevant") == []


def test_three_fdo_blocks_on_same_line():
    # Bloques de firma en columnas: tres "Fdo.: NOMBRE" en la misma línea de
    # texto extraído.  Cada nombre debe detectarse por separado y el valor no
    # debe absorber el "Fdo" del bloque siguiente (dejaba el nombre contiguo
    # sin redactar).
    text = "Fdo.: MARIA LOPEZ GARCIA   Fdo.: LUIS ORTENBACH ALONSO   Fdo.: ANA RUIZ SANZ"
    out = _persons(text)
    assert "MARIA LOPEZ GARCIA" in out
    assert "LUIS ORTENBACH ALONSO" in out
    assert "ANA RUIZ SANZ" in out
    assert not any("Fdo" in v for v in out), "el valor no puede contener 'Fdo'"


def test_two_consecutive_fdo_blocks():
    out = _persons("Fdo.: JUAN PEREZ RUIZ  Fdo.: LOLA ROMERA MARTINEZ")
    assert "JUAN PEREZ RUIZ" in out
    assert "LOLA ROMERA MARTINEZ" in out


def test_unknown_name_without_gazetteer_evidence_skipped():
    # No INE-known token → not emitted (precision guard).
    assert _persons("Fdo.: Zzqx Wnbk") == []
