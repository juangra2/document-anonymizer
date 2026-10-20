"""El filtro de datos personales del registro no rompe el registro de accesos.

Vaciaba los argumentos del mensaje y el formateador de accesos de uvicorn, que
los necesita (cliente, metodo, ruta, version, estado), fallaba con una traza
en cada peticion: en el contenedor, cada 30 s por la comprobacion de salud.
"""
import logging

from uvicorn.logging import AccessFormatter

from app.core.logging import NoPIIFilter


def _registro(*args):
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                             '%s - "%s %s HTTP/%s" %d', args, None)


def test_el_acceso_se_formatea_con_el_filtro_puesto():
    r = _registro("127.0.0.1:5000", "GET", "/health/ready", "1.1", 200)
    assert NoPIIFilter().filter(r)
    linea = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False).format(r)
    assert "/health/ready" in linea and "200" in linea


def test_un_dato_en_la_ruta_se_tacha_igual():
    r = _registro("127.0.0.1:5000", "GET", "/buscar?dni=12345678Z", "1.1", 200)
    NoPIIFilter().filter(r)
    linea = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False).format(r)
    assert "12345678Z" not in linea and "[REDACTED]" in linea


def test_un_mensaje_normal_sigue_tachandose():
    r = logging.LogRecord("app", logging.INFO, __file__, 1, "telefono %s", ("655 123 456",), None)
    NoPIIFilter().filter(r)
    assert "655" not in r.getMessage()
