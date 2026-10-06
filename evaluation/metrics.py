"""Entity matching and metric computation for the evaluation pipeline.

Matching strategy
-----------------
- Exact match  : text_iou == 1.0 (case-insensitive) and same entity_type → TP-exact
- Partial match: text_iou >= iou_threshold (default 0.5) and same type → TP-partial
- Unmatched GT : False Negative
- Unmatched det: False Positive

Matching is one-to-one (greedy bipartite, highest IoU first).

Se empareja por VALOR, no por aparicion: cada valor distinto de la etiqueta
cuenta una vez, y esta detectado si el sistema lo detecto en algun sitio del
documento (la geometria tapa todas sus apariciones).  Si cada aparicion queda
tapada lo mide la lectura de barras, que abre el PDF.  Un valor que solo
aparece en paginas que salen enteras en negro cuenta como detectado: lo decide
la negrura de la salida, no el clasificador.  Durante la transicion se guarda
tambien la cuenta antigua, por aparicion (DocResult.por_aparicion).
"""
from __future__ import annotations

import re
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
    #: Camino por el que el servicio proceso el documento (pdf_digital,
    #: pdf_scanned, pdf_hybrid).  Sin esto no se puede comprobar que un
    #: documento del corpus escaneado no se haya ido por el digital, que es
    #: exactamente el fallo que estuvo escondido meses.
    camino: str = ""
    tp_exact: int = 0
    tp_partial: int = 0
    fn: list[EntityRecord] = field(default_factory=list)
    fp: list[EntityRecord] = field(default_factory=list)
    by_type: dict[str, TypeResult] = field(default_factory=dict)
    #: De los TP, cuantos son valores que solo vivian en paginas que salieron
    #: enteras en negro (ya sumados en tp_exact).
    tp_pagina_negra: int = 0
    #: La cuenta antigua, aparicion por aparicion: {"tp", "fn", "fp"}.  Solo
    #: para la transicion; ver el docstring del modulo.
    por_aparicion: dict = field(default_factory=dict)

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
    # ORGANIZATION ya no esta fuera de alcance: el corpus anota la razon social
    # de empresa privada como PII (requisito del ayuntamiento), asi que debe
    # contar en la metrica global.
    "PROFESSIONAL_LICENSE",
    "ROLE_TABLE_ROW",
    # Importes de presupuesto de obra: el corpus NO los anota, porque cuando se
    # generó la decision era no ocultarlos.  Ahora se ofrecen como tipo propio,
    # marcado por defecto; contarlos como falsos positivos mediria la anotacion,
    # no el sistema.  Queda visible en el desglose por tipo.
    "BUDGET_AMOUNT",
})


# Telefonos con el formato que inventa nuestro generador de corpus
# (mobile_parens): "(618) 741-017", nueve cifras agrupadas al estilo de EE. UU.
# No es como se escribe un telefono en Espana, asi que fallar en el no dice nada
# del sistema; ajustar el detector a el seria ajustarse a datos falsos.  Se
# excluye por su FORMA, no todo PHONE_NUMBER.
# PROVISIONAL: el generador ya no lo produce.  Cuando se regeneren los corpus
# (ahora congelados para comparar con la linea base), QUITAR esta exclusion.
_TELEFONO_FORMATO_DEL_GENERADOR = re.compile(r"\(\d{3}\) \d{3}-\d{3}")


def fuera_de_alcance(tipo: str, texto: str = "") -> bool:
    """Si un dato queda fuera de la metrica global (y de la lectura de barras)."""
    if tipo in OUT_OF_SCOPE_GT_TYPES:
        return True
    return tipo == "PHONE_NUMBER" and bool(
        _TELEFONO_FORMATO_DEL_GENERADOR.fullmatch((texto or "").strip())
    )


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

# Fraccion de pixeles oscuros a partir de la cual una pagina de la salida esta
# tachada entera.  Una pagina tachada da ~1.0; una con barras sueltas, unas
# centesimas.  Una copia de DNI recortada (solo la tarjeta) NO pasa: ahi lo que
# cuenta es si se detecto.
NEGRURA_PAGINA_TACHADA = 0.9


