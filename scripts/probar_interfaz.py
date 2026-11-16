"""Prueba la interfaz en un navegador de verdad, contra el servicio real.

No comprueba que los endpoints respondan —de eso ya hay tests—, sino **lo que
se lee en pantalla**: que un documento terminado dice cuántos datos se han
ocultado, que el botón Parar no aparece antes de tener una página hecha, que un
PDF con contraseña explica qué hacer, y que el indicador se pone en rojo cuando
el servicio no está listo.

Uso:
    python scripts/probar_interfaz.py                 # flujos + capturas
    python scripts/probar_interfaz.py --capturas-en carpeta
    python scripts/probar_interfaz.py --ver           # con navegador visible

Necesita el servicio levantado en http://127.0.0.1:8000 (u otra direccion en
la variable PROBAR_BASE) y `playwright`
instalado (`pip install playwright && python -m playwright install chromium`).
Los estados que dependen de la configuración del servidor (cola llena, límites
diminutos, servicio no listo) se prueban arrancando el servicio con esas
variables; este guion los detecta y los recorre si están puestas.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import fitz
import httpx
from playwright.sync_api import expect, sync_playwright

# Para los documentos de prueba del corpus (evaluation/corpus_word.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Otra direccion con la variable PROBAR_BASE (si el 8000 esta ocupado).
BASE = os.environ.get("PROBAR_BASE", "http://127.0.0.1:8000")
NOMBRE = "Marcos Villalba Tena"


# --------------------------------------------------------------- documentos

def pdf_simple(destino: Path, paginas: int = 1) -> Path:
    doc = fitz.open()
    for i in range(paginas):
        page = doc.new_page()
        page.insert_text((72, 100), f"Expediente 2026/{i + 1:03d}", fontsize=12)
        page.insert_text((72, 130), f"Interesado: {NOMBRE}", fontsize=11)
        page.insert_text((72, 150), "DNI: 12345678Z", fontsize=11)
    doc.save(destino)
    doc.close()
    return destino


def pdf_con_contrasena(destino: Path) -> Path:
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "Confidencial", fontsize=12)
    doc.save(destino, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="secreto")
    doc.close()
    return destino


def pdf_plano(destino: Path) -> Path:
    """Plano A1 sintetico con cajetin: el informe lleva el aviso con codigo
    plan_titleblock_only_verified."""
    ancho, alto = 2384, 1684
    doc = fitz.open()
    page = doc.new_page(width=ancho, height=alto)
    for i in range(2500):
        x = 40 + (i * 7) % (ancho - 120)
        y = 40 + (i * 13) % (alto - 120)
        page.draw_line((x, y), (x + 30, y + 5))
    x0, y0 = ancho - 340, alto - 500
    for i in range(7):
        page.draw_line((x0, y0 + 70 * i), (ancho - 20, y0 + 70 * i))
    page.draw_rect(fitz.Rect(x0, y0, ancho - 20, alto - 20))
    for k, lab in enumerate([f"PROMOTOR: {NOMBRE.upper()}", "ESCALA: 1/500",
                             "FECHA: 01/2026", "PLANO: 03"]):
        page.insert_text((x0 + 10, y0 + 30 + 55 * k), lab, fontsize=13)
    doc.save(destino)
    return destino


def no_es_pdf(destino: Path) -> Path:
    destino.write_bytes(b"GIF89a")      # un tipo que no se admite (el .txt si)
    return destino


def pdf_escaneado(destino: Path, paginas: int = 4) -> Path:
    """Sin capa de texto: obliga al camino de OCR, que es el lento."""
    plantilla = fitz.open()
    p = plantilla.new_page()
    p.insert_text((72, 90), "AJUNTAMENT DE VALENCIA", fontsize=14)
    p.insert_text((72, 130), f"Interesado: {NOMBRE}", fontsize=12)
    p.insert_text((72, 160), "DNI: 12345678Z", fontsize=12)
    png = p.get_pixmap(dpi=150).tobytes("png")
    plantilla.close()

    doc = fitz.open()
    for _ in range(paginas):
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=png)
    doc.save(destino)
    doc.close()
    return destino


# ------------------------------------------------------------------ ayudas

class Prueba:
    def __init__(self, capturas: Path, ver: bool) -> None:
        self.capturas = capturas
        self.capturas.mkdir(parents=True, exist_ok=True)
        self.ver = ver
        self.fallos: list[str] = []
        self.hechas: list[str] = []

    def captura(self, pagina, nombre: str) -> None:
        ruta = self.capturas / f"{nombre}.png"
        pagina.screenshot(path=str(ruta), full_page=True)
        print(f"   captura: {ruta.name}")

    def comprobar(self, condicion: bool, que: str) -> None:
        if condicion:
            print(f"   OK   {que}")
            self.hechas.append(que)
        else:
            print(f"   FALLA {que}")
            self.fallos.append(que)


def limpiar_servidor() -> None:
    """Deja el servicio sin trabajos de pruebas anteriores."""
    try:
        httpx.get(f"{BASE}/health", timeout=10)
    except httpx.HTTPError:
        print("El servicio no responde en", BASE)
        sys.exit(2)


def abrir(pagina) -> None:
    """Abre la página con el navegador limpio.

    Cada flujo empieza de cero: si no, los documentos recordados de flujos
    anteriores aparecen en la lista —que es justo lo que tiene que pasar en la
    vida real, y se prueba aparte en `flujo_recuperar`—.
    """
    pagina.goto(BASE)
    pagina.evaluate("localStorage.clear()")
    pagina.reload()
    pagina.wait_for_selector("#grupos .grp")


def esperar_texto(pagina, selector: str, texto: str, ms: int = 120000) -> bool:
    try:
        expect(pagina.locator(selector)).to_contain_text(texto, timeout=ms)
        return True
    except AssertionError:
        return False


# ------------------------------------------------------------------ flujos

def flujo_documento_completo(p, pagina, tmp: Path) -> None:
    print("\n1) Un documento de principio a fin")
    abrir(pagina)
    limites = httpx.get(f"{BASE}/health", timeout=10).json()["limits"]
    aviso = pagina.locator("#retencion").inner_text()
    if limites["store_original"] is False and limites["result_ttl_minutes"] == 360:
        p.comprobar(
            aviso == "El documento original se borra del servidor en cuanto termina de "
                     "procesarse. El anonimizado se borra 6 horas después: descárgalo antes.",
            "un solo aviso de borrado, con el texto acordado",
        )
    else:
        p.comprobar(
            ("se guarda" in aviso) == limites["store_original"],
            "el aviso de borrado dice lo que hace este servidor con el original",
        )
    p.comprobar(pagina.get_by_text("seis horas").count() == 0, "sin un segundo aviso de plazo")
    p.comprobar(
        pagina.locator("#organizacion").inner_text() == "Universitat de València",
        "la cabecera dice Universitat de València",
    )
    pagina.set_input_files("#ficheros", str(pdf_simple(tmp / "expediente.pdf", 2)))
    p.comprobar(
        pagina.locator(".ficha").count() == 1,
        "el documento elegido aparece como ficha en el paso 1",
    )
    p.comprobar(
        "expediente.pdf" in pagina.locator(".ficha .nombre").first.inner_text(),
        "con su nombre debajo",
    )
    p.captura(pagina, "01-subida")

    pagina.click("#anonimizar")
    ok = esperar_texto(pagina, ".doc .st", "Terminado")
    p.comprobar(ok, "termina y dice cuantos datos ha ocultado")
    p.comprobar(
        "datos ocultados" in pagina.locator(".doc .st").first.inner_text(),
        "el texto explica el resultado en lenguaje llano",
    )
    p.captura(pagina, "02-terminado")

    with pagina.expect_download(timeout=30000) as descarga:
        pagina.click(".doc a.btn.primary")
    fichero = descarga.value
    p.comprobar(
        fichero.suggested_filename.startswith("expediente"),
        "la descarga conserva el nombre original, que lo pone el navegador",
    )

    with pagina.expect_download(timeout=30000) as descarga:
        pagina.click(".doc button:has-text('Informe en PDF')")
    informe = tmp / "informe_descargado.pdf"
    descarga.value.save_as(informe)
    p.comprobar(descarga.value.suggested_filename == "expediente_informe.pdf",
                "el informe en PDF se descarga junto al documento")
    with fitz.open(informe) as doc:
        texto = " ".join(pg.get_text() for pg in doc)
    p.comprobar("Universitat de València" in texto and "Qué se ha ocultado" in texto,
                "el informe en PDF se lee en castellano")
    p.comprobar(NOMBRE not in texto and "12345678Z" not in texto and "expediente.pdf" not in texto,
                "el informe en PDF no lleva ningun dato ocultado ni el nombre del fichero")


def flujo_solo_metadatos(p, pagina, tmp: Path) -> None:
    print("\n1c) Solo quitar metadatos: avisa de que el contenido no se revisa")
    abrir(pagina)
    entrada = pdf_simple(tmp / "solo_meta.pdf")
    with fitz.open(entrada) as doc:
        doc.set_metadata({"author": "Autor del expediente"})
        doc.saveIncr()
    pagina.check("#tratMetadatos")
    p.comprobar(pagina.locator("#grupos").is_hidden() and pagina.locator("#activos").is_hidden(),
                "no se eligen tipos ni se cuentan: no se oculta nada")
    p.comprobar("NO se revisa" in pagina.locator("#avisoMetadatos").inner_text(),
                "el aviso de que el contenido no se revisa se ve antes de empezar")
    p.comprobar(pagina.locator("#anonimizar").inner_text() == "Quitar metadatos",
                "el boton dice lo que va a hacer")
    pagina.set_input_files("#ficheros", str(entrada))
    pagina.click("#anonimizar")
    ok = esperar_texto(pagina, ".doc .st", "metadatos quitados")
    p.comprobar(ok and "NO se ha revisado" in pagina.locator(".doc .st").first.inner_text(),
                "termina diciendo que el contenido NO se ha revisado")
    p.captura(pagina, "16-solo-metadatos")
    with pagina.expect_download(timeout=30000) as descarga:
        pagina.click(".doc a.btn.primary")
    fichero = descarga.value
    p.comprobar(fichero.suggested_filename == "solo_meta_sin_metadatos.pdf",
                "la descarga se llama _sin_metadatos")
    salida = tmp / "solo_meta_salida.pdf"
    fichero.save_as(salida)
    with fitz.open(salida) as doc:
        p.comprobar(NOMBRE in doc[0].get_text(), "el contenido sale tal cual")
        p.comprobar(not doc.metadata.get("author"), "sin el autor en los metadatos")
    pagina.check("#tratAnonimizar")
    p.comprobar(pagina.locator("#grupos").is_visible(), "al volver a anonimizar, los tipos vuelven")


def foto_jpeg(destino: Path) -> Path:
    """Foto de movil con EXIF y un dato escrito, para el camino de imagen."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (900, 600), "white")
    d = ImageDraw.Draw(img)
    d.text((40, 60), f"Interesado: {NOMBRE}", fill="black")
    d.text((40, 100), "DNI: 12345678Z", fill="black")
    exif = Image.Exif()
    exif[0x010F] = "Fabricante del movil"
    img.save(destino, format="JPEG", quality=90, exif=exif)
    return destino


