"""CSV: se anonimiza por columnas, como un Excel, y sale el mismo CSV.

Lo que se fija (datos inventados):
  - el CSV se lee bien: separador (punto y coma, coma, tabulador), comillas,
    comillas dobladas, saltos de linea dentro de un campo, codificacion y
    saltos de linea de Windows;
  - se sustituye sobre el texto original: solo cambian los campos con datos;
    la cabecera, los demas campos, las comillas, el separador, la
    codificacion y los saltos se quedan; un CSV sin datos sale identico;
  - las columnas se clasifican con la logica del Excel: por la cabecera, por
    el contenido ("Col3" llena de DNI), observaciones analizadas celda a
    celda, importes de personas, presupuesto de obra, casillas desmarcadas;
  - un campo que empieza por "=" se escribe tal cual (no se interpreta);
  - solo quitar metadatos devuelve el fichero tal cual y avisa;
  - por la cola, un .csv vuelve .csv; no se puede seudonimizar.
"""
import time
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.errors import OfficeTooLargeError
from app.main import app
from app.services.formatos import comprobar, reconocer
from app.services.pii.analyzer import PiiAnalyzer
from app.services.texto.tabla_csv import anonimizar_csv, leer_csv
from app.services.texto.texto import solo_metadatos_texto

PERSONAS = [
    ("Teresa Navarro Gil", "29384751C", "676 410 293", "teresa.navarro@correo.es", "Ciutat Vella"),
    ("Vicent Ferrer Alandí", "48105627T", "963 528 104", "vicent.ferrer@correo.es", "Russafa"),
    ("Marina Soler Pastor", "73021984C", "611 203 945", "marina.soler@correo.es", "Benimaclet"),
    ("Lucía Ferrando Climent", "53810247E", "654 218 937", "lucia.ferrando@correo.es", "Campanar"),
]


@pytest.fixture(scope="module")
def analyzer():
    return PiiAnalyzer()


def _anonimizar(tmp_path: Path, analyzer, datos: bytes, nombre: str = "e.csv"):
    entrada, salida = tmp_path / nombre, tmp_path / f"s_{nombre}"
    entrada.write_bytes(datos)
    informe = anonimizar_csv(entrada, salida, analyzer, "t")
    return salida.read_bytes(), informe


def _listado(separador: str = ";") -> str:
    filas = [separador.join(["Nombre y apellidos", "DNI", "Teléfono", "Correo electrónico", "Distrito"])]
    for nombre, dni, tel, correo, distrito in PERSONAS:
        filas.append(separador.join([nombre, dni, tel, correo, distrito]))
    return "\r\n".join(filas) + "\r\n"


# --- lectura -------------------------------------------------------------------------

def test_lee_comillas_comillas_dobladas_y_saltos_dentro_de_un_campo():
    texto = 'a;"b;c";"dijo ""hola""";"dos\r\nlineas"\r\n1;;3;\r\n'
    separador, filas = leer_csv(texto)
    assert separador == ";"
    assert [[c.valor for c in fila] for fila in filas] == [
        ["a", "b;c", 'dijo "hola"', "dos\r\nlineas"], ["1", "", "3", ""]]
    campo = filas[0][1]
    assert campo.entrecomillado and texto[campo.inicio:campo.fin] == '"b;c"'


@pytest.mark.parametrize("separador", [";", ",", "\t"])
def test_reconoce_el_separador(separador):
    assert leer_csv(_listado(separador))[0] == separador


def test_un_csv_se_reconoce_por_la_extension_y_el_contenido(tmp_path):
    ruta = tmp_path / "subida"
    ruta.write_bytes(_listado().encode("cp1252"))
    assert reconocer(ruta, "listado.csv") == "csv"
    assert reconocer(ruta, "LISTADO.CSV") == "csv"


# --- columnas ------------------------------------------------------------------------

