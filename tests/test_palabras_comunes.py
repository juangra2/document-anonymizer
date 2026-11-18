"""Palabras comunes que el analizador tomaba por nombres y el barrido propagaba.

El caso: seudonimizar el README del proyecto dejaba "para" como [PERSONA_1]
en todo el texto, y tambien dentro de palabras ("pre[PERSONA_1]" por
"prepara").  La cabecera de tabla "| Qué | Para qué |" llega al analizador
con las celdas separadas por tabuladores; las reglas de nombres con las
listas del INE juntaban "Qué⇥Para" (los dos son apellidos del padron), el
recorte por celdas se quedaba con "Para" y el barrido de Word y texto lo
buscaba en todo el documento, sin distinguir mayusculas ni palabras.

Tres protecciones:
  1. Una palabra comun nunca es un nombre: una secuencia de nombre no cruza
     un tabulador (cada celda va por su cuenta), un candidato necesita una
     palabra que no sea comun, y una persona que se queda en una sola palabra
     comun se descarta.  Salvo con tratamiento ("Sr. Mas") o tras el rotulo
     de una persona ("Solicitante: Mas Sola"): en Valencia Mas, Sola o Ferrer
     son apellidos corrientes.
  2. El barrido no propaga un dato de una sola palabra comun ("para"), pero
     si un nombre de una palabra ("María").
  3. El barrido de Word y texto busca palabras enteras, como el del PDF.
Y dos fallos del mismo README: un identificador con guiones bajos
(NER_USE_GPU) no es una persona, y un nombre de fichero con extension
(referencia_tecnica.md) no es un numero de expediente.
"""
import re
from pathlib import Path

import pytest

from app.services.office.docx import _barrido
from app.services.pii.analyzer import PiiAnalyzer

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def analyzer():
    return PiiAnalyzer()


def _de_tipo(analyzer, texto: str, tipo: str) -> list[str]:
    return [texto[e.start:e.end] for e in analyzer.analyze(texto, mode="strict", zone_type=None, context_text=texto)
            if e.entity_type == tipo]


# --- 1. Una palabra comun no es un nombre -------------------------------------

@pytest.mark.parametrize("texto", [
    "Qué\tPara qué\tCómo comprobarlo",          # cabecera de tabla (Markdown, Word)
    "Qué Para qué Cómo comprobarlo",            # la misma fila en una linea de PDF
    "Mas\tSola",
    "Plazo\tSolo\tDías hábiles",
    "Una\tDos\tTres",
    "Dia\tHora\tLloc",
    "Sí\tNo",
    "Solo Para Uso Interno",
    "Para Qué Sirve",
    "Ante Todo",
    "Nada Que Declarar",
    "Fins Ara",
])
def test_palabras_comunes_no_son_un_nombre(analyzer, texto):
    assert _de_tipo(analyzer, texto, "PERSON") == []


@pytest.mark.parametrize("texto,nombre", [
    ("El Sr. Mas presentó la solicitud.", "Mas"),
    ("Solicitante: Mas Sola", "Mas Sola"),
    ("La Dña. Sola Ferrer firma el escrito.", "Sola Ferrer"),
    ("Joan Mas Sola ha presentado el recurso.", "Joan Mas Sola"),
    ("Comparece la Sra. Dña. Mas Sola en la oficina.", "Mas Sola"),
    ("Escrito del Sr. D. Mas Ferrer.", "Mas Ferrer"),
    ("Nombre: Sola", "Sola"),
    ("Interesado: Ferrer Mas, con DNI 12345678Z", "Ferrer Mas"),
])
def test_un_apellido_comun_con_tratamiento_o_rotulo_sigue_siendo_un_nombre(analyzer, texto, nombre):
    assert nombre in _de_tipo(analyzer, texto, "PERSON")


# --- Identificadores y ficheros -----------------------------------------------

@pytest.mark.parametrize("texto", [
    "NER_USE_GPU: ${NER_USE_GPU:-false}",
    "Abre el navegador en el puerto de HOST_PORT (8000).",
    "MAX_OFFICE_CELLS\t2000000\tCeldas como máximo de un Excel",
    "OCR_ENGINE auto auto",
])
def test_un_identificador_con_guiones_bajos_no_es_una_persona(analyzer, texto):
    assert _de_tipo(analyzer, texto, "PERSON") == []


def test_un_fichero_con_el_nombre_del_interesado_sigue_siendo_una_persona(analyzer):
    texto = "Se adjunta TFM_Teresa_Navarro.pdf con la memoria."
    assert "TFM_Teresa_Navarro.pdf" in _de_tipo(analyzer, texto, "PERSON")


