"""Lectura de barras del corpus de imagenes: que se tapa, que queda a la vista y
que se tapa de mas.

Para cada imagen de evaluation/imagenes/corpus (corpus_imagenes.py) se pasa el
servicio de verdad y se lee la ENTRADA y la SALIDA en plano: la transformacion
con que se fotografio cada documento es conocida y se deshace antes de leer, asi
que la medida no depende del enderezador del sistema (si fallara el, aqui no se
heredaria su error).  Los carnets oscuros se leen tambien invertidos.

Las reglas son las de lectura_salida.py:
  - un dato es FUGA si se lee en la salida; TAPADO si se leia en la entrada, ya
    no se lee y la lectura de la salida es de fiar; NO CONCLUYENTE si no se leia
    ni en la entrada o la lectura de la salida no es de fiar;
  - la comparacion tolera erratas (lectura_salida.aparece).
Ademas, para lo que tiene que quedar a la vista (rotulos, titulos): CONSERVADO,
TAPADO DE MAS o NO CONCLUYENTE (no se leia en la entrada).  El codigo QR se
comprueba descodificandolo.

Por pixeles, sin OCR (el generador guarda la caja exacta de cada dato y rotulo
en el documento plano): COBERTURA es la parte de lo claro de la caja en la
entrada que es negro en la salida.  Un dato ASOMA si su cobertura no llega al
97 %: la barra se queda corta aunque el resto no se lea.  Un rotulo esta TAPADO
(por pixeles) si su cobertura pasa del 30 %.

"Entera": la imagen sale tapada (casi) entera: al menos el 85 % de lo que no era
oscuro en la entrada es negro en la salida.

Uso: python -m evaluation.lectura_imagenes [--solo NOMBRE]
"""
from __future__ import annotations

import json
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps, ImageSequence

from evaluation.lectura_salida import FUGA, NO_CONCLUYENTE, TAPADO, _aplanar, aparece

CORPUS = Path("evaluation/imagenes/corpus")
SALIDAS = Path("evaluation/imagenes/salidas")
RESULTADOS = Path("evaluation/imagenes/resultados.json")
if "--resultados" in sys.argv:
    RESULTADOS = Path(sys.argv[sys.argv.index("--resultados") + 1])
CONSERVADO, DE_MAS = "conservado", "tapado_de_mas"
_OSCURO = 60
_ENTERA = 0.85


def _paginas_rgb(ruta: Path) -> list[np.ndarray]:
    salida = []
    with Image.open(ruta) as img:
        for marco in ImageSequence.Iterator(img):
            m = marco.convert("RGBA")
            fondo = Image.new("RGB", m.size, "white")
            fondo.paste(m, mask=m.getchannel("A"))
            salida.append(np.asarray(fondo))
    return salida


def en_plano(foto: np.ndarray, pagina: dict) -> np.ndarray:
    m = np.array(pagina["matriz"], float)
    return cv2.warpPerspective(foto, m, (pagina["ancho"], pagina["alto"]),
                               flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                               borderValue=(255, 255, 255))


class Lector:
    def __init__(self) -> None:
        from app.services.ocr.factory import get_word_ocr_engine

        self.motor = get_word_ocr_engine(None)
        self.tmp = Path(tempfile.mkdtemp(prefix="lectura_imagenes_"))

    def leer(self, plano: np.ndarray, oscuro: bool) -> tuple[str, int]:
        textos, caracteres = [], 0
        variantes = [plano] + ([255 - plano] if oscuro else [])
        for i, v in enumerate(variantes):
            ruta = self.tmp / f"p{i}.png"
            Image.fromarray(v).save(ruta)
            pagina = self.motor.analyze_image(image_path=ruta, page_index=0)
            textos.append(" ".join(t.text for t in pagina.tokens))
            caracteres = max(caracteres, sum(len(t.text) for t in pagina.tokens))
        return _aplanar(" ".join(textos)), caracteres


def _qr(plano: np.ndarray) -> bool:
    try:
        texto, _, _ = cv2.QRCodeDetector().detectAndDecode(plano)
    except cv2.error:
        return False
    return bool(texto)


def _cambio_a_negro(antes: np.ndarray, despues: np.ndarray) -> float:
    a = cv2.cvtColor(antes, cv2.COLOR_RGB2GRAY)
    d = cv2.cvtColor(despues, cv2.COLOR_RGB2GRAY)
    claros = a > _OSCURO
    return float(((d <= _OSCURO) & claros).sum() / max(1, claros.sum()))


_ASOMA = 0.97
_ROTULO_TAPADO = 0.30


