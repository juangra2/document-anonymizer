"""Generate a self-contained HTML evaluation report.

All CSS and SVG charts are inlined — no external dependencies or CDN.
"""
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from evaluation.metrics import AggregatedMetrics, DocResult

_MAX_EXAMPLES = 5  # FN/FP examples shown per entity type


# ---------------------------------------------------------------------------
# SVG bar chart helpers
# ---------------------------------------------------------------------------

def _bar_chart_svg(
    labels: list[str],
    series: dict[str, list[float]],
    colors: dict[str, str],
    width: int = 900,
    bar_height: int = 20,
    gap: int = 6,
    group_gap: int = 14,
    x_label_width: int = 220,
    title: str = "",
) -> str:
    """Horizontal grouped bar chart as inline SVG."""
    n_groups = len(labels)
    n_series = len(series)
    if n_groups == 0:
        return ""

    row_h = n_series * (bar_height + gap) + group_gap
    chart_h = n_groups * row_h + 40  # 40px for axis
    chart_w = width - x_label_width

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{chart_h + 20}" '
        f'style="font-family:inherit;font-size:11px;display:block;margin:0 auto">',
    ]

    if title:
        lines.append(
            f'<text x="{width//2}" y="14" text-anchor="middle" '
            f'font-weight="bold" font-size="13">{html.escape(title)}</text>'
        )

    # Grid lines at 0, 0.25, 0.5, 0.75, 1.0
    for v in (0, 0.25, 0.5, 0.75, 1.0):
        x = x_label_width + int(v * chart_w)
        lines.append(
            f'<line x1="{x}" y1="20" x2="{x}" y2="{chart_h}" '
            f'stroke="#ddd" stroke-width="1"/>'
        )
        lines.append(
            f'<text x="{x}" y="{chart_h + 14}" text-anchor="middle" '
            f'fill="#888">{v:.0%}</text>'
        )

    for gi, label in enumerate(labels):
        y_base = 20 + gi * row_h
        # Label
        lines.append(
            f'<text x="{x_label_width - 6}" y="{y_base + row_h // 2 + 4}" '
            f'text-anchor="end" fill="#333">{html.escape(label)}</text>'
        )
        for si, (sname, vals) in enumerate(series.items()):
            val = max(0.0, min(1.0, vals[gi]))
            bar_w = int(val * chart_w)
            y = y_base + si * (bar_height + gap)
            color = colors.get(sname, "#888")
            lines.append(
                f'<rect x="{x_label_width}" y="{y}" '
                f'width="{bar_w}" height="{bar_height}" fill="{color}" rx="2"/>'
            )
            if bar_w > 28:
                lines.append(
                    f'<text x="{x_label_width + bar_w - 4}" y="{y + bar_height - 5}" '
                    f'text-anchor="end" fill="white" font-size="10">'
                    f'{val:.1%}</text>'
                )
            else:
                lines.append(
                    f'<text x="{x_label_width + bar_w + 4}" y="{y + bar_height - 5}" '
                    f'text-anchor="start" fill="#555" font-size="10">'
                    f'{val:.1%}</text>'
                )

    # Legend
    lx = x_label_width
    ly = chart_h + 4
    for sname, color in colors.items():
        lines.append(
            f'<rect x="{lx}" y="{ly}" width="12" height="12" fill="{color}" rx="2"/>'
        )
        lines.append(
            f'<text x="{lx + 16}" y="{ly + 10}" fill="#555">{html.escape(sname)}</text>'
        )
        lx += 80

    lines.append("</svg>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       font-size: 14px; color: #222; background: #f7f8fa; line-height: 1.5; }
h1 { font-size: 24px; font-weight: 700; color: #1a1a2e; }
h2 { font-size: 17px; font-weight: 600; color: #1a1a2e; margin-bottom: 12px; }
h3 { font-size: 14px; font-weight: 600; color: #444; margin-bottom: 8px; }
.page { max-width: 1100px; margin: 0 auto; padding: 24px 16px 60px; }
.header { background: linear-gradient(135deg,#1a1a2e,#16213e);
          color: white; padding: 32px; border-radius: 12px; margin-bottom: 24px; }
.header p { opacity: .75; font-size: 13px; margin-top: 6px; }
.cards { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }
.card { background: white; border-radius: 10px; padding: 20px 24px;
        box-shadow: 0 1px 4px rgba(0,0,0,.08); flex: 1; min-width: 160px; }
.card .value { font-size: 28px; font-weight: 700; margin-top: 6px; }
.card .label { font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: .5px; }
.section { background: white; border-radius: 10px; padding: 24px;
           box-shadow: 0 1px 4px rgba(0,0,0,.08); margin-bottom: 20px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { background: #f0f2f5; text-align: left; padding: 8px 10px; font-weight: 600;
     border-bottom: 2px solid #ddd; }
td { padding: 7px 10px; border-bottom: 1px solid #eee; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: #fafbff; }
.good { color: #16a34a; font-weight: 600; }
.warn { color: #d97706; font-weight: 600; }
.bad  { color: #dc2626; font-weight: 600; }
.tag  { display: inline-block; padding: 2px 7px; border-radius: 4px;
        font-size: 11px; font-weight: 600; }
.tag-q-clean    { background:#d1fae5; color:#065f46; }
.tag-q-scanned  { background:#fef3c7; color:#92400e; }
.tag-q-watermark{ background:#ede9fe; color:#5b21b6; }
.tag-lang { background:#e0f2fe; color:#075985; }
.chip { display:inline-block; padding:1px 6px; border-radius:12px;
        background:#e5e7eb; font-size:11px; color:#374151; margin:1px; }
.example-block { background:#f9fafb; border:1px solid #e5e7eb; border-radius:6px;
                 padding:10px 14px; margin:4px 0; }
.example-block .entity-text { font-family: monospace; font-size:13px;
                               background:#fff3cd; padding:1px 5px; border-radius:3px; }
.example-block .meta { font-size:11px; color:#6b7280; margin-top:3px; }
.cols2 { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
@media(max-width:700px){ .cols2{ grid-template-columns:1fr; } }
"""

_COLORS = {"Precision": "#3b82f6", "Recall": "#10b981", "F1": "#f59e0b"}


def _pct(v: float) -> str:
    cls = "good" if v >= 0.8 else ("warn" if v >= 0.5 else "bad")
    return f'<span class="{cls}">{v:.1%}</span>'


def _count(n: int) -> str:
    return f"{n:,}"


def _metric_row(label: str, agg: AggregatedMetrics) -> str:
    return (
        f"<tr><td>{html.escape(label)}</td>"
        f"<td>{_count(agg.tp_exact)}</td>"
        f"<td>{_count(agg.tp_partial)}</td>"
        f"<td>{_count(agg.fn_count)}</td>"
        f"<td>{_count(agg.fp_count)}</td>"
        f"<td>{_pct(agg.precision())}</td>"
        f"<td>{_pct(agg.recall())}</td>"
        f"<td>{_pct(agg.f1())}</td></tr>"
    )


def _metric_table_html(rows: list[tuple[str, AggregatedMetrics]], caption: str = "") -> str:
    header = (
        "<table>"
        + (f"<caption style='text-align:left;font-weight:600;margin-bottom:8px'>{html.escape(caption)}</caption>" if caption else "")
        + "<thead><tr>"
        "<th></th><th>TP-exact</th><th>TP-partial</th><th>FN</th><th>FP</th>"
        "<th>Precision</th><th>Recall</th><th>F1</th>"
        "</tr></thead><tbody>"
    )
    body = "".join(_metric_row(label, agg) for label, agg in rows)
    return header + body + "</tbody></table>"


def _quality_tag(q: str) -> str:
    css = {"clean": "tag-q-clean", "scanned": "tag-q-scanned", "watermark": "tag-q-watermark"}.get(q, "")
    return f'<span class="tag {css}">{html.escape(q)}</span>'


def _lang_tag(lang: str) -> str:
    return f'<span class="tag tag-lang">{html.escape(lang)}</span>'


def _examples_section(examples: list[dict], kind: str, max_per_type: int = _MAX_EXAMPLES) -> str:
    by_type: dict[str, list[dict]] = {}
    for ex in examples:
        by_type.setdefault(ex["type"], []).append(ex)

    if not by_type:
        return f"<p style='color:#888'>No {kind} examples found.</p>"

    parts = []
    for etype in sorted(by_type):
        items = by_type[etype][:max_per_type]
        parts.append(f"<h3>{html.escape(etype)} ({len(by_type[etype])} total)</h3>")
        for ex in items:
            text_esc = html.escape(ex["text"])
            doc_id_esc = html.escape(ex["doc_id"])
            doc_type_esc = html.escape(ex["doc_type"])
            parts.append(
                f'<div class="example-block">'
                f'<span class="entity-text">{text_esc}</span>'
                f'<div class="meta">{doc_id_esc} &nbsp;·&nbsp; {doc_type_esc}'
                f' &nbsp;·&nbsp; {_quality_tag(ex["quality"])} {_lang_tag(ex["language"])}'
                f'</div></div>'
            )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main report generator
# ---------------------------------------------------------------------------

def generate_report(
    doc_results: list[DocResult],
    aggregated: dict,
    output_path: Path,
) -> None:
    overall: AggregatedMetrics = aggregated["overall"]
    by_type: dict[str, AggregatedMetrics] = aggregated["by_type"]
    by_doc_type: dict[str, AggregatedMetrics] = aggregated["by_doc_type"]
    by_language: dict[str, AggregatedMetrics] = aggregated["by_language"]
    by_quality: dict[str, AggregatedMetrics] = aggregated["by_quality"]

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    n_docs = len(doc_results)
    n_errors = sum(1 for d in doc_results if d.api_status.startswith("error"))
    avg_ms = int(sum(d.processing_ms for d in doc_results) / n_docs) if n_docs else 0

    # ---- entity-type bar chart ----
    type_labels = sorted(by_type.keys())
    chart_svg = _bar_chart_svg(
        labels=type_labels,
        series={
            "Precision": [by_type[t].precision() for t in type_labels],
            "Recall":    [by_type[t].recall()    for t in type_labels],
            "F1":        [by_type[t].f1()         for t in type_labels],
        },
        colors=_COLORS,
        bar_height=18,
        gap=4,
        group_gap=10,
        x_label_width=200,
        title="Precision / Recall / F1 por tipo de entidad",
    )

    # ---- per-document table (sorted by F1 asc — worst first) ----
    sorted_docs = sorted(doc_results, key=lambda d: d.f1())
    doc_rows = []
    for d in sorted_docs:
        types_detected = ", ".join(sorted(d.by_type.keys()))
        doc_rows.append(
            f"<tr>"
            f"<td>{html.escape(d.doc_id)}</td>"
            f"<td>{html.escape(d.doc_type)}</td>"
            f"<td>{_quality_tag(d.quality)} {_lang_tag(d.language)}</td>"
            f"<td>{_pct(d.precision())}</td>"
            f"<td>{_pct(d.recall())}</td>"
            f"<td>{_pct(d.f1())}</td>"
            f"<td>{d.tp}/{len(d.fn)}/{len(d.fp)}</td>"
            f"<td>{d.processing_ms} ms</td>"
            f"<td style='font-size:11px;color:#888'>{html.escape(d.api_status)}</td>"
            f"</tr>"
        )

    html_parts = [
        "<!DOCTYPE html>",
        '<html lang="es">',
        "<head>",
        '<meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>Evaluation Report — Document Anonymizer</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body>",
        '<div class="page">',

        # --- Header ---
        '<div class="header">',
        "<h1>Evaluation Report — Document Anonymizer</h1>",
        f'<p>Generado el {now} &nbsp;·&nbsp; {n_docs} documentos evaluados</p>',
        "</div>",

        # --- Summary cards ---
        '<div class="cards">',
        f'<div class="card"><div class="label">F1 global</div>'
        f'<div class="value" style="color:#f59e0b">{overall.f1():.1%}</div></div>',
        f'<div class="card"><div class="label">Precision</div>'
        f'<div class="value" style="color:#3b82f6">{overall.precision():.1%}</div></div>',
        f'<div class="card"><div class="label">Recall</div>'
        f'<div class="value" style="color:#10b981">{overall.recall():.1%}</div></div>',
        f'<div class="card"><div class="label">TP total</div>'
        f'<div class="value">{_count(overall.tp)}</div></div>',
        f'<div class="card"><div class="label">FN total</div>'
        f'<div class="value" style="color:#dc2626">{_count(overall.fn_count)}</div></div>',
        f'<div class="card"><div class="label">FP total</div>'
        f'<div class="value" style="color:#d97706">{_count(overall.fp_count)}</div></div>',
        f'<div class="card"><div class="label">Errores API</div>'
        f'<div class="value">{n_errors}</div></div>',
        f'<div class="card"><div class="label">Tiempo medio</div>'
        f'<div class="value">{avg_ms} ms</div></div>',
        "</div>",

        # --- By entity type: table ---
        '<div class="section">',
        "<h2>Métricas por tipo de entidad</h2>",
        _metric_table_html(
            [(et, by_type[et]) for et in sorted(by_type)],
        ),
        "</div>",

        # --- Bar chart ---
        '<div class="section">',
        "<h2>Gráfico Precision / Recall / F1 por tipo</h2>",
        chart_svg,
        "</div>",

        # --- Breakdowns ---
        '<div class="section">',
        "<h2>Desglose por tipo de documento</h2>",
        _metric_table_html(
            [(dt, by_doc_type[dt]) for dt in sorted(by_doc_type)],
        ),
        "</div>",

        '<div class="section cols2">',
        '<div>',
        "<h2>Por idioma</h2>",
        _metric_table_html(
            [(lang, by_language[lang]) for lang in sorted(by_language)],
        ),
        "</div>",
        '<div>',
        "<h2>Por calidad</h2>",
        _metric_table_html(
            [(q, by_quality[q]) for q in sorted(by_quality)],
        ),
        "</div>",
        "</div>",

        # --- FN examples ---
        '<div class="section">',
        "<h2>Ejemplos de Falsos Negativos por tipo (entidades que escaparon)</h2>",
        _examples_section(overall.fn_examples, "FN"),
        "</div>",

        # --- FP examples ---
        '<div class="section">',
        "<h2>Ejemplos de Falsos Positivos por tipo (entidades detectadas de más)</h2>",
        _examples_section(overall.fp_examples, "FP"),
        "</div>",

        # --- Per-document table ---
        '<div class="section">',
        "<h2>Detalle por documento (ordenado por F1 ascendente)</h2>",
        "<div style='overflow-x:auto'>",
        "<table>",
        "<thead><tr>",
        "<th>Documento</th><th>Tipo</th><th>Calidad/Idioma</th>",
        "<th>Precision</th><th>Recall</th><th>F1</th>",
        "<th>TP/FN/FP</th><th>Tiempo</th><th>Estado API</th>",
        "</tr></thead><tbody>",
        "\n".join(doc_rows),
        "</tbody></table>",
        "</div>",
        "</div>",

        "</div>",  # .page
        "</body></html>",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(html_parts), encoding="utf-8")
