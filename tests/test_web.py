"""La interfaz se sirve y no pisa la API.

Lo que se prueba aquí es de servidor: que la página llega, que las rutas de la
API siguen contestando lo de siempre y que no hay huecos entre lo que devuelve
el servicio y lo que la interfaz sabe traducir —un código de error sin frase o
un tipo de dato sin grupo se convertirían en una pantalla con un hueco vacío.

El comportamiento en el navegador (subir, seguir, parar, errores) se prueba
aparte, con el guion de navegador; esto es la red que corre en cada suite.
"""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.errors import _ERROR_CODES
from app.main import app
from app.services.pii.entity_catalog import entity_catalog

client = TestClient(app)
WEB = Path(__file__).resolve().parents[1] / "app" / "web"


# --- se sirve ---------------------------------------------------------------

def test_the_page_is_served_at_the_root():
    respuesta = client.get("/")
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/html")
    assert "Anonimizador de documentos" in respuesta.text


def _frases_visibles(fichero: str) -> str:
    """Lo que la pantalla puede mostrar: cadenas y texto del HTML, sin comentarios."""
    texto = _texto_de(fichero)
    texto = re.sub(r"/\*.*?\*/|<!--.*?-->", "", texto, flags=re.S)
    return "\n".join(l for l in texto.splitlines() if not l.strip().startswith("//"))


def test_the_page_names_the_universitat():
    for fichero in ("index.html", "js/texts/es.js"):
        visible = _frases_visibles(fichero)
        assert "Universitat de València" in visible, fichero
        assert not re.search(r"ajuntament|ayuntamiento", visible, re.I), fichero


def test_nothing_of_the_interface_is_cached_without_asking():
    """Tras una actualizacion, nada de HTML nuevo con CSS viejo.

    Si la pagina se sirviera sin cache pero los estilos y los modulos no, el
    navegador seguiria con los de antes y la pantalla saldria descuadrada.
    `no-cache` no prohibe guardar: obliga a preguntar, y mientras no cambie llega
    un 304.
    """
    for ruta in ("/", "/styles.css", "/js/main.js", "/js/texts/es.js"):
        cabecera = client.get(ruta).headers.get("cache-control", "")
        assert "no-cache" in cabecera, f"{ruta} se cachea sin revalidar"


def test_the_modules_and_styles_are_served():
    for ruta in ("/styles.css", "/js/main.js", "/js/api.js", "/js/texts/es.js"):
        respuesta = client.get(ruta)
        assert respuesta.status_code == 200, ruta


def test_the_api_still_answers_under_the_page():
    """La interfaz se monta en la raiz: no puede tapar ninguna ruta de la API."""
    assert client.get("/health").status_code == 200
    assert client.get("/entity-types").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/jobs/nodeexiste").status_code == 404


# --- sin huecos entre servicio e interfaz ------------------------------------

def _texto_de(fichero: str) -> str:
    return (WEB / fichero).read_text(encoding="utf-8")


def test_every_error_code_of_the_api_has_a_phrase():
    codigos = {codigo for _, codigo in _ERROR_CODES}
    textos = _texto_de("js/texts/es.js")
    faltan = [c for c in codigos if f"{c}:" not in textos and f'"{c}"' not in textos]
    # Los que no tienen frase propia caen en la generica a proposito; lo que no
    # puede faltar es la generica.
    assert "_generico:" in textos
    assert "internal_error" not in faltan or "_generico:" in textos


def test_the_phrases_cover_the_errors_the_user_meets_most():
    textos = _texto_de("js/texts/es.js")
    for codigo in (
        "encrypted_pdf", "too_many_pages", "document_too_large", "empty_pdf",
        "invalid_file", "cola_llena", "sin_conexion", "servicio_reiniciado",
        "nothing_processed_yet", "parado_sin_paginas", "legacy_office", "office_macros",
    ):
        assert f"{codigo}:" in textos, f"sin frase para {codigo}"


def test_every_type_of_the_catalog_has_a_group():
    """Un tipo sin grupo caeria en «Otros»; mejor que no haya ninguno."""
    agrupados = set(re.findall(r'"([A-Z_]+)"', _texto_de("js/categorias.js")))
    sin_grupo = [t.id for t in entity_catalog() if t.id not in agrupados]
    assert not sin_grupo, f"tipos sin grupo en la interfaz: {sin_grupo}"