@pytest.mark.parametrize("texto", [
    "Ver la [referencia técnica](docs/referencia_tecnica.md).",
    "Detalle en docs/referencia_tecnica.md#despliegue",
    "Adjunto: Ref_proyecto_final.pdf",
])
def test_un_nombre_de_fichero_no_es_un_numero_de_expediente(analyzer, texto):
    assert _de_tipo(analyzer, texto, "CASE_NUMBER") == []


def test_un_expediente_de_verdad_sigue_detectandose(analyzer):
    assert _de_tipo(analyzer, "Expediente AMO/2019/0892/00 del Servicio.", "CASE_NUMBER")


# --- 2 y 3. El barrido ---------------------------------------------------------

def _barridos(texto: str, valores: dict[str, str]) -> list[str]:
    from types import SimpleNamespace
    return [texto[i:j] for i, j, _ in _barrido(SimpleNamespace(texto=texto), valores, [])]


def test_el_barrido_no_propaga_una_palabra_comun():
    texto = "Para qué sirve: para todo. Sola, la casa."
    assert _barridos(texto, {"Para": "PERSON", "Sola": "PERSON"}) == []


def test_el_barrido_propaga_un_nombre_de_una_palabra():
    texto = "María firmó. Después, MARÍA volvió y maría se fue."
    assert _barridos(texto, {"María": "PERSON"}) == ["María", "MARÍA", "maría"]


def test_el_barrido_busca_palabras_enteras():
    texto = "Teresa y Teresalandia; Navarro Gilabert; Navarro Gil. Exp 2028013/2024 y 28013."
    assert _barridos(texto, {"Teresa": "PERSON", "Navarro Gil": "PERSON", "28013": "POSTAL_CODE"}) == [
        "Teresa", "Navarro Gil", "28013"]


def test_el_barrido_no_corta_palabras_ni_numeros_pero_si_separa_letras_de_cifras():
    texto = "DNI12345678Z y 12345678Z"
    assert _barridos(texto, {"12345678Z": "ES_DNI"}) == ["12345678Z", "12345678Z"]


# --- El caso del README --------------------------------------------------------

_PARA = re.compile(r"para", re.IGNORECASE)


def _procesar(tmp_path, analyzer, tratamiento: str, nombre: str, datos: bytes) -> str:
    from app.services.formatos import procesar
    from app.services.seudonimo.tabla import Cifrador

    entrada, salida = tmp_path / nombre, tmp_path / f"s_{nombre}"
    entrada.write_bytes(datos)
    pipeline = type("P", (), {"_analyzer": analyzer})
    extra = {}
    if tratamiento == "pseudonymize":
        extra = {"cifrador": Cifrador("una clave larga de prueba"), "tabla": tmp_path / "t.tabla"}
    formato = nombre.rsplit(".", 1)[1]
    procesar(formato, tratamiento, entrada, salida, "t", pipeline, "strict", frozenset(), **extra)
    if formato == "docx":
        from app.services.office.paquete import texto_de_todas_las_partes
        return texto_de_todas_las_partes(salida)
    return salida.read_text(encoding="utf-8")


@pytest.mark.parametrize("tratamiento", ["anonymize", "pseudonymize"])
@pytest.mark.parametrize("nombre", ["README.md", "README.txt"])
def test_el_readme_conserva_cada_para(tmp_path, analyzer, tratamiento, nombre):
    """El README del proyecto: cada "para", suelto o dentro de una palabra,
    sigue igual al anonimizarlo y al seudonimizarlo."""
    original = (RAIZ / "README.md").read_bytes()
    salida = _procesar(tmp_path, analyzer, tratamiento, nombre, original)
    assert len(_PARA.findall(salida)) == len(_PARA.findall(original.decode("utf-8")))
    assert "pre[" not in salida and "OFFICE_[" not in salida


def test_una_tabla_de_word_no_hace_un_nombre_de_sus_celdas(tmp_path, analyzer):
    import docx

    doc = docx.Document()
    doc.add_paragraph("Qué tiene que tener el servidor")
    tabla = doc.add_table(rows=2, cols=3)
    for celda, texto in zip(tabla.rows[0].cells, ["Qué", "Para qué", "Cómo comprobarlo"]):
        celda.text = texto
    for celda, texto in zip(tabla.rows[1].cells, ["Docker", "Para arrancar el servicio", "docker --version"]):
        celda.text = texto
    doc.add_paragraph("Esto prepara la imagen para el despliegue. Para pararlo, docker compose down.")
    entrada = tmp_path / "e.docx"
    doc.save(entrada)
    salida = _procesar(tmp_path, analyzer, "anonymize", "tabla.docx", entrada.read_bytes())
    assert "prepara la imagen para el despliegue. Para pararlo" in salida
    assert "[" not in salida.split("Esto prepara")[0].split("servidor", 1)[1]
