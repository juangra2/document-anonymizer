"""Corpus de casos dificiles de imagenes (JPEG, PNG, TIFF), con datos inventados.

Dos familias:
  - Lo que una imagen guarda ademas de la foto: la miniatura del EXIF (una copia
    pequeña de la foto sin tachar), la de Photoshop y la de XMP, las imagenes
    extra de un JPEG de movil (MPF), los textos de un PNG, las etiquetas y
    paginas de miniatura de un TIFF.  Cada uno se prueba al anonimizar y al
    solo quitar metadatos.
  - Fotos dificiles: giradas 180 y 45 grados, perspectiva fuerte, dos
    documentos en la misma foto, parte tapada, reflejo, desenfoque, poca luz,
    marca de agua, letra manuscrita, captura de un chat, foto de una pantalla,
    JPEG en CMYK y progresivo, enorme y diminuta.
  Y ficheros dañados normales: cortado, vacio, extension que no corresponde.

Los documentos y la "camara" son los de corpus_imagenes.py; la etiqueta de cada
foto es la misma (paginas con su matriz, cajas de datos y rotulos), y la lectura
de barras (lectura_imagenes.medir_imagen) sirve tal cual.  Etiqueta de cada caso
(casos.json): id, fichero, nombre_subida, tratamiento, que_prueba, esperado,
rechazo, imagen (etiqueta de barras o None), metadatos (valores escondidos que no
pueden quedar) y paginas_esperadas.

Uso: python -m evaluation.corpus_imagenes_dificil [DESTINO]
"""
from __future__ import annotations

import base64
import io
import json
import struct
import subprocess
import sys
from pathlib import Path

import cv2
import fitz
import numpy as np
from PIL import Image, PngImagePlugin

from evaluation.corpus_imagenes import (DPI_DOC, PERSONAS, Documento, Foto, _con_cajas, _render, captura,
                                        carta, fotografiar, formulario)

DESTINO = Path("evaluation/imagenes_dificil/corpus")
AUTORA = "Vicenta Ribes Mollà"
AUTOR = "Ferran Gil Aparici"
# Lo que se busca de la autora: la "à" no siempre se guarda bien en EXIF e IPTC.
AUTORA_BUSCA = "Vicenta Ribes"


# --- etiquetas -----------------------------------------------------------------

def _etiqueta(docs: list[Documento], fotos: list[tuple[np.ndarray, np.ndarray]]) -> dict:
    paginas, datos, rotulos, cajas_rotulos = [], [], [], []
    for doc, (_, m) in zip(docs, fotos):
        indice = len(paginas)
        pagina = doc.paginas[0]
        paginas.append({"ancho": pagina.shape[1], "alto": pagina.shape[0], "matriz": m.tolist()})
        datos += [{**d, "pagina": indice} for d in doc.datos]
        rotulos += doc.rotulos
        cajas_rotulos += [{"pagina": indice, "cajas": c} for c in doc.cajas_rotulos]
    return {"datos": datos, "rotulos": rotulos, "cajas_rotulos": cajas_rotulos, "paginas": paginas,
            "entera": any(d.entera for d in docs), "oscuro": any(d.oscuro for d in docs)}


def _foto(doc: Documento, f: Foto, semilla: int) -> tuple[np.ndarray, np.ndarray]:
    foto, _, m = fotografiar(doc.paginas[0], f, semilla)
    return foto, m


def _jpeg(img: np.ndarray | Image.Image, **opciones) -> bytes:
    buf = io.BytesIO()
    (img if isinstance(img, Image.Image) else Image.fromarray(img)).save(buf, format="JPEG",
                                                                         **{"quality": 90, **opciones})
    return buf.getvalue()


def _exiftool(ruta: Path, *argumentos: str) -> None:
    subprocess.run(["exiftool", "-q", "-q", "-overwrite_original", *argumentos, str(ruta)], check=True)


