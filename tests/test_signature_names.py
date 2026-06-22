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


def test_unknown_name_without_gazetteer_evidence_skipped():
    # No INE-known token → not emitted (precision guard).
    assert _persons("Fdo.: Zzqx Wnbk") == []
