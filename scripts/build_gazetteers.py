"""Build INE gazetteer artefacts from the raw public INE Excel files.

Reads the files downloaded into ``data/ine_raw/`` and writes normalised,
version-controlled text artefacts into ``app/services/pii/gazetteers/``.

Raw sources (download once from https://www.ine.es):
  * apellidos_frecuencia.xls       - apellidos con frecuencia >= 20
  * nombres_por_edad_media.xls     - nombres con frecuencia >= 20 (Hombres/Mujeres)
  * diccionario25.xlsx             - relacion de municipios y codigos por provincia

Run:  python scripts/build_gazetteers.py
Requires (build-time only):  pandas, openpyxl, xlrd>=2.0
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "ine_raw"
OUT = ROOT / "app" / "services" / "pii" / "gazetteers"

sys.path.insert(0, str(ROOT))
from app.services.pii.gazetteer import normalize  # noqa: E402

# Tokens that appear inside compound names/surnames but are not names themselves.
_PARTICLES = {"DE", "DEL", "LA", "LAS", "LO", "LOS", "Y", "I", "DA", "DO", "EL"}

# 52 INE province codes to names (public, stable classification).
_PROVINCES = {
    "01": "Araba/Alava", "02": "Albacete", "03": "Alicante/Alacant",
    "04": "Almeria", "05": "Avila", "06": "Badajoz", "07": "Illes Balears",
    "08": "Barcelona", "09": "Burgos", "10": "Caceres", "11": "Cadiz",
    "12": "Castellon/Castello", "13": "Ciudad Real", "14": "Cordoba",
    "15": "A Coruna", "16": "Cuenca", "17": "Girona", "18": "Granada",
    "19": "Guadalajara", "20": "Gipuzkoa", "21": "Huelva", "22": "Huesca",
    "23": "Jaen", "24": "Leon", "25": "Lleida", "26": "La Rioja", "27": "Lugo",
    "28": "Madrid", "29": "Malaga", "30": "Murcia", "31": "Navarra",
    "32": "Ourense", "33": "Asturias", "34": "Palencia", "35": "Las Palmas",
    "36": "Pontevedra", "37": "Salamanca", "38": "Santa Cruz de Tenerife",
    "39": "Cantabria", "40": "Segovia", "41": "Sevilla", "42": "Soria",
    "43": "Tarragona", "44": "Teruel", "45": "Toledo", "46": "Valencia/Valencia",
    "47": "Valladolid", "48": "Bizkaia", "49": "Zamora", "50": "Zaragoza",
    "51": "Ceuta", "52": "Melilla",
}


def _column_after_header(df: pd.DataFrame, header_label: str, col: int) -> list[str]:
    """Return values of *col* below the row whose any cell == header_label."""
    header_row = None
    for i in range(len(df)):
        row = [str(x).strip() for x in df.iloc[i].tolist()]
        if header_label in row:
            header_row = i
            break
    if header_row is None:
        return []
    values = df.iloc[header_row + 1:, col].dropna().tolist()
    return [str(v).strip() for v in values]


def _tokens(raw: str) -> list[str]:
    out = []
    for tok in normalize(raw).split():
        if len(tok) >= 2 and tok not in _PARTICLES and tok.isalpha():
            out.append(tok)
    return out


def build_surnames() -> set[str]:
    tokens: set[str] = set()
    xl = pd.ExcelFile(RAW / "apellidos_frecuencia.xls", engine="xlrd")
    for sheet in xl.sheet_names:
        df = xl.parse(sheet, header=None)
        for raw in _column_after_header(df, "Apellido", 1):
            tokens.update(_tokens(raw))
    return tokens


def build_given_names() -> set[str]:
    tokens: set[str] = set()
    xl = pd.ExcelFile(RAW / "nombres_por_edad_media.xls", engine="xlrd")
    for sheet in xl.sheet_names:
        df = xl.parse(sheet, header=None)
        for raw in _column_after_header(df, "Nombre", 1):
            tokens.update(_tokens(raw))
    return tokens


def build_municipalities() -> list[tuple[str, str]]:
    df = pd.read_excel(RAW / "diccionario25.xlsx", engine="openpyxl", header=None)
    header_row = None
    for i in range(len(df)):
        row = [str(x).strip() for x in df.iloc[i].tolist()]
        if "CPRO" in row and "NOMBRE" in row:
            header_row = i
            cpro_col = row.index("CPRO")
            name_col = row.index("NOMBRE")
            break
    if header_row is None:
        raise RuntimeError("CPRO/NOMBRE header not found in diccionario25.xlsx")

    pairs: set[tuple[str, str]] = set()
    for i in range(header_row + 1, len(df)):
        cpro = df.iloc[i, cpro_col]
        name = df.iloc[i, name_col]
        if pd.isna(cpro) or pd.isna(name):
            continue
        cpro = str(cpro).strip().zfill(2)
        if not cpro.isdigit():
            continue
        # Bilingual "Name1/Name2" municipalities: index each variant separately
        # (normalize turns "/" into a space, which would otherwise merge them).
        for variant in str(name).split("/"):
            norm = normalize(variant)
            if norm:
                pairs.add((norm, cpro))
            # INE stores the inverted article form ("Coruna, A", "Rioja, La").
            # Also index the natural order ("A Coruna") used in written addresses.
            if "," in variant:
                head, _, tail = variant.partition(",")
                deinv = normalize(f"{tail} {head}")
                if deinv:
                    pairs.add((deinv, cpro))
    return sorted(pairs)


def main() -> None:
    warnings.simplefilter("ignore")
    OUT.mkdir(parents=True, exist_ok=True)

    surnames = build_surnames()
    (OUT / "ine_surnames.txt").write_text(
        "\n".join(sorted(surnames)) + "\n", encoding="utf-8"
    )
    print(f"ine_surnames.txt: {len(surnames)} tokens")

    names = build_given_names()
    (OUT / "ine_given_names.txt").write_text(
        "\n".join(sorted(names)) + "\n", encoding="utf-8"
    )
    print(f"ine_given_names.txt: {len(names)} tokens")

    munis = build_municipalities()
    (OUT / "ine_municipalities.tsv").write_text(
        "\n".join(f"{n}\t{c}" for n, c in munis) + "\n", encoding="utf-8"
    )
    print(f"ine_municipalities.tsv: {len(munis)} (name, province) pairs")

    (OUT / "ine_provinces.tsv").write_text(
        "\n".join(f"{c}\t{normalize(n)}" for c, n in sorted(_PROVINCES.items()))
        + "\n",
        encoding="utf-8",
    )
    print(f"ine_provinces.tsv: {len(_PROVINCES)} provinces")


if __name__ == "__main__":
    main()
