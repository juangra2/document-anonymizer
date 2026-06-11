"""NER engine selection and fallback behaviour."""
import app.services.pii.analyzer as analyzer_module
from app.services.pii.analyzer import PiiAnalyzer

SAMPLE = "El solicitante Juan Pérez García con DNI 12345678Z reside en Madrid."


def test_engine_is_lazy_at_init():
    """Constructing the analyzer must not build the NLP engine (no model load)."""
    analyzer = PiiAnalyzer()
    assert analyzer._engine is None
    assert analyzer._ner_backend is None


def test_default_backend_is_spacy():
    analyzer = PiiAnalyzer()
    analyzer.analyze(SAMPLE, mode="strict")
    assert analyzer._ner_backend == "spacy"
    assert analyzer._engine is not None


def test_transformers_request_falls_back_to_spacy(monkeypatch):
    """When the transformer stack fails to build, fall back to spaCy silently."""
    monkeypatch.setattr(analyzer_module.settings, "ner_engine", "transformers")

    def _raise(self):
        raise RuntimeError("transformer stack unavailable")

    monkeypatch.setattr(PiiAnalyzer, "_build_transformers_engine", _raise)

    analyzer = PiiAnalyzer()
    entities = analyzer.analyze(SAMPLE, mode="strict")

    assert analyzer._ner_backend == "spacy"
    # Detection still works through the fallback engine.
    assert any(e.entity_type == "ES_DNI" for e in entities)