def test_las_columnas_personales_llevan_el_marcador_y_lo_demas_no_cambia(tmp_path, analyzer):
    original = _listado().encode("cp1252")
    salida, informe = _anonimizar(tmp_path, analyzer, original)
    texto = salida.decode("cp1252")
    lineas = texto.split("\r\n")
    assert lineas[0] == "Nombre y apellidos;DNI;Teléfono;Correo electrónico;Distrito"
    for linea, (*_, distrito) in zip(lineas[1:], PERSONAS):
        assert linea == f"[NOMBRE];[DNI];[TELÉFONO];[CORREO];{distrito}"
    assert texto.count("\r\n") == original.decode("cp1252").count("\r\n")
    assert informe.document_type == "csv" and informe.text_encoding == "cp1252"
    columnas = {c["header"]: (c["type"], c["cells"]) for c in informe.replaced_columns}
    assert columnas == {"Nombre y apellidos": ("PERSON", 4), "DNI": ("ES_DNI", 4),
                        "Teléfono": ("PHONE_NUMBER", 4), "Correo electrónico": ("EMAIL_ADDRESS", 4)}
    assert informe.status == "completed", informe.warnings


def test_las_comillas_de_un_campo_se_conservan(tmp_path, analyzer):
    texto = ('"Apellidos, nombre";"DNI";"Distrito"\n'
             + "".join(f'"{n.split()[1]} {n.split()[2]}, {n.split()[0]}";"{d}";"{dist}"\n'
                       for n, d, _, _, dist in PERSONAS))
    salida, _ = _anonimizar(tmp_path, analyzer, texto.encode("utf-8"))
    lineas = salida.decode("utf-8").splitlines()
    assert lineas[0] == '"Apellidos, nombre";"DNI";"Distrito"'
    assert lineas[1] == '"[NOMBRE]";"[DNI]";"Ciutat Vella"'


def test_una_columna_que_no_dice_nada_se_reconoce_por_su_contenido(tmp_path, analyzer):
    texto = "Col1,Col2,Col3\n" + "".join(f"{i},Expediente abierto,{d}\n" for i, (_, d, *_r) in enumerate(PERSONAS, 1))
    salida, _ = _anonimizar(tmp_path, analyzer, texto.encode("utf-8"))
    lineas = salida.decode("utf-8").splitlines()
    assert lineas[1] == "1,Expediente abierto,[DNI]"


def test_las_observaciones_se_analizan_y_solo_cambia_el_dato(tmp_path, analyzer):
    texto = ('Expediente;Observaciones\n'
             '2026/001;"Llamar a Teresa Navarro Gil al 676 410 293; dice ""urgente"""\n'
             '2026/002;"Sin incidencias en la tramitación del expediente de vado"\n'
             '2026/003;"Pendiente de recibir la documentación del interesado"\n')
    salida, _ = _anonimizar(tmp_path, analyzer, texto.encode("utf-8"))
    lineas = salida.decode("utf-8").splitlines()
    assert "Teresa" not in lineas[1] and "676 410 293" not in lineas[1]
    assert lineas[1].endswith('"Llamar a [NOMBRE] al [TELÉFONO]; dice ""urgente"""')
    assert lineas[2].endswith('"Sin incidencias en la tramitación del expediente de vado"')


def test_una_casilla_desmarcada_deja_su_columna(tmp_path, analyzer):
    salida, _ = _anonimizar(tmp_path, analyzer.with_disabled_types({"ES_DNI"}), _listado().encode("utf-8"))
    lineas = salida.decode("utf-8").split("\r\n")
    assert lineas[1] == f"[NOMBRE];{PERSONAS[0][1]};[TELÉFONO];[CORREO];{PERSONAS[0][4]}"


def test_importes_de_personas_y_de_un_presupuesto(tmp_path, analyzer):
    ayudas = "Beneficiario;DNI;Importe\n" + "".join(f"{n};{d};1.200,50\n" for n, d, *_ in PERSONAS)
    salida, _ = _anonimizar(tmp_path, analyzer, ayudas.encode("utf-8"), "ayudas.csv")
    assert salida.decode("utf-8").splitlines()[1] == "[NOMBRE];[DNI];[IMPORTE]"
    obra = ("Partida;Descripción;Medición;Precio unitario;Importe\n"
            "01.01;Demolición de tabique;12,50;18,20;227,50\n"
            "01.02;Retirada de escombros;3,00;45,00;135,00\n"
            "01.03;Enlucido de paramentos;40,00;12,10;484,00\n")
    salida, _ = _anonimizar(tmp_path, analyzer, obra.encode("utf-8"), "obra.csv")
    assert salida.decode("utf-8").splitlines()[1] == "01.01;Demolición de tabique;[IMPORTE];[IMPORTE];[IMPORTE]"
    salida, _ = _anonimizar(tmp_path, analyzer.with_disabled_types({"BUDGET_AMOUNT"}), obra.encode("utf-8"),
                            "obra2.csv")
    assert salida == obra.encode("utf-8")


