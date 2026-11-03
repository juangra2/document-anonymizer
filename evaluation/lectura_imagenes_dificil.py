"""Medicion del corpus de casos dificiles de imagenes (corpus_imagenes_dificil.py).

Cada caso se sube por la API de verdad (POST /jobs, en proceso), con su
tratamiento (anonimizar o solo metadatos) y las casillas del catalogo; despues de
cada uno se comprueba que el servicio sigue respondiendo.

Del fichero de salida se mira:
  - FUGA A LA VISTA (anonimizar): la lectura de barras de lectura_imagenes.py
    (en plano, deshaciendo la transformacion conocida de la foto): un dato que
    se sigue leyendo, o cuya barra no cubre el 97 % de su tinta (asoma);
  - FUGA RECUPERABLE: lo que no se ve pero esta en el fichero: otra imagen
    escondida (miniatura EXIF o de Photoshop, segunda imagen MPF, datos al final
    del JPEG, miniatura en base64 en el XMP), un metadato con un valor del caso
    (ExifTool), o mas paginas de las que tenia la entrada;
  - SOBRE-REDACCION: un rotulo tapado (por lectura o por pixeles, mas del 30 %),
    o la imagen tapada entera sin ser una copia de DNI (se anota el motivo);
  - ERROR: se rechaza o falla lo que se tenia que procesar, o un rechazo
    esperado llega con otro codigo; la salida no es del formato de la entrada.
En "solo metadatos" el contenido se queda a la vista por diseño: se mira solo
lo escondido.

Uso: python -m evaluation.lectura_imagenes_dificil [--solo ID]
"""
from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

from evaluation.lectura_excel_dificil import (_GRAVEDAD, CORRECTO, CUELGUE, ERROR, FUGA, SOBRE, compacto,
                                              imagenes_escondidas, metadatos_exiftool, subir, tabla)

CORPUS = Path("evaluation/imagenes_dificil/corpus")
SALIDAS = Path("evaluation/imagenes_dificil/salidas")
RESULTADOS = Path("evaluation/imagenes_dificil/resultados.json")
_FORMATO = {".jpg": "JPEG", ".png": "PNG", ".tif": "TIFF"}


def _formato_y_paginas(ruta: Path) -> tuple[str | None, int]:
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    try:
        with Image.open(ruta) as img:
            return img.format, getattr(img, "n_frames", 1)
    except Exception:
        return None, 0


def escondido(datos: bytes) -> list[str]:
    """Imagenes metidas dentro del fichero ademas de la imagen principal."""
    salida = list(imagenes_escondidas(datos)) if datos[:3] == b"\xff\xd8\xff" else []
    for m in re.finditer(rb"/9j/[A-Za-z0-9+/=\s]{400,}", datos):
        salida.append(f"JPEG en base64 (XMP) en el byte {m.start()}")
    if datos[:3] == b"\xff\xd8\xff":
        fin = datos.rfind(b"\xff\xd9")
        if 0 < fin < len(datos) - 64 and not salida:
            salida.append(f"{len(datos) - fin - 2} bytes despues del final de la imagen")
    return salida


