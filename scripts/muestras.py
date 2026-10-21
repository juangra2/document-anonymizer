"""El unico recorrido de data/samples/: deja fuera data/samples/real/.

data/samples/real/ guarda documentos con datos personales reales.  Ningun script,
prototipo ni test la recorre salvo que se le pida por RUTA explicita (como
tests/test_dni_pegado.py, que lee un fichero concreto y se salta si no esta).
Para recorrer las muestras se usa recorrer_muestras(); recorrer data/samples/ con
glob/rglob por otro lado lo detecta tests/test_muestras_privadas.py.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

MUESTRAS = Path(__file__).resolve().parents[1] / "data" / "samples"
PRIVADA = "real"


def recorrer_muestras(patron: str = "*.pdf") -> Iterator[Path]:
    """Ficheros de data/samples/ que casan con `patron`, sin entrar en data/samples/real/."""
    for ruta in sorted(MUESTRAS.rglob(patron)):
        if PRIVADA not in ruta.relative_to(MUESTRAS).parts:
            yield ruta
