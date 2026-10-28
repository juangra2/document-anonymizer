"""Un solo valor por defecto, el del catalogo, en la pantalla y en la API.

La API parte del catalogo (`default_enabled`), igual que la pantalla;
`disabled_types` apaga tipos y `enabled_types` enciende los que vienen apagados.
La evaluacion pide todos los tipos de forma explicita.

Decision del ayuntamiento (22-10): el numero de expediente se tacha por defecto,
en todos sus formatos, tambien el municipal; si se desmarca, no se tacha.  El
unico tipo apagado del catalogo son los importes de presupuesto de obra: solo se
tachan si se marca su casilla.  El mecanismo de `enabled_types` con el
expediente se prueba simulandolo apagado.
"""
import json
import zipfile
from io import BytesIO

import fitz
import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.pii.entity_catalog import entity_catalog, known_type_ids

client = TestClient(app)
APAGADOS_DEL_CATALOGO = sorted(i.id for i in entity_catalog() if not i.default_enabled)


def _pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Expediente: E-03001-2022-000074-00", fontsize=11)
    page.insert_text((72, 130), "DNI: 12345678Z", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def _anonimizar(query: str = ""):
    return client.post(
        f"/anonymize?output=zip{query}",
        files={"file": ("in.pdf", BytesIO(_pdf()), "application/pdf")},
    )


def _desactivados_y_texto(response) -> tuple[list[str], str]:
    with zipfile.ZipFile(BytesIO(response.content)) as zf:
        informe = json.loads(zf.read("report.json"))
        pdf = next(n for n in zf.namelist() if n.endswith(".pdf"))
        texto = " ".join(fitz.open(stream=zf.read(pdf), filetype="pdf")[0].get_text().split())
    return informe["entity_types_disabled"], texto


@pytest.fixture
def expediente_apagado(monkeypatch):
    """Un catalogo con el expediente apagado, para probar `enabled_types`."""
    import app.api.routes as routes

    monkeypatch.setattr(routes, "default_disabled_ids", lambda: frozenset({"CASE_NUMBER"}))


def test_el_catalogo_solo_trae_apagado_el_presupuesto_de_obra():
    assert APAGADOS_DEL_CATALOGO == ["BUDGET_AMOUNT"]


def _presupuesto() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    lineas = [
        "PRESUPUESTO DE EJECUCIÓN MATERIAL",
        "Capítulo 1. Demoliciones",
        "Partida  Descripción  Ud  Medición  Precio unitario  Importe",
        "1.1  Demolición de tabiquería  m2  24,00  18,50  444,00",
        "1.2  Retirada de escombros  m3  3,00  120,00  360,00",
        "TOTAL PRESUPUESTO DE EJECUCIÓN MATERIAL  804,00 €",
    ]
    for i, linea in enumerate(lineas):
        page.insert_text((40, 80 + 20 * i), linea, fontsize=9)
    data = doc.tobytes()
    doc.close()
    return data


PRECIOS = ["18,50", "444,00", "120,00", "360,00", "804,00"]


def _presupuesto_anonimizado(query: str = ""):
    response = client.post(
        f"/anonymize?output=zip{query}",
        files={"file": ("presupuesto.pdf", BytesIO(_presupuesto()), "application/pdf")},
    )
    assert response.status_code == 200
    return _desactivados_y_texto(response)


def test_sin_tocar_nada_un_presupuesto_queda_a_la_vista():
    desactivados, texto = _presupuesto_anonimizado()
    assert "BUDGET_AMOUNT" in desactivados
    assert all(precio in texto for precio in PRECIOS)


def test_con_la_casilla_marcada_el_presupuesto_se_tacha():
    desactivados, texto = _presupuesto_anonimizado("&enabled_types=BUDGET_AMOUNT")
    assert "BUDGET_AMOUNT" not in desactivados
    assert not [precio for precio in PRECIOS if precio in texto]


def test_sin_listas_el_expediente_se_tacha_tambien_el_municipal():
    response = _anonimizar()
    assert response.status_code == 200
    desactivados, texto = _desactivados_y_texto(response)
    assert desactivados == ["BUDGET_AMOUNT"]
    assert "E-03001-2022-000074-00" not in texto


def test_desmarcado_el_expediente_no_se_tacha():
    response = _anonimizar("&disabled_types=CASE_NUMBER")
    assert response.status_code == 200
    desactivados, texto = _desactivados_y_texto(response)
    assert desactivados == ["BUDGET_AMOUNT", "CASE_NUMBER"]
    assert "E-03001-2022-000074-00" in texto


def test_sin_listas_se_aplica_un_tipo_apagado_del_catalogo(expediente_apagado):
    desactivados, texto = _desactivados_y_texto(_anonimizar())
    assert desactivados == ["CASE_NUMBER"]
    assert "E-03001-2022-000074-00" in texto


def test_enabled_types_enciende_un_tipo_apagado(expediente_apagado):
    response = _anonimizar("&enabled_types=case_number")
    assert response.status_code == 200
    desactivados, texto = _desactivados_y_texto(response)
    assert desactivados == []
    assert "E-03001-2022-000074-00" not in texto


def test_un_tipo_en_las_dos_listas_se_rechaza():
    response = _anonimizar("&enabled_types=CASE_NUMBER&disabled_types=CASE_NUMBER")
    assert response.status_code == 422


def test_un_tipo_inexistente_en_enabled_types_se_rechaza():
    assert _anonimizar("&enabled_types=NO_EXISTE").status_code == 422


def test_jobs_admite_enabled_types():
    response = client.post(
        "/jobs?enabled_types=CASE_NUMBER",
        files={"file": ("in.pdf", BytesIO(_pdf()), "application/pdf")},
    )
    assert response.status_code == 202


def test_la_evaluacion_pide_todos_los_tipos(tmp_path, monkeypatch):
    from evaluation import evaluator

    enviados = {}

    def falso_post(url, files, params, timeout):
        enviados.update(params)
        return httpx.Response(500, text="no importa")

    monkeypatch.setattr(evaluator.httpx, "post", falso_post)
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(_pdf())
    evaluator.call_api(pdf)
    assert sorted(enviados["enabled_types"]) == sorted(known_type_ids())
    assert list(enviados["disabled_types"]) == []

    evaluator.call_api(pdf, disabled_types=["URL"])
    assert "URL" not in enviados["enabled_types"]
