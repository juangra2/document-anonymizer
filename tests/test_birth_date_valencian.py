"""Fecha de nacimiento con etiqueta en valenciano.

Si con estas etiquetas la fecha solo se detectara como DATE_TIME, al desactivar
"Fechas" se publicarían fechas de nacimiento.  Los textos imitan fragmentos del
corpus.  Se analizan con DATE_TIME desactivado: si la fecha queda cubierta, es
que DATE_OF_BIRTH la detecta por sí mismo.
"""
import pytest

from app.services.pii.analyzer import PiiAnalyzer

analyzer = PiiAnalyzer().with_disabled_types({"DATE_TIME"})


def _dob_covers(text: str, value: str) -> bool:
    start = text.index(value)
    end = start + len(value)
    return any(
        e.entity_type == "DATE_OF_BIRTH" and e.start <= start and e.end >= end
        for e in analyzer.analyze(text, mode="strict")
    )


@pytest.mark.parametrize("text, value", [
    # Prosa, a media línea.
    ("A efectes de nòmina i comunicacions: NAF: C/C: i Nascut/da el: 24/10/1954.\n", "24/10/1954"),
    ("La persona interessada, nascuda el 03/05/1980, sol·licita l'alta.\n", "03/05/1980"),
    ("nascut el 12/03/1985 a València\n", "12/03/1985"),
    ("nascuda el 3 de maig de 1980\n", "3 de maig de 1980"),
    # Etiqueta con dos puntos a media línea.
    ("A efectes de nòmina i comunicacions: Nº S.S.: C/C: i Data de naixement: 24/02/1967 01:29.\n",
     "24/02/1967 01:29"),
    # La misma etiqueta partida en varias líneas por la extracción.
    ("A efectes de nòmina i comunicacions: Nº S.S.: C/C: i\nData\nde\nnaixement: 21/01/1971 21:08.\n",
     "21/01/1971 21:08"),
])
def test_valencian_birth_date_is_detected_without_date_time(text, value):
    assert _dob_covers(text, value)


@pytest.mark.parametrize("text, value", [
    ("Data de la sol·licitud: 12/03/2024\n", "12/03/2024"),
    ("Data de naixement del projecte? No: data d'inici 01/02/2020\n", "01/02/2020"),
    ("Nascut el projecte l'any passat, es va aprovar el 05/06/2021\n", "05/06/2021"),
])
def test_other_valencian_dates_are_not_birth_dates(text, value):
    assert not _dob_covers(text, value)