def flujo_imagen(p, pagina, tmp: Path) -> None:
    print("\n1d) Una foto JPEG sale JPEG, tapada y sin metadatos")
    abrir(pagina)
    pagina.set_input_files("#ficheros", str(foto_jpeg(tmp / "foto.jpg")))
    p.comprobar(pagina.locator(".ficha.err").count() == 0, "la imagen se admite")
    pagina.click("#anonimizar")
    ok = esperar_texto(pagina, ".doc .st", "Terminado", ms=180000)
    p.comprobar(ok, "la imagen termina")
    with pagina.expect_download(timeout=30000) as descarga:
        pagina.click(".doc a.btn.primary")
    p.comprobar(descarga.value.suggested_filename == "foto_anonimizado.jpg",
                "se descarga como JPEG con su nombre")
    salida = tmp / "foto_salida.jpg"
    descarga.value.save_as(salida)
    from PIL import Image

    with Image.open(salida) as img:
        p.comprobar(img.format == "JPEG" and img.size == (900, 600),
                    "es un JPEG del mismo tamano")
        p.comprobar(len(img.getexif()) == 0, "sin los datos de la camara")


def flujo_word(p, pagina, tmp: Path) -> None:
    print("\n1e) Un Word sale Word, con el dato sustituido por su tipo")
    import docx

    from evaluation.corpus_word import PERSONAS, carta

    abrir(pagina)
    carta(tmp / "carta.docx", PERSONAS[0])
    antiguo = tmp / "antiguo.doc"
    antiguo.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504)
    pagina.set_input_files("#ficheros", [str(tmp / "carta.docx"), str(antiguo)])
    icono = pagina.locator(".ficha:has-text('carta.docx') svg")
    p.comprobar("DOCX" in (icono.text_content() or "") and "#2B579A" in icono.inner_html(),
                "el icono del Word dice DOCX, en azul")
    p.comprobar("formato antiguo" in pagina.locator(".ficha.err .motivo").first.inner_text(),
                "el .doc antiguo se rechaza diciendo que hay que guardarlo como .docx")
    pagina.click("#anonimizar")
    ok = esperar_texto(pagina, ".doc .st", "Terminado", ms=180000)
    p.comprobar(ok, "el Word termina")
    with pagina.expect_download(timeout=30000) as descarga:
        pagina.click(".doc a.btn.primary")
    p.comprobar(descarga.value.suggested_filename == "carta_anonimizado.docx",
                "se descarga como Word con su nombre")
    salida = tmp / "carta_salida.docx"
    descarga.value.save_as(salida)
    texto = "\n".join(par.text for par in docx.Document(salida).paragraphs)
    p.comprobar(PERSONAS[0]["nombre"] not in texto and "[NOMBRE]" in texto,
                "el Word se abre y el nombre se ha sustituido por [NOMBRE]")