def medir(caso: dict, r: dict, lector) -> dict:
    from evaluation.lectura_imagenes import CONSERVADO, DE_MAS, medir_imagen
    from evaluation.lectura_salida import FUGA as LEIDO

    m = {"id": caso["id"], "que_prueba": caso["que_prueba"], "esperado": caso["esperado"], "estado": r["estado"],
         "codigo": r.get("codigo"), "segundos": r.get("segundos"), "fugas_visibles": {}, "fugas_recuperables": {},
         "legitimos_perdidos": [], "notas": []}
    if r["estado"] == "colgado":
        m["resultado"] = CUELGUE
        return m
    if caso["rechazo"]:
        bien = r["estado"] in ("rechazado", "fallido") and r.get("codigo") == caso["rechazo"]
        m["resultado"] = CORRECTO if bien else ERROR
        if not bien:
            m["notas"].append(f"se esperaba el rechazo {caso['rechazo']}")
        return m
    if r["estado"] != "completado":
        m["resultado"] = ERROR
        return m

    entrada, salida = CORPUS / caso["fichero"], Path(r["salida"])
    formato_in, paginas_in = _formato_y_paginas(entrada)
    formato_out, paginas_out = _formato_y_paginas(salida)
    if formato_out != formato_in:
        m["notas"].append(f"entra {formato_in} y sale {formato_out} ({r['extension']})")
    if paginas_out != paginas_in:
        m["notas"].append(f"entra con {paginas_in} paginas y sale con {paginas_out}")
    datos = salida.read_bytes()
    escondidas = escondido(datos)
    if escondidas:
        m["fugas_recuperables"]["imagen escondida"] = escondidas
    meta = compacto(metadatos_exiftool(datos))
    for valor in caso["metadatos"]:
        if compacto(valor) in meta:
            m["fugas_recuperables"][valor] = ["metadatos (ExifTool)"]
    informe = r.get("informe") or {}
    motivos = ([f"ilegible:{p['reason']}" for p in informe.get("pages_redacted_in_full", [])]
               + [f"contenido:{p['reason']}" for p in informe.get("pages_redacted_by_content", [])])
    if motivos:
        m["notas"].append("tapada entera: " + ", ".join(motivos))

    if caso["tratamiento"] == "anonymize" and caso["imagen"] and formato_out:
        lectura = medir_imagen(caso["imagen"], entrada, salida, lector)
        for texto, tipo, estado, cobertura in lectura["datos"]:
            if estado == LEIDO:
                m["fugas_visibles"][texto] = ["se lee en la salida"]
            elif cobertura is not None and cobertura < 0.97:
                m["fugas_visibles"][texto] = [f"asoma: barra sobre el {cobertura:.0%} de la tinta"]
        no_concluyentes = [t for t, _, e, c in lectura["datos"] if e != LEIDO and c is None]
        if no_concluyentes:
            m["notas"].append(f"sin caja para medir: {no_concluyentes}")
        tapados = [r_ for r_, e in lectura["rotulos"] if e == DE_MAS]
        tapados += [caso["imagen"]["rotulos"][i] for i, c in enumerate(lectura["cob_rotulos"])
                    if c is not None and c > 0.30 and caso["imagen"]["rotulos"][i] not in tapados]
        if lectura["entera_esperada"]:
            if not lectura["entera"]:
                m["fugas_visibles"]["imagen"] = ["se esperaba tapada entera"]
        elif lectura["entera"]:
            m["legitimos_perdidos"].append("imagen tapada entera")
        elif tapados:
            m["legitimos_perdidos"] += tapados
        conservados = sum(1 for _, e in lectura["rotulos"] if e == CONSERVADO)
        m["notas"].append(f"rotulos legibles en la salida: {conservados}/{len(lectura['rotulos'])}; "
                          f"negro {lectura['negro']}")
    m["resultado"] = (FUGA if m["fugas_visibles"] or m["fugas_recuperables"]
                      else SOBRE if m["legitimos_perdidos"] else CORRECTO)
    return m


def main(argv: list[str]) -> int:
    from fastapi.testclient import TestClient

    from app.main import app
    from evaluation.lectura_imagenes import Lector

    casos = json.loads((CORPUS / "casos.json").read_text(encoding="utf-8"))
    if "--solo" in argv:
        casos = [c for c in casos if c["id"] == argv[argv.index("--solo") + 1]]
    SALIDAS.mkdir(parents=True, exist_ok=True)
    client = TestClient(app)
    crudos = []
    for caso in casos:
        r = subir(client, caso, CORPUS, SALIDAS)
        r["servicio_vivo"] = client.get("/health").status_code == 200
        crudos.append((caso, r))
        print(f"{caso['id']:5s} {r['estado']:11s} {r.get('codigo') or '':20s} {r.get('segundos')} s  "
              f"servicio {'vivo' if r['servicio_vivo'] else 'CAIDO'}", flush=True)
    lector = Lector()
    resultados = []
    for caso, r in crudos:
        m = medir(caso, r, lector)
        if not r["servicio_vivo"]:
            m["notas"].append("el servicio dejo de responder")
            m["resultado"] = CUELGUE
        resultados.append(m)
    resultados.sort(key=lambda m: (_GRAVEDAD[m["resultado"]], m["id"]))
    RESULTADOS.write_text(json.dumps(resultados, ensure_ascii=False, indent=1), encoding="utf-8")
    texto = tabla(resultados)
    RESULTADOS.with_suffix(".md").write_text(texto, encoding="utf-8")
    print("\n" + texto)
    print("\n" + ", ".join(f"{k}: {sum(1 for m in resultados if m['resultado'] == k)}" for k in _GRAVEDAD))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
