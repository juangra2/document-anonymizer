"""Cabeceras de columna en valenciano (y sus pares en castellano), Excel y CSV.

"Adreça electrònica" es el correo, no la direccion: antes la palabra
"adreça" la hacia direccion y el correo salia como "[DIRECCIÓN]".  Con el
punto volat, "Sol·licitant" se partia en dos palabras y no se reconocia.
"""
import pytest

from app.services.office.xlsx import tipo_por_cabecera


@pytest.mark.parametrize("cabecera,tipo", [
    # correo
    ("Adreça electrònica", "EMAIL_ADDRESS"), ("Adreça de correu electrònic", "EMAIL_ADDRESS"),
    ("Direcció electrònica", "EMAIL_ADDRESS"), ("Dirección electrónica", "EMAIL_ADDRESS"),
    ("Dirección de correo electrónico", "EMAIL_ADDRESS"), ("Correu-e", "EMAIL_ADDRESS"),
    ("Correu electrònic", "EMAIL_ADDRESS"),
    # direccion, que sigue siendolo
    ("Adreça", "ADDRESS"), ("Adreça postal", "ADDRESS"), ("Domicili", "ADDRESS"), ("Dirección", "ADDRESS"),
    ("Direcció", "ADDRESS"), ("Adreça de notificació", "ADDRESS"),
    # personas
    ("Sol·licitant", "PERSON"), ("Persona sol·licitant", "PERSON"), ("Interessada", "PERSON"),
    ("Interessat", "PERSON"), ("Inscrit", "PERSON"), ("Inscrita", "PERSON"), ("Signant", "PERSON"),
    ("Nom i cognoms", "PERSON"), ("Cognoms i nom", "PERSON"), ("Beneficiària", "PERSON"),
    # cuenta: los pares de lo que ya habia en castellano ("cuenta corriente")
    ("Compte corrent", "IBAN_CODE"), ("Compte bancari", "IBAN_CODE"), ("Cuenta corriente", "IBAN_CODE"),
    # el resto, como estaba
    ("Telèfon mòbil", "PHONE_NUMBER"), ("Data de naixement", "DATE_OF_BIRTH"),
    ("Lloc de naixement", "PLACE_OF_BIRTH"), ("Codi postal", "POSTAL_CODE"), ("DNI/NIE", "ES_DNI"),
    ("Núm. Seguretat Social", "ES_NSS"), ("Expedient", "CASE_NUMBER"), ("Observacions", "TEXTO_LIBRE"),
    # no personales
    ("Població", None), ("Activitat", None), ("Districte", None), ("Data d'alta", None),
])
def test_cabeceras(cabecera, tipo):
    assert tipo_por_cabecera(cabecera) == tipo