def flujo_excel(p, pagina, tmp: Path) -> None:
    print("\n1f) Un Excel sale Excel: la columna conserva su cabecera y lleva el marcador")
    import openpyxl

    from evaluation.corpus_excel import listado

    abrir(pagina)
    listado(tmp / "listado.xlsx")
    pagina.set_input_files("#ficheros", str(tmp / "listado.xlsx"))
    icono = pagina.locator(".ficha:has-text('listado.xlsx') svg")
    p.comprobar("XLSX" in (icono.text_content() or "") and "#1D6F42" in icono.inner_html(),
                "el icono del Excel dice XLSX, en verde")
    pagina.click("#anonimizar")
    ok = esperar_texto(pagina, ".doc .st", "Terminado", ms=180000)
    p.comprobar(ok, "el Excel termina")
    with pagina.expect_download(timeout=30000) as descarga:
        pagina.click(".doc a.btn.primary")
    p.comprobar(descarga.value.suggested_filename == "listado_anonimizado.xlsx",
                "se descarga como Excel con su nombre")
    salida = tmp / "listado_salida.xlsx"
    descarga.value.save_as(salida)
    ws = openpyxl.load_workbook(salida).active
    p.comprobar(ws["D1"].value == "DNI" and ws["D2"].value == "[DNI]",
                "el Excel se abre, la cabecera se queda y la celda lleva [DNI]")


