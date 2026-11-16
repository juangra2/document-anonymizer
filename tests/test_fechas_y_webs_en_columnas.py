"""Fechas y direcciones web en columnas que no son personales (Excel y CSV).

Decision del 16-11: se sustituyen cuando su casilla esta marcada, como en
PDF, Word y texto, donde ya se tachan todas; si el usuario no las quiere
tachar, desmarca la casilla.  Lo que se fija:
  - Excel: una fecha guardada como fecha (numero con formato de fecha) y una
    escrita como texto pasan a "[FECHA]"; una direccion web, a "[WEB]"; un
    numero que no es fecha (un importe de una estadistica) no cambia;
  - CSV: lo mismo, con las fechas y las direcciones escritas como texto;
  - con la casilla de FECHA (o de WEB) desmarcada, se quedan;
  - una columna de un tipo desmarcado ("Fecha de nacimiento") no se
    sustituye por la puerta de atras como "[FECHA]".
"""
import datetime as dt

import openpyxl
import pytest

from app.services.office.xlsx import anonimizar_xlsx
from app.services.pii.analyzer import PiiAnalyzer
from app.services.texto.tabla_csv import anonimizar_csv


@pytest.fixture(scope="module")
def analyzer():
    return PiiAnalyzer()


def _sin_imagenes(entrada, salida, formato):
    salida.write_bytes(entrada.read_bytes())


def _excel(ruta):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Registro"
    ws.append(["Expediente", "Fecha de entrada", "Fecha (texto)", "Enlace", "Unidades", "Estado"])
    for i in range(4):
        ws.append([f"2026/LIC/{10 + i}", dt.date(2026, 3, i + 1), f"0{i + 1}/03/2026",
                   f"https://sede.example.es/consulta/{i}", 3 + i, "Abierto"])
    ws["B2"].number_format = "dd/mm/yyyy"
    for f in range(2, 6):
        ws.cell(f, 2).number_format = "dd/mm/yyyy"
    wb.save(ruta)


def _anonimizar_excel(tmp_path, analyzer, nombre="s.xlsx"):
    entrada, salida = tmp_path / "e.xlsx", tmp_path / nombre
    _excel(entrada)
    anonimizar_xlsx(entrada, salida, analyzer, _sin_imagenes, "t")
    return openpyxl.load_workbook(salida).active


def test_excel_fechas_y_webs_de_columnas_no_personales(tmp_path, analyzer):
    ws = _anonimizar_excel(tmp_path, analyzer)
    assert [c.value for c in ws[1]] == ["Expediente", "Fecha de entrada", "Fecha (texto)", "Enlace", "Unidades",
                                        "Estado"]
    for f in range(2, 6):
        assert ws.cell(f, 2).value == "[FECHA]"
        assert ws.cell(f, 3).value == "[FECHA]"
        assert ws.cell(f, 4).value == "[WEB]"
        assert ws.cell(f, 5).value == f + 1          # un numero que no es fecha no cambia
        assert ws.cell(f, 6).value == "Abierto"


def test_excel_con_las_casillas_desmarcadas_se_quedan(tmp_path, analyzer):
    ws = _anonimizar_excel(tmp_path, analyzer.with_disabled_types({"DATE_TIME", "URL"}))
    assert isinstance(ws.cell(2, 2).value, dt.datetime)
    assert ws.cell(2, 3).value == "01/03/2026"
    assert ws.cell(2, 4).value == "https://sede.example.es/consulta/0"


def test_excel_una_columna_de_un_tipo_desmarcado_no_pasa_a_fecha(tmp_path, analyzer):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Nombre", "Fecha de nacimiento", "Fecha de alta"])
    for i, n in enumerate(["Teresa Navarro Gil", "Vicent Ferrer Alandí", "Marina Soler Pastor"]):
        ws.append([n, f"1{i}/04/1979", f"0{i + 1}/09/2025"])
    entrada, salida = tmp_path / "n.xlsx", tmp_path / "ns.xlsx"
    wb.save(entrada)
    anonimizar_xlsx(entrada, salida, analyzer.with_disabled_types({"DATE_OF_BIRTH"}), _sin_imagenes, "t")
    hoja = openpyxl.load_workbook(salida).active
    assert hoja["A2"].value == "[NOMBRE]"
    assert hoja["B2"].value == "10/04/1979"        # su casilla esta desmarcada
    assert hoja["C2"].value == "[FECHA]"


def test_csv_fechas_y_webs_de_columnas_no_personales(tmp_path, analyzer):
    texto = ("Expediente;Fecha de entrada;Enlace;Unidades;Estado\n"
             + "".join(f"2026/LIC/{10 + i};0{i + 1}/03/2026;https://sede.example.es/c/{i};{3 + i};Abierto\n"
                       for i in range(4)))
    entrada, salida = tmp_path / "e.csv", tmp_path / "s.csv"
    entrada.write_text(texto, encoding="utf-8")
    anonimizar_csv(entrada, salida, analyzer, "t")
    lineas = salida.read_text(encoding="utf-8").splitlines()
    assert lineas[0] == "Expediente;Fecha de entrada;Enlace;Unidades;Estado"
    assert lineas[1] == "[Nº EXPEDIENTE];[FECHA];[WEB];3;Abierto"
    anonimizar_csv(entrada, salida, analyzer.with_disabled_types({"DATE_TIME", "URL", "CASE_NUMBER"}), "t")
    assert salida.read_text(encoding="utf-8") == texto


@pytest.mark.parametrize("texto,esperado", [
    ('=HYPERLINK("https://sede.example.es/exp/0")', '=HYPERLINK("[WEB]")'),
    ("Ver (https://sede.example.es/exp/1).", "Ver ([WEB])."),
    ("https://sede.example.es/consulta?id=(3)", "[WEB]"),
])
def test_una_direccion_web_no_se_lleva_las_comillas_ni_el_parentesis(analyzer, texto, esperado):
    from collections import Counter

    from app.services.office.xlsx import fechas_y_webs

    assert fechas_y_webs(texto, analyzer, "strict", Counter()) == esperado
