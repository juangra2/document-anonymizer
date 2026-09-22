"""Que no se escape texto del documento por los sitios que nadie mira.

Tres fugas encontradas y cerradas aquí:

1. El filtro anti-PII del registro estaba puesto en el logger raíz, donde no se
   aplica a lo que escriben los módulos: Python solo ejecuta los filtros del
   logger donde nace el mensaje, nunca los de sus antepasados.
2. Los avisos de la capa de verificación copiaban el valor de un metadato o de
   un campo de formulario sin limpiar; ese aviso va al informe, que se conserva
   horas.
3. El informe de un trabajo de la cola podía llevar los valores detectados en
   claro si se pedía `include_entities`.
"""
import logging
from io import BytesIO

import fitz
from fastapi.testclient import TestClient

from app.core.logging import (
    NoPIIFilter,
    configure_logging,
    install_pii_filter,
    warn_if_logging_pii,
)
from app.main import app
from app.services.pii.analyzer import PiiAnalyzer
from app.services.verification.layers import check_metadata

client = TestClient(app)

_DNI = "12345678Z"
_NOMBRE = "Roberto Iglesias Calvo"
_CORREO = "roberto.iglesias@example.com"


# --- 1. el registro ----------------------------------------------------------

def test_the_log_filter_reaches_messages_written_by_modules():
    """Un DNI registrado por un módulo cualquiera no puede salir en claro."""
    import io

    configure_logging()
    stream = io.StringIO()
    buffer = logging.StreamHandler(stream)
    logging.getLogger().addHandler(buffer)
    install_pii_filter()
    try:
        logging.getLogger("app.services.pdf.scanned_redactor").warning(
            "pagina con DNI %s y correo %s", _DNI, _CORREO,
        )
    finally:
        logging.getLogger().removeHandler(buffer)

    escrito = stream.getvalue()
    assert _DNI not in escrito, "el DNI llego al registro en claro"
    assert _CORREO not in escrito
    assert "[REDACTED]" in escrito


def test_the_filter_is_installed_on_handlers_not_on_the_root_logger():
    """Es la diferencia entre que funcione y que no; queda fijado aquí."""
    configure_logging()
    install_pii_filter()
    manejadores = logging.getLogger().handlers
    assert manejadores, "sin manejadores no hay nada que filtrar"
    assert all(
        any(isinstance(f, NoPIIFilter) for f in h.filters) for h in manejadores
    )


# --- 2. los avisos de verificación -------------------------------------------

def _pdf_con_metadatos(path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Documento de prueba", fontsize=11)
    doc.set_metadata({"author": f"{_NOMBRE} ({_DNI})", "title": "Expediente"})
    doc.save(path)
    doc.close()


def test_a_metadata_warning_never_carries_the_value(tmp_path):
    ruta = tmp_path / "con_metadatos.pdf"
    _pdf_con_metadatos(ruta)

    resultado = check_metadata(ruta, PiiAnalyzer())
    avisos = "\n".join(resultado.warnings)

    assert avisos, "el metadato sin limpiar tiene que avisarse igualmente"
    assert _NOMBRE not in avisos and _DNI not in avisos, (
        "el aviso copiaba el valor del metadato, que puede ser el interesado"
    )
    # Sigue sirviendo para arreglarlo: dice qué campo y cuánto ocupa.
    assert "author" in avisos.lower()
    assert "characters" in avisos.lower()


# --- 3. el informe de un trabajo de la cola ----------------------------------

def _pdf_con_datos() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), f"Nombre: {_NOMBRE}", fontsize=11)
    page.insert_text((72, 120), f"DNI: {_DNI}", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def test_the_retained_report_never_contains_detected_values():
    """Se conserva horas: no puede llevar dentro lo que acaba de tapar."""
    import json
    import time

    response = client.post(
        "/jobs?include_entities=true",
        files={"file": ("expediente.pdf", BytesIO(_pdf_con_datos()), "application/pdf")},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    deadline = time.time() + 120
    while time.time() < deadline:
        estado = client.get(f"/jobs/{job_id}").json()
        if estado["status"] in ("completed", "failed"):
            break
        time.sleep(0.2)

    informe = client.get(f"/jobs/{job_id}/report").json()
    crudo = json.dumps(informe, ensure_ascii=False)

    assert informe["entities_detected"] > 0, "sigue detectando, solo que sin valores"
    assert not informe.get("detected_entities")
    assert informe["security_checks"]["report_contains_pii_values"] is False
    assert _NOMBRE not in crudo and _DNI not in crudo, (
        "el informe retenido lleva datos personales en claro"
    )


# --- 4. el interruptor de depuracion no se queda encendido en silencio -------

def test_logging_pii_announces_itself_at_startup(monkeypatch, caplog):
    from app.core.config import settings

    monkeypatch.setattr(settings, "log_pii", True)
    with caplog.at_level("WARNING"):
        assert warn_if_logging_pii() is True
    aviso = chr(10).join(r.getMessage() for r in caplog.records)
    assert "LOG_PII" in aviso and "datos personales" in aviso


def test_nothing_is_announced_when_it_is_off(monkeypatch, caplog):
    from app.core.config import settings

    monkeypatch.setattr(settings, "log_pii", False)
    with caplog.at_level("WARNING"):
        assert warn_if_logging_pii() is False
    assert "LOG_PII" not in chr(10).join(r.getMessage() for r in caplog.records)
