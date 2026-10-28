"""Las casillas de tipos, en todos los formatos, igual que en el PDF.

Desmarcar un tipo deja ese dato a la vista y no cambia lo que se hace con los
demas.  Se prueba por el despacho comun (formatos.procesar), el mismo de la
cola y de la ruta sincrona, con el OCR simulado: imagenes (JPEG, PNG, TIFF),
Word con una imagen dentro y Excel con formulas y texto libre.  Los caminos
especiales: la copia de DNI tapada entera en una imagen (como en el PDF, es una
decision sobre la pagina, no sobre un tipo), la imagen incrustada en Word, las
formulas y el texto libre en Excel.  Solo metadatos no toca el contenido, se
marque lo que se marque.
"""
import zipfile
from io import BytesIO
from pathlib import Path

import docx
import fitz
import numpy as np
import openpyxl
import pytest
from PIL import Image

from app.services.formatos import procesar
from app.services.ocr.models import OcrPage, OcrToken
from app.services.pii.analyzer import PiiAnalyzer
from app.services.pii.entity_catalog import known_type_ids

DNI, NOMBRE, CORREO = "12345678Z", "Marcos Villalba Tena", "mvillalba@correo.es"
RENGLON = (40, 40, 560, 75)
TELEFONO = "612345678"
# El OCR simulado devuelve el renglon entero tambien al releer palabra a palabra,
# asi que un nombre de varias palabras solo se taparia en parte: en la imagen van
# datos de una sola palabra.
TEXTO_IMAGEN = f"DNI {DNI} correo {CORREO} tel {TELEFONO}"
TEXTO_CARNET = ("DOCUMENTO NACIONAL DE IDENTIDAD ESPAÑA NACIONALIDAD ESP "
                "VALIDEZ 01 01 2030 IDESP<<<<<<<<<<")


class _OcrSimulado:
    """Lee *texto* en el renglon, en pixeles de una imagen de 600 de ancho."""

    engine_name = "fake"
    provides_layout = False
    supports_pdf_input = False

    def __init__(self, texto: str) -> None:
        self.texto = texto

    def analyze_image(self, image_path, page_index=0):
        with Image.open(image_path) as img:
            ancho, alto = img.size
        caja = fitz.Rect(*RENGLON) * (ancho / 600)
        palabras = self.texto.split()
        paso = caja.width / len(palabras)
        tokens = [OcrToken(text=p, confidence=0.95,
                           rect=fitz.Rect(caja.x0 + i * paso, caja.y0, caja.x0 + (i + 1) * paso - 4, caja.y1))
                  for i, p in enumerate(palabras)]
        return OcrPage(page_index=page_index, width=ancho, height=alto, tokens=tokens)


def _caja_de(palabra: str, texto: str = TEXTO_IMAGEN) -> tuple[int, int, int, int]:
    palabras = texto.split()
    paso = (RENGLON[2] - RENGLON[0]) / len(palabras)
    i = palabras.index(palabra)
    return (int(RENGLON[0] + i * paso) + 2, RENGLON[1] + 4, int(RENGLON[0] + (i + 1) * paso) - 6, RENGLON[3] - 4)


@pytest.fixture(scope="module")
def analyzer():
    return PiiAnalyzer()


@pytest.fixture
def pipeline(monkeypatch, analyzer):
    """El pipeline del despacho, con el OCR simulado: el analizador de la
    peticion es el de la casilla, como en DocumentPipeline.process."""
    import app.services.pdf.scanned_redactor as sr

    estado = {"texto": TEXTO_IMAGEN}

    def motor(*_a, **_k):
        return _OcrSimulado(estado["texto"])

    monkeypatch.setattr(sr, "get_ocr_engine", motor)
    monkeypatch.setattr(sr, "get_word_ocr_engine", motor)

    class _Pipeline:
        _analyzer = analyzer

        def process(self, entrada, salida, job_id, mode, collect, disabled):
            informe = sr.redact_pdf_scanned(entrada, salida, job_id, analyzer.with_disabled_types(disabled))
            informe.entity_types_disabled = sorted(disabled)
            return informe

    fabrica = lambda: _Pipeline()   # noqa: E731
    fabrica.estado = estado
    return fabrica


def _anonimizar(pipeline, formato: str, entrada: Path, disabled=frozenset()):
    salida = entrada.with_name(f"salida_{'_'.join(sorted(disabled)) or 'todo'}{entrada.suffix}")
    informe = procesar(formato, "anonymize", entrada, salida, "t", pipeline, "strict", frozenset(disabled))
    return salida, informe


