"""Cortar el valor de un campo de formulario donde de verdad termina (bloque 5b).

La redacción estructural tachaba desde la etiqueta hasta el final de la fila
visual.  Eso sobre-redacta en dos formas medidas en el corpus:

* **Dos columnas** (castellano | valenciano): «telèfon +34 706 206 505 i correu
  │ teléfono +34 706 …» quedaba como un único valor que cruzaba el canal.  El
  texto está justificado (17,7 pt entre palabras, 27,6 pt en el canal): el
  hueco no basta, hace falta el canal de la página.
* **Dos campos en la misma fila** (nóminas): «Nombre Sergio Garrido Ramos ·
  Titular del 70740740P».  Dentro del valor hay 2,6 pt entre palabras y 72,6 pt
  antes del segundo campo.

Tres cortes: el canal de columnas, la siguiente etiqueta de la fila y un salto
horizontal grande.

**Invariante de seguridad.**  Un corte nunca deja sin tapar una palabra que
antes se tapaba, salvo las reconocidas como etiqueta.  Lo que queda tras un
corte y no empieza por etiqueta forma su propio valor sin tipo, que se sigue
tachando.  La zona de formulario solo la cubre su redacción estructural, así
que perder cobertura aquí sería una fuga.

Funciona con cualquier objeto que tenga `.text` y `.rect` (WordBox del camino
digital y OcrToken del OCR).
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Callable, Sequence

# Salto horizontal que separa dos campos: múltiplo de la altura de línea.
GAP_HEIGHT_FACTOR = 3.0

# Canal de columnas: franja del tercio central que no toca ninguna palabra en al
# menos esta fracción de las filas con texto a ambos lados…
GUTTER_CLEAR_FRACTION = 0.6
# …y que al menos esta fracción de las filas con varias palabras tenga texto a
# ambos lados (si no, no hay dos columnas).
GUTTER_BOTH_SIDES_FRACTION = 0.5
GUTTER_MIN_WIDTH = 6.0

_COLON_LABEL_RE = re.compile(r"^[^\W\d_][\w./·'’ ]{0,18}:$", re.UNICODE)

LabelFinder = Callable[[Sequence], "int | None"]


@dataclass
class FieldSegment:
    """Un par etiqueta → valor dentro de una fila.

    `label_words` se protegen; `value_words` se tachan.  Sin etiqueta
    (`label_words` vacío) el valor es un resto sin tipo y se tacha igual.  Sin
    valor (`value_words` vacío) es una etiqueta suelta: solo se protege.
    """

    label_words: list = field(default_factory=list)
    value_words: list = field(default_factory=list)

    @property
    def label_text(self) -> str | None:
        return " ".join(w.text for w in self.label_words) or None


def detect_column_gutter(rows: Sequence[Sequence], page_width: float) -> float | None:
    """x del canal entre dos columnas de la página, o None si es a una columna."""
    multi = [row for row in rows if len(row) >= 2]
    if not multi or not page_width:
        return None
    best: tuple[float, float] | None = None
    x = page_width / 3
    while x < 2 * page_width / 3:
        both = clear = 0
        for row in multi:
            left = any(w.rect.x1 <= x for w in row)
            right = any(w.rect.x0 >= x + GUTTER_MIN_WIDTH for w in row)
            if left and right:
                both += 1
                if not any(w.rect.x0 < x + GUTTER_MIN_WIDTH and w.rect.x1 > x for w in row):
                    clear += 1
        if both and both >= GUTTER_BOTH_SIDES_FRACTION * len(multi) and clear >= GUTTER_CLEAR_FRACTION * both:
            score = clear / both
            if best is None or score > best[1]:
                best = (x + GUTTER_MIN_WIDTH / 2, score)
        x += 4.0
    return best[0] if best else None


def median_word_height(words: Sequence) -> float:
    heights = [w.rect.height for w in words if w.rect.height > 0]
    return statistics.median(heights) if heights else 10.0


def _is_colon_label(word) -> bool:
    return bool(_COLON_LABEL_RE.match(word.text.strip()))


def _remainder_label_length(words: Sequence, find_label: LabelFinder) -> int | None:
    """Longitud de la etiqueta al inicio de *words*.

    Además de las etiquetas reconocidas se acepta un token corto terminado en dos
    puntos («C/C:», «DNI/NIE:») seguido de algo.  Solo se usa DENTRO de una fila
    que ya empezó por etiqueta: al inicio de fila convertiría en campo cualquier
    «Asunto: …».
    """
    if not words:
        return None
    n = find_label(words)
    if n:
        return n
    if len(words) >= 2 and _is_colon_label(words[0]):
        return 1
    return None


def _first_cut(
    words: Sequence, height: float, gutter_x: float | None, find_label: LabelFinder,
) -> int | None:
    gap_limit = GAP_HEIGHT_FACTOR * height
    for i in range(1, len(words)):
        prev, cur = words[i - 1], words[i]
        if gutter_x is not None and prev.rect.x1 <= gutter_x <= cur.rect.x0:
            return i
        if cur.rect.x0 - prev.rect.x1 > gap_limit:
            return i
        if _remainder_label_length(words[i:], find_label):
            return i
    return None


def split_labeled_row(
    label_words: Sequence,
    value_words: Sequence,
    *,
    find_label: LabelFinder,
    height: float,
    gutter_x: float | None = None,
) -> list[FieldSegment]:
    """Parte `etiqueta + valor` en los pares reales de la fila.

    `find_label(words)` devuelve la longitud de una etiqueta reconocida al inicio
    de *words*, o None.  Cada camino (digital, OCR) pasa su propio buscador.
    Toda palabra de *value_words* acaba en algún segmento, como valor o como
    etiqueta: nunca se pierde.
    """
    segments: list[FieldSegment] = []
    label: list = list(label_words)
    label_used = False
    rest: list = list(value_words)

    def close_label() -> None:
        # Una etiqueta que se queda sin valor se registra igual: se protege, y
        # consta como etiqueta para el invariante de cobertura.
        if label and not label_used:
            segments.append(FieldSegment(label_words=label, value_words=[]))

    while rest:
        # Frontera etiqueta → valor: si el canal cae entre ambos, la etiqueta no
        # tiene valor en su columna y lo de la derecha es otra cosa.
        if label and gutter_x is not None and label[-1].rect.x1 <= gutter_x <= rest[0].rect.x0:
            close_label()
            label, label_used = [], False
        # El valor empieza a su vez por una etiqueta («correu teléfono +34…»).
        n = _remainder_label_length(rest, find_label)
        if n:
            close_label()
            label, label_used, rest = rest[:n], False, rest[n:]
            continue
        cut = _first_cut(rest, height, gutter_x, find_label)
        head, rest = (rest, []) if cut is None else (rest[:cut], rest[cut:])
        segments.append(FieldSegment(label_words=label, value_words=head))
        label_used = True
        if rest:
            label, label_used = [], False
    close_label()
    return segments