def _mpf(primaria: bytes, secundaria: bytes) -> bytes:
    """JPEG con una segunda imagen al final, indexada en un segmento MPF (APP2),
    como guardan algunos moviles."""
    entradas_off = 50
    cabecera_tiff = b"II*\x00" + struct.pack("<I", 8)
    ifd = struct.pack("<H", 3)
    ifd += struct.pack("<HHI4s", 0xB000, 7, 4, b"0100")
    ifd += struct.pack("<HHII", 0xB001, 4, 1, 2)
    ifd += struct.pack("<HHII", 0xB002, 7, 32, entradas_off)
    ifd += struct.pack("<I", 0)
    largo_app2 = 2 + 4 + len(cabecera_tiff) + len(ifd) + 32
    inicio_mp = 2 + 4 + 4                       # SOI + marca y largo del APP2 + "MPF\0"
    total_primaria = len(primaria) + 2 + largo_app2
    entradas = struct.pack("<IIIHH", 0x20030000, total_primaria, 0, 0, 0)
    entradas += struct.pack("<IIIHH", 0x00010001, len(secundaria), total_primaria - inicio_mp, 0, 0)
    app2 = b"\xff\xe2" + struct.pack(">H", largo_app2) + b"MPF\x00" + cabecera_tiff + ifd + entradas
    return primaria[:2] + app2 + primaria[2:] + secundaria


def _xmp_con_miniatura(miniatura: bytes) -> str:
    return ('<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description rdf:about="" '
            'xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:xmpGImg="http://ns.adobe.com/xap/1.0/g/img/" '
            f'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator><rdf:Seq><rdf:li>{AUTORA}</rdf:li>'
            '</rdf:Seq></dc:creator><xmp:Thumbnails><rdf:Alt><rdf:li rdf:parseType="Resource">'
            '<xmpGImg:format>JPEG</xmpGImg:format><xmpGImg:width>256</xmpGImg:width>'
            f'<xmpGImg:image>{base64.b64encode(miniatura).decode()}</xmpGImg:image></rdf:li></rdf:Alt>'
            '</xmp:Thumbnails></rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>')


# --- fotos dificiles -------------------------------------------------------------

def _dos_documentos() -> Documento:
    a, b = formulario(PERSONAS[0], 1), carta(PERSONAS[1])
    pa, pb = a.paginas[0], b.paginas[0]
    hueco = 60
    pagina = np.full((max(pa.shape[0], pb.shape[0]), pa.shape[1] + hueco + pb.shape[1], 3), 255, np.uint8)
    pagina[:pa.shape[0], :pa.shape[1]] = pa
    pagina[:pb.shape[0], pa.shape[1] + hueco:] = pb
    dx = pa.shape[1] + hueco
    datos = a.datos + [{**d, "cajas": [[x0 + dx, y0, x1 + dx, y1] for x0, y0, x1, y1 in d["cajas"]]} for d in b.datos]
    cajas = a.cajas_rotulos + [[[x0 + dx, y0, x1 + dx, y1] for x0, y0, x1, y1 in c] for c in b.cajas_rotulos]
    doc = Documento("dos", "dos_documentos", [pagina], datos=datos, rotulos=a.rotulos + b.rotulos)
    doc.cajas_rotulos = cajas
    return doc


