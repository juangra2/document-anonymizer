"""Columnas de Excel que la clasificacion no reconocia (corpus de casos dificiles).

  - Ficha en vertical: los rotulos en la columna A y cada persona en una
    columna; se sustituye por filas y los rotulos se quedan.  Un listado con un
    titulo de grupo encima ("Datos del solicitante", X06) no es una ficha.
  - Abreviaturas y sinonimos de cabecera: «Nº documento», «F. Nac.».
  - Telefonos como numero con el 34 delante.
  - Cuenta repartida en columnas (pais, entidad, oficina, DC, cuenta): un solo
    dato.  «Entidad» sola (un organismo) no es una cuenta.
  - El numero de expediente se tacha por defecto (decision del ayuntamiento,
    22-10), tambien en una columna de Excel.
"""
import openpyxl
import pytest

from app.services.office.xlsx import anonimizar_xlsx
from app.services.pii.analyzer import PiiAnalyzer
from evaluation.corpus_excel import con_observaciones
from evaluation.corpus_excel_dificil import casos as casos_excel
from evaluation.lectura_excel_dificil import donde_esta, partes_del_zip


@pytest.fixture(scope="module")
def analyzer():
    return PiiAnalyzer()


@pytest.fixture(scope="module")
def excel(tmp_path_factory):
    carpeta = tmp_path_factory.mktemp("excel_dificil")
    return carpeta, {c["id"]: c for c in casos_excel(carpeta)}


def _sin_imagenes(entrada, salida, formato):
    salida.write_bytes(entrada.read_bytes())


def _procesar(excel, analyzer, id_):
    carpeta, casos = excel
    salida = carpeta / f"salida_{id_}.xlsx"
    informe = anonimizar_xlsx(carpeta / casos[id_]["fichero"], salida, analyzer, _sin_imagenes, "t")
    return casos[id_], openpyxl.load_workbook(salida).active, salida, informe


def _columna(ws, cabecera):
    col = next(c.column for c in ws[1] if c.value == cabecera)
    return {ws.cell(row=f, column=col).value for f in range(2, ws.max_row + 1)}


@pytest.mark.parametrize("id_", ["X06", "X08", "X10", "X11", "X12", "X13"])
def test_ningun_dato_queda(excel, analyzer, id_):
    caso, _, salida, informe = _procesar(excel, analyzer, id_)
    assert donde_esta(partes_del_zip(salida), caso["datos"]) == {}
    assert not informe.manual_review_required


def test_ficha_en_vertical(excel, analyzer):
    _, ws, _, _ = _procesar(excel, analyzer, "X08")
    assert [ws.cell(row=f, column=1).value for f in range(1, 5)] == ["Nombre", "DNI", "Teléfono", "Correo"]
    assert [ws.cell(row=f, column=2).value for f in range(1, 5)] == ["[NOMBRE]", "[DNI]", "[TELÉFONO]", "[CORREO]"]


def test_cabecera_bajo_una_fila_de_grupo(excel, analyzer):
    """"Datos del solicitante | Contacto" encima no es la cabecera."""
    _, ws, _, _ = _procesar(excel, analyzer, "X06")
    assert [c.value for c in ws[2]] == ["Nombre", "DNI", "Teléfono", "Correo electrónico"]
    assert ws["A1"].value == "Datos del solicitante" and ws["D1"].value == "Contacto"


def test_abreviaturas_y_sinonimos(excel, analyzer):
    _, ws, _, _ = _procesar(excel, analyzer, "X10")
    assert _columna(ws, "Nº documento") == {"[DNI]"}
    assert _columna(ws, "Observación") == {"Revisado"}
    _, ws, _, _ = _procesar(excel, analyzer, "X12")
    assert _columna(ws, "F. Nac.") == {"[FECHA DE NACIMIENTO]"}


def test_telefono_con_34_delante(excel, analyzer):
    _, ws, _, _ = _procesar(excel, analyzer, "X11")
    assert _columna(ws, "Contacto 2") == {"[TELÉFONO]"}


def test_cuenta_repartida_en_columnas(excel, analyzer):
    _, ws, _, _ = _procesar(excel, analyzer, "X13")
    for cabecera in ("País", "Entidad", "Oficina", "DC", "Cuenta"):
        assert _columna(ws, cabecera) == {"[IBAN]"}, cabecera
    assert [c.value for c in ws[1]] == ["Titular", "País", "Entidad", "Oficina", "DC", "Cuenta"]


def test_entidad_sola_no_es_una_cuenta(tmp_path, analyzer):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Entidad", "Convenio", "Importe"])
    for fila in (["Cruz Roja Valencia", "Atención social", 12000], ["Cáritas Diocesana", "Comedor", 8500],
                 ["Fundación Secretariado Gitano", "Empleo", 9100]):
        ws.append(fila)
    entrada, salida = tmp_path / "e.xlsx", tmp_path / "s.xlsx"
    wb.save(entrada)
    anonimizar_xlsx(entrada, salida, analyzer, _sin_imagenes, "t")
    assert _columna(openpyxl.load_workbook(salida).active, "Entidad") == {
        "Cruz Roja Valencia", "Cáritas Diocesana", "Fundación Secretariado Gitano"}


def test_columna_de_expedientes(tmp_path, analyzer):
    entrada, salida = tmp_path / "e.xlsx", tmp_path / "s.xlsx"
    con_observaciones(entrada)
    informe = anonimizar_xlsx(entrada, salida, analyzer, _sin_imagenes, "t")
    ws = openpyxl.load_workbook(salida)["Expedientes"]
    assert _columna(ws, "Expediente") == {"[Nº EXPEDIENTE]"}
    assert _columna(ws, "Tipo") == {"Licencia de obra menor"}
    assert not informe.manual_review_required


def test_expedientes_desmarcados_se_quedan(tmp_path, analyzer):
    entrada, salida = tmp_path / "e.xlsx", tmp_path / "s.xlsx"
    con_observaciones(entrada)
    anonimizar_xlsx(entrada, salida, analyzer.with_disabled_types({"CASE_NUMBER"}), _sin_imagenes, "t")
    assert all(v.startswith("EXP-2026-") for v in _columna(openpyxl.load_workbook(salida)["Expedientes"],
                                                           "Expediente"))
