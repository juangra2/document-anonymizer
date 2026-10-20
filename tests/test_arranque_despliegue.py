"""Arranque en el contenedor: sin GPU funciona en CPU y lo dice; sin permiso de
escritura en la carpeta de trabajos se para con un mensaje que explica el arreglo."""
import logging

import pytest

from app.core import dispositivo
from app.core.config import settings


@pytest.fixture
def sin_cuda(monkeypatch):
    dispositivo.cuda_disponible.cache_clear()
    monkeypatch.setattr(dispositivo, "_cuda", lambda: False)
    yield
    dispositivo.cuda_disponible.cache_clear()


def test_sin_cuda_nadie_pide_la_gpu(sin_cuda):
    assert dispositivo.usar_gpu(True) is False
    assert dispositivo.usar_gpu(False) is False


def test_mineru_carga_en_cpu_si_no_hay_cuda(sin_cuda):
    from app.services.ocr.mineru_engine import MinerUEngine

    assert MinerUEngine(use_gpu=True)._dispositivo() == "cpu"


def test_sin_cuda_el_arranque_avisa_en_el_log(sin_cuda, caplog, monkeypatch):
    monkeypatch.setattr(settings, "ocr_use_gpu", True)
    with caplog.at_level(logging.WARNING):
        dispositivo.avisar_al_arrancar()
    assert "SIN GPU" in caplog.text and "CPU" in caplog.text


def test_carpeta_de_trabajos_sin_permiso_da_un_mensaje_claro(tmp_path, monkeypatch):
    from app.services.storage.temp_storage import comprobar_carpeta_de_trabajos

    destino = tmp_path / "trabajos"
    monkeypatch.setattr(settings, "temp_root", str(destino))

    def sin_permiso(*_a, **_k):
        raise PermissionError("denegado")

    monkeypatch.setattr("pathlib.Path.write_bytes", sin_permiso)
    with pytest.raises(RuntimeError) as error:
        comprobar_carpeta_de_trabajos()
    mensaje = str(error.value)
    assert str(destino) in mensaje and "10001" in mensaje


def test_carpeta_de_trabajos_escribible(tmp_path, monkeypatch):
    from app.services.storage.temp_storage import comprobar_carpeta_de_trabajos

    monkeypatch.setattr(settings, "temp_root", str(tmp_path / "trabajos"))
    comprobar_carpeta_de_trabajos()
    assert not list((tmp_path / "trabajos").glob(".prueba*")), "no deja restos"
