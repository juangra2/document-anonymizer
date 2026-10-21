"""data/samples/real/ no se recorre nunca por defecto.

Guarda datos personales reales.  Un prototipo que recorrio data/samples/ entero llego
a analizar —en memoria, sin escribir nada— el DNI real del usuario.  La regla:
solo se toca por ruta explicita.  Aqui se fija que el recorrido comun la deje
fuera y que nadie recorra data/samples/ por su cuenta.
"""
import re
from pathlib import Path

from scripts.muestras import MUESTRAS, recorrer_muestras

RAIZ = Path(__file__).resolve().parents[1]


def test_el_recorrido_comun_no_entra_en_real():
    assert all("real" not in p.relative_to(MUESTRAS).parts
               for p in recorrer_muestras("*"))


# Una linea que menciona samples y la recorre con glob/rglob/walk/listdir.
_RECORRE = re.compile(r"samples.*\b(rglob|glob|walk|listdir|iterdir)\b"
                      r"|\b(rglob|glob|walk|listdir|iterdir)\b.*samples")


def test_nadie_recorre_samples_por_su_cuenta():
    sueltos = []
    for carpeta in ("scripts", "tests", "evaluation", "app"):
        for py in (RAIZ / carpeta).rglob("*.py"):
            if py.name in ("muestras.py", "test_muestras_privadas.py"):
                continue
            for n, linea in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if _RECORRE.search(linea):
                    sueltos.append(f"{py.relative_to(RAIZ)}:{n}")
    assert not sueltos, f"recorren data/samples/ sin scripts/muestras.py: {sueltos}"
