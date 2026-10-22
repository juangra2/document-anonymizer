"""Páginas de redacción total: copia de DNI y autoliquidación."""
import pytest

from app.core.config import settings
from app.services.pdf.full_redaction import (
    DNI_SCAN,
    TAX_FORM,
    classify_page,
    classify_pages,
    form_references,
)

# --- B1: copia de DNI --------------------------------------------------------

_DNI_ANVERSO = (
    "Documentación aportada al expediente\n"
    "ESPAÑA DNI\nDOCUMENTO NACIONAL DE IDENTIDAD\n"
    "APELLIDOS\nNavarro Martín\nNOMBRE\nTeresa\n"
    "SEXO M NACIONALIDAD ESP\nVALIDEZ 12/2030 Nº SOPORTE C123456\n"
)
_DNI_REVERSO = (
    "REVERSO\nLUGAR DE NACIMIENTO València\nHIJO/A DE Juan / Ana\n"
    "IDESP12345678901<<<<<<<<<<<<<<\n"
    "6401039M3012319ESP<<<<<<<<<<<<\n"
)


def test_dni_front_detected():
    assert classify_page(_DNI_ANVERSO) == DNI_SCAN


def test_dni_back_detected_by_its_own_signals():
    # El reverso no lleva el rótulo grande: puntúa por MRZ y filiación.
    assert classify_page(_DNI_REVERSO) == DNI_SCAN


def test_dni_requires_a_strong_signal():
    # Señales débiles solas (una dirección en España + "validez" de un
    # certificado) no bastan: pintarían en negro un documento normal.
    weak = (
        "CERTIFICADO DE EMPADRONAMIENTO\n"
        "Domicilio: Calle Mayor 1, 46001 València, ESPAÑA\n"
        "La validez de este certificado es de tres meses.\n"
        "APELLIDOS Y NOMBRE: Rosa García Díaz\n"
    )
    assert classify_page(weak) is None


# --- B2: autoliquidación ------------------------------------------------------

_TAX = (
    "AUTOLIQUIDACIÓN — MODELO 600\n"
    "Impuesto sobre Transmisiones Patrimoniales\n"
    "SUJETO PASIVO\nApellidos y nombre: Luis Vela Rius\n"
    "Base imponible 845,20 €\nCuota tributaria 84,52 €\nTOTAL A INGRESAR 84,52 €\n"
)


def test_tax_form_detected():
    assert classify_page(_TAX) == TAX_FORM


def test_notification_mentioning_a_tax_is_not_a_form():
    # Una notificación que CITA una liquidación no se redacta entera.
    text = (
        "NOTIFICACIÓN\nSe le notifica la liquidación del Impuesto sobre Bienes "
        "Inmuebles correspondiente al ejercicio en curso, por importe de "
        "1.234,56 €, con cargo a los presupuestos municipales.\n"
    )
    assert classify_page(text) is None


def test_resolution_granting_aid_is_not_a_form():
    text = (
        "RESOLUCIÓN ADMINISTRATIVA\nConceder la ayuda económica solicitada por "
        "importe de 500 €, con cargo a la partida presupuestaria 3.456.78.\n"
    )
    assert classify_page(text) is None


# --- alcance por página y propagación ----------------------------------------

def test_scope_is_per_page_in_a_concatenated_file():
    # Expediente con autoliquidación + notificación + plano en el mismo PDF.
    pages = [_TAX, "NOTIFICACIÓN de la resolución del expediente.", "PLANO 03"]
    result = classify_pages(pages)
    assert result == {0: TAX_FORM}, "no debe arrastrar las páginas ajenas"


_TAX_CON_REFERENCIA = _TAX + "\nReferencia: 600-2023-362396\n"


def test_propagation_requires_shared_form_reference():
    # Compartir el numero de modelo NO propaga: una notificacion que solo dice
    # «en relacion con el modelo 600 presentado» saldria ENTERA EN NEGRO.  El
    # numero de modelo identifica un TIPO de impreso, no un expediente; para
    # arrastrar a una pagina vecina hace falta el justificante o la referencia,
    # que si identifican el documento.
    solo_menciona = "MODELO 600 — Instrucciones de cumplimentacion del impreso."
    assert classify_pages([_TAX, solo_menciona]) == {0: TAX_FORM}

    # El segundo ejemplar del MISMO impreso si se arrastra.
    mismo = "EJEMPLAR PARA EL INTERESADO\nReferencia: 600-2023-362396\n"
    assert classify_pages([_TAX_CON_REFERENCIA, mismo]) == {0: TAX_FORM, 1: TAX_FORM}


def test_no_propagation_without_shared_reference():
    second = "Anexo fotográfico del inmueble, sin referencia al impreso."
    assert classify_pages([_TAX, second]) == {0: TAX_FORM}


def test_dni_copies_do_not_propagate():
    pages = [_DNI_ANVERSO, "Escrito de alegaciones del interesado."]
    assert classify_pages(pages) == {0: DNI_SCAN}


def test_form_references_extracted():
    # El numero de modelo no cuenta como referencia (ver el test de
    # propagacion); lo que identifica al expediente es el justificante.
    assert form_references(_TAX) == set()
    assert "ref:600-2023-362396" in form_references(_TAX_CON_REFERENCIA)


# --- configuración ------------------------------------------------------------

def test_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "full_redaction_enabled", False)
    assert classify_page(_DNI_ANVERSO) is None
    assert classify_page(_TAX) is None


def test_threshold_is_configurable(monkeypatch):
    # Subir el umbral es la palanca ante un falso positivo en producción.
    monkeypatch.setattr(settings, "dni_scan_min_signals", 9)
    assert classify_page(_DNI_ANVERSO) is None
