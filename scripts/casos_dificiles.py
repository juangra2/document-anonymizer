"""Casos dificiles 11-14 con datos inventados, en version digital y escaneada.

Imitan documentos que llegan de verdad (membrete, tablas, redaccion de un
ayuntamiento o un banco), con datos ficticios:

  11. certificado bancario: tabla (titular, IBAN, importes) y un parrafo que
      repite el IBAN y el titular;
  12. solicitud bilingue valenciano | castellano a dos columnas, con los
      valores repetidos en las dos;
  13. anexo en A3 apaisado (pagina girada) dentro de un documento vertical, con
      datos junto a los bordes superior e inferior;
  14. bloque de firmas con varios nombres seguidos en una fila.

Cada caso deja <caso>_digital.pdf, <caso>_escaneado.pdf y <caso>.json con lo que
debe quedar tachado ("tachar") y lo que no ("conservar").  Lo usa
tests/test_casos_dificiles.py.

    python scripts/casos_dificiles.py [destino]      # por defecto data/samples/casos_dificiles
"""
from __future__ import annotations

import io
import json
import random
import sys
from pathlib import Path

import fitz
from PIL import Image, ImageFilter

_OUT = Path("data/samples/casos_dificiles")


def escanear(origen: Path, destino: Path) -> None:
    esc = fitz.open()
    for pag in fitz.open(origen):
        img = Image.open(io.BytesIO(pag.get_pixmap(dpi=200).tobytes("png"))).convert("L")
        img = img.rotate(random.uniform(-0.7, 0.7), fillcolor=255)
        img = img.filter(ImageFilter.GaussianBlur(0.45))
        q = esc.new_page(width=pag.rect.width, height=pag.rect.height)
        b = io.BytesIO()
        img.save(b, format="JPEG", quality=78)
        q.insert_image(q.rect, stream=b.getvalue())
    esc.save(destino)


def guardar(nombre, doc, tachar, conservar):
    d = _OUT / f"{nombre}_digital.pdf"
    doc.save(d)
    escanear(d, _OUT / f"{nombre}_escaneado.pdf")
    (_OUT / f"{nombre}.json").write_text(json.dumps(
        {"tachar": tachar, "conservar": conservar}, ensure_ascii=False, indent=1), encoding="utf-8")


def linea(p, a, b, w=0.5):
    p.draw_line(a, b, color=(0.35, 0.35, 0.35), width=w)


