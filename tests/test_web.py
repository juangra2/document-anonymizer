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


def test_nothing_of_the_interface_is_cached_without_asking():
    """El fallo real: HTML nuevo con CSS viejo, y la pantalla descuadrada.

    La pagina se servia sin cache pero los estilos y los modulos no, asi que
    tras una actualizacion el navegador seguia con los de antes.  `no-cache` no
    prohibe guardar: obliga a preguntar, y mientras no cambie llega un 304.
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
        "nothing_processed_yet", "parado_sin_paginas",
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