def _oscuridad(img: Image.Image, caja) -> float:
    return 255 - float(np.asarray(img.convert("L").crop(caja)).mean())


def _blanca(ruta: Path, formato: str, paginas: int = 1) -> Path:
    marcos = [Image.new("RGB", (600, 800), "white") for _ in range(paginas)]
    marcos[0].save(ruta, format=formato, save_all=paginas > 1, append_images=marcos[1:])
    return ruta


def _tapado(img: Image.Image, palabra: str, texto: str = TEXTO_IMAGEN) -> bool:
    return _oscuridad(img, _caja_de(palabra, texto)) > 100


# --- imagenes ------------------------------------------------------------------

@pytest.mark.parametrize("formato,pil", [("jpeg", "JPEG"), ("png", "PNG"), ("tiff", "TIFF")])
def test_imagen_desmarcar_el_dni_lo_deja_y_lo_demas_se_tapa(tmp_path, pipeline, formato, pil):
    entrada = _blanca(tmp_path / f"e.{formato}", pil)
    todo, _ = _anonimizar(pipeline, formato, entrada)
    sin_dni, informe = _anonimizar(pipeline, formato, entrada, {"ES_DNI"})
    with Image.open(todo) as a, Image.open(sin_dni) as b:
        assert _tapado(a, DNI) and _tapado(a, CORREO) and _tapado(a, TELEFONO)
        assert not _tapado(b, DNI), "el DNI desmarcado se ha tapado"
        assert _tapado(b, CORREO) and _tapado(b, TELEFONO), "desmarcar el DNI ha destapado otro dato"
    assert "ES_DNI" in informe.entity_types_disabled
    assert "ES_DNI" not in (informe.entities_by_type or {})


def test_imagen_copia_de_dni_se_tapa_entera_como_en_el_pdf(tmp_path, pipeline):
    """Tapar la copia entera es una decision sobre la pagina: no depende de las
    casillas, igual que en el PDF escaneado."""
    pipeline.estado["texto"] = TEXTO_CARNET
    entrada = _blanca(tmp_path / "carnet.png", "PNG")
    salida, informe = _anonimizar(pipeline, "png", entrada, {"DATE_TIME"})
    with Image.open(salida) as img:
        assert _oscuridad(img, (0, 0, 600, 800)) > 200
    assert [p.reason for p in informe.pages_redacted_by_content] == ["dni_scan"]


# --- Word ----------------------------------------------------------------------

def _word(ruta: Path, con_imagen: bool = True) -> Path:
    d = docx.Document()
    d.add_paragraph(f"Don {NOMBRE}, con DNI {DNI} y correo {CORREO}, solicita licencia.")
    d.add_paragraph("Asunto: licencia de obra menor en la calle del Mar.")
    if con_imagen:
        buffer = BytesIO()
        Image.new("RGB", (600, 800), "white").save(buffer, format="PNG")
        d.add_picture(BytesIO(buffer.getvalue()))
    d.save(ruta)
    return ruta


def _texto_word(ruta: Path) -> str:
    return "\n".join(p.text for p in docx.Document(ruta).paragraphs)


def _imagen_word(ruta: Path) -> Image.Image:
    with zipfile.ZipFile(ruta) as z:
        nombre = next(n for n in z.namelist() if n.startswith("word/media/"))
        return Image.open(BytesIO(z.read(nombre))).convert("RGB")


def test_word_desmarcar_el_dni_lo_deja_tambien_en_la_imagen(tmp_path, pipeline):
    entrada = _word(tmp_path / "e.docx")
    todo, _ = _anonimizar(pipeline, "docx", entrada)
    sin_dni, informe = _anonimizar(pipeline, "docx", entrada, {"ES_DNI"})

    assert DNI not in _texto_word(todo) and NOMBRE not in _texto_word(todo)
    texto = _texto_word(sin_dni)
    assert DNI in texto, "el DNI desmarcado se ha sustituido"
    assert NOMBRE not in texto and CORREO not in texto, "desmarcar el DNI ha dejado otro dato"
    assert "[NOMBRE]" in texto and "[CORREO]" in texto
    assert "licencia de obra menor" in texto

    assert _tapado(_imagen_word(todo), DNI)
    img = _imagen_word(sin_dni)
    assert not _tapado(img, DNI), "el DNI desmarcado se ha tapado en la imagen"
    assert _tapado(img, CORREO) and _tapado(img, TELEFONO)
    assert "ES_DNI" in informe.entity_types_disabled


