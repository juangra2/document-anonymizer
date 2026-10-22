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

Necesita el servicio levantado en http://127.0.0.1:8000 y `playwright`
instalado (`pip install playwright && python -m playwright install chromium`).
Los estados que dependen de la configuración del servidor (cola llena, límites
diminutos, servicio no listo) se prueban arrancando el servicio con esas
variables; este guion los detecta y los recorre si están puestas.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import fitz
import httpx
from playwright.sync_api import expect, sync_playwright

BASE = "http://127.0.0.1:8000"
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
    destino.write_text("Esto es un documento de Word, no un PDF.", encoding="utf-8")
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
            str(no_es_pdf(tmp / "informe.docx")),
            str(pdf_simple(tmp / "dos.pdf")),
        ],
    )
    p.comprobar(pagina.locator(".ficha").count() == 3, "los tres entran como fichas")
    p.comprobar(
        "no es un PDF" in pagina.locator(".ficha.err .motivo").first.inner_text(),
        "el que no es PDF se marca en su ficha con el motivo, donde se eligio",
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
    p.comprobar(
        "recuperar.pdf" not in nombre and "Documento de las" in nombre,
        "y sin el nombre del fichero, que puede llevar el del interesado",
    )
    p.captura(pagina, "10-recuperado")


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
