"""Hojas de calculo (.xlsx): la columna con datos personales conserva su
cabecera y sus celdas llevan el marcador del tipo, como en Word.

Lo que se fija, sobre libros inventados (evaluation/corpus_excel.py):
  - ningun dato queda en NINGUNA parte del fichero (celdas, cadenas
    compartidas, comentarios, hojas ocultas, formulas y sus valores guardados,
    nombres definidos, tablas dinamicas, propiedades);
  - la cabecera se reconoce aunque haya un titulo encima, y la columna se
    clasifica por el nombre de la cabecera o, si no dice nada, por su contenido;
  - el texto libre se analiza celda a celda: solo se sustituye el dato;
  - un libro sin datos personales no cambia;
  - importes con la regla del PDF: los de personas pasan a [IMPORTE] (la suma
    y la media se quedan con su valor; el maximo y el minimo no); un
    presupuesto de obra solo se toca con su casilla marcada; una estadistica
    sin personas, nunca;
  - el fichero se vuelve a abrir; por la cola, un Excel vuelve Excel.
"""
import time
import zipfile
from io import BytesIO
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

import evaluation.corpus_excel as ce
from app.main import app
from app.services.office.paquete import verificar
from app.services.office.xlsx import anonimizar_xlsx, solo_metadatos_xlsx
from app.services.pii.analyzer import PiiAnalyzer
from app.services.pii.entity_catalog import default_disabled_ids


@pytest.fixture(scope="module")
def analyzer():
    return PiiAnalyzer()


@pytest.fixture(scope="module")
def por_defecto(analyzer):
    """Las casillas como vienen en la pantalla: la de presupuesto, desmarcada."""
    return analyzer.with_disabled_types(default_disabled_ids())


def _sin_imagenes(entrada, salida, formato):
    salida.write_bytes(entrada.read_bytes())


def _procesar(tmp_path, hacer, analyzer):
    entrada = tmp_path / "e.xlsx"
    etiqueta = hacer(entrada)
    salida = tmp_path / "s.xlsx"
    informe = anonimizar_xlsx(entrada, salida, analyzer, _sin_imagenes, "t")
    return etiqueta, entrada, salida, informe


def _columna(ws, cabecera: str, fila_cabecera: int = 1) -> list:
    for celda in ws[fila_cabecera]:
        if celda.value == cabecera:
            return [ws.cell(row=r, column=celda.column).value for r in range(fila_cabecera + 1, ws.max_row + 1)]
    raise AssertionError(f"sin cabecera {cabecera}")


@pytest.mark.parametrize("hacer", [ce.listado, ce.valenciano_con_titulo, ce.sin_cabecera_clara,
                                   ce.con_observaciones, ce.cargado, ce.con_tabla_dinamica])
def test_ningun_dato_queda_en_ninguna_parte(tmp_path, analyzer, hacer):
    etiqueta, entrada, salida, _ = _procesar(tmp_path, hacer, analyzer)
    assert verificar(entrada, [d["text"] for d in etiqueta["datos"]]), "la entrada lleva los datos"
    assert verificar(salida, [d["text"] for d in etiqueta["datos"]]) == []
    assert zipfile.ZipFile(salida).testzip() is None
    openpyxl.load_workbook(salida)


def test_la_columna_conserva_su_cabecera_y_lleva_el_marcador(tmp_path, analyzer):
    _, _, salida, informe = _procesar(tmp_path, ce.listado, analyzer)
    ws = openpyxl.load_workbook(salida)["Solicitantes"]
    assert [c.value for c in ws[1]][:4] == ["Nº", "Nombre", "Apellidos", "DNI"]
    assert set(_columna(ws, "DNI")) == {"[DNI]"}
    assert set(_columna(ws, "Correo electrónico")) == {"[CORREO]"}
    assert set(_columna(ws, "Fecha de nacimiento")) == {"[FECHA DE NACIMIENTO]"}
    assert set(_columna(ws, "Estado")) == {"Concedida", "Pendiente"}, "lo que no es personal se queda"
    assert set(_columna(ws, "Importe concedido")) == {"[IMPORTE]"}, "importe ligado a personas"
    assert {c["header"] for c in informe.replaced_columns} >= {"Nombre", "DNI", "Teléfono"}