def generar(destino: Path) -> list[str]:
    """Genera los casos en *destino* y devuelve sus nombres."""
    global _OUT
    _OUT = Path(destino)
    _OUT.mkdir(parents=True, exist_ok=True)
    random.seed(11)
    # --- 11. Banco: tabla (titular, IBAN, importe) + parrafo que los repite ------------
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.draw_rect(fitz.Rect(0, 0, 595, 70), color=None, fill=(0.0, 0.27, 0.45))
    p.insert_text((50, 42), "Caixa Rural del Túria", fontsize=17, fontname="hebo", color=(1, 1, 1))
    p.insert_text((50, 58), "Oficina 0412 · Av. del Cid, 46 · 46018 València", fontsize=8, color=(1, 1, 1))
    p.insert_text((50, 110), "CERTIFICADO DE TITULARIDAD Y MOVIMIENTOS", fontsize=12, fontname="hebo")
    p.insert_text((50, 126), "Ref. certificado: CRT-2026-0091847   Fecha de emisión: 03/09/2026", fontsize=8)
    filas = [("Titular", "Rosa Benlloch Soriano"), ("NIF", "48297531W"),
             ("IBAN", "ES79 3159 0012 4825 9930 1176"), ("Saldo a fecha", "12.483,57 €"),
             ("Último abono (nómina)", "2.146,90 €")]
    y = 150
    for et, v in filas:
        p.draw_rect(fitz.Rect(50, y, 545, y + 22), color=(0.75, 0.75, 0.75), width=0.5)
        linea(p, (210, y), (210, y + 22))
        p.insert_text((56, y + 15), et, fontsize=9, fontname="hebo")
        p.insert_text((216, y + 15), v, fontsize=9)
        y += 22
    p.insert_textbox(fitz.Rect(50, y + 25, 545, y + 160),
                     "Caixa Rural del Túria certifica que Rosa Benlloch Soriano figura como titular única "
                     "de la cuenta ES79 3159 0012 4825 9930 1176, abierta en esta oficina el 14 de marzo de "
                     "2011, y que en la misma se viene abonando con carácter mensual la nómina de la "
                     "titular. Y para que conste a petición de la interesada, a efectos de su presentación "
                     "ante el Ajuntament de València en el procedimiento de ayudas al alquiler, se expide el "
                     "presente certificado, que no supone compromiso alguno por parte de la entidad.",
                     fontsize=9.5, align=3)
    p.insert_text((50, 780), "Caixa Rural del Túria, S.C.C. · CIF F46012345 · Inscrita en el Registro "
                  "Mercantil de Valencia, Tomo 3412, Folio 88", fontsize=6.5, color=(0.4, 0.4, 0.4))
    guardar("c11_banco", doc,
            tachar=["Rosa Benlloch Soriano", "48297531W", "ES79 3159 0012 4825 9930 1176",
                    "12.483,57", "2.146,90"],
            conservar=["CERTIFICADO DE TITULARIDAD", "Titular", "IBAN", "Saldo a fecha",
                       "certifica que", "figura como titular única", "procedimiento de ayudas al alquiler"])

    # --- 12. Bilingue a dos columnas, valores repetidos ---------------------------------
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((50, 52), "AJUNTAMENT DE VALÈNCIA", fontsize=13, fontname="hebo")
    p.insert_text((50, 66), "Regidoria de Benestar Social · Servei d'Atenció Social", fontsize=8)
    linea(p, (50, 76), (545, 76), 1)
    linea(p, (297, 90), (297, 560))
    col = [
        ("SOL·LICITUD D'AJUDA D'EMERGÈNCIA SOCIAL", [
            ("Nom i cognoms:", "Jordi Alapont Ribes"), ("DNI/NIE:", "29184735K"),
            ("Domicili:", "C/ de la Reina, 87, 3r, 6a"), ("Codi postal:", "46011"),
            ("Telèfon:", "655 412 908"), ("Data de naixement:", "07/11/1984")],
         "La persona sol·licitant declara que les dades consignades són certes i autoritza "
         "l'Ajuntament a consultar-les en altres administracions."),
        ("SOLICITUD DE AYUDA DE EMERGENCIA SOCIAL", [
            ("Nombre y apellidos:", "Jordi Alapont Ribes"), ("DNI/NIE:", "29184735K"),
            ("Domicilio:", "C/ de la Reina, 87, 3º, 6ª"), ("Código postal:", "46011"),
            ("Teléfono:", "655 412 908"), ("Fecha de nacimiento:", "07/11/1984")],
         "La persona solicitante declara que los datos consignados son ciertos y autoriza "
         "al Ayuntamiento a consultarlos en otras administraciones."),
    ]
    for k, (titulo, campos, decl) in enumerate(col):
        x = 50 + k * 257
        p.insert_text((x, 100), titulo, fontsize=8.5, fontname="hebo")
        yy = 125
        for et, v in campos:
            p.insert_text((x, yy), et, fontsize=8, fontname="hebo")
            p.insert_text((x + 95, yy), v, fontsize=8)
            yy += 20
        p.insert_textbox(fitz.Rect(x, yy + 15, x + 238, yy + 90), decl, fontsize=8, align=3)
    p.insert_text((50, 600), "València, 2 de setembre de 2026 / Valencia, 2 de septiembre de 2026", fontsize=8.5)
    p.insert_text((50, 640), "Signatura / Firma:", fontsize=8.5)
    guardar("c12_bilingue", doc,
            tachar=["Jordi Alapont Ribes", "29184735K", "C/ de la Reina, 87", "46011", "655 412 908",
                    "07/11/1984"],
            conservar=["Nom i cognoms", "Nombre y apellidos", "Domicili", "Domicilio",
                       "AJUDA D'EMERGÈNCIA SOCIAL", "declara que los datos consignados"])

    # --- 13. A3 apaisada (tabla de mediciones) en un documento vertical ------------------
    doc = fitz.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((50, 60), "PROYECTO DE REFORMA DE LOCAL COMERCIAL", fontsize=12, fontname="hebo")
    p1.insert_textbox(fitz.Rect(50, 80, 545, 200),
                      "Promotor: Vicent Garcia Llorens, con NIF 73015628G. Técnico redactor: Laura "
                      "Pastor Gil, arquitecta. Se adjunta en el anexo la tabla de mediciones de la obra.",
                      fontsize=10)
    p2 = doc.new_page(width=842, height=1191)       # A3 vertical...
    p2.set_rotation(90)                              # ...que se ve apaisada
    # Se dibuja en el sistema sin girar para que se LEA apaisada: transformacion.
    m = p2.derotation_matrix


    def t(pt):
        return fitz.Point(pt) * m


    def texto_ap(x, y, s, fs=8, bold=False):
        p2.insert_text(t((x, y)), s, fontsize=fs, rotate=90, fontname="hebo" if bold else "helv")


    texto_ap(40, 30, "Promotor: Vicent Garcia Llorens · NIF 73015628G · Tel. 678 214 093", 8)
    texto_ap(40, 60, "ANEXO II · MEDICIONES Y PRESUPUESTO", 12, True)
    cab = ["Código", "Descripción de la partida", "Ud", "Medición", "Precio", "Importe"]
    xs = [40, 110, 520, 570, 650, 740]
    yy = 90
    for x, c in zip(xs, cab):
        texto_ap(x, yy, c, 8, True)
    partidas = [("01.01", "Demolición de tabiquería de ladrillo hueco", "m2", "48,20", "12,40", "597,68"),
                ("01.02", "Levantado de solado cerámico", "m2", "96,50", "8,15", "786,48"),
                ("02.01", "Tabique de placa de yeso laminado 15+48+15", "m2", "61,30", "31,90", "1.955,47"),
                ("03.01", "Instalación eléctrica completa del local", "ud", "1,00", "4.820,00", "4.820,00")]
    for fila in partidas:
        yy += 22
        for x, c in zip(xs, fila):
            texto_ap(x, yy, c, 8)
    texto_ap(40, 817, "Firmado: Laura Pastor Gil, arquitecta colegiada 14.226 · laura.pastor@arquitectes.example", 8)
    p3 = doc.new_page(width=595, height=842)
    p3.insert_text((50, 60), "Documento firmado electrónicamente.", fontsize=9)
    guardar("c13_apaisada", doc,
            tachar=["Vicent Garcia Llorens", "73015628G", "678 214 093", "Laura Pastor Gil",
                    "laura.pastor@arquitectes.example"],
            conservar=["MEDICIONES Y PRESUPUESTO", "Demolición de tabiquería", "Descripción de la partida",
                       "14.226"])

    # --- 14. Bloque de firmas: varios nombres en una fila ------------------------------
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((50, 60), "ACTA DE LA COMISIÓN DE VALORACIÓN", fontsize=12, fontname="hebo")
    p.insert_textbox(fitz.Rect(50, 80, 545, 200),
                     "Reunida la comisión en la fecha indicada, se valoran las solicitudes presentadas "
                     "y se acuerda elevar la propuesta de resolución al órgano competente.", fontsize=10)
    p.insert_text((50, 600), "Fdo.: Ana López Ferrer   Pedro Gil Marco   Carmen Soler Pons", fontsize=9)
    p.insert_text((50, 640), "La presidenta          El vocal          La secretaria", fontsize=8)
    guardar("c14_firmas", doc,
            tachar=["Ana López Ferrer", "Pedro Gil Marco", "Carmen Soler Pons"],
            conservar=["ACTA DE LA COMISIÓN", "La presidenta", "La secretaria"])
    return ["c11_banco", "c12_bilingue", "c13_apaisada", "c14_firmas"]


if __name__ == "__main__":
    print(generar(Path(sys.argv[1]) if len(sys.argv) > 1 else _OUT))
