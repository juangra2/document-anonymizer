"""Bloque 5b — cortar el valor de un campo donde termina, sin perder cobertura.

Coordenadas reales medidas en el corpus (nomina_0001, nomina_0005,
acuerdo_adscripcion_0001).  El invariante que se fija en todos los casos: toda
palabra de valor original sigue tapada salvo las que se reconocen como
etiqueta.
"""
from dataclasses import dataclass

import fitz
import pytest

from app.services.layout.value_split import detect_column_gutter, split_labeled_row
from app.services.pdf.table_layout import find_label_length_at_row_start, normalize_token

H = 13.1


@dataclass
class W:
    text: str
    rect: fitz.Rect


def row(*spec):
    return [W(t, fitz.Rect(x0, 100, x1, 100 + H)) for t, x0, x1 in spec]


def find_label(words):
    return find_label_length_at_row_start([normalize_token(w.text) for w in words])


def _split(words, gutter_x=None):
    n = find_label(words)
    assert n, "la fila debe empezar por etiqueta"
    return split_labeled_row(words[:n], words[n:], find_label=find_label, height=H, gutter_x=gutter_x)


def _covered(segments):
    return {w.text for s in segments for w in s.value_words}


def _labels(segments):
    return {w.text for s in segments for w in s.label_words}


def _invariant(words, segments):
    value_words = {w.text for w in words[find_label(words):]}
    assert value_words - _labels(segments) <= _covered(segments), "un corte no puede destapar un valor"


# --- dos campos en la misma fila (nómina) ------------------------------------------

def test_nomina_second_field_label_is_no_longer_redacted():
    words = row(("Nombre", 67, 103), ("Sergio", 164, 193), ("Garrido", 196, 230), ("Ramos", 233, 264),
                ("Titular", 337, 366), ("del", 369, 382), ("70740740P", 424, 472))
    segments = _split(words)
    _invariant(words, segments)
    assert [w.text for w in segments[0].value_words] == ["Sergio", "Garrido", "Ramos"]
    assert "Titular" in _labels(segments), "la segunda etiqueta ya no se tacha"
    assert "70740740P" in _covered(segments), "el DNI de la derecha sigue tapado"


def test_nomina_colon_label_after_a_gap_opens_its_own_field():
    words = row(("Teléfono:", 67, 107), ("+34", 164, 180), ("634", 182, 198), ("833", 201, 217), ("090", 219, 235),
                ("C/C:", 337, 356), ("ES7500819984015916029770", 424, 553))
    segments = _split(words)
    _invariant(words, segments)
    assert [w.text for w in segments[0].value_words] == ["+34", "634", "833", "090"]
    assert "C/C:" in _labels(segments)
    iban = [s for s in segments if any(w.text.startswith("ES75") for w in s.value_words)]
    assert iban and iban[0].label_text == "C/C:", "el IBAN queda con su propia etiqueta y tipo"


# --- dos columnas (bilingüe justificado) ----------------------------------------------

BILINGUE = (("telèfon", 57, 85), ("+34", 103, 119), ("706", 137, 152), ("206", 170, 186), ("505", 204, 219),
            ("i", 237, 239), ("correu", 257, 284), ("teléfono", 311, 345), ("+34", 362, 378), ("706", 394, 410),
            ("206", 426, 442), ("505", 458, 474), ("y", 491, 495), ("correo", 512, 539))


def test_bilingual_row_is_cut_at_the_column_gutter():
    words = row(*BILINGUE)
    segments = _split(words, gutter_x=298.0)
    _invariant(words, segments)
    assert all(w.rect.x1 <= 298 for w in segments[0].value_words), "el primer valor no cruza el canal"
    assert "teléfono" in _labels(segments), "la etiqueta de la otra columna ya no se tacha"


def test_bilingual_row_is_also_cut_by_the_next_label_without_gutter():
    words = row(*BILINGUE)
    segments = _split(words, gutter_x=None)
    _invariant(words, segments)
    assert "teléfono" in _labels(segments)


def test_justified_spacing_alone_does_not_cut_a_value():
    words = row(("Nombre", 57, 90), ("Rosa", 108, 130), ("Cruz", 148, 170), ("Martín", 188, 220))
    segments = _split(words)
    assert len(segments) == 1 and len(segments[0].value_words) == 3