def flujo_texto(p, pagina, tmp: Path) -> None:
    print("\n1g) Un texto sale texto, en su codificacion; un Markdown, Markdown")
    from evaluation.corpus_word import PERSONAS

    persona = PERSONAS[0]
    abrir(pagina)
    texto = (f"Ajuntament de València\r\nSolicitante: {persona['nombre']}, con DNI {persona['dni']}.\r\n"
             "Peñíscola, a 3 de març.\r\n")
    (tmp / "nota.txt").write_bytes(texto.encode("cp1252"))
    (tmp / "acta.md").write_text(f"# Acta\n\n| Nombre | DNI |\n|---|---|\n| {persona['nombre']} | "
                                 f"{persona['dni']} |\n", encoding="utf-8")
    pagina.set_input_files("#ficheros", [str(tmp / "nota.txt"), str(tmp / "acta.md")])
    icono = pagina.locator(".ficha:has-text('nota.txt') svg")
    p.comprobar("TXT" in (icono.text_content() or "") and "#6A4C93" in icono.inner_html(),
                "el icono del texto dice TXT")
    pagina.click("#anonimizar")
    ok = all(esperar_texto(pagina, f".doc:has-text('{n}') .st", "Terminado", ms=180000)
             for n in ("nota.txt", "acta.md"))
    p.comprobar(ok, "el texto y el Markdown terminan")
    with pagina.expect_download(timeout=30000) as descarga:
        pagina.click(".doc:has-text('nota.txt') a.btn.primary")
    p.comprobar(descarga.value.suggested_filename == "nota_anonimizado.txt",
                "el texto se descarga como .txt con su nombre")
    salida = tmp / "nota_salida.txt"
    descarga.value.save_as(salida)
    leido = salida.read_bytes().decode("cp1252")
    p.comprobar(persona["nombre"] not in leido and "[NOMBRE]" in leido and "Peñíscola" in leido
                and leido.count("\r\n") == 3,
                "sale en Windows-1252 con sus tildes y sus saltos de linea, y [NOMBRE] en lugar del nombre")
    with pagina.expect_download(timeout=30000) as descarga:
        pagina.click(".doc:has-text('acta.md') a.btn.primary")
    p.comprobar(descarga.value.suggested_filename == "acta_anonimizado.md",
                "el Markdown se descarga como .md")
    salida = tmp / "acta_salida.md"
    descarga.value.save_as(salida)
    leido = salida.read_text(encoding="utf-8")
    p.comprobar("| [NOMBRE] | [DNI] |" in leido, "la tabla del Markdown sigue siendo una tabla")

    abrir(pagina)
    pagina.check("#tratMetadatos")
    p.comprobar("no tienen metadatos" in pagina.locator("#avisoMetadatos").inner_text(),
                "el aviso de solo metadatos dice que los textos salen igual")
    pagina.set_input_files("#ficheros", str(tmp / "nota.txt"))
    pagina.click("#anonimizar")
    ok = esperar_texto(pagina, ".doc .st", "sale igual")
    p.comprobar(ok, "un texto con solo metadatos termina diciendo que sale igual")
    with pagina.expect_download(timeout=30000) as descarga:
        pagina.click(".doc a.btn.primary")
    salida = tmp / "nota_sin.txt"
    descarga.value.save_as(salida)
    p.comprobar(salida.read_bytes() == (tmp / "nota.txt").read_bytes(), "y sale identico")
    pagina.check("#tratAnonimizar")


def flujo_csv(p, pagina, tmp: Path) -> None:
    print("\n1i) Un CSV sale CSV: la columna conserva su cabecera y lleva el marcador")
    from evaluation.corpus_word import PERSONAS

    abrir(pagina)
    filas = ["Nombre;DNI;Distrito"] + [f"{q['nombre']};{q['dni']};Russafa" for q in PERSONAS]
    original = ("\r\n".join(filas) + "\r\n").encode("cp1252")
    (tmp / "padron.csv").write_bytes(original)
    pagina.set_input_files("#ficheros", str(tmp / "padron.csv"))
    icono = pagina.locator(".ficha:has-text('padron.csv') svg")
    p.comprobar("CSV" in (icono.text_content() or ""), "el icono dice CSV")
    pagina.click("#anonimizar")
    ok = esperar_texto(pagina, ".doc .st", "Terminado", ms=120000)
    p.comprobar(ok, "el CSV termina")
    with pagina.expect_download(timeout=30000) as descarga:
        pagina.click(".doc a.btn.primary")
    p.comprobar(descarga.value.suggested_filename == "padron_anonimizado.csv", "se descarga como CSV con su nombre")
    salida = tmp / "padron_salida.csv"
    descarga.value.save_as(salida)
    lineas = salida.read_bytes().decode("cp1252").split("\r\n")
    p.comprobar(lineas[0] == "Nombre;DNI;Distrito" and lineas[1] == "[NOMBRE];[DNI];Russafa",
                "en su codificacion y con sus saltos: la cabecera se queda y cada celda lleva su marcador")


