"""Medicion de la seudonimizacion: ida y vuelta, consistencia y etiquetas alteradas.

Sobre los corpus de textos (corpus_texto.py) y de Word (corpus_word.py), con el
servicio de verdad (formatos.procesar, tratamiento `pseudonymize`):

  1. IDA: ningun dato queda en el documento con etiquetas; la tabla cifrada no
     lleva ningun dato en claro y se abre con la contrasena.
  2. VUELTA EXACTA: reconstruir el documento tal cual salio devuelve el
     original, byte a byte en texto y Markdown; en Word, el mismo texto visible
     que deja la limpieza sin sustituir nada (solo quitar metadatos).
  3. CONSISTENCIA: cuantos datos del corpus han quedado repartidos en mas de
     una etiqueta (no rompe la vuelta, pero el modelo veria dos donde hay uno).
  4. ETIQUETAS ALTERADAS: cada etiqueta de cada tabla se mete en una frase
     escrita como la escribiria un modelo de lenguaje (minusculas, sin
     corchetes, llaves, escapada, otro separador, traducida, con sufijo, en
     negrita) y se mira que la reconstruccion pone su dato; y con etiquetas
     inventadas, que las deja como estan y avisa de cada una.

Uso: python -m evaluation.lectura_seudonimo [--sin-word]
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

TEXTO = Path("evaluation/texto/corpus")
WORD = Path("evaluation/word/corpus")
SALIDAS = Path("evaluation/seudonimo/salidas")
RESULTADOS = Path("evaluation/seudonimo/resultados.json")
CLAVE = "clave de la medicion"
_ETIQUETA = re.compile(r"\[([A-Z]+)_(\d+)\]")
_INGLES = {"PERSONA": "PERSON", "TELEFONO": "PHONE", "CORREO": "EMAIL", "DIRECCION": "ADDRESS",
           "FECHA": "DATE", "IMPORTE": "AMOUNT", "EMPRESA": "ORGANIZATION", "WEB": "URL"}
_VALENCIANO = {"TELEFONO": "TELÈFON", "CORREO": "CORREU", "DIRECCION": "ADREÇA", "FECHA": "DATA",
               "IMPORTE": "IMPORT", "EXPEDIENTE": "EXPEDIENT"}

#: Lo que un modelo hace con "[PERSONA_1]": nombre -> funcion(tipo, numero).
ALTERACIONES = {
    "minusculas": lambda t, n: f"[{t.lower()}_{n}]",
    "inicial mayuscula": lambda t, n: f"[{t.capitalize()}_{n}]",
    "sin corchetes": lambda t, n: f"{t}_{n}",
    "llaves": lambda t, n: f"{{{t}_{n}}}",
    "escapada (Markdown)": lambda t, n: f"\\[{t}\\_{n}\\]",
    "con espacio": lambda t, n: f"[{t} {n}]",
    "con guion": lambda t, n: f"[{t}-{n}]",
    "sin separador": lambda t, n: f"[{t}{n}]",
    "en ingles": lambda t, n: f"[{_INGLES.get(t, t)}_{n}]",
    "en valenciano": lambda t, n: f"[{_VALENCIANO.get(t, t)}_{n}]",
    "con sufijo": lambda t, n: f"[{t}_{n}]'s",
    "en negrita sin corchetes": lambda t, n: f"**{t}_{n}**",
}
INVENTADAS = {
    "numero que no existe": lambda t, n: f"[{t}_{int(n) + 500}]",
    "tipos mezclados": lambda t, n: f"[{t}_DATO_{n}]",
}


def _visible_docx(ruta: Path) -> str:
    from evaluation.lectura_word import texto_visible

    return texto_visible(ruta)


def medir(con_word: bool = True) -> dict:
    from app.services.formatos import procesar, reconocer
    from app.services.office.docx import solo_metadatos_docx
    from app.services.pdf.pipeline import PdfAnonymizationPipeline
    from app.services.seudonimo.etiquetas import normalizar
    from app.services.seudonimo.reconstruir import reconstruir_fichero, reconstruir_texto
    from app.services.seudonimo.tabla import Cifrador, abrir_tabla

    _pipeline = PdfAnonymizationPipeline()
    pipeline = lambda: _pipeline                                    # noqa: E731
    SALIDAS.mkdir(parents=True, exist_ok=True)
    casos = [(TEXTO, e) for e in json.loads((TEXTO / "etiquetas.json").read_text(encoding="utf-8"))]
    if con_word:
        casos += [(WORD, e) for e in json.loads((WORD / "etiquetas.json").read_text(encoding="utf-8"))]
    total: Counter = Counter()
    alteradas: dict[str, Counter] = {n: Counter() for n in list(ALTERACIONES) + list(INVENTADAS)}
    documentos = []
    for carpeta, e in casos:
        fichero = e.get("fichero") or e["documento"]
        e.setdefault("nombre", Path(fichero).stem)
        entrada, salida = carpeta / fichero, SALIDAS / fichero
        tabla_cifrada = SALIDAS / f"{fichero}.tabla"
        formato = reconocer(entrada, fichero)
        informe = procesar(formato, "pseudonymize", entrada, salida, e["nombre"], pipeline, "strict",
                           frozenset(), cifrador=Cifrador(CLAVE), tabla=tabla_cifrada)
        seudo = salida.read_bytes()
        cifrada = tabla_cifrada.read_bytes()
        tabla = abrir_tabla(cifrada, CLAVE)
        datos = [d["text"] for d in e["datos"]]
        r = {"nombre": e["nombre"], "formato": formato, "etiquetas": len(tabla["etiquetas"]),
             "estado": informe.status}

        # 1. Ida.
        if formato == "docx":
            from app.services.office.paquete import verificar
            r["fugas"] = sorted({v for _, v in verificar(salida, datos)})
            texto_seudo = _visible_docx(salida)
        else:
            from app.services.texto.codificacion import leer
            texto_seudo = leer(seudo).texto
            r["fugas"] = [d for d in datos if d.casefold() in texto_seudo.casefold()]
        r["en_claro_en_la_tabla"] = [d for d in datos if d.encode("utf-8") in cifrada]

        # 2. Vuelta exacta.
        vuelta = reconstruir_fichero(seudo, formato, tabla)
        if formato == "docx":
            limpio, recuperado = SALIDAS / f"limpio_{fichero}", SALIDAS / f"vuelta_{fichero}"
            solo_metadatos_docx(entrada, limpio)
            recuperado.write_bytes(vuelta.datos)
            r["vuelta"] = vuelta.exacta and _visible_docx(recuperado) == _visible_docx(limpio)
        else:
            r["vuelta"] = vuelta.exacta and vuelta.datos == entrada.read_bytes()

        # 3. Consistencia: datos del corpus repartidos en mas de una etiqueta.
        repartidos = []
        for d in e["datos"]:
            clave = normalizar(d["type"], d["text"])
            con = [et for et, v in tabla["etiquetas"].items()
                   if any(clave and clave in normalizar(v["tipo"], x) for x in v["variantes"])]
            if len(con) > 1:
                repartidos.append(f"{d['type']} en {len(con)} etiquetas")
        r["repartidos"] = repartidos

        # 4. Etiquetas alteradas: cada etiqueta de la tabla, dentro de una frase
        # como la que escribiria un modelo ("Segun consta, X presento el escrito.").
        for etiqueta, e_tabla in tabla["etiquetas"].items():
            tipo, numero = etiqueta.rsplit("_", 1)
            frase = "Según consta, {} presentó el escrito y se le notificó la resolución."
            for nombre, f in ALTERACIONES.items():
                rec = reconstruir_texto(frase.format(f(tipo, numero)), tabla)
                alrededor = {"con sufijo": "{}'s", "en negrita sin corchetes": "**{}**"}.get(nombre, "{}")
                alteradas[nombre]["etiquetas"] += 1
                alteradas[nombre]["bien"] += (rec.texto == frase.format(alrededor.format(e_tabla["principal"]))
                                              and not rec.no_resueltas)
            for nombre, f in INVENTADAS.items():
                inventada = f(tipo, numero)
                rec = reconstruir_texto(frase.format(inventada), tabla)
                alteradas[nombre]["etiquetas"] += 1
                alteradas[nombre]["bien"] += rec.texto == frase.format(inventada) and rec.no_resueltas == [inventada]

        documentos.append(r)
        total["documentos"] += 1
        total["datos"] += len(datos)
        total["fugas"] += len(r["fugas"])
        total["en_claro_en_la_tabla"] += len(r["en_claro_en_la_tabla"])
        total["vuelta_exacta"] += bool(r["vuelta"])
        total["datos_repartidos"] += len(repartidos)
        total["etiquetas"] += r["etiquetas"]
        print(f"{'OK ' if r['vuelta'] and not r['fugas'] else '!! '}{e['nombre']:<28} {formato:<4} "
              f"etiquetas {r['etiquetas']:>3}  fugas {len(r['fugas'])}  vuelta {'exacta' if r['vuelta'] else 'NO'}"
              f"  repartidos {len(repartidos)}  [{informe.status}]")
        for f_ in r["fugas"]:
            print(f"     FUGA: {f_}")
    print("\nETIQUETAS ALTERADAS (etiquetas bien / etiquetas)")
    for nombre, c in alteradas.items():
        print(f"  {nombre:<28} {c['bien']}/{c['etiquetas']}")
    resultado = {"total": dict(total), "alteradas": {n: dict(c) for n, c in alteradas.items()},
                 "documentos": documentos}
    RESULTADOS.parent.mkdir(parents=True, exist_ok=True)
    RESULTADOS.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nTOTAL", dict(total))
    return resultado


if __name__ == "__main__":
    medir(con_word="--sin-word" not in sys.argv)