# --- restos sin etiqueta ------------------------------------------------------------

def test_a_remainder_without_label_is_still_redacted_untyped():
    words = row(("Nombre", 67, 103), ("Sergio", 164, 193), ("Ramos", 196, 230), ("BORRADOR", 330, 420))
    segments = _split(words)
    _invariant(words, segments)
    tail = [s for s in segments if any(w.text == "BORRADOR" for w in s.value_words)]
    assert tail and tail[0].label_text is None, "resto sin etiqueta: se tapa, sin tipo"


# --- canal de columnas ----------------------------------------------------------------

def _page_rows(two_columns: bool):
    rows = []
    for i in range(12):
        y = 100 + i * 16
        left = [W(f"izq{i}{k}", fitz.Rect(60 + k * 45, y, 100 + k * 45, y + H)) for k in range(5)]
        right = [W(f"der{i}{k}", fitz.Rect(320 + k * 45, y, 360 + k * 45, y + H)) for k in range(5)]
        rows.append(left + right if two_columns else left + [W(f"sig{i}{k}", fitz.Rect(285 + k * 45, y, 325 + k * 45, y + H)) for k in range(5)])
    return rows


def test_gutter_is_found_on_a_two_column_page():
    gutter = detect_column_gutter(_page_rows(True), page_width=595)
    assert gutter is not None and 280 < gutter < 320


def test_no_gutter_on_a_single_column_page():
    assert detect_column_gutter(_page_rows(False), page_width=595) is None


# --- etiqueta de varias palabras tras el canal (caso dificil 12) -------------------

def test_bilingue_a_dos_columnas_la_etiqueta_de_la_derecha_no_se_tacha():
    """Solicitud bilingue a dos columnas: tras el canal, "Nombre y apellidos:" no
    se reconocia como etiqueta y, por el invariante, se tachaba como valor."""
    words = row(("Nom", 50, 68), ("i", 70, 72), ("cognoms:", 74, 113), ("Jordi", 145, 162),
                ("Alapont", 165, 192), ("Ribes", 194, 214), ("Nombre", 307, 337), ("y", 339, 344),
                ("apellidos:", 346, 383), ("Jordi", 402, 419), ("Alapont", 422, 449), ("Ribes", 451, 471))
    segments = split_labeled_row(words[:3], words[3:], find_label=find_label, height=H, gutter_x=293.3)
    assert {"Nombre", "y", "apellidos:"} <= _labels(segments)
    assert [w.text for w in segments[-1].value_words] == ["Jordi", "Alapont", "Ribes"]
    assert sum(1 for s in segments for w in s.value_words if w.text == "Alapont") == 2


def test_una_frase_con_cifras_no_es_una_etiqueta():
    words = row(("Nom", 50, 68), ("i", 70, 72), ("cognoms:", 74, 113), ("Jordi", 145, 162),
                ("Ribes", 194, 214), ("Exp", 307, 325), ("2024", 327, 350), ("nº:", 352, 365),
                ("Valor", 402, 430))
    segments = split_labeled_row(words[:3], words[3:], find_label=find_label, height=H, gutter_x=293.3)
    assert "2024" in _covered(segments), "lo que lleva cifras se sigue tachando"


def test_un_nombre_antes_de_otra_etiqueta_no_se_toma_por_etiqueta():
    """Lectura de barras c4 (nomina del hibrido): "Nombre | Eva González Domínguez |
    Doc. identitat: | 89650526E".  La etiqueta de varias palabras terminada en dos
    puntos se usaba tambien para BUSCAR el corte, y "González Domínguez Doc.
    identitat:" pasaba por etiqueta: se cortaba tras "Eva" y los apellidos
    quedaban a la vista."""
    words = row(("Nombre", 67, 103), ("Eva", 164, 181), ("González", 183, 225), ("Domínguez", 227, 270),
                ("Doc.", 337, 358), ("identitat:", 360, 400), ("89650526E", 424, 472))
    segments = split_labeled_row(words[:1], words[1:], find_label=find_label, height=H)
    assert {"Eva", "González", "Domínguez"} <= _covered(segments)
    assert "89650526E" in _covered(segments)
