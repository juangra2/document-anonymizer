"""Texto plano (.txt) y Markdown (.md): el dato se sustituye por su tipo, como en Word.

Lo que se fija (datos inventados, los del corpus de Word):
  - la codificacion se reconoce (UTF-8 con y sin BOM, UTF-16, las antiguas de
    Windows y de MS-DOS) y la salida se escribe en la misma, con los mismos
    finales de linea: un documento sin datos sale identico, byte a byte;
  - un binario con extension .txt no se toma por texto;
  - en Markdown la sintaxis queda intacta (encabezados, tablas, enlaces,
    imagenes, enfasis, codigo): la estructura que lee markdown-it es la misma
    antes y despues; y se revisa tambien dentro de los enlaces;
  - solo quitar metadatos devuelve el fichero tal cual y avisa de que el
    formato no tiene metadatos;
  - por la cola, un .txt vuelve .txt y un .md vuelve .md.
"""
import time
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from markdown_it import MarkdownIt

from app.core.errors import FileValidationError
from app.main import app
from app.services.formatos import reconocer
from app.services.pii.analyzer import PiiAnalyzer
from app.services.texto.codificacion import leer
from app.services.texto.texto import anonimizar_texto, solo_metadatos_texto

NOMBRE = "Teresa Navarro Gil"
DNI = "29.384.751-C"
TELEFONO = "676 410 293"
CORREO = "teresa.navarro@correo.es"
DATOS = [NOMBRE, DNI, TELEFONO, CORREO]

CARTA = (
    "Ajuntament de València\n"
    "Servicio de Licencias\n"
    "\n"
    f"Doña {NOMBRE}, con DNI {DNI}, teléfono {TELEFONO} y correo {CORREO},\n"
    "solicita la licencia de obra menor para la vivienda.\n"
    "\n"
    f"Se notifica a {NOMBRE.upper()} la resolución del expediente.\n"
    "Peñíscola: camión, año y façana. València, 3 de març.\n"
)
LEGITIMOS = ["Ajuntament de València", "Servicio de Licencias", "solicita la licencia de obra menor",
             "Peñíscola: camión, año y façana"]


@pytest.fixture(scope="module")
def analyzer():
    return PiiAnalyzer()


def _anonimizar(tmp_path: Path, analyzer, datos: bytes, formato: str = "txt"):
    entrada = tmp_path / f"e.{formato}"
    salida = tmp_path / f"s.{formato}"
    entrada.write_bytes(datos)
    informe = anonimizar_texto(entrada, salida, analyzer, "t", formato=formato)
    return salida.read_bytes(), informe


# --- codificacion --------------------------------------------------------------

@pytest.mark.parametrize("codificacion,bom", [
    ("utf-8", b""), ("utf-8", b"\xef\xbb\xbf"), ("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff"),
    ("cp1252", b""), ("cp850", b""),
])
def test_la_codificacion_se_reconoce(codificacion, bom):
    datos = bom + CARTA.encode(codificacion)
    lectura = leer(datos)
    assert lectura.texto == CARTA
    assert lectura.escribir(lectura.texto) == datos


def test_los_finales_de_linea_de_windows_se_conservan(tmp_path, analyzer):
    datos = CARTA.replace("\n", "\r\n").encode("cp1252")
    salida, informe = _anonimizar(tmp_path, analyzer, datos)
    texto = salida.decode("cp1252")
    assert texto.count("\r\n") == CARTA.count("\n")
    assert "\n" not in texto.replace("\r\n", "")
    assert informe.text_encoding == "cp1252"


def test_un_binario_con_extension_txt_no_es_texto(tmp_path):
    ruta = tmp_path / "subida"
    ruta.write_bytes(b"\x00\x01\x02\x03binario\x00\x00" * 50)
    with pytest.raises(FileValidationError):
        reconocer(ruta, "notas.txt")


def test_el_formato_de_texto_se_decide_por_la_extension_y_el_contenido(tmp_path):
    ruta = tmp_path / "subida"
    ruta.write_bytes(CARTA.encode("utf-8"))
    assert reconocer(ruta, "carta.txt") == "txt"
    assert reconocer(ruta, "carta.TXT") == "txt"
    assert reconocer(ruta, "carta.md") == "md"
    assert reconocer(ruta, "carta.markdown") == "md"
    with pytest.raises(FileValidationError):
        reconocer(ruta, "carta.pdf")
    with pytest.raises(FileValidationError):
        reconocer(ruta, None)
    # Un PDF con extension .txt sigue siendo un PDF: manda el contenido.
    ruta.write_bytes(b"%PDF-1.7\n")
    assert reconocer(ruta, "carta.txt") == "pdf"


