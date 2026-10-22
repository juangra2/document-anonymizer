"""El recuento por tipo no depende de include_entities.

En páginas a dos columnas la extracción intercala las líneas de ambas y el trozo
de texto de cada zona arrastra texto de la otra columna: la misma entidad se
detecta en varias zonas.  Se deduplica siempre, pida o no la petición la lista
de entidades, para que el informe no cuente de más.

Reproducir el orden de extracción con un PDF sintético no es fiable, así que se
prueba el punto exacto: varias zonas cuyo texto cubre la misma entidad.
"""
import fitz

from app.services.layout.models import PageZone, ZonedPage, ZoneType
from app.services.pdf.extractor import PageText, WordBox
from app.services.pdf.redactor import _collect_redaction_rects
from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer()

TEXT = "Solicitante con DNI 12345678Z presenta la solicitud dentro de plazo."


def _page_text() -> PageText:
    words, offset, x = [], 0, 40.0
    for token in TEXT.split(" "):
        start = TEXT.index(token, offset)
        end = start + len(token)
        width = 6.0 * len(token)
        words.append(WordBox(text=token, start=start, end=end,
                             rect=fitz.Rect(x, 100, x + width, 112),
                             block_no=0, line_no=0, word_no=len(words)))
        offset, x = end, x + width + 4.0
    return PageText(page_index=0, text=TEXT, words=words)


def _overlapping_zones(n: int = 3) -> ZonedPage:
    zones = [
        PageZone(zone_type=ZoneType.BODY, rect=fitz.Rect(30, 95, 560, 115),
                 text=TEXT, page_text_offset=0, source="digital")
        for _ in range(n)
    ]
    return ZonedPage(page_index=0, width=595, height=842, zones=zones)


def _counts(entities) -> dict[str, int]:
    return PiiAnalyzer.count_by_type(entities)


def test_counts_do_not_depend_on_collect_entities():
    page_text = _page_text()
    _, without, _ = _collect_redaction_rects(_overlapping_zones(), page_text, analyzer, "strict", False)
    _, with_list, _ = _collect_redaction_rects(_overlapping_zones(), page_text, analyzer, "strict", True)
    assert _counts(without) == _counts(with_list)


def test_an_entity_found_in_several_zones_is_counted_once():
    page_text = _page_text()
    _, entities, _ = _collect_redaction_rects(_overlapping_zones(), page_text, analyzer, "strict", False)
    assert _counts(entities).get("ES_DNI") == 1, _counts(entities)