def cobertura(antes: np.ndarray, despues: np.ndarray, cajas: list[list[float]],
              solo_oscuro: bool = False) -> float | None:
    """Parte de lo claro de las cajas en la entrada que es negro en la salida.

    `solo_oscuro`: la caja se ajusta a lo oscuro (los modulos de un QR, sin su
    margen blanco, que no lleva datos)."""
    a = cv2.cvtColor(antes, cv2.COLOR_RGB2GRAY)
    d = cv2.cvtColor(despues, cv2.COLOR_RGB2GRAY)
    claros = tapados = 0
    for x0, y0, x1, y1 in cajas:
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        x1, y1 = min(a.shape[1], int(x1)), min(a.shape[0], int(y1))
        if x1 <= x0 or y1 <= y0:
            continue
        # La caja de la fuente es mas alta que las letras (ascendentes y
        # descendentes de todo el cuerpo): se ajusta a la tinta del dato, que es
        # lo que tiene que quedar debajo de la barra.
        zona = a[y0:y1, x0:x1].astype(int)
        tinta = zona < 100 if solo_oscuro else np.abs(zona - np.median(zona)) > 80
        if tinta.any():
            ys, xs = np.nonzero(tinta)
            x0, x1 = x0 + xs.min(), x0 + xs.max() + 1
            y0, y1 = y0 + ys.min(), y0 + ys.max() + 1
        ca = a[y0:y1, x0:x1] > _OSCURO
        claros += int(ca.sum())
        tapados += int((ca & (d[y0:y1, x0:x1] <= _OSCURO)).sum())
    return tapados / claros if claros else None


def medir_imagen(etiqueta: dict, entrada: Path, salida: Path, lector: Lector) -> dict:
    pags_in, pags_out = _paginas_rgb(entrada), _paginas_rgb(salida)
    lecturas, planos = [], []
    for i, pagina in enumerate(etiqueta["paginas"]):
        pin = en_plano(pags_in[i], pagina)
        pout = en_plano(pags_out[i], pagina) if i < len(pags_out) else np.zeros_like(pin)
        planos.append((pin, pout))
        tin, cin = lector.leer(pin, etiqueta["oscuro"])
        tout, cout = lector.leer(pout, etiqueta["oscuro"])
        lecturas.append(dict(tin=tin, tout=tout, cin=cin, cout=cout, qr_in=_qr(pin), qr_out=_qr(pout),
                             negro=_cambio_a_negro(pin, pout)))
    texto_in = " ".join(l["tin"] for l in lecturas)
    texto_out = " ".join(l["tout"] for l in lecturas)
    entera = all(l["negro"] >= _ENTERA for l in lecturas)

    rotulos = []
    for r in etiqueta["rotulos"]:
        a = _aplanar(r)
        if not aparece(a, texto_in):
            rotulos.append((r, NO_CONCLUYENTE))
        else:
            rotulos.append((r, CONSERVADO if aparece(a, texto_out) else DE_MAS))
    # La lectura de la salida es de fiar si se tapo entera, si sigue leyendo
    # algun rotulo o si recupera al menos la mitad de lo que leia la entrada.
    fiable = (entera or any(e == CONSERVADO for _, e in rotulos)
              or sum(l["cout"] for l in lecturas) >= 0.5 * sum(l["cin"] for l in lecturas))

    datos = []
    for d in etiqueta["datos"]:
        if d["type"] == "QR_CODE":
            qin = any(l["qr_in"] for l in lecturas)
            qout = any(l["qr_out"] for l in lecturas)
            estado = NO_CONCLUYENTE if not qin else FUGA if qout else TAPADO
        else:
            a = _aplanar(d["text"])
            en_in, en_out = aparece(a, texto_in), aparece(a, texto_out)
            estado = (NO_CONCLUYENTE if not en_in else FUGA if en_out
                      else TAPADO if fiable else NO_CONCLUYENTE)
        pin, pout = planos[d.get("pagina", 0)]
        c = cobertura(pin, pout, d.get("cajas", []), solo_oscuro=d["type"] == "QR_CODE")
        datos.append((d["text"], d["type"], estado, None if c is None else round(c, 3)))
    cob_rotulos = []
    for r in etiqueta.get("cajas_rotulos", []):
        pin, pout = planos[r["pagina"]]
        c = cobertura(pin, pout, r["cajas"])
        cob_rotulos.append(None if c is None else round(c, 3))
    return dict(datos=datos, rotulos=rotulos, cob_rotulos=cob_rotulos, entera=entera,
                entera_esperada=etiqueta["entera"], negro=[round(l["negro"], 2) for l in lecturas],
                fiable=fiable)