def test_la_cabecera_bajo_un_titulo_y_en_valenciano(tmp_path, analyzer):
    _, _, salida, _ = _procesar(tmp_path, ce.valenciano_con_titulo, analyzer)
    ws = openpyxl.load_workbook(salida)["Inscrits"]
    assert ws["A1"].value == "CURS DE VALENCIÀ PER A ADULTS · TARDOR 2026"
    assert set(_columna(ws, "Nom i cognoms", 3)) == {"[NOMBRE]"}
    assert set(_columna(ws, "Telèfon", 3)) == {"[TELÉFONO]"}, "tambien los telefonos guardados como numero"
    assert set(_columna(ws, "Nivell", 3)) == {"A2", "B1", "C1"}


def test_sin_cabecera_que_lo_diga_decide_el_contenido(tmp_path, analyzer):
    _, _, salida, _ = _procesar(tmp_path, ce.sin_cabecera_clara, analyzer)
    ws = openpyxl.load_workbook(salida)["Hoja1"]
    assert set(_columna(ws, "Col2")) == {"[NOMBRE]"}
    assert set(_columna(ws, "Col3")) == {"[DNI]"}
    assert set(_columna(ws, "Col5")) == {"[IBAN]"}
    assert _columna(ws, "Col1")[0] == "REF-1000"


def test_el_texto_libre_se_analiza_celda_a_celda(tmp_path, analyzer):
    _, _, salida, _ = _procesar(tmp_path, ce.con_observaciones, analyzer)
    ws = openpyxl.load_workbook(salida)["Expedientes"]
    obs = _columna(ws, "Observaciones")
    assert obs[0] == "Documentación completa, pendiente de informe técnico."
    assert obs[1].startswith("Llamar a [NOMBRE] al [TELÉFONO]"), obs[1]
    assert obs[1].endswith("para completar la documentación.")


def test_lo_que_el_excel_guarda_aparte(tmp_path, analyzer):
    _, _, salida, informe = _procesar(tmp_path, ce.cargado, analyzer)
    with zipfile.ZipFile(salida) as z:
        nombres = set(z.namelist())
        libro = z.read("xl/workbook.xml").decode("utf-8")
    assert not any("comments" in n or "threadedComment" in n or "vmlDrawing" in n for n in nombres)
    assert "Responsable" not in libro, "el nombre definido con un valor"
    wb = openpyxl.load_workbook(salida)
    assert set(_columna(wb["Datos internos"], "Responsable")) == {"[NOMBRE]"}, "la hoja oculta tambien"
    assert wb["Resumen"]["B2"].value == "[NOMBRE]", "la formula que copia un nombre"
    assert wb["Resumen"]["B3"].value.startswith("=COUNTA"), "la formula que no copia datos se queda"
    for clave in ("comments", "document_info", "defined_names"):
        assert informe.metadata_removed.get(clave, 0) > 0, clave


def test_la_tabla_dinamica_se_elimina(tmp_path, analyzer):
    etiqueta, _, salida, informe = _procesar(tmp_path, ce.con_tabla_dinamica, analyzer)
    with zipfile.ZipFile(salida) as z:
        assert not any(n.startswith(("xl/pivotTables/", "xl/pivotCache/")) for n in z.namelist())
        assert "pivotCache" not in z.read("xl/workbook.xml").decode("utf-8")
    ws = openpyxl.load_workbook(salida)["Resumen dinámico"]
    assert ws["A1"].value == "[TABLA DINÁMICA ELIMINADA]"
    assert ws["A2"].value is None and ws["B2"].value is None
    assert informe.metadata_removed.get("pivot_tables") == 1


def _filas(ruta: Path) -> list:
    return [[c.value for c in f] for f in openpyxl.load_workbook(ruta).active.iter_rows()]


@pytest.mark.parametrize("hacer, desmarcados", [(ce.presupuesto, {"BUDGET_AMOUNT"}),
                                                (ce.estadistica, set())])
def test_un_libro_sin_datos_no_cambia(tmp_path, por_defecto, hacer, desmarcados):
    """Un presupuesto con su casilla desmarcada; una estadistica sin personas."""
    analyzer = por_defecto.with_disabled_types(set(por_defecto.disabled_types) | desmarcados)
    _, entrada, salida, informe = _procesar(tmp_path, hacer, analyzer)
    assert _filas(entrada) == _filas(salida)
    assert not informe.replaced_columns


def test_una_estadistica_sin_personas_no_cambia_ni_con_todo_marcado(tmp_path, analyzer):
    _, entrada, salida, _ = _procesar(tmp_path, ce.estadistica, analyzer)
    assert _filas(entrada) == _filas(salida)


