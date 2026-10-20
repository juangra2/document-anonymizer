"""Genera evaluation/informe_final.html: las metricas finales, para quien no ha seguido el trabajo.

Todo sale de ficheros bajo seguimiento, asi que se regenera tras cada medicion:

  evaluation/reference/c4/resultados_{digital,escaneado,hibrido}.json   metricas por corpus y tipo
  evaluation/reference/c4/lectura_barras.json                          lectura de barras y fugas
  evaluation/reference/c4/banco_contacto.json                          banco de telefonos y direcciones
  evaluation/reference/c4/comprobaciones.json                          casos dificiles y metadatos
  evaluation/linea_base/transicion_contabilidad.json                   evolucion (linea base a c4)
  README.md, seccion "Limitaciones conocidas y pendientes"

El HTML es autocontenido: sin librerias ni fuentes de internet.

Uso:
    python -m evaluation.informe_final [--ref evaluation/reference/c4] [--salida evaluation/informe_final.html]
"""
from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
CORPUS = ("digital", "escaneado", "hibrido")
NOMBRE = {"digital": "Digital", "escaneado": "Escaneado", "hibrido": "Híbrido"}
DESCRIPCION_CORPUS = {
    "digital": "PDF generados por ordenador, con capa de texto.",
    "escaneado": "PDF que son imágenes de papel escaneado: el texto se lee por OCR.",
    "hibrido": "PDF que mezclan páginas digitales y escaneadas, o páginas con imágenes pegadas.",
}
MEDICIONES = [
    ("linea_base_antes_del_arreglo", "Línea base", "28-09-2026", "Antes de los arreglos: los documentos híbridos iban por el camino digital."),
    ("c1_2026_10_06", "c1", "06-10-2026", "Lectura de códigos QR y de barras, código seguro de verificación."),
    ("c2_2026_10_07", "c2", "07-10-2026", "Arreglo del camino híbrido página a página y del tachado escaneado."),
    ("c3_2026_10_14", "c3", "14-10-2026", "Barrido de todas las apariciones de un dato, filtro de contacto institucional, nombres en el pie."),
    ("c4_2026_10_19", "c4", "19-10-2026", "Imágenes pegadas leídas por separado, formularios, páginas giradas, dos columnas, firmas en fila."),
]
TIPOS = {
    "PERSON": "Nombre de persona", "ES_DNI": "DNI / NIF", "ES_NIE": "NIE", "ES_CIF": "CIF de entidad",
    "ES_NSS": "Nº de la Seguridad Social", "PASSPORT": "Pasaporte", "ADDRESS": "Dirección postal",
    "POSTAL_CODE": "Código postal", "PHONE_NUMBER": "Teléfono", "EMAIL_ADDRESS": "Correo electrónico",
    "IBAN_CODE": "Cuenta bancaria (IBAN)", "DATE_OF_BIRTH": "Fecha de nacimiento", "DATE_TIME": "Otras fechas",
    "PLACE_OF_BIRTH": "Lugar de nacimiento", "CASE_NUMBER": "Número de expediente", "CSV": "Código seguro de verificación",
    "LICENSE_PLATE": "Matrícula", "IP_ADDRESS": "Dirección IP", "URL": "Dirección web", "QR_CODE": "Código QR",
    "MONEY": "Importe", "ORGANIZATION": "Organización", "LOCATION": "Lugar (municipio, provincia)",
}
TIPOS_DOC = {
    "contrato_laboral": "contratos laborales", "resolucion_universitaria": "resoluciones universitarias",
    "acuerdo_adscripcion": "acuerdos de adscripción", "formulario_medico": "formularios médicos",
    "notificacion_ayuntamiento": "notificaciones del ayuntamiento", "nomina": "nóminas",
    "contrato_prestacion_servicios": "contratos de servicios", "solicitud_generica": "solicitudes",
    "certificado_servicios": "certificados de servicios", "resolucion_administrativa": "resoluciones administrativas",
    "acuerdo_modificativo": "acuerdos modificativos", "notificacion_sancionador": "notificaciones de sanción",
    "certificado_empadronamiento": "certificados de empadronamiento", "dni_escaneado": "copias de DNI",
    "autoliquidacion": "autoliquidaciones",
    # hibrido: el tipo esta en el prefijo del identificador
    "hibrido_expediente_con_escaneo": "expedientes con páginas escaneadas",
    "hibrido_escaneado_con_digital": "escaneados con páginas digitales",
    "hibrido_impreso_y_normales": "impresos escaneados entre páginas digitales",
    "hibrido_pagina_mixta": "páginas con una copia pegada",
    "hibrido_prosa_densa_escaneada": "prosa densa escaneada",
    "hibrido_giro_dentro": "con una página girada",
}


