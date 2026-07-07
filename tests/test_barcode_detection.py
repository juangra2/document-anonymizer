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


def test_tiny_detection_ignored(monkeypatch):
    monkeypatch.setattr(cd, "_PYZBAR_AVAILABLE", True)
    monkeypatch.setattr(cd, "_pyzbar_decode", lambda img: [_zbar(10, 10, 40, 8)])
    assert cd.detect_redactable_codes(_img()) == []