def flujo_seudonimizar(p, pagina, tmp: Path) -> None:
    print("\n1h) Seudonimizar: etiquetas, tabla cifrada y recuperar los datos")
    from evaluation.corpus_word import PERSONAS

    persona = PERSONAS[0]
    abrir(pagina)
    activada = httpx.get(f"{BASE}/health", timeout=10).json().get("pseudonymization", {}).get("enabled")
    if not activada:
        p.comprobar(pagina.locator("#tratSeudonimizarFila").is_hidden() and pagina.locator("#pestanas").is_hidden(),
                    "desactivada (por defecto): ni la tercera opcion ni la pestana existen")
        print("   (el resto se omite: arranque el servicio con PSEUDONYMIZATION_ENABLED=true)")
        return
    original = (f"Ajuntament de València\r\nSolicitante: {persona['nombre']}\r\n"
                f"Se notifica a {persona['nombre']}, con DNI {persona['dni']}.\r\n").encode("cp1252")
    (tmp / "nota_s.txt").write_bytes(original)
    pagina.check("#tratSeudonimizar")
    p.comprobar("NO es anonimizar" in pagina.locator("#paraSeudonimizar").inner_text(),
                "al elegirla se lee que seudonimizar NO es anonimizar")
    p.comprobar(pagina.locator("#clave").is_visible() and pagina.locator("#clave2").is_visible()
                and "si la pierdes" in pagina.locator("#claveNota").inner_text(),
                "pide la contrasena dos veces y avisa de que no se guarda")
    p.comprobar(pagina.locator("#avisoSinHttps").is_hidden(),
                "en este mismo equipo no avisa de HTTPS (la contrasena no sale a la red)")
    p.comprobar(pagina.locator("#grupos").is_visible(), "las casillas de tipos siguen valiendo")
    pagina.set_input_files("#ficheros", [str(tmp / "nota_s.txt"), str(pdf_simple(tmp / "no_seudo.pdf"))])
    p.comprobar("No se puede seudonimizar" in pagina.locator(".ficha:has-text('no_seudo.pdf') .motivo").inner_text(),
                "el PDF se marca antes de subir: no se puede seudonimizar")
    p.comprobar(pagina.locator("#anonimizar").is_disabled()
                and "Faltan 12 caracteres" in pagina.locator("#claveProgresoTexto").inner_text(),
                "sin contrasena, el boton esta desactivado y dice que faltan 12 caracteres")
    pagina.locator("#clave").press_sequentially("corta")
    barra = pagina.locator("#claveBarra")
    p.comprobar("Faltan 7 caracteres" in pagina.locator("#claveProgresoTexto").inner_text()
                and barra.get_attribute("aria-valuenow") == "5" and "lista" not in (barra.get_attribute("class") or "")
                and "Faltan 7" in (barra.get_attribute("aria-valuetext") or ""),
                "mientras se escribe, la barra avanza y dice cuantos faltan (tambien para el lector de pantalla)")
    p.comprobar(pagina.locator("#claveProgresoTexto").get_attribute("aria-live") == "polite"
                and pagina.locator("#clave2Estado").get_attribute("aria-live") == "polite"
                and "claveProgresoTexto" in (pagina.locator("#clave").get_attribute("aria-describedby") or ""),
                "los mensajes de la contrasena se anuncian y van ligados a su campo")
    pagina.fill("#clave", "clave-larga-de-prueba")
    p.comprobar("Longitud suficiente" in pagina.locator("#claveProgresoTexto").inner_text()
                and "lista" in (barra.get_attribute("class") or "") and pagina.locator("#anonimizar").is_disabled(),
                "al llegar a 12 cambia de aspecto y lo dice; sin repetirla, el boton sigue desactivado")
    pagina.locator("#clave2").press_sequentially("clave-larga-de-o")
    p.comprobar("No coincide" in pagina.locator("#clave2Estado").inner_text()
                and pagina.locator("#clave2").get_attribute("aria-invalid") == "true"
                and pagina.locator("#anonimizar").is_disabled(),
                "la repeticion avisa en cuanto no coincide, y el boton sigue desactivado")
    pagina.fill("#clave2", "clave-larga-de-prueba")
    p.comprobar("Coinciden" in pagina.locator("#clave2Estado").inner_text()
                and pagina.locator("#clave2").get_attribute("aria-invalid") == "false"
                and pagina.locator("#anonimizar").is_enabled(),
                "cuando coinciden lo confirma y el boton se activa")
    with pagina.expect_request("**/jobs*") as peticion:
        pagina.click("#anonimizar")
    p.comprobar("treatment=pseudonymize" in peticion.value.url and "clave-larga" not in peticion.value.url,
                "la contrasena no va en la direccion")
    ok = esperar_texto(pagina, ".doc:has-text('nota_s.txt') .st", "Terminado", ms=120000)
    p.comprobar(ok and "etiqueta" in pagina.locator(".doc:has-text('nota_s.txt') .st").inner_text(),
                "el texto termina diciendo cuantas etiquetas lleva")
    p.comprobar(pagina.locator(".doc").count() == 1
                and pagina.locator(".ficha.err:has-text('no_seudo.pdf')").count() == 1,
                "el PDF se queda rechazado en su ficha y el resto de la tanda sigue")
    p.comprobar(pagina.locator("#clave").input_value() == "" and pagina.locator("#anonimizar").is_disabled(),
                "la pagina suelta la contrasena al subir y el boton vuelve a desactivarse")
    fila = pagina.locator(".doc:has-text('nota_s.txt')")
    p.comprobar(fila.locator(".note[data-codigo='pseudonymized_not_anonymous']").count() == 0
                and fila.locator("button:has-text('Copiar instrucciones')").count() == 0
                and fila.locator("button:has-text('Recuperar datos de un texto')").count() == 0,
                "el resultado no repite el aviso ni lleva copiar instrucciones ni el atajo a recuperar")
    p.captura(pagina, "17-seudonimizado")
    with pagina.expect_download(timeout=30000) as descarga:
        fila.locator("a.btn.primary").click()
    p.comprobar(descarga.value.suggested_filename == "nota_s_seudonimizado.txt", "el documento se llama _seudonimizado")
    seudo = tmp / "nota_s_seudonimizado.txt"
    descarga.value.save_as(seudo)
    leido = seudo.read_bytes().decode("cp1252")
    p.comprobar(persona["nombre"] not in leido and leido.count("[PERSONA_1]") == 2 and "[DNI_1]" in leido,
                "lleva [PERSONA_1] las dos veces y [DNI_1], sin los datos")
    with pagina.expect_download(timeout=30000) as descarga:
        fila.locator("button:has-text('Tabla')").click()
    tabla = tmp / "nota_s.tabla"
    descarga.value.save_as(tabla)
    p.comprobar(descarga.value.suggested_filename == "nota_s.tabla"
                and persona["nombre"].split()[0].encode() not in tabla.read_bytes(),
                "la tabla se descarga aparte y no lleva ningun dato en claro")

    pagina.click("#tabRecuperar")
    p.comprobar(pagina.locator("#vistaRecuperar").is_visible() and pagina.locator("#vistaAnonimizar").is_hidden()
                and pagina.locator("#recQuitarTabla").is_hidden(),
                "la pestana «Recuperar datos» se abre sin tabla, y sin X que pulsar")
    alto = pagina.locator("#recTexto").evaluate("e => { e.style.height = '20px'; return e.getBoundingClientRect().height; }")
    p.comprobar(alto >= 150, f"el cuadro del texto no encoge por debajo de su altura minima ({alto:.0f} px)")
    pagina.locator("#recTexto").evaluate("e => { e.style.height = ''; }")
    pagina.set_input_files("#recTabla", str(tabla))
    quitar = pagina.locator("#recQuitarTabla")
    p.comprobar("nota_s.tabla" in pagina.locator("#recTablaNombre").inner_text() and quitar.is_visible()
                and quitar.get_attribute("aria-label") == "Quitar la tabla",
                "elegida la tabla, aparece su X con un nombre para el lector de pantalla")
    quitar.click()
    p.comprobar("Ninguna tabla" in pagina.locator("#recTablaNombre").inner_text() and quitar.is_hidden(),
                "la X quita la tabla elegida")
    pagina.set_input_files("#recTabla", str(tabla))
    pagina.set_input_files("#recFichero", str(tabla))
    quitar_fichero = pagina.locator("#recQuitarFichero")
    p.comprobar(quitar_fichero.is_visible() and pagina.locator("#recTexto").is_hidden()
                and quitar_fichero.get_attribute("aria-label") == "Quitar el archivo",
                "elegido un archivo en el paso del texto, tiene tambien su X")
    quitar_fichero.click()
    p.comprobar(pagina.locator("#recTexto").is_visible() and pagina.locator("#recFicheroElegido").is_hidden(),
                "la X quita el archivo y vuelve el cuadro para pegar")
    pagina.fill("#recTexto", "Resumen: [Persona 1] (DNI_1) pide la licencia. Consta también [PERSONA_9].")
    pagina.fill("#recClave", "otra-clave-bien-larga")
    pagina.click("#recLanzar")
    pagina.wait_for_selector("#recError:not(.hidden)", timeout=30000)
    p.comprobar("no se puede abrir" in pagina.locator("#recError").inner_text(),
                "con otra contrasena dice que la tabla no se puede abrir")
    pagina.fill("#recClave", "clave-larga-de-prueba")
    pagina.click("#recLanzar")
    pagina.wait_for_selector("#recResultado:not(.hidden)", timeout=30000)
    vista = pagina.locator("#recVista").inner_text()
    p.comprobar(persona["nombre"] in vista and persona["dni"] in vista and "[PERSONA_9]" in vista,
                "el resumen recupera el nombre y el DNI aunque las etiquetas vinieran alteradas")
    p.comprobar("[PERSONA_9]" in pagina.locator("#recNoResueltas").inner_text()
                and "[Persona 1]" in pagina.locator("#recAlteradas").inner_text(),
                "avisa de la etiqueta que no esta en la tabla y lista las alteradas")
    p.captura(pagina, "18-recuperar")
    pagina.set_input_files("#recFichero", str(seudo))
    pagina.click("#recLanzar")
    esperar_texto(pagina, "#recResumen", "tal como era", ms=30000)
    with pagina.expect_download(timeout=30000) as descarga:
        pagina.click("#recDescargar")
    vuelta = tmp / "nota_s_recuperado.txt"
    descarga.value.save_as(vuelta)
    p.comprobar(descarga.value.suggested_filename == "nota_s_recuperado.txt" and vuelta.read_bytes() == original,
                "con el documento tal cual salio, la vuelta es identica al original, byte a byte")
    pagina.click("#tabAnonimizar")
    p.comprobar(pagina.locator("#vistaAnonimizar").is_visible(), "la otra pestana vuelve a anonimizar")
    pagina.check("#tratAnonimizar")


