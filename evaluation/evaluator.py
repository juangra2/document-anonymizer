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

def call_api(
    pdf_path: Path,
    timeout: int = _DEFAULT_TIMEOUT,
    disabled_types: list[str] | None = None,
    guardar_salidas: Path | None = None,
) -> tuple[dict, str | None]:
    """POST pdf_path to /anonymize?output=zip&include_entities=true.

    Returns (report_dict, error_string). error_string is None on success.

    El zip trae el informe Y el PDF anonimizado, y hasta ahora el PDF se tiraba.
    Con `guardar_salidas` se escribe en disco: no cuesta ni un segundo de
    proceso y es lo unico que permite comprobar despues si las barras negras
    tapan de verdad el dato, que es algo que las metricas de entidades no ven.
    """
    try:
        with open(pdf_path, "rb") as fh:
            response = httpx.post(
                API_ENDPOINT,
                files={"file": (pdf_path.name, fh, "application/pdf")},
                params={
                    "output": "zip",
                    "include_entities": "true",
                    # Tipos desactivados en la peticion (compuerta del bloque 2b):
                    # httpx repite la clave por cada valor.
                    "disabled_types": list(disabled_types or []),
                },
                timeout=timeout,
            )

        if response.status_code != 200:
            snippet = response.text[:300].replace("\n", " ")
            return {}, f"HTTP {response.status_code}: {snippet}"

        with zipfile.ZipFile(BytesIO(response.content)) as zf:
            with zf.open("report.json") as rf:
                report = json.load(rf)

            pdfs = [n for n in zf.namelist() if n.lower().endswith(".pdf")]
            # Paginas de la salida que estan de verdad en negro: es lo que deja
            # contar como detectado un dato de una pagina tachada entera (ver
            # metrics.NEGRURA_PAGINA_TACHADA).  Se mide sobre el PDF, no se
            # cree al clasificador.
            if pdfs:
                from evaluation.lectura_salida import negrura
                from evaluation.metrics import NEGRURA_PAGINA_TACHADA
                report["_paginas_en_negro"] = [
                    i + 1 for i, n in enumerate(negrura(zf.read(pdfs[0])))
                    if n >= NEGRURA_PAGINA_TACHADA
                ]

            if guardar_salidas is not None:
                guardar_salidas.mkdir(parents=True, exist_ok=True)
                if not pdfs:
                    report.setdefault("warnings", []).append(
                        "El zip no traia ningun PDF: no se pudo guardar la salida."
                    )
                for nombre in pdfs:
                    destino = guardar_salidas / f"{pdf_path.stem}.pdf"
                    destino.write_bytes(zf.read(nombre))

        return report, None

    except httpx.ConnectError:
        return {}, "Connection refused — is the API server running on port 8000?"
    except Exception as exc:
        return {}, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Single-document evaluation
# ---------------------------------------------------------------------------

def _doc_result_desde_json(obj: dict) -> DocResult:
    """Reconstruye un DocResult guardado en el checkpoint.

    `dataclasses.asdict` aplana las entidades a diccionarios, y volver a meterlos
    tal cual dejaba un DocResult con dicts donde el agregador espera objetos:
    la tanda se evaluaba entera —hora y media— y reventaba al agregar, con todo
    el trabajo hecho y ningun resultado escrito.
    """
    from evaluation.metrics import EntityRecord, TypeResult

    def entidades(bruto) -> list[EntityRecord]:
        return [
            e if isinstance(e, EntityRecord) else EntityRecord(
                **{k: v for k, v in e.items()
                   if k in EntityRecord.__dataclass_fields__}
            )
            for e in (bruto or [])
        ]

    campos = {k: v for k, v in obj.items() if k in DocResult.__dataclass_fields__}
    campos["fn"] = entidades(campos.get("fn"))
    campos["fp"] = entidades(campos.get("fp"))
    campos["detectados"] = entidades(campos.get("detectados"))

    por_tipo = {}
    for tipo, valor in (campos.get("by_type") or {}).items():
        if isinstance(valor, TypeResult):
            por_tipo[tipo] = valor
        else:
            datos = {k: v for k, v in valor.items()
                     if k in TypeResult.__dataclass_fields__}
            for clave in ("fn", "fp"):
                if clave in datos:
                    datos[clave] = entidades(datos[clave])
            por_tipo[tipo] = TypeResult(**datos)
    campos["by_type"] = por_tipo

    return DocResult(**campos)