# --- texto plano ---------------------------------------------------------------

@pytest.mark.parametrize("codificacion", ["utf-8", "cp1252"])
def test_ningun_dato_queda_y_el_texto_legitimo_sigue(tmp_path, analyzer, codificacion):
    salida, informe = _anonimizar(tmp_path, analyzer, CARTA.encode(codificacion))
    texto = salida.decode(codificacion)
    for dato in DATOS:
        assert dato.casefold() not in texto.casefold(), dato
    for legitimo in LEGITIMOS:
        assert legitimo in texto, legitimo
    assert "[NOMBRE]" in texto and "[DNI]" in texto and "[TELÉFONO]" in texto and "[CORREO]" in texto
    assert informe.document_type == "txt"
    assert informe.entities_by_type.get("PERSON", 0) >= 2
    assert informe.status == "completed", informe.warnings


def test_un_texto_sin_datos_sale_identico(tmp_path, analyzer):
    datos = "Ajuntament de València\r\nServicio de Licencias\r\n\r\nHorario de atención: de 9 a 14 h.\r\n".encode("cp1252")
    salida, informe = _anonimizar(tmp_path, analyzer, datos)
    assert salida == datos
    assert informe.entities_redacted == 0


def test_un_texto_vacio_no_falla(tmp_path, analyzer):
    salida, informe = _anonimizar(tmp_path, analyzer, b"")
    assert salida == b""
    assert informe.entities_redacted == 0


# --- Markdown -------------------------------------------------------------------

MARKDOWN = f"""# Expediente de {NOMBRE}

Solicitante: **{NOMBRE}**, con DNI {DNI}.

> Notificado a _{NOMBRE}_ el día 3.

- Teléfono: {TELEFONO}
- Correo: [{CORREO}](mailto:{CORREO})
- Ficha: <{CORREO}>

| Nombre | DNI | Teléfono |
|--------|-----|----------|
| {NOMBRE} | {DNI} | {TELEFONO} |
| Vicent Ferrer Alandí | 48.105.627-T | 963 528 104 |

![Foto de {NOMBRE}](fotos/ficha.png "Ficha de {NOMBRE}")

Véase [la ficha][ficha] y el código `{TELEFONO}`.

```
Contacto: {CORREO}
```

[ficha]: https://sede.example.es/ficha?correo={CORREO}
"""


def _estructura(texto: str) -> list[tuple]:
    """Lo que hace la sintaxis: tipos de bloque y de elemento en linea, sin el texto."""
    md = MarkdownIt("commonmark").enable("table")
    salida = []
    for t in md.parse(texto):
        salida.append((t.type, t.tag, t.nesting))
        for h in t.children or []:
            if h.type not in ("text", "softbreak"):
                salida.append((h.type, h.tag, h.nesting))
    return salida


@pytest.fixture(scope="module")
def markdown(tmp_path_factory, analyzer):
    tmp = tmp_path_factory.mktemp("md")
    salida, informe = _anonimizar(tmp, analyzer, MARKDOWN.encode("utf-8"), "md")
    return salida.decode("utf-8"), informe


def test_markdown_ningun_dato_queda_en_ninguna_parte(markdown):
    texto, informe = markdown
    for dato in DATOS + ["Vicent Ferrer Alandí", "48.105.627-T", "963 528 104"]:
        assert dato.casefold() not in texto.casefold(), dato
    assert informe.document_type == "md"


def test_markdown_la_sintaxis_queda_intacta(markdown):
    texto, _ = markdown
    # Un enlace automatico cuyo correo entero es el dato se queda sin
    # direccion: pasa a ser texto con el marcador.
    assert _estructura(texto) == _estructura(MARKDOWN.replace(f"<{CORREO}>", "CORREO"))
    assert "- Ficha: [CORREO]" in texto
    assert texto.startswith("# Expediente de [NOMBRE]\n")
    assert "**[NOMBRE]**" in texto
    assert "_[NOMBRE]_" in texto
    assert "|--------|-----|----------|" in texto


def test_markdown_revisa_dentro_de_los_enlaces(markdown):
    texto, _ = markdown
    md = MarkdownIt("commonmark").enable("table")
    destinos = [h.attrs.get("href") or h.attrs.get("src") for t in md.parse(texto)
                for h in (t.children or []) if h.type in ("link_open", "image")]
    assert len(destinos) == 3           # el correo, la imagen y la referencia
    for destino in destinos:
        assert "teresa" not in destino.casefold() and " " not in destino, destino
    # el texto alternativo y el titulo de la imagen
    assert f"Foto de {NOMBRE}" not in texto and f"Ficha de {NOMBRE}" not in texto