def flujo_expediente(p, pagina, tmp: Path) -> None:
    print("\n1b) El numero de expediente empieza marcado y la casilla manda")
    abrir(pagina)
    pagina.click("#grupos .grp-head:has-text('Empresas y documentos') .spacer")
    fila = pagina.locator(".grp-body .row:has-text('Número de expediente')")
    p.comprobar("off" not in (fila.get_attribute("class") or ""),
                "la casilla del numero de expediente sale marcada")
    pagina.set_input_files("#ficheros", str(pdf_simple(tmp / "exp_marcado.pdf")))
    with pagina.expect_request("**/jobs*") as peticion:
        pagina.click("#anonimizar")
    p.comprobar("CASE_NUMBER" not in peticion.value.url,
                "sin tocarla no se envia nada: el servicio aplica el mismo catalogo")

    abrir(pagina)
    pagina.click("#grupos .grp-head:has-text('Empresas y documentos') .spacer")
    pagina.locator(".grp-body .row:has-text('Número de expediente')").click()
    pagina.set_input_files("#ficheros", str(pdf_simple(tmp / "exp_desmarcado.pdf")))
    with pagina.expect_request("**/jobs*") as peticion:
        pagina.click("#anonimizar")
    p.comprobar("disabled_types=CASE_NUMBER" in peticion.value.url,
                "desmarcada, el documento se envia con el expediente sin tachar")