def _all_gt_as_fn(gt_doc: dict) -> DocResult:
    """Return a DocResult where every GT entity is a FN (API failed)."""
    fn_list = [
        EntityRecord(text=e["text"], entity_type=e["type"], page=e.get("page"))
        for e in gt_doc.get("entities", [])
    ]
    return DocResult(
        doc_id=gt_doc["document_id"],
        doc_type=gt_doc.get("document_type", "unknown"),
        language=gt_doc.get("language", "unknown"),
        quality=gt_doc.get("quality", "unknown"),
        api_status="api_error",
        processing_ms=0,
        fn=fn_list,
    )


def evaluate_single(
    pdf_path: Path,
    gt: dict[str, dict],
    iou_threshold: float = 0.5,
    disabled_types: list[str] | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
    guardar_salidas: Path | None = None,
) -> DocResult | None:
    """Evaluate one document. Returns None when no ground truth exists."""
    doc_id = pdf_path.stem
    gt_doc = gt.get(doc_id)
    if gt_doc is None:
        return None

    report, error = call_api(pdf_path, timeout=timeout,
                             disabled_types=disabled_types,
                             guardar_salidas=guardar_salidas)
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

    resultado = evaluate_document(
        doc_id=doc_id,
        doc_type=gt_doc.get("document_type", "unknown"),
        language=gt_doc.get("language", "unknown"),
        quality=gt_doc.get("quality", "unknown"),
        api_status=report.get("status", "unknown"),
        camino=report.get("document_type", ""),
        processing_ms=report.get("processing_ms") or 0,
        gt_entities=gt_doc.get("entities", []),
        detected_entities=report.get("detected_entities", []),
        iou_threshold=iou_threshold,
        paginas_en_negro=set(report.get("_paginas_en_negro", [])),
    )
    ocr = report.get("ocr") or {}
    resultado.red = {
        "imagenes_tachadas": ocr.get("safety_net_images_redacted", 0),
        "zonas_repasadas": ocr.get("safety_net_zones_repainted", 0),
        "siguen_legibles": ocr.get("safety_net_still_readable", 0),
        "residuos_por_tipo": report.get("residual_entities_by_type") or {},
        "revision_manual": bool(report.get("manual_review_required")),
    }
    return resultado


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
    checkpoint_path: Path | None = None,
    resume: bool = False,
    disabled_types: list[str] | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
    guardar_salidas: Path | None = None,
) -> list[DocResult]:
    """Evaluate all PDFs in pdfs_dir against labels_dir ground truth.

    workers=1 is the safe default (the API itself is already single-threaded
    per request; concurrency > 1 is only useful when the server can handle
    parallel requests without contention).

    checkpoint_path: if set, append each DocResult as JSONL after completion.
    resume: if True and checkpoint_path exists, skip already-evaluated docs.
    """
    import dataclasses

    gt = load_ground_truth(labels_dir)
    pdf_files = sorted(pdfs_dir.glob("*.pdf"))
    if limit is not None:
        pdf_files = pdf_files[:limit]

    # Load existing checkpoint results and skip those doc_ids.
    results: list[DocResult] = []
    done_ids: set[str] = set()
    if resume and checkpoint_path and checkpoint_path.exists():
        with open(checkpoint_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    r = _doc_result_desde_json(obj)
                    results.append(r)
                    done_ids.add(r.doc_id)
                except Exception as exc:
                    # Callar aqui convertia un checkpoint ilegible en "no hay
                    # nada que reanudar", y la tanda se repetia entera en
                    # silencio.  Mejor decirlo y seguir con el resto.
                    print(f"  aviso: linea de checkpoint descartada ({exc})")
        if verbose and done_ids:
            print(f"Resuming: loaded {len(done_ids)} results from checkpoint.")

    pdf_files = [p for p in pdf_files if p.stem not in done_ids]
    total = len(pdf_files) + len(done_ids)
    if verbose:
        remaining = len(pdf_files)
        print(f"Evaluating {total} document(s) (workers={workers}, IoU>={iou_threshold})"
              + (f", {remaining} remaining" if done_ids else ""))

    ckpt_file = open(checkpoint_path, "a", encoding="utf-8") if checkpoint_path else None
    completed = len(done_ids)

    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(evaluate_single, pdf, gt, iou_threshold, disabled_types,
                                timeout, guardar_salidas): pdf
                for pdf in pdf_files
            }
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

                if ckpt_file:
                    ckpt_file.write(json.dumps(dataclasses.asdict(result), ensure_ascii=False) + "\n")
                    ckpt_file.flush()

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
    finally:
        if ckpt_file:
            ckpt_file.close()

    if verbose:
        print(f"\nFinished: {len(results)}/{total} documents evaluated.")

    return results
