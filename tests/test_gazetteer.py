"""INE gazetteer loading + compositional ADDRESS extractor."""
from app.services.pii import gazetteer as gz
from app.services.pii.analyzer import PiiAnalyzer


# --- gazetteer loading / normalisation -----------------------------------

def test_normalize_strips_accents_punctuation():
    assert gz.normalize("  Málaga. ") == "MALAGA"
    assert gz.normalize("A Coruña") == "A CORUNA"
    assert gz.normalize("Vitoria-Gasteiz") == "VITORIA GASTEIZ"


def test_surnames_and_given_names_loaded():
    assert "GARCIA" in gz.surnames()
    assert "DELGADO" in gz.surnames()
    assert "JUAN" in gz.given_names()
    assert "CRISTINA" in gz.given_names()


def test_municipality_province_lookup():
    assert "28" in gz.municipality_province_codes("MADRID")
    assert "46" in gz.municipality_province_codes("VALENCIA")
    assert "15" in gz.municipality_province_codes("A CORUNA")
    assert gz.municipality_province_codes("CIUDAD INEXISTENTE") == frozenset()


def test_provinces_and_toponyms():
    assert gz.provinces()["28"] == "MADRID"
    assert gz.is_toponym("Granada")
    assert not gz.is_toponym("Rodríguez")
    # Some surnames are also municipalities (León, Soria, Cervantes); the PERSON
    # filter must therefore stay conservative about these.
    assert gz.is_toponym("León") and gz.normalize("León") in gz.surnames()


# --- compositional ADDRESS extractor --------------------------------------

def _addrs(text):
    return [
        (round(e.score, 2), text[e.start:e.end])
        for e in PiiAnalyzer._detect_address_gazetteer(text)
    ]


def test_address_with_confirmed_municipality():
    text = "centro de trabajo Calle de Cervantes, 96, 5 C, 28013 Madrid. A efectos"
    out = _addrs(text)
    assert len(out) == 1
    score, span = out[0]
    assert score == 0.80
    assert span.startswith("Calle de Cervantes")
    assert "28013 Madrid" in span


def test_address_abbreviated_via_types():
    for text in [
        "Trav. de la Paz, 4, Bajo D, 30003 Murcia",
        "Avda. de Madrid, 72, 4 D, 18002 Granada",
        "Cno. de la Constitucion, 9, 28020 Madrid",
        "C/ Mayor 12, 46001 Valencia",
    ]:
        out = _addrs(text)
        assert len(out) == 1 and out[0][0] == 0.80, text


def test_address_invalid_province_rejected():
    # CP prefix 99 is not a Spanish province → not an address.
    assert _addrs("Calle Falsa 123, 99999 Nowhere") == []


def test_address_cp_municipality_mismatch_downgraded():
    # CP 28 = Madrid but municipality says Barcelona → unconfirmed: 0.62 and the
    # span stops at the postal code (the wrong municipality is not included).
    out = _addrs("C/ X 1, 28001 Barcelona")
    assert len(out) == 1
    score, span = out[0]
    assert score == 0.62
    assert span.endswith("28001")


def test_address_prose_not_matched():
    # No street type → no spurious address from a bare 5-digit number.
    assert _addrs("según el artículo 28013 del reglamento vigente") == []
    assert _addrs("En Valencia, a 5 de marzo de 2026") == []


# --- institutional-context guard (A) --------------------------------------

def test_institutional_addresses_rejected():
    for text in [
        # signing location (followed by signing date)
        "signen el present document en Campus Nord, Av. de la Ciència, s/n, "
        "46022 València, a 1 de junio de 2024",
        # issuer letterhead "·" separator
        "Ajuntament de Bellaterra · Plaza de la Vila, 1, 08193 Cerdanyola",
        # signing verb prefix + signing date
        "expedido en Plaza del Hospital, 2, 46300 Utiel, a 5 de mayo",
        "Paseo de España, 20, 15002 A Coruña, a 3 de marzo de 2025",
    ]:
        assert _addrs(text) == [], text


def test_sn_without_dwelling_rejected():
    assert _addrs("Carrer de la Universitat, 3, s/n, 43500 Tortosa") == []


def test_floor_door_overrides_institutional_guard():
    # A residential dwelling marker keeps the address even in a signing clause.
    text = "firmado en Calle de Cervantes, 96, 5º C, 28013 Madrid, a 1 de junio"
    out = _addrs(text)
    assert len(out) == 1 and out[0][1].startswith("Calle de Cervantes")


def test_personal_work_center_address_kept():
    # No institutional cue → personal address kept (recall preserved).
    out = _addrs("centro habitual de trabajo Instituto X, "
                 "Paseo de la Merced, 109, 18002 Granada")
    assert len(out) == 1 and "18002 Granada" in out[0][1]


# --- prose-pattern over-extension bound (B) -------------------------------

def test_format_c_does_not_overextend_into_prose():
    a = PiiAnalyzer()
    text = (
        "El trabajador, con domicilio en Trav. de la Paz, 4, Bajo D, 30003 "
        "Murcia. Reconociéndose capacidad legal para el otorgamiento del "
        "presente documento."
    )
    spans = [
        text[e.start:e.end]
        for e in a.analyze(text, mode="strict", zone_type="body")
        if e.entity_type == "ADDRESS"
    ]
    assert spans, "address should still be detected"
    for s in spans:
        assert "Reconociéndose" not in s
        assert len(s) <= 45


# --- PERSON gazetteer helpers ---------------------------------------------

def test_name_tokens_normalised():
    from app.services.pii.analyzer import _name_tokens
    assert _name_tokens("Juan Pérez García") == ["JUAN", "PEREZ", "GARCIA"]


def test_looks_like_toponym():
    f = PiiAnalyzer._looks_like_toponym
    assert f("Madrid")            # municipality/province, not a given name
    assert f("Albacete")
    assert not f("Juan García")   # real person name
    assert not f("Cervantes 96")  # not a clean toponym span
    # Conservative: a toponym that is also a given name (León) is NOT dropped,
    # so a person genuinely named like a place survives.
    assert gz.is_toponym("León") and not f("León")


def test_person_token_membership():
    assert gz.is_person_token("GARCIA")
    assert gz.is_person_token("JUAN")
    assert not gz.is_person_token("ZZZQX")