def _clave(e: EntityRecord) -> tuple[str, str]:
    return e.entity_type, " ".join(e.text.lower().split())


def _emparejar_por_valor(
    gt: list[EntityRecord],
    det: list[EntityRecord],
    paginas_en_negro: set[int],
    iou_threshold: float,
) -> tuple[int, int, list[EntityRecord], list[EntityRecord], int]:
    """(tp_exact, tp_partial, fn, fp, tp_pagina_negra) contando cada valor una vez."""
    paginas: dict[tuple, set] = {}
    gt_unicos: dict[tuple, EntityRecord] = {}
    for e in gt:
        k = _clave(e)
        gt_unicos.setdefault(k, e)
        paginas.setdefault(k, set()).add(e.page)
    det_unicos: dict[tuple, EntityRecord] = {}
    for e in det:
        det_unicos.setdefault(_clave(e), e)

    te, tp, fn, fp = match_entities(list(gt_unicos.values()), list(det_unicos.values()),
                                    iou_threshold)
    # Un valor no detectado cuyas apariciones caen TODAS en paginas en negro
    # esta tapado.  Si alguna aparicion esta en una pagina visible, o no se
    # sabe en que pagina estaba, sigue siendo falso negativo.
    negros = 0
    fn_final = []
    for e in fn:
        pags = paginas[_clave(e)]
        if paginas_en_negro and None not in pags and pags <= paginas_en_negro:
            negros += 1
        else:
            fn_final.append(e)
    return te + negros, tp, fn_final, fp, negros


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
    camino: str = "",
    paginas_en_negro: set[int] | None = None,
) -> DocResult:
    """Evalua un documento.  `paginas_en_negro`: numeracion de usuario (1 = primera)."""
    negras = set(paginas_en_negro or ())
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
    gt_scope = [e for e in gt_records if not fuera_de_alcance(e.entity_type, e.text)]
    det_scope = [e for e in det_records if not fuera_de_alcance(e.entity_type, e.text)]
    tp_exact, tp_partial, fn_list, fp_list, tp_negra = _emparejar_por_valor(
        gt_scope, det_scope, negras, iou_threshold)
    a_te, a_tp, a_fn, a_fp = match_entities(gt_scope, det_scope, iou_threshold)

    # Per-type breakdown includes ALL types (out-of-scope appear for reference).
    all_types = sorted(
        {e.entity_type for e in gt_records} | {e.entity_type for e in det_records}
    )
    by_type: dict[str, TypeResult] = {}
    for et in all_types:
        gt_t = [e for e in gt_records if e.entity_type == et]
        det_t = [e for e in det_records if e.entity_type == et]
        te, tp, fn_t, fp_t, _ = _emparejar_por_valor(gt_t, det_t, negras, iou_threshold)
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
        camino=camino,
        tp_exact=tp_exact,
        tp_partial=tp_partial,
        fn=fn_list,
        fp=fp_list,
        by_type=by_type,
        tp_pagina_negra=tp_negra,
        por_aparicion={"tp": a_te + a_tp, "fn": len(a_fn), "fp": len(a_fp)},
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
    # La cuenta antigua, por aparicion, solo para la transicion.
    por_aparicion = AggregatedMetrics()
    tp_pagina_negra = 0
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
        tp_pagina_negra += doc.tp_pagina_negra
        if doc.por_aparicion:
            por_aparicion.tp_exact += doc.por_aparicion["tp"]
            por_aparicion.fn_count += doc.por_aparicion["fn"]
            por_aparicion.fp_count += doc.por_aparicion["fp"]

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
        "overall_por_aparicion": por_aparicion,
        "tp_pagina_negra": tp_pagina_negra,
        "by_type": by_type,
        "by_doc_type": by_doc_type,
        "by_language": by_language,
        "by_quality": by_quality,
    }
