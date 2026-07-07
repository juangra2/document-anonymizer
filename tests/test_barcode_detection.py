"""Añadido 2: códigos de barras lineales vía pyzbar con la guardia compartida."""
from types import SimpleNamespace

from PIL import Image

import app.services.vision.code_detector as cd


def _zbar(left, top, width, height, data=b"PAYLOAD-123"):
    return SimpleNamespace(
        rect=SimpleNamespace(left=left, top=top, width=width, height=height),
        data=data,
    )


def _img(w=1000, h=1000):
    return Image.new("RGB", (w, h), "white")


def test_wide_linear_barcode_detected_and_decoded(monkeypatch):
    # Un Code128 es ancho (no cuadrado): debe salir de detect_and_decode y
    # sobrevivir a la guardia compartida (payload + tamaño).
    monkeypatch.setattr(cd, "_PYZBAR_AVAILABLE", True)
    monkeypatch.setattr(cd, "_pyzbar_decode", lambda img: [_zbar(100, 100, 300, 60)])
    pairs = cd.detect_and_decode_visual_codes(_img())
    assert any(t == "PAYLOAD-123" for _, t in pairs)

    out = cd.detect_redactable_codes(_img())
    assert len(out) == 1 and out[0][1] == "PAYLOAD-123"


def test_empty_payload_barcode_not_redactable(monkeypatch):
    monkeypatch.setattr(cd, "_PYZBAR_AVAILABLE", True)
    monkeypatch.setattr(cd, "_pyzbar_decode", lambda img: [_zbar(100, 100, 300, 60, data=b"")])
    assert cd.detect_redactable_codes(_img()) == []


def test_oversized_barcode_dropped_by_size_cap(monkeypatch):
    monkeypatch.setattr(cd, "_PYZBAR_AVAILABLE", True)
    monkeypatch.setattr(cd, "_pyzbar_decode", lambda img: [_zbar(0, 0, 900, 900)])
    assert cd.detect_redactable_codes(_img()) == []
    assert cd.detect_visual_code_rects(_img()) == []


def test_fast_path_includes_decoded_barcode(monkeypatch):
    # El fast-path (imagen-dominante) también debe cubrir lineales decodificados.
    monkeypatch.setattr(cd, "_PYZBAR_AVAILABLE", True)
    monkeypatch.setattr(cd, "_pyzbar_decode", lambda img: [_zbar(100, 100, 300, 60)])
    rects = cd.detect_visual_code_rects(_img())
    assert len(rects) == 1
    assert (rects[0].x1 - rects[0].x0) > (rects[0].y1 - rects[0].y0), "es ancho"


def test_degenerate_zbar_bbox_recovered(monkeypatch):
    # zbar decodifica Code128 reales pero a veces reporta bbox width=0 (solo un
    # borde).  La extensión se recupera con OpenCV desde el patrón de barras.
    from PIL import ImageDraw
    img = _img()
    d = ImageDraw.Draw(img)
    x0, x1, y0, y1 = 300, 700, 400, 500
    x = x0
    while x < x1:                       # patrón de barras verticales
        d.rectangle([x, y0, x + 3, y1], fill="black")
        x += 8
    # zbar degenerado: reporta solo el borde derecho, width=0.
    deg = SimpleNamespace(
        rect=SimpleNamespace(left=x1, top=y0, width=0, height=y1 - y0),
        data=b"9052146250002201855",
        polygon=[SimpleNamespace(x=x1, y=y0), SimpleNamespace(x=x1, y=y1)],
    )
    monkeypatch.setattr(cd, "_PYZBAR_AVAILABLE", True)
    monkeypatch.setattr(cd, "_pyzbar_decode", lambda im: [deg])

    out = cd.detect_redactable_codes(img)
    assert len(out) == 1, "el bbox degenerado debe recuperarse y redactarse"
    r = out[0][0]
    assert (r.x1 - r.x0) > 300, "debe cubrir el ancho real de las barras"
    assert r.x0 <= x0 + 20 and r.x1 >= x1 - 20


def test_wide_recovered_barcode_allowed_above_qr_cap(monkeypatch):
    # Un lineal ancho decodificado puede superar el 5% pensado para QR; el tope
    # de forma (20% para aspect>=3) lo mantiene redactable.
    monkeypatch.setattr(cd, "_PYZBAR_AVAILABLE", True)
    monkeypatch.setattr(cd, "_pyzbar_decode",
                        lambda im: [_zbar(50, 450, 900, 90)])   # 8.1% del área
    out = cd.detect_redactable_codes(_img())
    assert len(out) == 1


def test_tiny_detection_ignored(monkeypatch):
    monkeypatch.setattr(cd, "_PYZBAR_AVAILABLE", True)
    monkeypatch.setattr(cd, "_pyzbar_decode", lambda img: [_zbar(10, 10, 40, 8)])
    assert cd.detect_redactable_codes(_img()) == []