def test_sin_tocar_nada_el_presupuesto_se_sustituye(tmp_path, por_defecto):
    _, _, salida, informe = _procesar(tmp_path, ce.presupuesto_marcado, por_defecto)
    ws = openpyxl.load_workbook(salida)["Presupuesto"]
    for cabecera in ("Unidades", "Precio unitario", "Importe"):
        assert set(v for v in _columna(ws, cabecera) if v is not None) == {"[IMPORTE]"}, cabecera
    assert _columna(ws, "Descripción")[0] == "Demolición de tabiquería"
    assert _columna(ws, "Partida")[0] == "1.1"
    assert {c["type"] for c in informe.replaced_columns} == {"BUDGET_AMOUNT"}


def test_importes_de_personas_la_suma_y_la_media_se_quedan(tmp_path, analyzer):
    etiqueta, _, salida, informe = _procesar(tmp_path, ce.ayudas_con_totales, analyzer)
    wb = openpyxl.load_workbook(salida)
    assert set(_columna(wb["Ayudas"], "Cuantía (€)")) == {"[IMPORTE]"}
    assert set(_columna(wb["Ayudas"], "Concepto")) == {"Ayuda al alquiler"}
    for celda, esperado in etiqueta["celdas"]["Resumen"].items():
        assert wb["Resumen"][celda].value == esperado, celda
    assert any(c["type"] == "MONEY" for c in informe.replaced_columns)


def test_desmarcado_el_importe_se_queda_con_sus_formulas(tmp_path, analyzer):
    _, entrada, salida, _ = _procesar(tmp_path, ce.ayudas_con_totales,
                                      analyzer.with_disabled_types({"MONEY"}))
    a, b = openpyxl.load_workbook(entrada), openpyxl.load_workbook(salida)
    assert _columna(b["Ayudas"], "Cuantía (€)") == _columna(a["Ayudas"], "Cuantía (€)")
    assert [c.value for c in b["Resumen"]["B"]] == [c.value for c in a["Resumen"]["B"]]
    assert set(_columna(b["Ayudas"], "NIF")) == {"[DNI]"}


def test_solo_metadatos_deja_las_celdas(tmp_path):
    entrada = tmp_path / "e.xlsx"
    etiqueta = ce.cargado(entrada)
    salida = tmp_path / "s.xlsx"
    informe = solo_metadatos_xlsx(entrada, salida, "t")
    ws = openpyxl.load_workbook(salida)["Beneficiarios"]
    assert ws["B2"].value == etiqueta["datos"][1]["text"], "el contenido no se toca"
    assert informe.treatment == "metadata_only"
    with zipfile.ZipFile(salida) as z:
        assert not any("comments" in n for n in z.namelist())


# --- por la cola -----------------------------------------------------------------

client = TestClient(app)
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _esperar(job_id):
    fin = time.time() + 300
    while time.time() < fin:
        estado = client.get(f"/jobs/{job_id}").json()
        if estado["status"] in ("completed", "failed"):
            return estado
        time.sleep(0.3)
    raise AssertionError("no termino")


@pytest.mark.parametrize("tratamiento", ["anonymize", "metadata_only"])
def test_por_la_cola_un_excel_vuelve_excel(tmp_path, tratamiento):
    entrada = tmp_path / "e.xlsx"
    etiqueta = ce.listado(entrada)
    r = client.post(f"/jobs?treatment={tratamiento}",
                    files={"file": ("listado.xlsx", BytesIO(entrada.read_bytes()), _XLSX)})
    assert r.status_code == 202, r.text
    estado = _esperar(r.json()["job_id"])
    assert estado["status"] == "completed", estado
    descarga = client.get(f"/jobs/{r.json()['job_id']}/result")
    assert descarga.headers["content-type"] == _XLSX
    assert ".xlsx" in descarga.headers["content-disposition"]
    salida = tmp_path / "s.xlsx"
    salida.write_bytes(descarga.content)
    openpyxl.load_workbook(salida)
    if tratamiento == "anonymize":
        assert verificar(salida, [d["text"] for d in etiqueta["datos"]]) == []
    assert client.get(f"/jobs/{r.json()['job_id']}/report.pdf").status_code == 200


def test_la_cabecera_es_la_primera_fila_de_rotulos(tmp_path, analyzer):
    """"Concepto | Valor" es la cabecera aunque la fila de abajo diga
    "Primer beneficiario": una palabra de cabecera en los datos no la mueve."""
    _, _, salida, _ = _procesar(tmp_path, ce.cargado, analyzer)
    ws = openpyxl.load_workbook(salida)["Resumen"]
    assert ws["A2"].value == "Primer beneficiario" and ws["A3"].value == "Total de beneficiarios"
