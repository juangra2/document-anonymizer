"""Medicion del corpus de Word (corpus_word.py): que se elimina, que queda, que
se sustituye de mas y que todos los ficheros se abren.

Para cada documento se pasa el servicio de verdad (con el camino de imagenes
para las incrustadas) y se mira el fichero de SALIDA:
  - ABRE: el zip esta sano y python-docx lo vuelve a abrir;
  - cada dato es ELIMINADO si no esta en ninguna parte del fichero (texto,
    atributos y relaciones de todas las partes: app.services.office.paquete.
    verificar) o FUGA si sigue en alguna;
  - cada dato de una imagen incrustada se busca leyendo la imagen de salida con
    el OCR (NO CONCLUYENTE si tampoco se leia en la entrada);
  - cada texto legitimo es CONSERVADO si sigue en el texto visible, o
    SUSTITUIDO DE MAS si no;
  - en un documento sin datos, el texto visible tiene que quedar igual.

Uso: python -m evaluation.lectura_word [--solo NOMBRE]
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

from lxml import etree

CORPUS = Path("evaluation/word/corpus")
SALIDAS = Path("evaluation/word/salidas")
RESULTADOS = Path("evaluation/word/resultados.json")
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_TEXTO = re.compile(r"^word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$")


def texto_visible(ruta: Path) -> str:
    """Los parrafos de todas las partes de texto, uno por linea."""
    lineas = []
    with zipfile.ZipFile(ruta) as z:
        for nombre in sorted(n for n in z.namelist() if _TEXTO.match(n)):
            raiz = etree.fromstring(z.read(nombre))
            for p in raiz.iter(f"{W}p"):
                lineas.append("".join(t.text or "" for t in p.iter(f"{W}t")
                                      if next(t.iterancestors(f"{W}p")) is p))
    return "\n".join(lineas)


def _normal(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip().casefold()


def abre(ruta: Path) -> bool:
    import docx

    try:
        with zipfile.ZipFile(ruta) as z:
            if z.testzip() is not None:
                return False
        docx.Document(str(ruta))
        return True
    except Exception:
        return False


def _leer_imagenes(ruta: Path) -> str:
    from app.services.ocr.factory import get_word_ocr_engine
    from evaluation.lectura_salida import _aplanar

    motor = get_word_ocr_engine(None)
    textos = []
    with zipfile.ZipFile(ruta) as z, tempfile.TemporaryDirectory() as tmp:
        for nombre in z.namelist():
            if nombre.startswith("word/media/"):
                destino = Path(tmp) / Path(nombre).name
                destino.write_bytes(z.read(nombre))
                try:
                    textos.append(" ".join(t.text for t in motor.analyze_image(
                        image_path=destino, page_index=0).tokens))
                except Exception:
                    continue
    return _aplanar(" ".join(textos))


def medir(etiqueta: dict, entrada: Path, salida: Path) -> dict:
    from app.services.office.paquete import verificar
    from evaluation.lectura_salida import _aplanar, aparece

    r = {"documento": etiqueta["documento"], "tipo": etiqueta["tipo"], "idioma": etiqueta["idioma"],
         "abre": abre(salida)}
    fugas = {v for _, v in verificar(salida, [d["text"] for d in etiqueta["datos"]])}
    r["datos"] = [(d["text"], d["type"], "fuga" if d["text"] in fugas else "eliminado")
                  for d in etiqueta["datos"]]
    if etiqueta["imagen"]:
        leido_in, leido_out = _leer_imagenes(entrada), _leer_imagenes(salida)
        r["imagen"] = [(d["text"], d["type"],
                        "no_concluyente" if not aparece(_aplanar(d["text"]), leido_in)
                        else "fuga" if aparece(_aplanar(d["text"]), leido_out) else "eliminado")
                       for d in etiqueta["imagen"]]
    else:
        r["imagen"] = []
    visible = _normal(texto_visible(salida))
    r["legitimos"] = [(t, "conservado" if _normal(t) in visible else "sustituido_de_mas")
                      for t in etiqueta["legitimos"]]
    r["sin_datos_igual"] = (texto_visible(entrada) == texto_visible(salida)) if not (
        etiqueta["datos"] or etiqueta["imagen"]) else None
    r["marcadores"] = len(re.findall(r"\[[A-ZÁÉÍÓÚÑº ]+\]", texto_visible(salida)))
    return r


def resumen(resultados: list[dict]) -> None:
    def fila(grupo):
        c = Counter()
        for r in grupo:
            c["docs"] += 1
            c["abren"] += r["abre"]
            for *_, e in r["datos"] + r["imagen"]:
                c[e] += 1
            for _, e in r["legitimos"]:
                c[e] += 1
            if r["sin_datos_igual"] is not None:
                c["sin_datos"] += 1
                c["sin_datos_igual"] += r["sin_datos_igual"]
        medibles = c["eliminado"] + c["fuga"]
        legit = c["conservado"] + c["sustituido_de_mas"]
        sd = f"  ·  sin datos intactos {c['sin_datos_igual']}/{c['sin_datos']}" if c["sin_datos"] else ""
        return (f"{c['docs']:2d} docs, abren {c['abren']}/{c['docs']}  ·  datos {c['eliminado']} eliminados / "
                f"{c['fuga']} fugas / {c['no_concluyente']} nc ({100 * c['eliminado'] / medibles if medibles else 100:.1f} %)"
                f"  ·  legitimos {c['conservado']} conservados / {c['sustituido_de_mas']} de mas"
                f" ({100 * c['conservado'] / legit if legit else 100:.1f} %){sd}")

    print("\nPOR TIPO")
    for t in sorted({r["tipo"] for r in resultados}):
        print(f"  {t:12s} {fila([r for r in resultados if r['tipo'] == t])}")
    print("\nPOR IDIOMA")
    for t in sorted({r["idioma"] for r in resultados}):
        print(f"  {t:12s} {fila([r for r in resultados if r['idioma'] == t])}")
    print(f"\nTOTAL\n  {fila(resultados)}")


def main(argv: list[str]) -> int:
    from app.core.config import settings
    from app.services.formatos import procesar
    from app.services.pdf.pipeline import PdfAnonymizationPipeline

    etiquetas = json.loads((CORPUS / "etiquetas.json").read_text(encoding="utf-8"))
    if "--solo" in argv:
        etiquetas = [e for e in etiquetas if argv[argv.index("--solo") + 1] in e["documento"]]
    SALIDAS.mkdir(parents=True, exist_ok=True)
    pipeline = PdfAnonymizationPipeline()
    resultados = []
    for e in etiquetas:
        entrada, salida = CORPUS / e["documento"], SALIDAS / e["documento"]
        procesar("docx", "anonymize", entrada, salida, "w", lambda: pipeline,
                 settings.default_redaction_mode, frozenset())
        r = medir(e, entrada, salida)
        resultados.append(r)
        fugas = [f"{t}" for _, t, s in r["datos"] + r["imagen"] if s == "fuga"]
        de_mas = [t for t, s in r["legitimos"] if s == "sustituido_de_mas"]
        print(f"{e['documento']:28s} abre={r['abre']}  fugas={fugas}  de_mas={de_mas}"
              + (f"  sin_datos_igual={r['sin_datos_igual']}" if r["sin_datos_igual"] is not None else ""),
              flush=True)
    RESULTADOS.write_text(json.dumps(resultados, ensure_ascii=False, indent=1), encoding="utf-8")
    resumen(resultados)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
