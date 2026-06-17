"""INE public gazetteers: Spanish surnames, given names and municipalities.

Runtime loads pre-processed, version-controlled text artefacts from
``gazetteers/`` (built by ``scripts/build_gazetteers.py`` from public INE
files — no network dependency at runtime).  Values are stored already
normalised so lookups are O(1) against :func:`normalize`.

Data sources (Instituto Nacional de Estadística, public):
  * Apellidos con frecuencia ≥ 20  → ine_surnames.txt
  * Nombres con frecuencia ≥ 20     → ine_given_names.txt
  * Relación de municipios y códigos por provincia → ine_municipalities.tsv
  * Relación de provincias y códigos → ine_provinces.tsv
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).parent / "gazetteers"

_ACCENTS = {
    "Á": "A", "É": "E", "Í": "I", "Ó": "O", "Ú": "U", "Ü": "U", "Ñ": "N",
    "À": "A", "È": "E", "Ì": "I", "Ò": "O", "Ù": "U", "Ç": "C", "·": "",
}


def normalize(value: str) -> str:
    """Uppercase, strip accents/punctuation, collapse whitespace.

    Mirrors ``PiiAnalyzer._normalize_false_positive_text`` so a value can be
    compared against the gazetteers and the analyzer's FP catalogue alike.
    """
    text = value.strip().upper().strip(":. ")
    for src, dst in _ACCENTS.items():
        text = text.replace(src, dst)
    for ch in "/-_,;.":
        text = text.replace(ch, " ")
    return " ".join(text.split())


def _load_lines(name: str) -> list[str]:
    path = _DATA / name
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [ln.rstrip("\n") for ln in fh if ln.strip()]


@lru_cache(maxsize=1)
def surnames() -> frozenset[str]:
    """Normalised single-token surnames (INE, frequency ≥ 20)."""
    return frozenset(_load_lines("ine_surnames.txt"))


@lru_cache(maxsize=1)
def given_names() -> frozenset[str]:
    """Normalised single-token given names (INE, frequency ≥ 20)."""
    return frozenset(_load_lines("ine_given_names.txt"))


@lru_cache(maxsize=1)
def municipalities() -> dict[str, frozenset[str]]:
    """Map normalised municipality name → set of 2-digit province codes."""
    out: dict[str, set[str]] = {}
    for line in _load_lines("ine_municipalities.tsv"):
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        name, cpro = parts
        out.setdefault(name, set()).add(cpro)
    return {k: frozenset(v) for k, v in out.items()}


@lru_cache(maxsize=1)
def provinces() -> dict[str, str]:
    """Map 2-digit province code → normalised province name."""
    out: dict[str, str] = {}
    for line in _load_lines("ine_provinces.tsv"):
        parts = line.split("\t")
        if len(parts) == 2:
            out[parts[0]] = parts[1]
    return out


@lru_cache(maxsize=1)
def toponyms() -> frozenset[str]:
    """Normalised municipality and province names (for PERSON FP filtering)."""
    return frozenset(municipalities()) | frozenset(provinces().values())


def is_person_token(token: str) -> bool:
    """True if *token* (already normalised) is a known INE name or surname."""
    return token in surnames() or token in given_names()


def municipality_province_codes(name: str) -> frozenset[str]:
    """Province codes for a (normalised) municipality name; empty if unknown."""
    return municipalities().get(name, frozenset())


def is_toponym(value: str) -> bool:
    """True if the (raw) *value* normalises to a known municipality/province."""
    return normalize(value) in toponyms()
