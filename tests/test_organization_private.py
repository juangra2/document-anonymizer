"""A1.3 — razón social de empresa privada: se anonimiza; la institución
pública mencionada, nunca."""
import pytest

from app.services.pii.analyzer import PiiAnalyzer
from app.services.pii.institution_filter import PUBLIC_INSTITUTION_RE

analyzer = PiiAnalyzer()


def _orgs(text):
    return [
        text[e.start:e.end]
        for e in analyzer.analyze(text, mode="strict", zone_type="body")
        if e.entity_type == "ORGANIZATION"
    ]


# --- forma societaria (detector determinista) -------------------------------

@pytest.mark.parametrize("text,expected", [
    ("contratada TODINA INVESTMENTS, S.L. para el suministro", "TODINA INVESTMENTS, S.L."),
    ("la oferta de Consultores de Gestión Ibérica, S.A. fue admitida",
     "Consultores de Gestión Ibérica, S.A."),
    ("Ingeniería y Proyectos del Levante, S.L.U. ejecutará la obra",
     "Ingeniería y Proyectos del Levante, S.L.U."),
    ("Reformas Integrales Levante, C.B. presenta factura",
     "Reformas Integrales Levante, C.B."),
])
def test_company_with_legal_form_suffix(text, expected):
    assert expected in _orgs(text), text


def test_legal_form_as_prefix():
    # "U.T.E. Ronda Norte Fase II": la forma societaria abre la razón social.
    assert "U.T.E. Ronda Norte Fase II" in _orgs("adjudicado a U.T.E. Ronda Norte Fase II")


# --- razón social sin sufijo (retipado del NER) -----------------------------

def test_company_without_suffix_by_head_noun():
    assert "Constructora Peñalba" in _orgs("La empresa Constructora Peñalba ejecutará")


def test_company_without_suffix_by_context():
    assert "Grupotec Ingeniería" in _orgs("la mercantil Grupotec Ingeniería presenta")


# --- veto de institución pública --------------------------------------------

@pytest.mark.parametrize("text", [
    "el Ajuntament de València resuelve el expediente",
    "la Conselleria de Sanitat comunica",
    "la Universitat Politècnica emite certificado",
    "el Registro Mercantil de Madrid inscribe",
    "el Hospital General atiende la solicitud",
])
def test_public_institution_never_redacted(text):
    assert _orgs(text) == [], text


def test_public_veto_is_stricter_than_broad_filter():
    # El filtro amplio veta raíces genéricas de profesión; el estricto no, para
    # no dejar fuera empresas privadas legítimas.
    assert not PUBLIC_INSTITUTION_RE.search("Grupotec Ingeniería")
    assert PUBLIC_INSTITUTION_RE.search("Ajuntament de València")


# --- no-regresión ------------------------------------------------------------

def test_person_still_person():
    text = "comparece Roberto Iglesias Calvo con DNI 12345678Z"
    types = {e.entity_type for e in analyzer.analyze(text, mode="strict", zone_type="body")}
    assert "PERSON" in types


# --- contexto etiquetado y ORG del NER con evidencia -------------------------

def test_labeled_company_context():
    # "Empresa adjudicataria: X" / "Empresa consultora: X" son etiquetas de
    # formulario, no sintagmas sueltos.
    assert "Constructora Peñalba" in _orgs("Empresa adjudicataria: Constructora Peñalba")
    assert "Grupotec Ingeniería" in _orgs("Empresa consultora: Grupotec Ingeniería")


def test_ner_organization_with_company_context_is_kept():
    # El NER acierta a veces etiquetando ORG; con contexto de empresa se redacta.
    assert "Tecnobra Levante" in _orgs("la mercantil Tecnobra Levante presenta oferta")


def test_ner_organization_without_evidence_is_dropped():
    # Sin contexto de empresa, un ORGANIZATION del NER nombra casi siempre una
    # unidad del propio organismo público (741 FP frente a 9 TP en el corpus).
    assert _orgs("Instituto de Robótica Aplicada y Digital") == []
    assert _orgs("Unidad de Proyectos Europeos") == []


def test_labeled_context_does_not_beat_public_veto():
    assert _orgs("Empresa adjudicataria: Ajuntament de València") == []