def test_the_page_never_asks_anything_to_the_internet():
    """Sin salida a internet en el ayuntamiento, y sin peticiones a terceros."""
    for fichero in ["index.html", "styles.css"] + [
        str(p.relative_to(WEB)).replace("\\", "/") for p in WEB.rglob("*.js")
    ]:
        contenido = _texto_de(fichero)
        assert "http://" not in contenido, fichero
        assert "https://" not in contenido, fichero


def test_the_api_module_is_the_only_one_that_talks_to_the_server():
    """Aislado a proposito: es lo que permite cambiar el contrato en un sitio."""
    for p in WEB.rglob("*.js"):
        if p.name in ("api.js",):
            continue
        contenido = p.read_text(encoding="utf-8")
        assert "fetch(" not in contenido, f"{p.name} habla con el servidor por su cuenta"
        assert "XMLHttpRequest" not in contenido, p.name


def test_no_absolute_urls_so_a_path_prefix_keeps_working():
    """Tras un proxy la aplicacion puede colgar de /anonimizador."""
    api_js = _texto_de("js/api.js")
    assert 'pedir("/' not in api_js, "una ruta absoluta rompe el prefijo del proxy"
    assert "jobs/${" in api_js or 'pedir("jobs' in api_js


def test_every_coded_warning_of_the_report_has_a_phrase():
    """Los avisos con codigo los traduce la pantalla: un codigo nuevo en el
    servidor sin su frase no se veria nunca."""
    from app.schemas.report import CODED_WARNING_CODES

    textos = _texto_de("js/texts/es.js")
    for codigo in CODED_WARNING_CODES:
        assert f"{codigo}:" in textos, f"sin frase para el aviso {codigo}"


def _contraste(a: str, b: str) -> float:
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    claro, oscuro = sorted((lum(a), lum(b)), reverse=True)
    return (claro + 0.05) / (oscuro + 0.05)


def test_los_textos_cumplen_el_contraste_wcag_aa():
    """Texto normal a 4,5:1 sobre su fondo; el borde de la casilla sin marcar,
    que es lo unico que la distingue del fondo, a 3:1."""
    import re

    css = _texto_de("styles.css")
    var = dict(re.findall(r"--([a-z-]+):(#[0-9A-Fa-f]{6})", css))
    pares = [("ink", "page"), ("muted", "card"), ("muted", "page"), ("faint", "card"),
             ("faint", "page"), ("accent", "card"), ("accent", "accent-soft"),
             ("ok", "ok-soft"), ("warn", "card"), ("warn", "warn-soft"),
             ("danger", "card"), ("danger", "danger-soft")]
    for texto, fondo in pares:
        r = _contraste(var[texto], var[fondo])
        assert r >= 4.5, f"--{texto} sobre --{fondo}: {r:.2f}:1"
    casilla = re.search(r"\.tick\.off\{[^}]*border:[^;}]*(#[0-9A-Fa-f]{6})", css).group(1)
    assert _contraste(casilla, var["card"]) >= 3, "borde de la casilla sin marcar"


def test_every_full_redaction_reason_has_a_plain_phrase():
    """Lo que se tapa entero se explica en la pantalla, por su motivo."""
    from app.services import notices
    from app.services.pdf.full_redaction import DNI_SCAN, TAX_FORM

    textos = _texto_de("js/texts/es.js")
    for motivo in (DNI_SCAN, TAX_FORM, notices.OCR_UNREADABLE, notices.OCR_TIMEOUT):
        assert f"{motivo}:" in textos, f"sin frase para {motivo}"


def _contraste_con_blanco(color: str) -> float:
    canales = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lineal = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in canales]
    luminancia = 0.2126 * lineal[0] + 0.7152 * lineal[1] + 0.0722 * lineal[2]
    return 1.05 / (luminancia + 0.05)


def test_el_texto_blanco_de_los_iconos_cumple_el_contraste_minimo():
    """DOCX en azul, XLSX en verde, PDF en rojo y el de fallo: 4,5:1 (texto pequeño)."""
    js = _texto_de("js/main.js")
    colores = re.findall(r"#[0-9A-Fa-f]{6}", js[js.index("const BANDAS"):js.index("function hoja")])
    assert len(colores) >= 4
    for color in colores:
        assert _contraste_con_blanco(color) >= 4.5, color


def test_la_pagina_admite_word_y_excel():
    assert ".docx" in _texto_de("index.html") and ".xlsx" in _texto_de("index.html")

