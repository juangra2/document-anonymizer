"""Fotos de documentos: no rendirse con una foto normal.

Antes de dar una pagina por ilegible (y taparla entera) se intenta lo razonable:
enderezar una inclinacion pequeña, recortar la hoja del fondo y corregir la
perspectiva, invertir los colores de un texto claro sobre fondo oscuro.  Y se
decide tambien por la confianza del OCR, no solo por caracteres por pieza.

Se fija ademas que las etiquetas de un formulario se conservan (la barra se
recorta al tramo del dato aunque sea mayoria en la linea), que un QR que se
descodifica se tapa aunque ocupe mucho de una tarjeta, y que una copia de DNI se
sigue tapando entera.  Lo que se mide aqui por pixeles es lo mismo que mide la
lectura de barras del corpus de imagenes (evaluation/lectura_imagenes.py).
"""
from pathlib import Path

import fitz
import numpy as np
import pytest
from PIL import Image

from app.core.config import settings
from app.services.ocr.models import OcrPage, OcrToken
from app.services.ocr.orientation import legible, resolve_orientation
from evaluation.corpus_imagenes import (
    PERSONAS, VARIANTES, Foto, carnet, dni_ejemplo, formulario, fotografiar,
)
from evaluation.lectura_imagenes import cobertura, en_plano


def _pagina(textos, confianza):
    tokens = [OcrToken(text=t, confidence=confianza, rect=fitz.Rect(0, i * 10, 50, i * 10 + 8))
              for i, t in enumerate(textos)]
    return OcrPage(page_index=0, width=100, height=100, tokens=tokens)


# --- decidir "ilegible" ----------------------------------------------------------

def test_palabras_sueltas_con_confianza_alta_son_legibles():
    # Lo que devuelve el OCR de un formulario inclinado: piezas cortas, bien leidas.
    p = _pagina(["NOMBRE", "COMPLETO:", "Alejandro", "Martín", "Gómez", "FECHA", "DE",
                 "NACIMIENTO:", "14/05/1982", "DNI:"], 0.85)
    assert legible(p)


def test_el_ruido_sigue_siendo_ilegible_aunque_traiga_confianza():
    assert not legible(_pagina(["8", "1", "g1", "%", ":", "~", "5", "H", "1", "2", "p", "4"], 0.9))


def test_palabras_cortas_con_confianza_baja_son_ilegibles():
    assert not legible(_pagina(["NOMBRE", "COMPLETO:", "Martín", "Gómez", "FECHA", "DE"], 0.4))


def test_una_pagina_legible_hoy_no_prueba_nada_mas():
    llamadas = []

    def ocr(img):
        llamadas.append(img.size)
        return _pagina(["Resolucion del expediente", "de licencia de obra menor"] * 4, 0.9)

    r = resolve_orientation(Image.new("RGB", (100, 200), "white"), ocr)
    assert len(llamadas) == 1 and r.ajuste is None and not r.poor_quality


def test_si_solo_se_lee_invertida_se_usa_invertida():
    """Texto claro sobre fondo oscuro: el OCR solo lo lee con los colores invertidos."""
    oscura = Image.new("RGB", (300, 200), (10, 10, 12))

    def ocr(img):
        clara = np.asarray(img.convert("L")).mean() > 128
        textos = ["PEÑA EL CARAJILLO", "TEMPORADA 2026/27", "SOCIO Nº 0127",
                  "Ramón Escrivá Peris", "Valencia"] if clara else ["~", "8", "l", ":", "1", "g", "%", "4", "H", "2", "p", "5"]
        return _pagina(textos, 0.8 if clara else 0.3)

    r = resolve_orientation(oscura, ocr)
    assert not r.poor_quality
    assert r.ajuste is not None and r.ajuste.invertir


# --- de principio a fin, con el OCR de verdad -----------------------------------

@pytest.fixture(scope="module")
def pipeline():
    from app.services.pdf.pipeline import PdfAnonymizationPipeline

    return PdfAnonymizationPipeline()


