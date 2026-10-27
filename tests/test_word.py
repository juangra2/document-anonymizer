"""Documentos de Word (.docx): el dato se sustituye por su tipo, el resultado es un Word.

Lo que se fija, sobre un documento inventado que esconde un dato en cada sitio
donde un Word guarda texto (evaluation/corpus_word.documento_cargado):
  - ningun dato queda en NINGUNA parte del fichero de salida (el verificador
    busca en el XML de cada parte, sus atributos y sus relaciones);
  - el fichero se vuelve a abrir; el texto legitimo y el formato se conservan;
  - comentarios, texto borrado, texto oculto, propiedades, miniatura, datos
    personalizados, texto alternativo, enlaces y objetos incrustados no quedan;
  - la imagen incrustada pasa por el camino de imagenes (aqui simulado);
  - solo metadatos: el texto visible se queda tal cual;
  - un .doc antiguo se rechaza con un mensaje claro.
"""
import time
import zipfile
from io import BytesIO
from pathlib import Path

import docx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.services.office.docx import anonimizar_docx, solo_metadatos_docx
from app.services.office.paquete import texto_de_todas_las_partes, verificar
from app.services.pii.analyzer import PiiAnalyzer
from evaluation.corpus_word import documento_cargado


@pytest.fixture(scope="module")
def analyzer():
    return PiiAnalyzer()


def _imagen_en_negro(entrada: Path, salida: Path, formato: str):
    """Procesador de imagen simulado: la tapa entera (una copia de DNI)."""
    with Image.open(entrada) as img:
        Image.new("RGB", img.size, "black").save(salida, format={"jpeg": "JPEG", "png": "PNG"}[formato])
    return None


@pytest.fixture(scope="module")
def cargado(tmp_path_factory, analyzer):
    tmp = tmp_path_factory.mktemp("word")
    entrada = tmp / "cargado.docx"
    caso = documento_cargado(entrada)
    salida = tmp / "salida.docx"
    informe = anonimizar_docx(entrada, salida, analyzer, _imagen_en_negro, "t")
    return caso, entrada, salida, informe


def _visible(ruta: Path) -> str:
    d = docx.Document(ruta)
    partes = [p.text for p in d.paragraphs]
    for t in d.tables:
        partes += [c.text for fila in t.rows for c in fila.cells]
    for s in d.sections:
        partes += [p.text for p in s.header.paragraphs] + [p.text for p in s.footer.paragraphs]
    return "\n".join(partes)


def test_la_entrada_lleva_los_datos_en_todas_partes(cargado):
    caso, entrada, _, _ = cargado
    todo = texto_de_todas_las_partes(entrada)
    for d in caso.datos:
        assert d["text"].lower() in todo.lower(), d


def test_ningun_dato_queda_en_ninguna_parte_del_fichero(cargado):
    caso, _, salida, _ = cargado
    assert verificar(salida, [d["text"] for d in caso.datos]) == []


def test_el_fichero_se_abre_y_conserva_el_texto_legitimo(cargado):
    caso, _, salida, _ = cargado
    assert zipfile.ZipFile(salida).testzip() is None
    visible = _visible(salida)
    for texto in caso.legitimos:
        assert texto in visible, texto


def test_el_dato_se_sustituye_por_su_tipo_y_conserva_el_formato(cargado):
    _, _, salida, _ = cargado
    d = docx.Document(salida)
    visible = _visible(salida)
    assert "[NOMBRE]" in visible and "[DNI]" in visible and "[TELÉFONO]" in visible
    assert "[DIRECCIÓN]" in visible and "[IBAN]" in visible
    nombre = next(p for p in d.paragraphs if p.text.startswith("Nombre y apellidos:"))
    assert any(r.bold and "[NOMBRE]" in r.text for r in nombre.runs), "el dato en negrita sigue en negrita"


def test_fuera_lo_que_no_se_ve(cargado):
    _, _, salida, _ = cargado
    with zipfile.ZipFile(salida) as z:
        nombres = set(z.namelist())
        documento = z.read("word/document.xml").decode("utf-8")
        rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
    assert not any(n.startswith("word/comments") for n in nombres), "comentarios"
    assert "docProps/thumbnail.jpeg" not in nombres, "miniatura"
    assert "docProps/custom.xml" not in nombres and not any(n.startswith("customXml/") for n in nombres)
    assert not any(n.startswith("word/embeddings/") for n in nombres), "objeto incrustado"
    for marca in ("w:del ", "w:delText", "w:ins ", "w:vanish", "w:commentReference", "w:hyperlink",
                  "o:OLEObject", 'descr="', "w:instrText"):
        assert marca not in documento, marca
    assert "mailto:" not in rels and 'TargetMode="External"' not in rels


def test_el_informe_cuenta_lo_que_se_quito(cargado):
    _, _, _, informe = cargado
    assert informe.document_type == "docx"
    for clave in ("document_info", "comments", "tracked_changes", "hidden_text", "links", "thumbnails",
                  "custom_xml", "alt_text", "embedded_objects"):
        assert informe.metadata_removed.get(clave, 0) > 0, clave
    assert informe.entities_by_type.get("PERSON", 0) > 0 and informe.entities_by_type.get("ES_DNI", 0) > 0
    assert informe.detected_entities == []