def test_markdown_el_marcador_no_crea_enlaces(tmp_path, analyzer):
    """Un "[NOMBRE]" seguido de "(", "[" o ":" seria un enlace o una definicion:
    va escapado."""
    fuente = f"{NOMBRE}: solicitante.\n\n{NOMBRE}(interesada) y {NOMBRE}[1].\n"
    salida, _ = _anonimizar(tmp_path, analyzer, fuente.encode("utf-8"), "md")
    texto = salida.decode("utf-8")
    assert NOMBRE not in texto
    html = MarkdownIt("commonmark").render(texto)
    assert html.count("[NOMBRE]") == 3, html
    assert "<a" not in html


def test_markdown_sin_datos_sale_identico(tmp_path, analyzer):
    fuente = ("# Atención al público\n\n| Servicio | Planta |\n|---|---|\n| Registro | Baja |\n\n"
              "- [Sede](sede.html)\n- `de 9 a 14`\n")
    salida, _ = _anonimizar(tmp_path, analyzer, fuente.encode("utf-8"), "md")
    assert salida == fuente.encode("utf-8")


# --- solo metadatos ----------------------------------------------------------------

@pytest.mark.parametrize("formato", ["txt", "md"])
def test_solo_metadatos_devuelve_el_fichero_tal_cual_y_avisa(tmp_path, formato):
    entrada, salida = tmp_path / f"e.{formato}", tmp_path / f"s.{formato}"
    datos = CARTA.encode("cp1252")
    entrada.write_bytes(datos)
    informe = solo_metadatos_texto(entrada, salida, "t", formato=formato)
    assert salida.read_bytes() == datos
    assert informe.treatment == "metadata_only"
    codigos = {a.code for a in informe.coded_warnings}
    assert {"content_not_reviewed", "format_without_metadata"} <= codigos


# --- por la cola -----------------------------------------------------------------------

client = TestClient(app)


def _esperar(job_id: str, segundos: float = 120) -> dict:
    fin = time.time() + segundos
    while time.time() < fin:
        estado = client.get(f"/jobs/{job_id}").json()
        if estado["status"] in ("completed", "failed"):
            return estado
        time.sleep(0.3)
    raise AssertionError("no termino")


@pytest.mark.parametrize("nombre,tipo", [("carta.txt", "text/plain"), ("carta.md", "text/markdown"),
                                         ("carta.md", "application/octet-stream"), ("carta.md", "")])
@pytest.mark.parametrize("tratamiento", ["anonymize", "metadata_only"])
def test_por_la_cola_un_texto_vuelve_en_su_formato(nombre, tipo, tratamiento):
    datos = CARTA.encode("cp1252")
    r = client.post(f"/jobs?treatment={tratamiento}", files={"file": (nombre, BytesIO(datos), tipo)})
    assert r.status_code == 202, r.text
    estado = _esperar(r.json()["job_id"])
    assert estado["status"] == "completed", estado
    descarga = client.get(f"/jobs/{r.json()['job_id']}/result")
    extension = nombre.rsplit(".", 1)[1]
    assert f".{extension}" in descarga.headers["content-disposition"]
    assert descarga.headers["content-type"].startswith("text/")
    if tratamiento == "metadata_only":
        assert descarga.content == datos
    else:
        assert NOMBRE not in descarga.content.decode("cp1252")
    assert client.get(f"/jobs/{r.json()['job_id']}/report.pdf").status_code == 200


def test_una_codificacion_dudosa_se_avisa(tmp_path, analyzer):
    """"Año" en Windows-1252 se lee igual de bien en otras: se lee como
    Windows-1252, la habitual, y el informe pide mirar las tildes."""
    assert leer("Año\n".encode("cp1252")).dudosa
    assert not leer(CARTA.encode("cp1252")).dudosa
    salida, informe = _anonimizar(tmp_path, analyzer, "Año\n".encode("cp1252"))
    assert salida.decode("cp1252") == "Año\n"
    assert "text_encoding_uncertain" in {a.code for a in informe.coded_warnings}
    assert informe.status == "completed_with_warnings"


def test_por_la_ruta_directa_un_markdown_vuelve_markdown():
    r = client.post("/anonymize", files={"file": ("acta.md", BytesIO(MARKDOWN.encode("utf-8")), "text/markdown")})
    assert r.status_code == 200, r.text
    assert ".md" in r.headers["content-disposition"] and r.headers["content-type"].startswith("text/markdown")
    assert NOMBRE not in r.content.decode("utf-8")
