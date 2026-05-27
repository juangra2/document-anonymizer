"""Entity matching and metric computation for the evaluation pipeline.

Matching strategy
-----------------
- Exact match  : text_iou == 1.0 (case-insensitive) and same entity_type → TP-exact
- Partial match: text_iou >= iou_threshold (default 0.5) and same type → TP-partial
- Unmatched GT : False Negative
- Unmatched det: False Positive

Matching is one-to-one (greedy bipartite, highest IoU first).
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data records
# ---------------------------------------------------------------------------

@dataclass
class EntityRecord:
    text: str
    entity_type: str
    page: int | None = None
    score: float = 0.0
    source: str = ""


@dataclass
class TypeResult:
    entity_type: str
    tp_exact: int = 0
    tp_partial: int = 0
    fn: list[EntityRecord] = field(default_factory=list)
    fp: list[EntityRecord] = field(default_factory=list)

    @property
    def tp(self) -> int:
        return self.tp_exact + self.tp_partial

    def precision(self) -> float:
        denom = self.tp + len(self.fp)
        return self.tp / denom if denom else 0.0

    def recall(self) -> float:
        denom = self.tp + len(self.fn)
        return self.tp / denom if denom else 0.0

    def f1(self) -> float:
        p, r = self.precision(), self.recall()
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass
class DocResult:
    doc_id: str
    doc_type: str
    language: str
    quality: str
    api_status: str
    processing_ms: int
    tp_exact: int = 0
    tp_partial: int = 0
    fn: list[EntityRecord] = field(default_factory=list)
    fp: list[EntityRecord] = field(default_factory=list)
    by_type: dict[str, TypeResult] = field(default_factory=dict)

    @property
    def tp(self) -> int:
        return self.tp_exact + self.tp_partial

    def precision(self) -> float:
        denom = self.tp + len(self.fp)
        return self.tp / denom if denom else 0.0

    def recall(self) -> float:
        denom = self.tp + len(self.fn)
        return self.tp / denom if denom else 0.0

    def f1(self) -> float:
        p, r = self.precision(), self.recall()
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass
class AggregatedMetrics:
    tp_exact: int = 0
    tp_partial: int = 0
    fn_count: int = 0
    fp_count: int = 0
    fn_examples: list[dict] = field(default_factory=list)
    fp_examples: list[dict] = field(default_factory=list)

    @property
    def tp(self) -> int:
        return self.tp_exact + self.tp_partial

    def precision(self) -> float:
        denom = self.tp + self.fp_count
        return self.tp / denom if denom else 0.0

    def recall(self) -> float:
        denom = self.tp + self.fn_count
        return self.tp / denom if denom else 0.0

    def f1(self) -> float:
        p, r = self.precision(), self.recall()
        return 2 * p * r / (p + r) if (p + r) else 0.0


# Entity types that the system redacts by policy (NER over-detection, heuristics)
# but the GT corpus does not annotate as personal PII.  Excluded from global
# P/R/F1 so they do not distort the overall score; still visible per-type.
OUT_OF_SCOPE_GT_TYPES: frozenset[str] = frozenset({
    "LOCATION",
    "ORGANIZATION",
    "PROFESSIONAL_LICENSE",
})


# ---------------------------------------------------------------------------
# IoU helpers
# ---------------------------------------------------------------------------

def text_iou(a: str, b: str) -> float:
    """Similarity score [0, 1] between two entity strings.

    1.0  for identical strings (case-insensitive).
    Uses substring containment ratio for near-matches, then character
    trigram IoU as a fallback for partial overlaps.
    """
    a_n = a.strip().lower()
    b_n = b.strip().lower()
    if not a_n and not b_n:
        return 1.0
    if not a_n or not b_n:
        return 0.0
    if a_n == b_n:
        return 1.0
    shorter, longer = (a_n, b_n) if len(a_n) <= len(b_n) else (b_n, a_n)
    if shorter in longer:
        return len(shorter) / len(longer)
    # Character trigram IoU
    def trigrams(s: str) -> set[str]:
        return {s[i : i + 3] for i in range(len(s) - 2)} if len(s) >= 3 else {s}
    tg_a, tg_b = trigrams(a_n), trigrams(b_n)
    inter = len(tg_a & tg_b)
    union = len(tg_a | tg_b)
    return inter / union if union else 0.0


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def match_entities(
    gt: list[EntityRecord],
    detected: list[EntityRecord],
    iou_threshold: float = 0.5,
) -> tuple[int, int, list[EntityRecord], list[EntityRecord]]:
    """Greedy one-to-one bipartite matching (highest IoU first).

    Returns (tp_exact, tp_partial, fn_list, fp_list).
    """
    candidates: list[tuple[float, int, int]] = []
    for gi, g in enumerate(gt):
        for di, d in enumerate(detected):
            if g.entity_type != d.entity_type:
                continue
            iou = text_iou(g.text, d.text)
            if iou >= iou_threshold:
                candidates.append((iou, gi, di))
    candidates.sort(key=lambda x: -x[0])

    matched_gt: set[int] = set()
    matched_det: set[int] = set()
    tp_exact = 0
    tp_partial = 0

    for iou, gi, di in candidates:
        if gi in matched_gt or di in matched_det:
            continue
        matched_gt.add(gi)
        matched_det.add(di)
        if iou >= 0.99:
            tp_exact += 1
        else:
            tp_partial += 1

    fn = [gt[i] for i in range(len(gt)) if i not in matched_gt]
    fp = [detected[i] for i in range(len(detected)) if i not in matched_det]
    return tp_exact, tp_partial, fn, fp


# ---------------------------------------------------------------------------
# Document-level evaluation
# ---------------------------------------------------------------------------

def evaluate_document(
    doc_id: str,
    doc_type: str,
    language: str,
    quality: str,
    api_status: str,
    processing_ms: int,
    gt_entities: list[dict],
    detected_entities: list[dict],
    iou_threshold: float = 0.5,
) -> DocResult:
    gt_records = [
        EntityRecord(text=e["text"], entity_type=e["type"], page=e.get("page"))
        for e in gt_entities
    ]
    det_records = [
        EntityRecord(
            text=e["text"],
            entity_type=e["type"],
            score=e.get("score", 0.0),
            source=e.get("source", ""),
        )
        for e in detected_entities
    ]

    # Global TP/FP/FN exclude out-of-scope types so they don't distort P/R/F1.
    gt_scope = [e for e in gt_records if e.entity_type not in OUT_OF_SCOPE_GT_TYPES]
    det_scope = [e for e in det_records if e.entity_type not in OUT_OF_SCOPE_GT_TYPES]
    tp_exact, tp_partial, fn_list, fp_list = match_entities(gt_scope, det_scope, iou_threshold)

    # Per-type breakdown includes ALL types (out-of-scope appear for reference).
    all_types = sorted(
        {e.entity_type for e in gt_records} | {e.entity_type for e in det_records}
    )
    by_type: dict[str, TypeResult] = {}
    for et in all_types:
        gt_t = [e for e in gt_records if e.entity_type == et]
        det_t = [e for e in det_records if e.entity_type == et]
        te, tp, fn_t, fp_t = match_entities(gt_t, det_t, iou_threshold)
        by_type[et] = TypeResult(
            entity_type=et, tp_exact=te, tp_partial=tp, fn=fn_t, fp=fp_t
        )

    return DocResult(
        doc_id=doc_id,
        doc_type=doc_type,
        language=language,
        quality=quality,
        api_status=api_status,
        processing_ms=processing_ms,
        tp_exact=tp_exact,
        tp_partial=tp_partial,
        fn=fn_list,
        fp=fp_list,
        by_type=by_type,
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _example_dict(e: EntityRecord, doc: DocResult) -> dict:
    return {
        "text": e.text,
        "type": e.entity_type,
        "doc_id": doc.doc_id,
        "doc_type": doc.doc_type,
        "quality": doc.quality,
        "language": doc.language,
    }


def aggregate_results(doc_results: list[DocResult]) -> dict:
    """Aggregate evaluation results across documents.

    Returns a dict with keys:
      overall, by_type, by_doc_type, by_language, by_quality
    Each value is an AggregatedMetrics instance (except by_type which maps
    entity_type → AggregatedMetrics).
    """
    overall = AggregatedMetrics()
    by_type: dict[str, AggregatedMetrics] = {}
    by_doc_type: dict[str, AggregatedMetrics] = {}
    by_language: dict[str, AggregatedMetrics] = {}
    by_quality: dict[str, AggregatedMetrics] = {}

    for doc in doc_results:
        for bucket, key in [
            (by_doc_type, doc.doc_type),
            (by_language, doc.language),
            (by_quality, doc.quality),
        ]:
            if key not in bucket:
                bucket[key] = AggregatedMetrics()

        overall.tp_exact += doc.tp_exact
        overall.tp_partial += doc.tp_partial
        overall.fn_count += len(doc.fn)
        overall.fp_count += len(doc.fp)
        overall.fn_examples.extend(_example_dict(e, doc) for e in doc.fn)
        overall.fp_examples.extend(_example_dict(e, doc) for e in doc.fp)

        for bucket, key in [
            (by_doc_type, doc.doc_type),
            (by_language, doc.language),
            (by_quality, doc.quality),
        ]:
            agg = bucket[key]
            agg.tp_exact += doc.tp_exact
            agg.tp_partial += doc.tp_partial
            agg.fn_count += len(doc.fn)
            agg.fp_count += len(doc.fp)

        for et, tr in doc.by_type.items():
            if et not in by_type:
                by_type[et] = AggregatedMetrics()
            agg = by_type[et]
            agg.tp_exact += tr.tp_exact
            agg.tp_partial += tr.tp_partial
            agg.fn_count += len(tr.fn)
            agg.fp_count += len(tr.fp)
            agg.fn_examples.extend(_example_dict(e, doc) for e in tr.fn)
            agg.fp_examples.extend(_example_dict(e, doc) for e in tr.fp)

    return {
        "overall": overall,
        "by_type": by_type,
        "by_doc_type": by_doc_type,
        "by_language": by_language,
        "by_quality": by_quality,
    }
