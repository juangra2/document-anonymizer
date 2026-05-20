"""Entry point for the evaluation pipeline.

Usage:
    python -m evaluation.run --input-pdfs input/pdfs/ --input-labels input/labels/ --output evaluation/report.html
    python -m evaluation.run --input-pdfs input/pdfs/ --input-labels input/labels/ --limit 5
"""
from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path

from evaluation.evaluator import run_evaluation
from evaluation.metrics import AggregatedMetrics, aggregate_results
from evaluation.report import generate_report


def _serialize(obj):
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    raise TypeError(f"Not JSON serializable: {type(obj)}")


def save_results_json(doc_results, aggregated: dict, path: Path) -> None:
    overall: AggregatedMetrics = aggregated["overall"]
    data = {
        "summary": {
            "total_docs": len(doc_results),
            "overall_precision": round(overall.precision(), 4),
            "overall_recall": round(overall.recall(), 4),
            "overall_f1": round(overall.f1(), 4),
            "tp_exact": overall.tp_exact,
            "tp_partial": overall.tp_partial,
            "fn": overall.fn_count,
            "fp": overall.fp_count,
        },
        "by_type": {
            et: {
                "precision": round(agg.precision(), 4),
                "recall": round(agg.recall(), 4),
                "f1": round(agg.f1(), 4),
                "tp_exact": agg.tp_exact,
                "tp_partial": agg.tp_partial,
                "fn": agg.fn_count,
                "fp": agg.fp_count,
            }
            for et, agg in aggregated["by_type"].items()
        },
        "doc_results": [dataclasses.asdict(r) for r in doc_results],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    print(f"Results JSON saved to: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluation pipeline for the document anonymizer.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--input-pdfs", type=Path, required=True,
        help="Directory containing the input PDFs.",
    )
    parser.add_argument(
        "--input-labels", type=Path, required=True,
        help="Directory containing the ground truth JSON files.",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("evaluation/report.html"),
        help="Output path for the HTML report.",
    )
    parser.add_argument(
        "--results-json", type=Path, default=Path("evaluation/results.json"),
        help="Output path for the raw results JSON.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Evaluate only the first N documents (alphabetical order).",
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Parallel workers for API calls (1 = sequential, safe default).",
    )
    parser.add_argument(
        "--iou-threshold", type=float, default=0.5,
        help="Minimum text IoU to count as a partial TP match.",
    )
    args = parser.parse_args()

    doc_results = run_evaluation(
        pdfs_dir=args.input_pdfs,
        labels_dir=args.input_labels,
        limit=args.limit,
        workers=args.workers,
        iou_threshold=args.iou_threshold,
    )

    if not doc_results:
        print("No documents evaluated. Check paths and API availability.")
        return

    aggregated = aggregate_results(doc_results)
    save_results_json(doc_results, aggregated, args.results_json)
    generate_report(doc_results, aggregated, args.output)

    overall = aggregated["overall"]
    print(f"\n{'='*55}")
    print(f"  Documents evaluated : {len(doc_results)}")
    print(f"  Precision           : {overall.precision():.3f}")
    print(f"  Recall              : {overall.recall():.3f}")
    print(f"  F1                  : {overall.f1():.3f}")
    print(f"  TP (exact/partial)  : {overall.tp_exact}/{overall.tp_partial}")
    print(f"  FN                  : {overall.fn_count}")
    print(f"  FP                  : {overall.fp_count}")
    print(f"{'='*55}")
    print(f"\nReport: {args.output}")

    by_type = aggregated["by_type"]
    if by_type:
        print("\nPer entity type:")
        for et in sorted(by_type, key=lambda t: -by_type[t].f1()):
            agg = by_type[et]
            print(
                f"  {et:<22} P={agg.precision():.2f}  R={agg.recall():.2f}  "
                f"F1={agg.f1():.2f}  (TP={agg.tp} FN={agg.fn_count} FP={agg.fp_count})"
            )


if __name__ == "__main__":
    main()
