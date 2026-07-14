"""Detector de nombres en texto corrido avalados por el gazetteer INE."""
from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()


def _persons(text):
    return [
        text[e.start:e.end]
        for e in analyzer.analyze(text, mode="strict", zone_type="body")
        if e.entity_type == "PERSON"
    ]


# --- FN reales del corpus (prosa que el NER pierde) ----------------------------

def test_name_in_prose_detected():
    assert "Roberto Iglesias Calvo" in _persons(
        "entre la universidad y Roberto Iglesias Calvo, en adelante el adscrito"
    )


def test_name_with_accents():
    assert "Rosa García Díaz" in _persons(
        "comparece Rosa García Díaz en representación propia"
    )


def test_uppercase_name():
    assert "LUIS VELA RIUS" in _persons("comparece LUIS VELA RIUS con DNI")


def test_full_span_with_particles_beats_partial_ner():
    out = _persons("el técnico Luis Prados de la Guardia certifica")
    assert "Luis Prados de la Guardia" in out
    assert "Luis Prados" not in out


# --- precisión ------------------------------------------------------------------

def test_street_names_not_person():
    assert _persons("con domicilio en Plaza de la Paz, 101") == []
    assert _persons("la oficina de Calle Mayor") == []


def test_hagiotoponyms_not_person():
    assert _persons("la sede de San Juan de Alicante") == []


def test_admin_phrases_not_person():
    assert _persons("el Real Decreto y el Boletín Oficial del Estado") == []
    assert _persons("la Universidad Politécnica de Valencia resuelve") == []
    assert _persons("el Registro Mercantil de Madrid certifica") == []


def test_material_phrases_still_not_person():
    assert _persons("Hormigón Señalización y Dirección Facultativa") == []