# --- Excel ---------------------------------------------------------------------

def _excel(ruta: Path) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Solicitantes"
    ws.append(["Nombre", "DNI", "Correo", "Observaciones", "Inicial", "DNI copiado", "Total"])
    filas = [(NOMBRE, DNI, CORREO), ("Lucía Navarro Pons", "87654321X", "lnavarro@correo.es"),
             ("Andrés Soler Vidal", "11223344B", "asoler@correo.es")]
    for i, (nombre, dni, correo) in enumerate(filas, start=2):
        ws.append([nombre, dni, correo, f"Llamó {nombre}, con DNI {dni}, para pedir cita previa.",
                   f"=LEFT(A{i},1)", f"=B{i}", f"=COUNTA(B2:B4)"])
    wb.save(ruta)
    return ruta


def _celdas(ruta: Path) -> dict[str, object]:
    ws = openpyxl.load_workbook(ruta)["Solicitantes"]
    return {c.coordinate: c.value for fila in ws.iter_rows() for c in fila}


def test_excel_desmarcar_el_dni_lo_deja_en_columna_formulas_y_texto_libre(tmp_path, pipeline):
    entrada = _excel(tmp_path / "e.xlsx")
    todo = _celdas(_anonimizar(pipeline, "xlsx", entrada)[0])
    salida, informe = _anonimizar(pipeline, "xlsx", entrada, {"ES_DNI"})
    sin_dni = _celdas(salida)

    assert todo["B2"] == "[DNI]" and todo["F2"] == "[DNI]" and DNI not in todo["D2"]
    # La columna, la formula que la copia y el texto libre conservan el DNI.
    assert sin_dni["B2"] == DNI
    assert sin_dni["F2"] == "=B2"
    assert DNI in sin_dni["D2"]
    # Lo demas, igual que con todo marcado.
    for celda in ("A2", "C2", "E2", "G2"):
        assert sin_dni[celda] == todo[celda], celda
    assert NOMBRE not in sin_dni["D2"] and "[NOMBRE]" in sin_dni["D2"]
    assert "ES_DNI" in informe.entity_types_disabled
    assert all(c["type"] != "ES_DNI" for c in informe.replaced_columns)


def test_excel_desmarcar_el_nombre_lo_deja_y_el_resto_se_sustituye(tmp_path, pipeline):
    entrada = _excel(tmp_path / "e.xlsx")
    salida, _ = _anonimizar(pipeline, "xlsx", entrada, {"PERSON"})
    celdas = _celdas(salida)
    assert celdas["A2"] == NOMBRE and celdas["E2"] == "=LEFT(A2,1)"
    assert NOMBRE in celdas["D2"] and DNI not in celdas["D2"]
    assert celdas["B2"] == "[DNI]" and celdas["C2"] == "[CORREO]"


# --- solo metadatos ------------------------------------------------------------

@pytest.mark.parametrize("formato,crear", [
    ("png", lambda r: _blanca(r.with_suffix(".png"), "PNG")),
    ("jpeg", lambda r: _blanca(r.with_suffix(".jpg"), "JPEG")),
    ("tiff", lambda r: _blanca(r.with_suffix(".tif"), "TIFF", paginas=2)),
    ("docx", lambda r: _word(r.with_suffix(".docx"))),
    ("xlsx", lambda r: _excel(r.with_suffix(".xlsx"))),
])
def test_solo_metadatos_no_toca_el_contenido_se_marque_lo_que_se_marque(tmp_path, pipeline, formato, crear):
    entrada = crear(tmp_path / "e")
    salidas = []
    for disabled in (frozenset(), frozenset(known_type_ids()), frozenset({"ES_DNI", "PERSON"})):
        salida = tmp_path / f"s{len(salidas)}{entrada.suffix}"
        procesar(formato, "metadata_only", entrada, salida, "t", pipeline, "strict", disabled)
        salidas.append(salida)
    if formato == "docx":
        contenidos = [_texto_word(s) for s in salidas]
        assert contenidos[0] == _texto_word(entrada)
    elif formato == "xlsx":
        contenidos = [_celdas(s) for s in salidas]
        assert contenidos[0] == _celdas(entrada)
    else:
        contenidos = [np.asarray(Image.open(s).convert("RGB")).tobytes() for s in salidas]
    assert contenidos[1:] == contenidos[:1] * 2