def flujo_tanda(p, pagina, tmp: Path) -> None:
    print("\n2) Una tanda con un fichero rechazado")
    abrir(pagina)
    pagina.set_input_files(
        "#ficheros",
        [
            str(pdf_simple(tmp / "uno.pdf")),
            str(no_es_pdf(tmp / "animacion.gif")),
            str(pdf_simple(tmp / "dos.pdf")),
        ],
    )
    p.comprobar(pagina.locator(".ficha").count() == 3, "los tres entran como fichas")
    p.comprobar(
        "no se puede anonimizar" in pagina.locator(".ficha.err .motivo").first.inner_text(),
        "el que no es PDF ni imagen se marca en su ficha con el motivo, donde se eligio",
    )
    pagina.click("#anonimizar")
    ok = esperar_texto(pagina, "#resumen", "terminado")
    p.comprobar(ok, "el resumen cuenta los terminados")
    p.comprobar(
        pagina.locator(".doc").count() == 2,
        "al paso 3 solo pasan los dos validos: el rechazado no se sube",
    )
    p.comprobar(
        pagina.locator(".ficha.err").count() == 1,
        "y el rechazado sigue visible en el paso 1 con su motivo",
    )
    p.captura(pagina, "03-tanda")


def flujo_parar(p, pagina, tmp: Path) -> None:
    print("\n3) Parar un escaneado y quedarse con lo procesado")
    abrir(pagina)
    pagina.set_input_files("#ficheros", str(pdf_escaneado(tmp / "escaneado.pdf", 4)))
    pagina.click("#anonimizar")

    esperar_texto(pagina, ".doc .st", "Procesando", ms=60000)
    p.comprobar(
        pagina.locator(".doc .acts button", has_text="Parar").count() == 0,
        "sin ninguna pagina hecha NO se ofrece Parar, solo Eliminar",
    )
    p.captura(pagina, "04-procesando")

    # Esperar a que termine la primera pagina para que aparezca Parar.
    apareció = False
    for _ in range(120):
        if pagina.locator(".doc .acts button", has_text="Parar").count():
            apareció = True
            break
        time.sleep(1)
    p.comprobar(apareció, "con una pagina hecha aparece Parar")
    if not apareció:
        return

    pagina.click(".doc .acts button:has-text('Parar')")
    p.comprobar(esperar_texto(pagina, ".doc .st", "Parando", ms=20000),
                "mientras suelta la pagina en curso dice «Parando…»")
    ok = esperar_texto(pagina, ".doc .st", "Parcial", ms=600000)
    p.comprobar(ok, "acaba como parcial, diciendo que paginas cubre")
    p.captura(pagina, "05-parcial")


def flujo_eliminar(p, pagina, tmp: Path) -> None:
    print("\n4) Eliminar pide confirmacion")
    abrir(pagina)
    pagina.set_input_files("#ficheros", str(pdf_simple(tmp / "borrar.pdf")))
    pagina.click("#anonimizar")
    esperar_texto(pagina, ".doc .st", "Terminado")

    pagina.click(".doc .acts button:has-text('Eliminar')")
    p.comprobar(pagina.locator("#confirmacion:not(.hidden)").count() == 1,
                "eliminar pide confirmacion")
    p.captura(pagina, "06-confirmar-eliminar")
    pagina.click("#confNo")
    p.comprobar(pagina.locator(".doc").count() == 1, "cancelar no borra nada")

    pagina.click(".doc .acts button:has-text('Eliminar')")
    pagina.click("#confSi")
    time.sleep(2)
    p.comprobar(pagina.locator(".doc").count() == 0, "confirmar lo quita de la lista")


def flujo_recuperar(p, pagina, tmp: Path) -> None:
    print(chr(10) + "5) Cerrar la pestana y volver")
    abrir(pagina)
    pagina.set_input_files("#ficheros", str(pdf_simple(tmp / "recuperar.pdf", 3)))
    pagina.click("#anonimizar")
    esperar_texto(pagina, ".doc .st", "Terminado")

    pagina.reload()  # como cerrar la pestaña y volver a abrirla
    pagina.wait_for_selector(".doc", timeout=20000)
    p.comprobar(pagina.locator(".doc").count() >= 1, "el documento reaparece al volver")
    nombre = pagina.locator(".doc .name").first.inner_text()
    p.comprobar("recuperar.pdf" in nombre,
                "con el nombre del fichero, que el navegador guarda mientras el documento sigue en el servidor")
    p.captura(pagina, "10-recuperado")
    # Al eliminarlo, el navegador lo olvida, nombre incluido.
    pagina.click(".doc .acts button:has-text('Eliminar')")
    pagina.click("#confSi")
    time.sleep(2)
    guardado = pagina.evaluate("localStorage.getItem('anonimizador.trabajos') || ''")
    p.comprobar("recuperar.pdf" not in guardado, "al eliminarlo, el nombre se borra del navegador")


