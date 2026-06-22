"""Digital layout: fragmented name columns, e-signature table, vertical fields."""
import fitz

from app.services.layout._digital_analysis import analyze_digital_layout
from app.services.pdf.extractor import PageText, WordBox

_CW = 7.0   # char width
_H = 10.0   # line height


def _w(text, x0, y0):
    return WordBox(
        text=text, start=0, end=0,
        rect=fitz.Rect(x0, y0, x0 + len(text) * _CW, y0 + _H),
        block_no=0, line_no=0, word_no=0,
    )


def _page(words):
    return PageText(page_index=0, text=" ".join(w.text for w in words), words=words)


def _covered(word, rects):
    cx = (word.rect.x0 + word.rect.x1) / 2
    cy = (word.rect.y0 + word.rect.y1) / 2
    return any(r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1 for r in rects)


# --- #3: name split across columns ----------------------------------------

def test_fragmented_name_columns_redacted():
    words = [
        _w("Nombre", 0, 0), _w("Primer", 140, 0), _w("apellido", 175, 0),
        _w("Segundo", 320, 0), _w("apellido", 360, 0),
        # data row
        _w("LUIS", 0, 20), _w("PRADOS", 140, 20), _w("DE", 320, 20),
    ]
    a = analyze_digital_layout(_page(words))
    luis, prados, de = words[5], words[6], words[7]
    assert _covered(luis, a.redaction_rects)
    assert _covered(prados, a.redaction_rects)
    assert _covered(de, a.redaction_rects)


# --- #2: electronic-signature footer table --------------------------------

def test_electronic_signature_table_columns():
    words = [
        _w("Antefirma", 0, 0), _w("Nom", 150, 0), _w("Data", 230, 0),
        _w("Emissor", 320, 0), _w("cert", 375, 0),
        _w("Serie", 450, 0), _w("cert", 488, 0),
        # data row
        _w("ARQUITECTE", 0, 20), _w("LUIS", 150, 20), _w("2022-01-01", 230, 20),
        _w("ACCV", 320, 20), _w("10657193681292795182", 450, 20),
    ]
    a = analyze_digital_layout(_page(words))
    nom_val, data_val, serial = words[8], words[9], words[11]
    antefirma_val, emissor_val = words[7], words[10]
    assert _covered(nom_val, a.redaction_rects), "Nom value must be redacted"
    assert _covered(data_val, a.redaction_rects), "Data value must be redacted"
    assert _covered(serial, a.redaction_rects), "cert serial must be redacted"
    assert not _covered(antefirma_val, a.redaction_rects), "Antefirma (role) kept"
    assert not _covered(emissor_val, a.redaction_rects), "Emissor (ACCV) kept"


# --- #4: vertical label → value -------------------------------------------

def test_vertical_label_value_address():
    words = [
        _w("Nombre", 0, 0),
        _w("paseo", 0, 14), _w("de", 40, 14), _w("la", 60, 14),
        _w("castellana", 80, 14),
    ]
    a = analyze_digital_layout(_page(words))
    label, value = words[0], words[4]
    assert _covered(value, a.redaction_rects), "street value must be redacted"
    assert _covered(label, a.protected_rects), "label must be protected"
    assert not _covered(label, a.redaction_rects), "label must not be redacted"


def test_vertical_pairing_ignores_unrelated_far_row():
    # Label-only row followed by a far row (large vertical gap) → no redaction.
    words = [
        _w("Nombre", 0, 0),
        _w("texto", 0, 90), _w("lejano", 40, 90),
    ]
    a = analyze_digital_layout(_page(words))
    assert not _covered(words[1], a.redaction_rects)
    assert not _covered(words[2], a.redaction_rects)
