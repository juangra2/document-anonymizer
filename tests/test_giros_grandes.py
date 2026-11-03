"""Fotos giradas 180 y 45 grados (corpus de casos dificiles de imagenes, I07 e I08).

  - Del reves, EasyOCR saca lineas largas de basura con confianza muy baja: por
    caracteres por pieza parecia texto y no se probaban los giros.  Una lectura
    con confianza media muy baja tambien es ruido.
  - A 45 grados el enderezado no actuaba: solo mide inclinaciones de hasta 20
    grados.  Si no encuentra ninguna, se busca la direccion dominante de las
    lineas a cualquier angulo.  Las inclinaciones pequeñas se miden como antes.
  - Un ajuste se puede componer con un giro de 90, 180 o 270 grados sin perder
    la geometria: lo tapado vuelve a su sitio en la original.
"""
import cv2
import fitz
import numpy as np
from PIL import Image

from app.services.ocr import ajustes
from app.services.ocr.models import OcrPage, OcrToken
from app.services.ocr.orientation import _looks_like_noise


def _pagina(textos, confianza):
    tokens = [OcrToken(text=t, confidence=confianza, rect=fitz.Rect(0, 20 * i, 300, 20 * i + 15))
              for i, t in enumerate(textos)]
    return OcrPage(page_index=0, width=600, height=800, tokens=tokens)


LARGAS = ["'ojuaiweojede ap ejahJe} el #p uoipenouaJ", "ZCCI SOOO ZOSt 8/v0 Oo/2 /6S3",
          "sa'oaJop@opueJa} eIonj:OOINOHLO373", "B_JuMjBA 9009+ '8 ;€ 'Zv eoanS ap",
          ":OLNHIWIOVN 30 VHO3J", "3-Lvz018€S :IN@", "JuawllO opueJay ejpn7", "S7VYHNHD SOIOIAYHS"]


def test_una_lectura_del_reves_es_ruido():
    assert _looks_like_noise(_pagina(LARGAS, 0.17))


def test_una_lectura_buena_no_es_ruido():
    assert not _looks_like_noise(_pagina(["FORMULARIO DE SOLICITUD - SERVICIOS GENERALES"] * 8, 0.87))


def _renglones(grados: float) -> Image.Image:
    """Una hoja con renglones de texto (barras finas) girada *grados*, sobre gris."""
    hoja = np.full((900, 700), 255, np.uint8)
    for y in range(80, 820, 36):
        cv2.rectangle(hoja, (60, y), (640, y + 10), 0, -1)
    lienzo = np.full((1400, 1400), 140, np.uint8)
    lienzo[250:1150, 350:1050] = hoja
    m = cv2.getRotationMatrix2D((700, 700), grados, 1.0)
    return Image.fromarray(cv2.warpAffine(lienzo, m, (1400, 1400), borderValue=140)).convert("RGB")


def test_las_inclinaciones_pequenas_se_miden_como_antes():
    gris = np.asarray(_renglones(6).convert("L"))
    assert abs(abs(ajustes.inclinacion(gris)) - 6) < 1.0


def test_una_hoja_a_45_grados_tiene_candidato_para_enderezarla():
    tipos = [(a.tipo, a.detalle.get("grados")) for a in ajustes.candidatos(_renglones(45))]
    giros = [g for t, g in tipos if t == "inclinacion" and g is not None]
    assert giros and min(abs(abs(g) - 45) for g in giros) < 3, tipos


def test_un_ajuste_con_giro_deshace_lo_tapado_en_su_sitio():
    original = _renglones(0)
    base = ajustes.Ajuste("inclinacion", *ajustes._rotacion(original.width, original.height, 0.0))
    for grados in (90, 180, 270):
        ajuste = ajustes.con_giro(base, grados)
        trabajo = ajustes.aplicar(original, ajuste)
        tapado = trabajo.copy()
        # Tapar un cuadrado en el centro del trabajo: tiene que volver al centro.
        cx, cy = trabajo.width // 2, trabajo.height // 2
        tapado.paste((0, 0, 0), (cx - 20, cy - 20, cx + 20, cy + 20))
        vuelta = np.asarray(ajustes.deshacer(tapado, trabajo, original, ajuste).convert("L"))
        negro = (vuelta == 0) & (np.asarray(original.convert("L")) > 0)
        ys, xs = np.nonzero(negro)
        assert abs(xs.mean() - 700) < 6 and abs(ys.mean() - 700) < 6, grados