def _procesar(doc, foto: Foto, tmp_path: Path, pipeline, nombre: str):
    from app.services.image.imagen import anonimizar_imagen

    pagina = doc.paginas[0]
    imagen, _, matriz = fotografiar(pagina, foto, semilla=7)
    entrada = tmp_path / f"{nombre}.png"
    Image.fromarray(imagen).save(entrada)
    salida = tmp_path / f"{nombre}_salida.png"
    informe = anonimizar_imagen(entrada, salida, "png", lambda e, s: pipeline.process(
        e, s, "t", settings.default_redaction_mode, False, frozenset()))
    geo = {"ancho": pagina.shape[1], "alto": pagina.shape[0], "matriz": matriz.tolist()}
    pin = en_plano(np.asarray(Image.open(entrada).convert("RGB")), geo)
    pout = en_plano(np.asarray(Image.open(salida).convert("RGB")), geo)
    return informe, pin, pout


def test_un_formulario_inclinado_no_se_tapa_entero_y_se_tapan_sus_datos(tmp_path, pipeline):
    doc = formulario(PERSONAS[0], 1)
    informe, pin, pout = _procesar(doc, VARIANTES["inclinada_10"], tmp_path, pipeline, "inclinado")
    assert not informe.pages_redacted_in_full, "se ha rendido con una foto normal"
    for d in doc.datos:
        assert cobertura(pin, pout, d["cajas"]) >= 0.97, f"asoma {d['type']}"
    visibles = sum(cobertura(pin, pout, c) <= 0.30 for c in doc.cajas_rotulos)
    # Antes salia entera en negro (0 de 9).  Donde la posicion exacta no se puede
    # asegurar, la barra se come la etiqueta y no el dato: se exige mas de la
    # mitad (alguna etiqueta queda en el limite y la GPU no da siempre lo mismo).
    assert visibles > len(doc.cajas_rotulos) // 2, "las etiquetas tienen que quedar a la vista"


def test_un_formulario_recto_conserva_las_etiquetas(tmp_path, pipeline):
    doc = formulario(PERSONAS[1], 2)
    informe, pin, pout = _procesar(doc, VARIANTES["recta"], tmp_path, pipeline, "recto")
    for d in doc.datos:
        assert cobertura(pin, pout, d["cajas"]) >= 0.97, f"asoma {d['type']}"
    tapadas = [r for r, c in zip(doc.rotulos, doc.cajas_rotulos) if cobertura(pin, pout, c) > 0.30]
    assert len(tapadas) <= 1, f"etiquetas tapadas: {tapadas}"


def test_un_carnet_oscuro_tapa_el_nombre_y_el_qr_y_deja_el_titulo(tmp_path, pipeline):
    doc = carnet()
    informe, pin, pout = _procesar(doc, Foto(angulo=7, fondo=True, ocupa=0.55), tmp_path, pipeline, "carnet")
    assert not informe.pages_redacted_in_full
    for d in doc.datos:
        # El QR se mide por sus modulos: su margen blanco no lleva datos.
        assert cobertura(pin, pout, d["cajas"], solo_oscuro=d["type"] == "QR_CODE") >= 0.97, \
            f"asoma {d['type']}"
    assert cobertura(pin, pout, doc.cajas_rotulos[0]) <= 0.30, "el titulo tiene que verse"


def test_una_copia_de_dni_se_sigue_tapando_entera(tmp_path, pipeline):
    informe, _, _ = _procesar(dni_ejemplo(), Foto(angulo=8, fondo=True, ocupa=0.5), tmp_path, pipeline, "dni")
    assert [p.reason for p in informe.pages_redacted_by_content] == ["dni_scan"]


# --- QR ---------------------------------------------------------------------------

def test_un_qr_descodificado_grande_de_una_tarjeta_se_tapa(monkeypatch):
    import app.services.vision.code_detector as cd

    img = Image.new("RGB", (945, 598), "white")
    # 8 % de la imagen, como el QR de un carnet: por encima del 5 % de un A4.
    monkeypatch.setattr(cd, "detect_and_decode_visual_codes",
                        lambda image, padding=8: [(fitz.Rect(671, 296, 883, 508), "SOCIO 0127")])
    assert len(cd.detect_redactable_codes(img)) == 1


def test_un_codigo_sin_descodificar_sigue_con_el_tope_de_siempre(monkeypatch):
    import app.services.vision.code_detector as cd

    assert cd._QR_MAX_AREA_FRAC == 0.05


def test_la_imagen_ajustada_no_pasa_del_tope_de_megapixeles():
    """Enderezar amplia el lienzo: sin tope, una foto grande agotaba la GPU."""
    from app.services.ocr import ajustes

    m, tamaño = ajustes._con_tope(*ajustes._rotacion(4000, 3000, 9.0))
    assert tamaño[0] * tamaño[1] <= settings.ocr_max_megapixels * 1e6 + 1
