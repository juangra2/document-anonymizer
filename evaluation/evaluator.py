"""HTTP evaluation pipeline for the document anonymizer.

For each PDF in the corpus:
  1. POST to /anonymize?output=zip&include_entities=true&mode=strict
  2. Extract report.json from the ZIP response
  3. Compare detected_entities against the ground truth JSON
  4. Return a DocResult with TP/FP/FN counts and per-type breakdowns

Concurrency: ThreadPoolExecutor (httpx is synchronous here).
"""
from __future__ import annotations

import json
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path

import httpx

from evaluation.metrics import DocResult, EntityRecord, EntityRecord, evaluate_document

API_ENDPOINT = "http://127.0.0.1:8000/anonymize"
_DEFAULT_TIMEOUT = 180  # seconds per document


# ---------------------------------------------------------------------------
# Ground truth loading
# ---------------------------------------------------------------------------

def load_ground_truth(labels_dir: Path) -> dict[str, dict]:
    """Return {document_id: label_dict} for all JSONs in labels_dir."""
    gt: dict[str, dict] = {}
    for path in sorted(labels_dir.glob("*.json")):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        doc_id = data.get("document_id", path.stem)
        gt[doc_id] = data
    return gt


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------

def call_api(pdf_path: Path, timeout: int = _DEFAULT_TIMEOUT) -> tuple[dict, str | None]:
    """POST pdf_path to /anonymize?output=zip&include_entities=true.

    Returns (report_dict, error_string). error_string is None on success.
    """
    try:
        with open(pdf_path, "rb") as fh:
            response = httpx.post(
                API_ENDPOINT,
                files={"file": (pdf_path.name, fh, "application/pdf")},
                params={
                    "output": "zip",
                    "include_entities": "true",
                    "mode": "strict",
                },
                timeout=timeout,
            )

        if response.status_code != 200:
            snippet = response.text[:300].replace("\n", " ")
            return {}, f"HTTP {response.status_code}: {snippet}"

        with zipfile.ZipFile(BytesIO(response.content)) as zf:
            with zf.open("report.json") as rf:
                report = json.load(rf)

        return report, None

    except httpx.ConnectError:
        return {}, "Connection refused — is the API server running on port 8000?"
    except Exception as exc:
        return {}, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Single-document evaluation
# ---------------------------------------------------------------------------

def _all_gt_as_fn(gt_doc: dict) -> DocResult:
    """Return a DocResult where every GT entity is a FN (API failed)."""
    fn_list = [
        EntityRecord(text=e["text"], entity_type=e["type"], page=e.get("page"))
        for e in gt_doc.get("entities", [])
    ]
    return DocResult(
        doc_id=gt_doc["document_id"],
        doc_type=gt_doc["document_type"],
        language=gt_doc["language"],
        quality=gt_doc.get("quality", "unknown"),
        api_status="api_error",
        processing_ms=0,
        fn=fn_list,
    )


def evaluate_single(
    pdf_path: Path,
    gt: dict[str, dict],
    iou_threshold: float = 0.5,
) -> DocResult | None:
    """Evaluate one document. Returns None when no ground truth exists."""
    doc_id = pdf_path.stem
    gt_doc = gt.get(doc_id)
    if gt_doc is None:
        return None

    report, error = call_api(pdf_path)
    if error:
        result = _all_gt_as_fn(gt_doc)
        result = DocResult(
            doc_id=result.doc_id,
            doc_type=result.doc_type,
            language=result.language,
            quality=result.quality,
            api_status=f"error: {error[:120]}",
            processing_ms=0,
            fn=result.fn,
        )
        return result

    return evaluate_document(
        doc_id=doc_id,
        doc_type=gt_doc["document_type"],
        language=gt_doc["language"],
        quality=gt_doc.get("quality", "unknown"),
        api_status=report.get("status", "unknown"),
        processing_ms=report.get("processing_ms") or 0,
        gt_entities=gt_doc.get("entities", []),
        detected_entities=report.get("detected_entities", []),
        iou_threshold=iou_threshold,
    )


# ---------------------------------------------------------------------------
# Batch evaluation
# ---------------------------------------------------------------------------

def run_evaluation(
    pdfs_dir: Path,
    labels_dir: Path,
    limit: int | None = None,
    workers: int = 1,
    iou_threshold: float = 0.5,
    verbose: bool = True,
) -> list[DocResult]:
    """Evaluate all PDFs in pdfs_dir against labels_dir ground truth.

    workers=1 is the safe default (the API itself is already single-threaded
    per request; concurrency > 1 is only useful when the server can handle
    parallel requests without contention).
    """
    gt = load_ground_truth(labels_dir)
    pdf_files = sorted(pdfs_dir.glob("*.pdf"))
    if limit is not None:
        pdf_files = pdf_files[:limit]

    total = len(pdf_files)
    if verbose:
        print(f"Evaluating {total} document(s) (workers={workers}, IoU≥{iou_threshold})")

    results: list[DocResult] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(evaluate_single, pdf, gt, iou_threshold): pdf
            for pdf in pdf_files
        }
        completed = 0
        for future in as_completed(futures):
            pdf = futures[future]
            completed += 1
            try:
                result = future.result()
            except Exception as exc:
                if verbose:
                    print(f"  [{completed}/{total}] EXCEPTION {pdf.stem}: {exc}")
                continue

            if result is None:
                if verbose:
                    print(f"  [{completed}/{total}] SKIP {pdf.stem} (no ground truth)")
                continue

            results.append(result)

            if verbose:
                p = result.precision()
                r = result.recall()
                f = result.f1()
                tp = result.tp
                fn = len(result.fn)
                fp = len(result.fp)
                ms = result.processing_ms
                print(
                    f"  [{completed}/{total}] {result.doc_id}"
                    f"  P={p:.2f} R={r:.2f} F1={f:.2f}"
                    f"  TP={tp} FN={fn} FP={fp}"
                    f"  ({result.quality}/{result.language}, {ms}ms)"
                )

    if verbose:
        print(f"\nFinished: {len(results)}/{total} documents evaluated.")

    return results
