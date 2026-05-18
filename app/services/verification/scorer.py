"""Risk score calculation for verification results."""
from __future__ import annotations

from app.services.verification.models import (
    LAYER_SATURATION,
    LAYER_WEIGHTS,
    LayerResult,
    VerificationLayer,
)
from app.services.pii.models import PiiEntity


def compute_layer_score(
    layer: VerificationLayer,
    entities: list[PiiEntity],
) -> float:
    """Weighted risk score for one layer, in [0.0, 1.0].

    Formula:
        layer_score = min(1.0, n / SATURATION) * avg_confidence
        weighted    = layer_weight * layer_score

    An empty entity list always yields 0.0.
    """
    if not entities:
        return 0.0

    n = len(entities)
    avg_conf = sum(e.score for e in entities) / n
    saturation_factor = min(1.0, n / LAYER_SATURATION)
    raw_score = saturation_factor * avg_conf

    return LAYER_WEIGHTS[layer] * raw_score


def compute_page_risk(layer_results: list[LayerResult]) -> float:
    """Aggregate page risk from multiple layer results.

    Computed as the weighted average of individual layer scores,
    normalised by the sum of weights of the layers actually run.
    """
    if not layer_results:
        return 0.0

    total_weighted = sum(r.risk_score for r in layer_results)
    total_weight = sum(LAYER_WEIGHTS[r.layer] for r in layer_results)

    if total_weight <= 0:
        return 0.0

    return min(1.0, total_weighted / total_weight)


def risk_level(score: float) -> str:
    """Map a [0.0–1.0] risk score to a human-readable level."""
    if score == 0.0:
        return "none"
    if score < 0.20:
        return "low"
    if score < 0.50:
        return "medium"
    return "high"