def flujo_errores(p, pagina, tmp: Path) -> None:
    print("\n5) Errores del documento")
    abrir(pagina)
    pagina.set_input_files("#ficheros", str(pdf_con_contrasena(tmp / "con_clave.pdf")))
    pagina.click("#anonimizar")
    ok = esperar_texto(pagina, "#rechazos", "contraseña", ms=60000)
    p.comprobar(ok, "un PDF con contraseña se rechaza al subirlo")
    detalle = pagina.locator(".rechazo").first.inner_text()
    p.comprobar(
        "guárdalo sin ella" in detalle or "copia sin protección" in detalle,
        "y el aviso dice QUE HACER, no solo que ha fallado",
    )
    p.comprobar(
        pagina.locator(".ficha.err").count() == 1,
        "la ficha del fichero queda marcada en rojo",
    )
    p.captura(pagina, "07-error-contrasena")


def flujo_sin_conexion(p, pagina, tmp: Path) -> None:
    print("\n6) Se cae la conexion a mitad")
    abrir(pagina)
    pagina.set_input_files("#ficheros", str(pdf_escaneado(tmp / "lento.pdf", 3)))
    pagina.click("#anonimizar")
    esperar_texto(pagina, ".doc .st", "Procesando", ms=60000)

    pagina.context.set_offline(True)
    ok = esperar_texto(pagina, ".doc .st", "conexión", ms=60000)
    p.comprobar(ok, "avisa de que se ha perdido la conexion, sin dar el documento por muerto")
    p.captura(pagina, "08-sin-conexion")
    pagina.context.set_offline(False)


def flujo_avisos_del_informe(p, pagina, tmp: Path) -> None:
    print("\n7) Los avisos del informe, en castellano")
    abrir(pagina)
    pagina.set_input_files("#ficheros", str(pdf_plano(tmp / "plano.pdf")))
    pagina.click("#anonimizar")
    ok = esperar_texto(pagina, ".doc .st", "Terminado", ms=300000)
    p.comprobar(ok, "el plano termina")
    nota = pagina.locator('.doc .note[data-codigo="plan_titleblock_only_verified"]')
    p.comprobar(nota.count() == 1, "el aviso del plano aparece, por su codigo")
    p.comprobar(
        nota.count() == 1 and "solo se ha comprobado el cajetín" in nota.inner_text()
        and "página 1" in nota.inner_text(),
        "en castellano y con la pagina contada desde 1",
    )
    p.comprobar("title block" not in pagina.locator(".doc").first.inner_text(),
                "la frase en ingles del informe no se cuela en la pantalla")
    p.captura(pagina, "11-aviso-plano")


def flujo_servicio_caido(p, pagina) -> None:
    """Solo si el servicio arrancado no esta listo (modelos ausentes)."""
    salud = httpx.get(f"{BASE}/health", timeout=10).json()
    if salud.get("ready"):
        print("\n8) Servicio caido: se omite (este servicio SI esta listo)")
        return
    print("\n8) El servicio no esta listo")
    pagina.goto(BASE)
    p.comprobar(pagina.locator("#salud.down").count() == 1, "el indicador se pone en rojo")
    p.comprobar(pagina.locator("#avisoServicio:not(.hidden)").count() == 1,
                "y explica que no se pueden procesar documentos")
    p.captura(pagina, "09-servicio-caido")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capturas-en", type=Path, default=Path("capturas"))
    parser.add_argument("--ver", action="store_true", help="navegador visible")
    parser.add_argument("--solo", default=None, help="un flujo suelto por nombre")
    args = parser.parse_args()

    limpiar_servidor()
    tmp = Path(args.capturas_en) / "_documentos"
    tmp.mkdir(parents=True, exist_ok=True)
    prueba = Prueba(args.capturas_en, args.ver)

    flujos = {
        "completo": flujo_documento_completo,
        "solo-metadatos": flujo_solo_metadatos,
        "imagen": flujo_imagen,
        "word": flujo_word,
        "excel": flujo_excel,
        "texto": flujo_texto,
        "csv": flujo_csv,
        "seudonimizar": flujo_seudonimizar,
        "expediente": flujo_expediente,
        "tanda": flujo_tanda,
        "parar": flujo_parar,
        "eliminar": flujo_eliminar,
        "recuperar": flujo_recuperar,
        "errores": flujo_errores,
        "sin-conexion": flujo_sin_conexion,
        "avisos": flujo_avisos_del_informe,
    }

    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=not args.ver)
        contexto = navegador.new_context(
            viewport={"width": 1100, "height": 900}, accept_downloads=True,
        )
        pagina = contexto.new_page()
        try:
            for nombre, flujo in flujos.items():
                if args.solo and args.solo != nombre:
                    continue
                flujo(prueba, pagina, tmp)
            if not args.solo:
                flujo_servicio_caido(prueba, pagina)
        finally:
            contexto.close()
            navegador.close()

    print(f"\n{len(prueba.hechas)} comprobaciones pasadas, {len(prueba.fallos)} fallidas")
    for fallo in prueba.fallos:
        print(f"  FALLA: {fallo}")
    return 1 if prueba.fallos else 0


if __name__ == "__main__":
    sys.exit(main())