def procesar(etiqueta: dict, pipeline) -> tuple[Path, dict]:
    from app.core.config import settings
    from app.services.image.imagen import anonimizar_imagen, formato_de

    entrada = CORPUS / etiqueta["imagen"]
    salida = SALIDAS / etiqueta["imagen"]
    formato = formato_de(entrada.read_bytes()[:8])
    informe = anonimizar_imagen(entrada, salida, formato, lambda e, s: pipeline.process(
        e, s, "img", settings.default_redaction_mode, False, frozenset()))
    motivo = ([f"ilegible:{p.reason}" for p in informe.pages_redacted_in_full]
              + [f"contenido:{p.reason}" for p in informe.pages_redacted_by_content])
    return salida, {"motivo_entera": motivo, "avisos": [c.code for c in informe.coded_warnings],
                    "revision": informe.manual_review_reasons}


def resumen(resultados: list[dict]) -> None:
    def fila(grupo: list[dict]) -> str:
        c = defaultdict(int)
        for r in grupo:
            for _, _, e, cob in r["datos"]:
                c["d_" + e] += 1
                if cob is not None and cob < _ASOMA:
                    c["asoman"] += 1
            c["r_pix_tapados"] += sum(1 for x in r.get("cob_rotulos", []) if x is not None and x > _ROTULO_TAPADO)
            c["r_pix"] += sum(1 for x in r.get("cob_rotulos", []) if x is not None)
            for _, e in r["rotulos"]:
                c["r_" + e] += 1
            c["enteras_mal"] += r["entera"] != r["entera_esperada"]
        medibles = c["d_tapado"] + c["d_fuga"]
        rmed = c["r_conservado"] + c["r_tapado_de_mas"]
        return (f"datos {c['d_tapado']:3d} tapados / {c['d_fuga']:2d} fugas / {c['d_no_concluyente']:2d} nc"
                f" ({100 * c['d_tapado'] / medibles if medibles else 0:5.1f} %)  ·  rotulos "
                f"{c['r_conservado']:3d} a la vista / {c['r_tapado_de_mas']:3d} de mas / {c['r_no_concluyente']:2d} nc"
                f" ({100 * c['r_conservado'] / rmed if rmed else 0:5.1f} %)  ·  asoman {c['asoman']}"
                f"  ·  rotulos tapados (pix) {c['r_pix_tapados']}/{c['r_pix']}  ·  enteras mal {c['enteras_mal']}")

    print("\nPOR TIPO DE DOCUMENTO")
    for t in sorted({r["tipo"] for r in resultados}):
        print(f"  {t:12s} {fila([r for r in resultados if r['tipo'] == t])}")
    print("\nPOR VARIANTE")
    for v in sorted({r["variante"] for r in resultados}):
        print(f"  {v:18s} {fila([r for r in resultados if r['variante'] == v])}")
    print(f"\nTOTAL ({len(resultados)} imagenes)\n  {fila(resultados)}")
    print("\nTAPADAS ENTERAS")
    for r in resultados:
        if r["entera"] or r["entera_esperada"]:
            marca = "ok " if r["entera"] == r["entera_esperada"] else "MAL"
            print(f"  {marca} {r['imagen']:32s} esperada={r['entera_esperada']!s:5s} "
                  f"motivo={','.join(r['motivo_entera']) or '-'}")


def main(argv: list[str]) -> int:
    from app.services.pdf.pipeline import PdfAnonymizationPipeline

    etiquetas = json.loads((CORPUS / "etiquetas.json").read_text(encoding="utf-8"))
    if "--solo" in argv:
        etiquetas = [e for e in etiquetas if argv[argv.index("--solo") + 1] in e["imagen"]]
    SALIDAS.mkdir(parents=True, exist_ok=True)
    pipeline, lector, resultados = PdfAnonymizationPipeline(), Lector(), []
    for e in etiquetas:
        salida, sistema = procesar(e, pipeline)
        r = medir_imagen(e, CORPUS / e["imagen"], salida, lector)
        r.update(imagen=e["imagen"], tipo=e["tipo"], variante=e["variante"], **sistema)
        resultados.append(r)
        print(f"{e['imagen']:34s} " + " ".join(
            f"{t}:{s}" + (f"(asoma {cob:.2f})" if cob is not None and cob < _ASOMA else "")
            for _, t, s, cob in r["datos"])
              + f" | de mas: {[x for x, s in r['rotulos'] if s == DE_MAS]}"
              + (f" | ENTERA {sistema['motivo_entera']}" if r["entera"] else ""), flush=True)
    RESULTADOS.write_text(json.dumps(resultados, ensure_ascii=False, indent=1), encoding="utf-8")
    resumen(resultados)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
