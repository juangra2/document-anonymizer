"""A1.1 — toda fecha se anonimiza, en cualquier zona del documento."""
import pytest

from app.services.layout.models import ZoneType
from app.services.layout.zone_config import get_detector_config


@pytest.mark.parametrize("zone", [
    ZoneType.HEADER, ZoneType.FOOTER, ZoneType.BODY,
    ZoneType.FORM, ZoneType.TABLE, ZoneType.MARGINALIA, ZoneType.UNKNOWN,
])
def test_dates_detected_in_every_text_zone(zone):
    # Requisito del ayuntamiento: no hay fecha exenta, ni siquiera la
    # institucional del pie o del bloque de firma electrónica.
    types = get_detector_config(zone).entity_types
    assert types is not None, f"{zone} no analiza entidades"
    assert "DATE_TIME" in types, f"{zone} no detecta DATE_TIME"
    assert "DATE_OF_BIRTH" in types, f"{zone} no detecta DATE_OF_BIRTH"


def test_institutional_filter_never_suppresses_dates():
    # El filtro institucional solo puede suprimir datos de contacto; si alguna
    # vez incluyera fechas, dejaría pasar fechas del organismo emisor.
    from app.services.pii.institution_filter import SUPPRESSIBLE_TYPES
    assert "DATE_TIME" not in SUPPRESSIBLE_TYPES
    assert "DATE_OF_BIRTH" not in SUPPRESSIBLE_TYPES