def test_la_imagen_incrustada_pasa_por_el_camino_de_imagenes(cargado):
    _, _, salida, _ = cargado
    with zipfile.ZipFile(salida) as z:
        imagenes = [n for n in z.namelist() if n.startswith("word/media/")]
        assert imagenes
        img = Image.open(BytesIO(z.read(imagenes[0]))).convert("L")
    assert max(img.getdata()) < 30, "la copia del DNI tiene que salir tapada"


def test_solo_metadatos_deja_el_texto_y_quita_lo_demas(tmp_path):
    entrada = tmp_path / "e.docx"
    caso = documento_cargado(entrada)
    salida = tmp_path / "s.docx"
    informe = solo_metadatos_docx(entrada, salida, "t")
    visible = _visible(salida)
    assert caso.datos[0]["text"] in visible, "el contenido no se toca"
    assert informe.treatment == "metadata_only"
    with zipfile.ZipFile(salida) as z:
        assert not any(n.startswith("word/comments") for n in z.namelist())
        core = z.read("docProps/core.xml").decode("utf-8")
    assert caso.datos[0]["text"] not in core


def test_un_documento_sin_datos_no_cambia_su_texto(tmp_path, analyzer):
    from evaluation.corpus_word import sin_datos

    entrada = tmp_path / "e.docx"
    sin_datos(entrada, 0)
    salida = tmp_path / "s.docx"
    anonimizar_docx(entrada, salida, analyzer, _imagen_en_negro, "t")
    assert _visible(salida) == _visible(entrada)


# --- por la cola ---------------------------------------------------------------

client = TestClient(app)
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _esperar(job_id):
    fin = time.time() + 300
    while time.time() < fin:
        estado = client.get(f"/jobs/{job_id}").json()
        if estado["status"] in ("completed", "failed"):
            return estado
        time.sleep(0.3)
    raise AssertionError("no termino")


@pytest.mark.parametrize("tratamiento", ["anonymize", "metadata_only"])
def test_por_la_cola_un_word_vuelve_word(tmp_path, tratamiento):
    entrada = tmp_path / "e.docx"
    caso = documento_cargado(entrada)
    r = client.post(f"/jobs?treatment={tratamiento}",
                    files={"file": ("expediente.docx", BytesIO(entrada.read_bytes()), _DOCX)})
    assert r.status_code == 202, r.text
    estado = _esperar(r.json()["job_id"])
    assert estado["status"] == "completed", estado
    descarga = client.get(f"/jobs/{r.json()['job_id']}/result")
    assert descarga.headers["content-type"] == _DOCX
    assert ".docx" in descarga.headers["content-disposition"]
    salida = tmp_path / "s.docx"
    salida.write_bytes(descarga.content)
    docx.Document(salida)
    if tratamiento == "anonymize":
        assert verificar(salida, [d["text"] for d in caso.datos]) == []
    assert client.get(f"/jobs/{r.json()['job_id']}/report.pdf").status_code == 200


def test_un_doc_antiguo_se_rechaza_con_un_mensaje_claro():
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504
    r = client.post("/jobs", files={"file": ("antiguo.doc", BytesIO(ole), "application/msword")})
    assert r.status_code == 415
    assert r.json()["detail"]["code"] == "legacy_office"


def test_un_word_con_macros_se_rechaza(tmp_path):
    entrada = tmp_path / "e.docx"
    documento_cargado(entrada)
    with zipfile.ZipFile(entrada) as z:
        partes = {n: z.read(n) for n in z.namelist()}
    partes["[Content_Types].xml"] = partes["[Content_Types].xml"].replace(
        b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        b"application/vnd.ms-word.document.macroEnabled.main+xml")
    macros = tmp_path / "m.docm"
    with zipfile.ZipFile(macros, "w") as z:
        for n, d in partes.items():
            z.writestr(n, d)
    r = client.post("/jobs", files={"file": ("m.docm", BytesIO(macros.read_bytes()), _DOCX)})
    assert r.status_code == 415
    assert r.json()["detail"]["code"] == "office_macros"


def test_la_etiqueta_que_el_analizador_mete_en_el_dato_se_queda(cargado):
    """El analizador devuelve "de contacto: 611 203 945" como telefono; en Word
    la etiqueta no se sustituye (el dato empieza tras los dos puntos)."""
    _, _, salida, _ = cargado
    assert "Teléfono de contacto: [TELÉFONO]" in _visible(salida)


@pytest.mark.parametrize("hacer,legitimos", [
    ("formulario_bilingue", ["DNI / NIF"]),
    ("acta", ["Presidenta", "Secretario", "Vocal"]),
])
def test_un_dato_no_cruza_de_una_celda_a_otra(tmp_path, analyzer, hacer, legitimos):
    """El analizador une la celda de la etiqueta (o la del cargo) con la del
    dato; la sustitucion se queda dentro de la celda del dato."""
    import evaluation.corpus_word as cw

    entrada = tmp_path / "e.docx"
    caso = getattr(cw, hacer)(entrada, cw.PERSONAS[1]) if hacer != "acta" else cw.acta(entrada)
    salida = tmp_path / "s.docx"
    anonimizar_docx(entrada, salida, analyzer, _imagen_en_negro, "t")
    visible = _visible(salida)
    for texto in legitimos:
        assert texto in visible, texto
    assert verificar(salida, [d["text"] for d in caso.datos]) == []