def test_un_csv_sin_personas_sale_identico(tmp_path, analyzer):
    estadistica = ("Distrito;Habitantes;Superficie (km²)\r\n"
                   "Ciutat Vella;27.000;1,69\r\nL'Eixample;42.000;1,73\r\nExtramurs;49.000;1,97\r\n")
    salida, informe = _anonimizar(tmp_path, analyzer, estadistica.encode("cp1252"))
    assert salida == estadistica.encode("cp1252")
    assert informe.entities_redacted == 0


def test_un_campo_que_empieza_por_igual_se_escribe_tal_cual(tmp_path, analyzer):
    texto = ("Nombre,DNI,Enlace\n"
             + "".join(f'{n},{d},"=HYPERLINK(""https://sede.example.es/{i}"")"\n'
                       for i, (n, d, *_r) in enumerate(PERSONAS)))
    salida, _ = _anonimizar(tmp_path, analyzer, texto.encode("utf-8"))
    linea = salida.decode("utf-8").splitlines()[1]
    assert linea.startswith("[NOMBRE],[DNI],") and '"=HYPERLINK(' in linea


def test_un_csv_demasiado_grande_se_rechaza_al_subirlo(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "max_office_cells", 10)
    ruta = tmp_path / "grande.csv"
    ruta.write_bytes(_listado().encode("utf-8"))
    with pytest.raises(OfficeTooLargeError):
        comprobar(ruta, "csv")


def test_solo_metadatos_devuelve_el_csv_tal_cual(tmp_path):
    entrada, salida = tmp_path / "e.csv", tmp_path / "s.csv"
    entrada.write_bytes(_listado().encode("cp1252"))
    informe = solo_metadatos_texto(entrada, salida, "t", formato="csv")
    assert salida.read_bytes() == entrada.read_bytes()
    assert "format_without_metadata" in {a.code for a in informe.coded_warnings}


# --- por la cola -------------------------------------------------------------------------

client = TestClient(app)


def _esperar(job_id: str, segundos: float = 120) -> dict:
    fin = time.time() + segundos
    while time.time() < fin:
        estado = client.get(f"/jobs/{job_id}").json()
        if estado["status"] in ("completed", "failed"):
            return estado
        time.sleep(0.3)
    raise AssertionError("no termino")


@pytest.mark.parametrize("tipo", ["text/csv", "application/vnd.ms-excel", ""])
def test_por_la_cola_un_csv_vuelve_csv(tipo):
    datos = _listado().encode("cp1252")
    r = client.post("/jobs", files={"file": ("listado.csv", BytesIO(datos), tipo)})
    assert r.status_code == 202, r.text
    estado = _esperar(r.json()["job_id"])
    assert estado["status"] == "completed", estado
    descarga = client.get(f"/jobs/{r.json()['job_id']}/result")
    assert ".csv" in descarga.headers["content-disposition"] and descarga.headers["content-type"].startswith("text/csv")
    assert PERSONAS[0][0] not in descarga.content.decode("cp1252")
    assert client.get(f"/jobs/{r.json()['job_id']}/report.pdf").status_code == 200


def test_un_csv_no_se_puede_seudonimizar(monkeypatch):
    monkeypatch.setattr(settings, "pseudonymization_enabled", True)
    r = client.post("/jobs?treatment=pseudonymize", data={"password": "clave-larga-de-prueba"},
                    files={"file": ("listado.csv", BytesIO(_listado().encode("utf-8")), "text/csv")})
    assert r.status_code == 415 and r.json()["detail"]["code"] == "pseudonymize_unsupported"