def _json(ruta: Path):
    return json.loads(ruta.read_text(encoding="utf-8"))


def _e(texto) -> str:
    return html.escape(str(texto))


def _pct(x: float, decimales: int = 1) -> str:
    return f"{100 * x:.{decimales}f} %".replace(".", ",")


def _num(x: float, decimales: int = 3) -> str:
    return f"{x:.{decimales}f}".replace(".", ",")


def _tipos_de_documento(resultados: dict) -> str:
    cuenta = Counter()
    for d in resultados["doc_results"]:
        tipo = d.get("doc_type") or "?"
        if tipo == "mixto":
            tipo = d["doc_id"].rsplit("_", 1)[0]
        cuenta[TIPOS_DOC.get(tipo, tipo.replace("_", " "))] += 1
    return ", ".join(f"{n} {t}" for t, n in cuenta.most_common())


def _idiomas(resultados: dict) -> str:
    nombres = {"es": "en castellano", "ca": "en valenciano", "va": "en valenciano",
               "mixed": "bilingües"}
    cuenta = Counter(nombres.get(d.get("language"), d.get("language") or "?") for d in resultados["doc_results"])
    return "Idioma: " + ", ".join(f"{n} {t}" for t, n in cuenta.most_common())


def _markdown_simple(md: str) -> str:
    """Lista de viñetas del README a HTML (negritas, código y enlaces internos como texto)."""
    salida, item = [], None
    nivel_anterior = 0

    def linea(t: str) -> str:
        t = _e(t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
        t = re.sub(r"\[(.+?)\]\(#.+?\)", r"\1", t)
        return t

    items: list[tuple[int, str]] = []
    for crudo in md.splitlines():
        if not crudo.strip():
            continue
        m = re.match(r"^(\s*)- (.*)$", crudo)
        if m:
            items.append((len(m.group(1)) // 2, m.group(2)))
        elif items:
            n, t = items[-1]
            items[-1] = (n, t + " " + crudo.strip())
    for nivel, texto in items:
        if nivel > nivel_anterior:
            salida.append("<ul>")
        elif nivel < nivel_anterior:
            salida.append("</li></ul></li>")
        elif item is not None:
            salida.append("</li>")
        salida.append(f"<li>{linea(texto)}")
        item, nivel_anterior = texto, nivel
    salida.append("</li>" + "</ul></li>" * nivel_anterior)
    return "<ul>" + "".join(salida) + "</ul>"


def _limitaciones() -> str:
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    m = re.search(r"^## Limitaciones conocidas y pendientes\n(.*?)(?=^---|^## )", readme, re.S | re.M)
    return _markdown_simple(m.group(1)) if m else "<p>(sin sección de limitaciones en el README)</p>"


def _banco(ruta: Path) -> str:
    if not ruta.exists():
        return "<p>Sin resultados del banco.</p>"
    datos = _json(ruta)
    filas = list(datos["casos"].values()) if "casos" in datos else list(datos.values())
    reales = [f for f in filas if not f["caso"]["tipo"].startswith("FP_")]
    trampas = [f for f in filas if f["caso"]["tipo"].startswith("FP_")]
    out = []
    for version in ("digital", "escaneado"):
        tapados = sum(1 for f in reales if f[version]["legible"] == "tapado")
        legibles = sum(1 for f in reales if f[version]["legible"] == "LEGIBLE")
        falsas = sum(1 for f in trampas if f[version]["detectado"].startswith("SI"))
        out.append((version, tapados, legibles, len(reales), falsas, len(trampas)))
    filas_html = "".join(
        f"<tr><td>{NOMBRE[v]}</td><td class=n>{t} de {n}</td><td class=n>{l}</td><td class=n>{fa} de {nt}</td></tr>"
        for v, t, l, n, fa, nt in out)
    fecha = _e(datos.get("fecha", ""))
    return (f"<p>Una batería de formatos reales de teléfonos y direcciones (móviles con espacios, puntos o "
            f"guiones, prefijos internacionales, direcciones abreviadas, en valenciano, partidas en dos líneas…) "
            f"y de <em>trampas</em>: textos que se parecen pero no lo son (números de expediente, importes, códigos "
            f"postales sueltos, «la calle estaba cortada»). Se fabrica en versión digital y escaneada y se lee el "
            f"PDF entregado.{(' Medido el ' + fecha + '.') if fecha else ''}</p>"
            f"<table><thead><tr><th>Versión</th><th>Formatos reales tapados</th><th>Siguen legibles</th>"
            f"<th>Trampas tomadas por dato</th></tr></thead><tbody>{filas_html}</tbody></table>")


def generar(ref: Path, salida: Path) -> None:
    res = {c: _json(ref / f"resultados_{c}.json") for c in CORPUS}
    barras = _json(ref / "lectura_barras.json")
    comp = _json(ref / "comprobaciones.json")
    trans = _json(RAIZ / "evaluation/linea_base/transicion_contabilidad.json")
    c4 = trans["c4_2026_10_19"]
    codigo = re.search(r"codigo (\w+)", c4.get("fuente", ""))
    codigo = codigo.group(1) if codigo else "?"

    # --- cifras principales por corpus -------------------------------------------------
    tarjetas = []
    for c in CORPUS:
        s = res[c]["summary"]
        tp = s["tp_exact"] + s["tp_partial"]
        tarjetas.append(f"""
<section class=corpus>
  <h3>{NOMBRE[c]}</h3>
  <p class=desc>{_e(DESCRIPCION_CORPUS[c])}</p>
  <p class=desc>{s['total_docs']} documentos: {_e(_tipos_de_documento(res[c]))}. {_e(_idiomas(res[c]))}.</p>
  <dl>
    <div><dt>F1 por valor</dt><dd class=grande>{_num(s['overall_f1'])}</dd></div>
    <div><dt>F1 por aparición</dt><dd>{_num(s['por_aparicion']['f1'])}</dd></div>
    <div><dt>Precisión</dt><dd>{_pct(s['overall_precision'])}</dd></div>
    <div><dt>Recall</dt><dd>{_pct(s['overall_recall'])}</dd></div>
    <div><dt>Datos detectados</dt><dd>{tp}</dd></div>
    <div><dt>Falsos negativos</dt><dd>{s['fn']}</dd></div>
    <div><dt>Falsos positivos</dt><dd>{s['fp']}</dd></div>
    <div><dt>Sobre-redacción</dt><dd>{s['sobre_redaccion']} barras ({s['sobre_redaccion_caracteres']} caracteres)</dd></div>
  </dl>
</section>""")
    f1s = [res[c]["summary"]["overall_f1"] for c in CORPUS]
    f1a = [res[c]["summary"]["por_aparicion"]["f1"] for c in CORPUS]
    media, media_ap = sum(f1s) / 3, sum(f1a) / 3

    # --- evolucion ---------------------------------------------------------------------
    filas_ev = []
    for clave, nombre, fecha, que in MEDICIONES:
        m = trans.get(clave)
        if not m:
            continue
        celdas = "".join(f"<td class=n>{_num(m[c]['f1_por_valor'])}</td>" for c in CORPUS)
        filas_ev.append(f"<tr{' class=final' if clave.startswith('c4') else ''}><th>{nombre}</th><td>{fecha}</td>"
                        f"{celdas}<td class=n><strong>{_num(m['media_f1_por_valor'])}</strong></td><td>{_e(que)}</td></tr>")

    # --- F1 por tipo ---------------------------------------------------------------------
    from evaluation.metrics import OUT_OF_SCOPE_GT_TYPES

    tipos = sorted({t for c in CORPUS for t in res[c]["by_type"]},
                   key=lambda t: list(TIPOS).index(t) if t in TIPOS else 99)
    filas_tipo = []
    for t in tipos:
        celdas = []
        for c in CORPUS:
            b = res[c]["by_type"].get(t)
            n = (b["tp_exact"] + b["tp_partial"] + b["fn"]) if b else 0
            if not b or (n == 0 and b["fp"] == 0):
                celdas.append("<td class='n vacio'>—</td>")
            elif n == 0:
                celdas.append(f"<td class='n vacio'>sin datos · {b['fp']} FP</td>")
            else:
                celdas.append(f"<td class=n>{_num(b['f1'], 2)} <span class=nota>({n})</span></td>")
        nota = " <span class=nota>no se anonimiza</span>" if t in OUT_OF_SCOPE_GT_TYPES else ""
        filas_tipo.append(f"<tr><th>{_e(TIPOS.get(t, t))} <code>{t}</code>{nota}</th>{''.join(celdas)}</tr>")

    # --- lectura de barras -------------------------------------------------------------
    filas_barras, explica = [], []
    for c in CORPUS:
        b = barras["corpus"][c]
        filas_barras.append(f"<tr><th>{NOMBRE[c]}</th><td class=n>{b['documentos']}</td>"
                            f"<td class=n>{_num(b['tapado_pct'] / 100 * 100, 1)} %</td><td class=n>{b['tapados']}</td>"
                            f"<td class=n>{b['fugas']}</td><td class=n>{b['no_concluyentes']}</td></tr>")
        grupos = Counter(f["que_es"] for f in b["fugas_detalle"])
        if grupos:
            lista = "".join(f"<li>{n} × {_e(g)}</li>" for g, n in grupos.most_common())
            explica.append(f"<h4>{NOMBRE[c]}: {b['fugas']} fugas</h4><ul>{lista}</ul>")

    # --- comprobaciones ----------------------------------------------------------------
    cd = comp["casos_dificiles"]
    inventados = "".join(f"<tr><td>{_e(x['caso'])}</td><td>{_e(x['versiones'])}</td><td>{_e(x['resultado'])}</td></tr>"
                         for x in cd["inventados"])
    md = comp["metadatos"]
    filas_meta = "".join(f"<tr><th>{_e(f['que'])}</th><td>{_e(f['digital'])}</td><td>{_e(f['escaneado'])}</td>"
                         f"<td>{_e(f['hibrido'])}</td></tr>" for f in md["filas"])

    paginas = f"""<!doctype html>
<html lang=es>
<head>
<meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Anonimizador: informe final de calidad</title>
<style>
:root {{
  --fondo: #f7f6f2; --papel: #ffffff; --tinta: #1d2329; --suave: #545d66; --linea: #d9dcd6;
  --acento: #1f5f8b; --acento-suave: #e6eef4; --bien: #2c6e49; --aviso: #8a5a00;
}}
@media (prefers-color-scheme: dark) {{
  :root {{ --fondo: #15191d; --papel: #1d2328; --tinta: #e6e8e9; --suave: #a3abb2; --linea: #343c43;
          --acento: #7fb4d9; --acento-suave: #22313c; --bien: #7cc49a; --aviso: #e0b35a; }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--fondo); color: var(--tinta);
       font: 16px/1.55 "Segoe UI", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif; }}
main {{ max-width: 1040px; margin: 0 auto; padding: 32px 20px 64px; }}
header {{ border-bottom: 3px solid var(--acento); padding-bottom: 16px; margin-bottom: 28px; }}
h1 {{ font-size: 1.9rem; margin: 0 0 6px; line-height: 1.2; }}
h2 {{ font-size: 1.35rem; margin: 44px 0 10px; padding-top: 8px; border-top: 1px solid var(--linea); }}
h3 {{ margin: 0 0 4px; font-size: 1.15rem; }}
h4 {{ margin: 18px 0 4px; }}
p, li {{ max-width: 75ch; }}
.meta {{ color: var(--suave); margin: 0; }}
.meta strong {{ color: var(--tinta); }}
.resumen {{ background: var(--acento-suave); border-radius: 6px; padding: 16px 20px; }}
.corpus-fila {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 16px; }}
.corpus {{ background: var(--papel); border: 1px solid var(--linea); border-radius: 6px; padding: 16px 18px; }}
.corpus .desc {{ color: var(--suave); font-size: .92rem; margin: 2px 0; }}
dl {{ margin: 12px 0 0; display: grid; gap: 4px; }}
dl div {{ display: flex; justify-content: space-between; gap: 12px; border-bottom: 1px dotted var(--linea); padding: 2px 0; }}
dt {{ color: var(--suave); }}
dd {{ margin: 0; font-variant-numeric: tabular-nums; text-align: right; }}
dd.grande {{ font-size: 1.4rem; font-weight: 600; color: var(--acento); }}
.tabla {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; background: var(--papel); margin: 10px 0; font-size: .95rem; }}
th, td {{ border-bottom: 1px solid var(--linea); padding: 6px 10px; text-align: left; vertical-align: top; }}
thead th {{ background: var(--acento-suave); font-weight: 600; }}
tbody th {{ font-weight: 500; }}
td.n {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
td.vacio {{ color: var(--suave); }}
tr.final th, tr.final td {{ background: var(--acento-suave); font-weight: 600; }}
.nota {{ color: var(--suave); font-size: .85em; font-weight: normal; }}
code {{ font-family: Consolas, "Courier New", monospace; font-size: .85em; color: var(--suave); }}
.glosario dt {{ font-weight: 600; color: var(--tinta); margin-top: 8px; }}
.glosario dd {{ text-align: left; margin: 0 0 4px; color: var(--tinta); }}
.glosario {{ display: block; }}
.ok {{ color: var(--bien); font-weight: 600; }}
footer {{ margin-top: 48px; color: var(--suave); font-size: .85rem; }}
@media print {{ body {{ background: #fff; }} h2 {{ break-after: avoid; }} .corpus, table {{ break-inside: avoid; }} }}
</style>
</head>
<body>
<main>
<header>
  <h1>Anonimizador de documentos: informe final de calidad</h1>
  <p class=meta>Medición <strong>c4</strong> del <strong>19 de octubre de 2026</strong>, con el código del commit
  <strong><code>{_e(codigo)}</code></strong> del repositorio <code>document-anonymizer</code>.
  Informe generado por <code>evaluation/informe_final.py</code>.</p>
</header>

<section class=resumen>
<p><strong>En una frase:</strong> sobre 452 documentos de prueba con datos personales inventados, el
anonimizador encuentra entre el {_pct(min(res[c]['summary']['overall_recall'] for c in CORPUS), 0)} y el
{_pct(max(res[c]['summary']['overall_recall'] for c in CORPUS), 0)} de los datos según el tipo de documento, y al
abrir los PDF entregados el {_num(min(barras['corpus'][c]['tapado_pct'] for c in CORPUS), 1)}–{_num(max(barras['corpus'][c]['tapado_pct'] for c in CORPUS), 1)} %
de los datos está efectivamente tapado. La media del F1 por valor de los tres tipos de documento es
<strong>{_num(media)}</strong> (línea base: {_num(trans['linea_base_antes_del_arreglo']['media_f1_por_valor'])}).</p>
</section>

<h2>Cómo leer las cifras</h2>
<p>Los documentos de prueba son sintéticos: se generan con datos personales inventados y se sabe exactamente dónde
está cada dato. Se pasan por el anonimizador y se compara lo que encuentra con lo que había.</p>
<dl class=glosario>
  <dt>Precisión</dt><dd>De todo lo que el anonimizador marca como dato personal, qué parte lo es de verdad. Baja si tacha texto que no hacía falta.</dd>
  <dt>Recall (exhaustividad)</dt><dd>De todos los datos personales que hay en los documentos, qué parte encuentra. Baja si se le escapan datos.</dd>
  <dt>F1</dt><dd>Un solo número que combina precisión y recall (su media armónica): solo es alto si las dos lo son. Va de 0 a 1.</dd>
  <dt>Por valor y por aparición</dt><dd>«Por valor» cuenta cada dato distinto de un documento una vez (un DNI que sale tres veces es un dato), porque una vez detectado se tachan todas sus apariciones. «Por aparición» cuenta cada vez que aparece; es más estricta.</dd>
  <dt>Falsos negativos (FN)</dt><dd>Datos personales que el anonimizador no ha detectado.</dd>
  <dt>Falsos positivos (FP)</dt><dd>Detecciones que no son un dato personal: texto tachado de más.</dd>
  <dt>Sobre-redacción</dt><dd>Barras que tapan el dato correcto pero se alargan sobre el texto de al lado (por ejemplo, el nombre y la palabra «NIF» que le sigue). No es un error de detección, pero estropea el documento; se cuenta aparte.</dd>
  <dt>Lectura de barras</dt><dd>Comprobación independiente de las anteriores: se abre el PDF entregado y se intenta leer cada dato. «Tapado» es el porcentaje de datos que ya no se leen; una «fuga» es un dato que sigue legible.</dd>
</dl>

<h2>Resultados por tipo de documento</h2>
<div class=corpus-fila>{''.join(tarjetas)}</div>

<h3 style="margin-top:22px">Media de los tres</h3>
<p>F1 por valor: <strong>{_num(media)}</strong> · F1 por aparición: <strong>{_num(media_ap)}</strong>.
Es la <strong>media aritmética</strong> de los tres tipos de documento, cada uno con el mismo peso. No se suman
los documentos: el corpus digital tiene 400 y los otros dos 28 y 24, así que una media ponderada por documentos
diría casi lo mismo que el digital y ocultaría los escaneados y los híbridos, que son los casos difíciles y los
que más abundan en los expedientes reales.</p>

<h2>Evolución</h2>
<p>F1 por valor en cada medición, con la misma forma de contar en todas (la línea base se ha vuelto a contar con
ella).</p>
<div class=tabla><table>
<thead><tr><th>Medición</th><th>Fecha</th><th>Digital</th><th>Escaneado</th><th>Híbrido</th><th>Media</th><th>Qué cambió</th></tr></thead>
<tbody>{''.join(filas_ev)}</tbody></table></div>

<h2>F1 por tipo de dato</h2>
<p>F1 por valor de cada tipo, con el número de datos de ese tipo en el corpus entre paréntesis. «Sin datos» quiere
decir que el corpus no tiene ninguno de ese tipo y lo detectado cuenta como falso positivo; el lugar de nacimiento,
por ejemplo, se tacha pero el corpus no lo anota.</p>
<div class=tabla><table>
<thead><tr><th>Tipo de dato</th><th>Digital</th><th>Escaneado</th><th>Híbrido</th></tr></thead>
<tbody>{''.join(filas_tipo)}</tbody></table></div>

<h2>Lectura de barras</h2>
<p>{_e(barras['nota'])}</p>
<div class=tabla><table>
<thead><tr><th>Tipo de documento</th><th>Documentos leídos</th><th>Tapado</th><th>Datos tapados</th><th>Fugas</th><th>No concluyentes</th></tr></thead>
<tbody>{''.join(filas_barras)}</tbody></table></div>
<p>Qué son las fugas que quedan:</p>
{''.join(explica)}

<h2>Banco de teléfonos y direcciones</h2>
{_banco(ref / 'banco_contacto.json')}

<h2>Casos difíciles</h2>
<p>Documentos construidos a propósito con lo que el corpus no cubre. Se comprueba leyendo el PDF entregado.</p>
<div class=tabla><table><thead><tr><th>Caso</th><th>Versiones</th><th>Resultado</th></tr></thead>
<tbody>{inventados}
<tr><td>{_e(cd['reales']['descripcion'])}</td><td>—</td><td>{_e(cd['reales']['resultado'])}</td></tr></tbody></table></div>

<h2>Metadatos y contenido oculto</h2>
<p>{_e(md['principio'])}</p>
<div class=tabla><table><thead><tr><th>Qué</th><th>Digital</th><th>Escaneado</th><th>Híbrido</th></tr></thead>
<tbody>{filas_meta}</tbody></table></div>
<p><span class=ok>Comprobado</span> con herramientas ajenas al anonimizador ({_e(md['comprobado_por'])}). {_e(md['resultado'])}</p>

<h2>Limitaciones conocidas</h2>
{_limitaciones()}

<footer>Generado a partir de <code>{_e(ref.relative_to(RAIZ).as_posix())}</code>,
<code>evaluation/linea_base/transicion_contabilidad.json</code> y el README.
Para regenerarlo: <code>python -m evaluation.informe_final</code>.</footer>
</main>
</body>
</html>
"""
    salida.write_text(paginas, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", type=Path, default=RAIZ / "evaluation/reference/c4")
    ap.add_argument("--salida", type=Path, default=RAIZ / "evaluation/informe_final.html")
    args = ap.parse_args()
    generar(args.ref.resolve(), args.salida)
    print(f"informe -> {args.salida}")


if __name__ == "__main__":
    main()
