"""Una dirección IP no es un importe.

`_MONEY_RE` acepta el formato de miles con puntos, y una IPv4 tiene esa forma:
casaría entera, o en sus tres primeros grupos.  En una página con señales
financieras (nómina, cuenta) el gate de contexto la dejaría pasar como MONEY.
"""
import pytest

from app.services.pii.analyzer import PiiAnalyzer

# Contexto financiero de página, que es lo que abría el gate.
_PAGE = "A efectes de nòmina i comunicacions: NAF: 28/2343540952, IBAN: ES53 3058 4700 7070 2668 9384\n"


def _money(text: str) -> list[str]:
    return [text[e.start:e.end] for e in PiiAnalyzer._detect_money(text, context_text=_PAGE + text)]


@pytest.mark.parametrize("line", [
    "IP de signatura electrònica / IP de firma electrónica: 90.241.195.223\n",
    "IP de firma electrónica: 10.217.195.85\n",
    "IP de firma electrónica: 192.138.148.192\n",
    "Acceso desde 172.118.193.81 registrado\n",
])
def test_an_ip_address_is_not_money(line):
    assert _money(line) == []


@pytest.mark.parametrize("line, amount", [
    ("Salario base: 2.309,00 €\n", "2.309,00"),
    ("Importe de la transferencia: 1.250.000 €\n", "1.250.000"),
    ("Líquido a percibir 1.845,20\n", "1.845,20"),
])
def test_real_amounts_in_a_financial_page_are_still_money(line, amount):
    assert amount in _money(line)


def test_the_ip_is_still_detected_as_ip_address():
    text = "IP de firma electrónica: 90.241.195.223\n"
    entities = PiiAnalyzer().analyze(text, mode="strict", context_text=_PAGE + text)
    assert any(e.entity_type == "IP_ADDRESS" and text[e.start:e.end] == "90.241.195.223" for e in entities)
    assert not any(e.entity_type == "MONEY" for e in entities)
