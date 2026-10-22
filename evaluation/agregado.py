"""Junta las metricas de los tres corpus: digital, escaneado e hibrido.

Por que tres y no una
---------------------
Una sola cifra global esconde donde se esta flojo: el corpus digital es el
grande y dominaria la media, aunque el camino escaneado no se estuviera
midiendo bien.  Con un numero por camino, siempre se sabe donde se esta flojo.

La cifra global es la MEDIA ARITMETICA de los tres F1, no ponderada por
documentos.  Ponderar volveria a esconder el camino escaneado, que es el corpus
pequeno.  Por eso nunca se imprime
sola: esta funcion solo la muestra con las tres al lado.

Comprobacion de camino
----------------------
Cada corpus declara por que camino deberian ir sus documentos, y se compara
contra el `document_type` que devolvio el servicio.  Los desvios salen arriba
del todo, antes que ninguna metrica: un documento del corpus escaneado que sale
por el camino digital significa que no se ha mirado un solo pixel, y entonces
su F1 no dice lo que parece decir.

Uso:
    python -m evaluation.agregado \\
        --digital evaluation/reference/c4/resultados_digital.json \\
        --escaneado evaluation/reference/c4/resultados_escaneado.json \\
        --hibrido evaluation/reference/c4/resultados_hibrido.json

Sin argumentos usa los de la medicion final (c4).
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# Camino que le toca a cada corpus, y el que el servicio deberia declarar.
CAMINO_ESPERADO = {
    "digital": "pdf_digital",
    "escaneado": "pdf_scanned",
    "hibrido": "pdf_hybrid",
}

# Desvios de camino que se dan por esperados en un corpus, con su motivo: se
# imprimen como tales en vez de contarse como fallo.
DESVIO_ESPERADO = {
    "hibrido": (
        "ESPERADO hasta el arreglo del reparto: hoy un documento mixto con una "
        "media de 200 caracteres por pagina se manda ENTERO al camino digital, "
        "sin pasar una sola pagina por OCR. Esta es la linea base que se quiere "
        "medir. Deberia desaparecer despues del arreglo unificado."
    ),
}


@dataclass
class Corpus:
    nombre: str
    fichero: Path
    datos: dict

    @property
    def f1(self) -> float:
        return self.datos["summary"]["overall_f1"]

    @property
    def precision(self) -> float:
        return self.datos["summary"]["overall_precision"]

    @property
    def recall(self) -> float:
        return self.datos["summary"]["overall_recall"]

    @property
    def f1_por_aparicion(self) -> float | None:
        """F1 por aparicion; None en resultados que no lo guardan."""
        antigua = self.datos["summary"].get("por_aparicion")
        return antigua["f1"] if antigua else None

    @property
    def sobre_redaccion(self) -> int | None:
        """Barras mas largas que el dato; None en resultados anteriores."""
        return self.datos["summary"].get("sobre_redaccion")

    @property
    def documentos(self) -> list[dict]:
        return self.datos.get("doc_results", [])

    def por_tipo(self) -> Counter:
        return Counter(d.get("doc_type", "?") for d in self.documentos)

    def desvios(self) -> list[tuple[str, str]]:
        """(documento, camino_real) de los que no fueron por donde tocaba."""
        esperado = CAMINO_ESPERADO[self.nombre]
        fuera = []
        for d in self.documentos:
            camino = d.get("camino", "")
            if not camino:
                continue           # evaluacion anterior al registro del camino
            if camino != esperado:
                fuera.append((d.get("doc_id", "?"), camino))
        return fuera

    def sin_camino(self) -> int:
        return sum(1 for d in self.documentos if not d.get("camino"))


def cargar(nombre: str, ruta: Path) -> Corpus:
    if not ruta.exists():
        raise SystemExit(
            f"Falta el resultado del corpus {nombre}: {ruta}\n"
            "Lanza primero su evaluacion con python -m evaluation.run"
        )
    return Corpus(nombre, ruta, json.loads(ruta.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------
# presentacion
# --------------------------------------------------------------------------

def _barra(titulo: str) -> None:
    print("\n" + "=" * 74)
    print(titulo)
    print("=" * 74)


def imprimir_caminos(corpus: list[Corpus]) -> bool:
    """Arriba del todo.  Devuelve True si hay algun desvio no esperado."""
    _barra("COMPROBACION DE CAMINO — cada documento por donde le toca")
    hay_inesperado = False

    for c in corpus:
        desviados = c.desvios()
        total = len(c.documentos)
        esperado = CAMINO_ESPERADO[c.nombre]

        if not desviados:
            print(f"\n  [ok]  {c.nombre:10s} {total - len(desviados)}/{total} "
                  f"por {esperado}")
            if c.sin_camino():
                print(f"        ({c.sin_camino()} sin camino registrado: "
                      "resultado de una evaluacion anterior)")
            continue

        nota = DESVIO_ESPERADO.get(c.nombre)
        etiqueta = "ESPERADO" if nota else "!! ROJO"
        if not nota:
            hay_inesperado = True

        print(f"\n  [{etiqueta}]  {c.nombre:10s} {len(desviados)}/{total} "
              f"NO fueron por {esperado}")
        reparto = Counter(camino for _, camino in desviados)
        for camino, cuantos in reparto.most_common():
            print(f"        {cuantos:3d} salieron por {camino}")
        if nota:
            for linea in _envolver(nota, 64):
                print(f"        {linea}")
        else:
            print("        Un documento que no va por su camino no mide lo que")
            print("        parece medir. Revisar antes de leer sus metricas.")
            for doc_id, camino in desviados[:10]:
                print(f"          - {doc_id}: {camino}")
            if len(desviados) > 10:
                print(f"          ... y {len(desviados) - 10} mas")

    return hay_inesperado


def _envolver(texto: str, ancho: int) -> list[str]:
    palabras, lineas, actual = texto.split(), [], ""
    for palabra in palabras:
        if len(actual) + len(palabra) + 1 > ancho:
            lineas.append(actual)
            actual = palabra
        else:
            actual = f"{actual} {palabra}".strip()
    if actual:
        lineas.append(actual)
    return lineas


def imprimir_metricas(corpus: list[Corpus]) -> None:
    _barra("METRICAS POR CAMINO")
    print(f"\n  {'corpus':12s} {'docs':>5s} {'F1':>8s} {'precision':>10s} {'recall':>8s}"
          f" {'sobre-red.':>11s}   {'F1 por aparicion':>16s}")
    print("  " + "-" * 78)
    for c in corpus:
        antigua = "—" if c.f1_por_aparicion is None else f"{c.f1_por_aparicion:.4f}"
        sobre = "—" if c.sobre_redaccion is None else str(c.sobre_redaccion)
        print(f"  {c.nombre:12s} {len(c.documentos):5d} {c.f1:8.4f} "
              f"{c.precision:10.4f} {c.recall:8.4f} {sobre:>11s}   {antigua:>16s}")
    print("\n  sobre-red.: barras mas largas que el dato (variantes de un dato ya")
    print("  detectado).  No cuentan como falso positivo, pero se publican aqui.")
    print("\n  F1 cuenta cada valor una vez por documento; 'por aparicion' es la")
    print("  cuenta antigua, que se guarda mientras dure la transicion.")

    media = sum(c.f1 for c in corpus) / len(corpus)
    print("  " + "-" * 46)
    print(f"  {'media':12s} {'':5s} {media:8.4f}    <- media aritmetica de los "
          f"{len(corpus)} F1 de arriba")
    print("\n  La media no se lee sola: ponderarla por documentos volveria a")
    print("  esconder el camino escaneado, que es el corpus pequeno.")


def imprimir_composicion(corpus: list[Corpus]) -> None:
    _barra("COMPOSICION DE CADA CORPUS")
    for c in corpus:
        print(f"\n  {c.nombre} ({len(c.documentos)} documentos)")
        for tipo, cuantos in sorted(c.por_tipo().items()):
            print(f"    {cuantos:4d}  {tipo}")


def imprimir_por_tipo_de_dato(corpus: list[Corpus]) -> None:
    _barra("F1 POR TIPO DE DATO Y CAMINO")
    tipos = sorted({t for c in corpus for t in c.datos.get("by_type", {})})
    cabecera = "  " + f"{'tipo de dato':22s}" + "".join(f"{c.nombre:>12s}" for c in corpus)
    print("\n" + cabecera)
    print("  " + "-" * (22 + 12 * len(corpus)))
    for tipo in tipos:
        fila = f"  {tipo:22s}"
        for c in corpus:
            datos = c.datos.get("by_type", {}).get(tipo)
            fila += f"{datos['f1']:>12.4f}" if datos else f"{'—':>12s}"
        print(fila)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Junta las metricas de los tres corpus por camino.",
    )
    parser.add_argument("--digital", type=Path,
                        default=Path("evaluation/reference/c4/resultados_digital.json"))
    parser.add_argument("--escaneado", type=Path,
                        default=Path("evaluation/reference/c4/resultados_escaneado.json"))
    parser.add_argument("--hibrido", type=Path,
                        default=Path("evaluation/reference/c4/resultados_hibrido.json"))
    parser.add_argument("--json", type=Path, default=None,
                        help="Guardar el resumen combinado en este fichero")
    args = parser.parse_args()

    corpus = [
        cargar("digital", args.digital),
        cargar("escaneado", args.escaneado),
        cargar("hibrido", args.hibrido),
    ]

    hay_inesperado = imprimir_caminos(corpus)
    imprimir_metricas(corpus)
    imprimir_composicion(corpus)
    imprimir_por_tipo_de_dato(corpus)

    if hay_inesperado:
        print("\n  Hay desvios de camino NO esperados: las metricas de arriba")
        print("  no miden lo que parecen medir hasta resolverlos.")

    if args.json:
        resumen = {
            "media_f1": round(sum(c.f1 for c in corpus) / len(corpus), 4),
            "por_corpus": {
                c.nombre: {
                    "documentos": len(c.documentos),
                    "f1": c.f1, "precision": c.precision, "recall": c.recall,
                    "f1_por_aparicion": c.f1_por_aparicion,
                    "sobre_redaccion": c.sobre_redaccion,
                    "desviados": len(c.desvios()),
                    "desvio_esperado": c.nombre in DESVIO_ESPERADO,
                    "por_tipo_documento": dict(c.por_tipo()),
                }
                for c in corpus
            },
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(resumen, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        print(f"\n  Resumen combinado guardado en {args.json}")


if __name__ == "__main__":
    main()