def _con_marca_de_agua(doc: Documento) -> Documento:
    pagina = doc.paginas[0].copy()
    capa = np.zeros_like(pagina)
    alto, ancho = pagina.shape[:2]
    cv2.putText(capa, "COPIA NO VALIDA", (int(ancho * 0.05), int(alto * 0.62)), cv2.FONT_HERSHEY_DUPLEX,
                ancho / 380, (255, 255, 255), max(3, ancho // 120), cv2.LINE_AA)
    giro = cv2.getRotationMatrix2D((ancho / 2, alto / 2), 32, 1.0)
    capa = cv2.warpAffine(capa, giro, (ancho, alto))
    mascara = capa[..., 0] > 0
    pagina[mascara] = (pagina[mascara] * 0.55 + np.array([150, 150, 150]) * 0.45).astype(np.uint8)
    return Documento(doc.nombre + "_marca", doc.tipo, [pagina], datos=doc.datos, rotulos=doc.rotulos,
                     cajas_rotulos=doc.cajas_rotulos)


def _manuscrito(p: dict) -> Documento:
    """Formulario impreso rellenado a mano: los rotulos en letra de imprenta, los
    valores con trazo de escritura."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((150, 90), "SOLICITUD DE CITA PREVIA", fontsize=13, fontname="hebo")
    filas = [("Nombre y apellidos:", p["nombre"], "PERSON"), ("DNI:", p["dni"], "ES_DNI"),
             ("Teléfono:", p["telefono"], "PHONE_NUMBER")]
    y = 170
    for rotulo, _, _ in filas:
        page.insert_text((70, y), rotulo, fontsize=11, fontname="hebo")
        y += 60
    pagina = _render(page)
    k = DPI_DOC / 72
    datos, y = [], 170
    for rotulo, valor, tipo in filas:
        x = int((70 + fitz.get_text_length(rotulo, "hebo", 11) + 14) * k)
        escala = 1.25
        (w, h), base = cv2.getTextSize(valor, cv2.FONT_HERSHEY_SCRIPT_SIMPLEX, escala, 2)
        cv2.putText(pagina, valor, (x, int(y * k)), cv2.FONT_HERSHEY_SCRIPT_SIMPLEX, escala, (25, 35, 120), 2,
                    cv2.LINE_AA)
        datos.append({"text": valor, "type": tipo, "cajas": [[x, int(y * k) - h - 4, x + w, int(y * k) + base]]})
        y += 60
    d = Documento("manuscrito", "manuscrito", [pagina], datos=datos,
                  rotulos=[r for r, _, _ in filas] + ["SOLICITUD DE CITA PREVIA"])
    return _con_cajas(d, page)


def _chat() -> Documento:
    doc = fitz.open()
    page = doc.new_page(width=300, height=560)
    page.draw_rect(page.rect, color=None, fill=(0.93, 0.9, 0.86))
    page.draw_rect(fitz.Rect(0, 0, 300, 44), color=None, fill=(0.03, 0.37, 0.33))
    page.insert_text((14, 28), "Vecinos Russafa (grupo)", fontsize=12, fontname="hebo", color=(1, 1, 1))
    mensajes = [("izq", "Lucía Ferrando", "Mi móvil es 654 218 937, llamadme"),
                ("der", "", "Apuntado. El de Andrés es +34 622 487 105"),
                ("izq", "Andrés Peiró", "Correo: andres.peiro@correo.es"),
                ("der", "", "Reunión el jueves a las 19:00")]
    y = 70
    for lado, quien, texto in mensajes:
        ancho = fitz.get_text_length(texto, "helv", 10) + 20
        x0 = 12 if lado == "izq" else 300 - 12 - ancho
        alto = 46 if quien else 30
        page.draw_rect(fitz.Rect(x0, y, x0 + ancho, y + alto), color=None,
                       fill=(1, 1, 1) if lado == "izq" else (0.86, 0.97, 0.78), radius=0.2)
        if quien:
            page.insert_text((x0 + 10, y + 15), quien, fontsize=9, fontname="hebo", color=(0.1, 0.45, 0.6))
        page.insert_text((x0 + 10, y + alto - 11), texto, fontsize=10)
        y += alto + 16
    d = Documento("chat", "captura_chat", [_render(page)],
                  datos=[{"text": "654 218 937", "type": "PHONE_NUMBER"}, {"text": "+34 622 487 105", "type": "PHONE_NUMBER"},
                         {"text": "andres.peiro@correo.es", "type": "EMAIL_ADDRESS"},
                         {"text": "Lucía Ferrando", "type": "PERSON"}, {"text": "Andrés Peiró", "type": "PERSON"}],
                  rotulos=["Vecinos Russafa", "Reunión el jueves"])
    return _con_cajas(d, page)


def _efecto(foto: np.ndarray, nombre: str, semilla: int) -> np.ndarray:
    rng = np.random.default_rng(semilla)
    alto, ancho = foto.shape[:2]
    y, x = np.mgrid[0:alto, 0:ancho]
    if nombre == "reflejo":
        brillo = np.exp(-(((x - ancho * 0.55) / (ancho * 0.16)) ** 2 + ((y - alto * 0.33) / (alto * 0.07)) ** 2))
        return np.clip(foto + 230 * brillo[..., None], 0, 255).astype(np.uint8)
    if nombre == "desenfoque":
        return cv2.GaussianBlur(foto, (0, 0), 2.2)
    if nombre == "poca_luz":
        return np.clip(foto * 0.3 + rng.normal(0, 6, foto.shape), 0, 255).astype(np.uint8)
    if nombre == "pantalla":
        moire = 1 + 0.12 * np.sin(x * 0.9) * np.sin(y * 0.85)
        tinte = np.array([0.88, 0.95, 1.08])
        return np.clip(foto * moire[..., None] * tinte, 0, 255).astype(np.uint8)
    if nombre == "tapada":
        salida = foto.copy()
        cv2.rectangle(salida, (int(ancho * 0.05), int(alto * 0.44)), (int(ancho * 0.42), int(alto * 0.95)),
                      (28, 28, 32), -1)
        return salida
    raise ValueError(nombre)


# --- los casos -------------------------------------------------------------------

def casos(destino: Path) -> list[dict]:
    salida = []
    persona = PERSONAS[0]

    def nuevo(id_, fichero, que, esperado, tratamiento="anonymize", imagen=None, metadatos=(), rechazo=None,
              subida=None, paginas=None):
        salida.append({"id": id_, "fichero": fichero, "nombre_subida": subida or fichero, "tratamiento": tratamiento,
                       "que_prueba": que, "esperado": esperado, "rechazo": rechazo, "imagen": imagen,
                       "metadatos": list(metadatos), "paginas_esperadas": paginas})

    form = formulario(persona, 1)
    plana, m_plana = _foto(form, Foto(), 0)
    etiqueta_plana = _etiqueta([form], [(plana, m_plana)])
    principal = _jpeg(plana)
    pequena = Image.fromarray(plana).resize((320, round(plana.shape[0] * 320 / plana.shape[1])))
    miniatura = _jpeg(pequena, quality=85)
    (destino / "_mini.jpg").write_bytes(miniatura)

    def dos_tratamientos(id_, fichero, datos, que, esperado, metadatos, paginas=1):
        (destino / fichero).write_bytes(datos)
        nuevo(f"{id_}a", fichero, que + " (anonimizar)", esperado + " La foto se tacha.", "anonymize",
              etiqueta_plana if paginas == 1 else None, metadatos, paginas=paginas)
        nuevo(f"{id_}m", fichero, que + " (solo metadatos)", esperado + " La foto se queda.", "metadata_only",
              None, metadatos, paginas=paginas)

    ruta = destino / "i01_exif_con_miniatura.jpg"
    ruta.write_bytes(principal)
    _exiftool(ruta, f"-ThumbnailImage<={destino / '_mini.jpg'}", f"-Artist={AUTORA}", f"-XPAuthor={AUTORA}",
              "-GPSLatitude=39.4699", "-GPSLatitudeRef=N", "-GPSLongitude=0.3763", "-GPSLongitudeRef=W",
              f"-Comment=Foto de {AUTOR}", f"-IPTC:By-line={AUTOR}")
    dos_tratamientos("I01", ruta.name, ruta.read_bytes(), "JPEG con miniatura EXIF (la foto sin tachar en "
                     "pequeño), autora, GPS, comentario e IPTC", "Sin miniatura ni metadatos.", [AUTORA_BUSCA, AUTOR])

    ruta = destino / "i02_photoshop_miniatura.jpg"
    ruta.write_bytes(principal)
    _exiftool(ruta, f"-Photoshop:PhotoshopThumbnail<={destino / '_mini.jpg'}", f"-IPTC:Writer-Editor={AUTORA}")
    dos_tratamientos("I02", ruta.name, ruta.read_bytes(), "JPEG con miniatura de Photoshop (APP13) y "
                     "redactora en IPTC", "Sin miniatura ni metadatos.", [AUTORA_BUSCA])

    ruta = destino / "i03_xmp_miniatura.jpg"
    ruta.write_bytes(principal)
    xmp = destino / "_x.xmp"
    xmp.write_text(_xmp_con_miniatura(miniatura), encoding="utf-8")
    _exiftool(ruta, f"-xmp<={xmp}")
    xmp.unlink()
    dos_tratamientos("I03", ruta.name, ruta.read_bytes(), "JPEG con miniatura dentro del XMP (en base64) y "
                     "autora", "Sin miniatura ni metadatos.", [AUTORA_BUSCA])

    dos_tratamientos("I04", "i04_mpf_imagen_extra.jpg", _mpf(principal, _jpeg(plana, quality=70)),
                     "JPEG de movil con una segunda imagen dentro (MPF): la misma foto, sin tachar",
                     "Sin la segunda imagen.", [])

    form2 = carta(PERSONAS[1])
    plana2, m2 = _foto(form2, Foto(), 1)
    info = PngImagePlugin.PngInfo()
    info.add_text("Author", AUTORA)
    info.add_itxt("Comment", f"Escaneado por {AUTOR}")
    info.add_text("Description", f"DNI de {AUTOR}", zip=True)
    buf = io.BytesIO()
    Image.fromarray(plana).save(buf, format="PNG", pnginfo=info)
    dos_tratamientos("I05", "i05_png_con_textos.png", buf.getvalue(), "PNG con textos (tEXt, iTXt, zTXt) con "
                     "autora y autor", "Sin los textos.", [AUTORA_BUSCA, AUTOR])

    # copy(): la miniatura ya se guardo como JPEG y Pillow arrastraria sus opciones al TIFF.
    marcos = [Image.fromarray(plana), Image.fromarray(plana2), pequena.copy()]
    ruta = destino / "i06_tiff_etiquetas_y_miniatura.tif"
    marcos[0].save(ruta, format="TIFF", save_all=True, append_images=marcos[1:])
    _exiftool(ruta, f"-Artist={AUTORA}", f"-ImageDescription=Expediente de {AUTOR}")
    pequena_m = np.diag([320 / plana.shape[1]] * 2 + [1.0]) @ m_plana
    etiqueta_tiff = _etiqueta([form, form2, form], [(plana, m_plana), (plana2, m2), (None, pequena_m)])
    nuevo("I06a", ruta.name, "TIFF de tres paginas (la tercera, una miniatura de la primera) con autora y "
          "descripcion (anonimizar)", "Las tres paginas tachadas, sin etiquetas.", "anonymize", etiqueta_tiff,
          [AUTORA_BUSCA, AUTOR], paginas=3)
    nuevo("I06m", ruta.name, "TIFF de tres paginas con autora y descripcion (solo metadatos)",
          "Sin etiquetas; las paginas se quedan.", "metadata_only", None, [AUTORA_BUSCA, AUTOR], paginas=3)
    (destino / "_mini.jpg").unlink()

    fotos = [
        ("I07", "rotada_180", "Foto girada 180 grados", form, Foto(angulo=180, fondo=True), None),
        ("I08", "rotada_45", "Foto girada 45 grados", form, Foto(angulo=45, fondo=True, ocupa=0.55), None),
        ("I09", "perspectiva_fuerte", "Perspectiva muy fuerte", form, Foto(angulo=2, perspectiva=0.35, fondo=True), None),
        ("I10", "dos_documentos", "Dos documentos en la misma foto", _dos_documentos(), Foto(angulo=3, fondo=True, ocupa=0.6), None),
        ("I11", "parte_tapada", "Parte del documento tapada por un objeto", form, Foto(angulo=2, fondo=True), "tapada"),
        ("I12", "reflejo", "Reflejo de luz sobre el texto", form, Foto(angulo=-3, fondo=True), "reflejo"),
        ("I13", "desenfoque", "Foto desenfocada", form, Foto(angulo=2, fondo=True), "desenfoque"),
        ("I14", "poca_luz", "Foto con muy poca luz", form, Foto(angulo=-2, fondo=True), "poca_luz"),
        ("I15", "marca_de_agua", "Marca de agua encima del texto", _con_marca_de_agua(form), Foto(angulo=2, fondo=True), None),
        ("I16", "manuscrita", "Formulario rellenado a mano", _manuscrito(persona), Foto(angulo=2, fondo=True), None),
        ("I17", "captura_chat", "Captura de pantalla de un chat con telefonos", _chat(), Foto(formato="png"), None),
        ("I18", "foto_de_pantalla", "Foto de una pantalla (muaré, tono azulado, perspectiva)", form,
         Foto(angulo=4, perspectiva=0.1, fondo=True), "pantalla"),
        ("I19", "jpeg_cmyk", "JPEG en CMYK", form, Foto(angulo=2, fondo=True), None),
        ("I20", "jpeg_progresivo", "JPEG progresivo", form, Foto(angulo=2, fondo=True), None),
        ("I21", "enorme_60mp", "Foto enorme (unos 60 megapixeles)", form, Foto(angulo=2, fondo=True, ancho=9000), None),
        ("I22", "diminuta", "Foto diminuta (160 pixeles de ancho)", form, Foto(ancho=160), None),
    ]
    for n, (id_, nombre, que, doc, f, efecto) in enumerate(fotos, 10):
        foto, alfa, m = fotografiar(doc.paginas[0], f, n)
        if efecto:
            foto = _efecto(foto, efecto, n)
        etiqueta = _etiqueta([doc], [(foto, m)])
        if f.formato == "png":
            fichero = f"{id_.lower()}_{nombre}.png"
            Image.fromarray(foto).save(destino / fichero, format="PNG")
        elif nombre == "jpeg_cmyk":
            fichero = f"{id_.lower()}_{nombre}.jpg"
            Image.fromarray(foto).convert("CMYK").save(destino / fichero, format="JPEG", quality=90)
        else:
            fichero = f"{id_.lower()}_{nombre}.jpg"
            (destino / fichero).write_bytes(_jpeg(foto, progressive=nombre == "jpeg_progresivo"))
        esperado = ("Ilegible a ese tamaño: se tapa entera, como un escaneado que no se puede leer."
                    if nombre == "diminuta" else "Los datos tapados; los rotulos a la vista.")
        if nombre == "diminuta":
            etiqueta["entera"] = True
        nuevo(id_, fichero, que, esperado, imagen=etiqueta, paginas=1)

    grande = Image.new("RGB", (12000, 9000), (250, 250, 248))
    grande.paste(Image.fromarray(plana), (200, 200))
    grande.save(destino / "i23_fuera_de_limite.jpg", format="JPEG", quality=70)
    nuevo("I23", "i23_fuera_de_limite.jpg", "Foto de 108 megapixeles (por encima del limite de 100)",
          "Se rechaza con un error claro.", rechazo="document_too_large")
    (destino / "i24_cortada.jpg").write_bytes(principal[:len(principal) // 2])
    nuevo("I24", "i24_cortada.jpg", "JPEG cortado a la mitad (descarga interrumpida)",
          "Error claro, sin caerse; o, si se procesa, nada a la vista.", rechazo="invalid_file")
    (destino / "i25_vacia.jpg").write_bytes(b"")
    nuevo("I25", "i25_vacia.jpg", "Fichero vacio con extension .jpg", "Error claro.", rechazo="invalid_file")
    (destino / "i26_texto_como_jpg.jpg").write_text(f"Nombre: {persona['nombre']}\n", encoding="utf-8")
    nuevo("I26", "i26_texto_como_jpg.jpg", "Un fichero de texto con extension .jpg", "Error claro.",
          rechazo="invalid_file")
    buf = io.BytesIO()
    Image.fromarray(plana).save(buf, format="PNG")
    (destino / "i27_png_como_jpg.jpg").write_bytes(buf.getvalue())
    nuevo("I27", "i27_png_como_jpg.jpg", "Un PNG con extension .jpg", "Se reconoce por el contenido: se tacha "
          "y vuelve como PNG.", imagen=etiqueta_plana, paginas=1)
    return salida


def corpus(destino: Path = DESTINO) -> list[dict]:
    destino.mkdir(parents=True, exist_ok=True)
    salida = casos(destino)
    (destino / "casos.json").write_text(json.dumps(salida, ensure_ascii=False, indent=1), encoding="utf-8")
    return salida


if __name__ == "__main__":
    print(f"{len(corpus(Path(sys.argv[1]) if len(sys.argv) > 1 else DESTINO))} casos")
