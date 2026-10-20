"""Registro del servicio, con una red de seguridad contra datos personales.

El filtro va en los MANEJADORES, no en el logger raíz.  Es la diferencia entre
que funcione y que no: Python aplica los filtros del logger donde nace el
mensaje, y los de sus antepasados nunca llegan a ejecutarse.  Puesto en el
raíz, solo filtraba lo que se registrara con `logging.warning(...)` a secas —o
sea, nada de la aplicación, porque cada módulo tiene su propio logger.  En los
manejadores sí pasa todo lo que se escribe.

Es una red, no la protección: solo reconoce DNI, NIE, CIF, correo, teléfono y
IBAN.  Un nombre o una dirección no tienen forma reconocible, así que la regla
sigue siendo no registrar texto del documento (ver `log_pii`).
"""
import logging
import re


PII_PATTERNS = [
    re.compile(r"\b\d{8}[A-Z]\b", re.IGNORECASE),
    re.compile(r"\b[XYZ]\d{7}[A-Z]\b", re.IGNORECASE),
    re.compile(r"\b[A-HJNPQRSUVW]\d{7}[0-9A-J]\b", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?<!\d)(?:\+34[\s\-]?)?(?:6|7|8|9)\d{2}[\s\-]?\d{3}[\s\-]?\d{3}(?!\d)"),
    re.compile(r"\bES\d{2}(?:[\s\-]?\d{4}){5}\b", re.IGNORECASE),
]


def _tachar(texto: str) -> str:
    for pattern in PII_PATTERNS:
        texto = pattern.sub("[REDACTED]", texto)
    return texto


class NoPIIFilter(logging.Filter):
    """Tacha los datos reconocibles en el mensaje y en cada argumento.

    Los argumentos se tachan uno a uno, sin juntarlos en el mensaje: el
    formateador de accesos de uvicorn los lee por posicion (cliente, metodo,
    ruta...) y con los argumentos vaciados fallaba en cada peticion.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _tachar(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_tachar(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: _tachar(v) if isinstance(v, str) else v for k, v in record.args.items()}
        # Un dato partido entre el mensaje y un argumento (o que solo se forma
        # al juntarlos): se comprueba tambien el resultado final.
        try:
            final = record.getMessage()
        except Exception:  # noqa: BLE001 — un mensaje mal formado no se toca
            return True
        if _tachar(final) != final:
            record.msg, record.args = _tachar(final), ()
        return True


def install_pii_filter() -> int:
    """Pone el filtro en todos los manejadores que ya existan.

    Incluye los de uvicorn, que se configuran antes de importar la aplicación.
    Devuelve a cuántos se les puso, que es lo que comprueban los tests.
    """
    installed = 0
    loggers: list[logging.Logger] = [logging.getLogger()]
    loggers += [
        logging.getLogger(name)
        for name in list(logging.root.manager.loggerDict)
        if isinstance(logging.getLogger(name), logging.Logger)
    ]

    for logger in loggers:
        for handler in logger.handlers:
            if any(isinstance(f, NoPIIFilter) for f in handler.filters):
                continue
            handler.addFilter(NoPIIFilter())
            installed += 1
    return installed


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    install_pii_filter()
    warn_if_logging_pii()
    warn_if_safety_net_off()


def warn_if_safety_net_off() -> bool:
    """Avisa, bien visible, si la segunda pasada esta desactivada.

    La red de seguridad —lo que la segunda pasada todavia lee tras tachar se
    tapa antes de entregar— vive dentro de esa pasada.  Desactivarla para ganar
    velocidad (duplica el coste del OCR) quita la red SIN que nada lo diga, y
    vuelven a poder entregarse datos que el propio sistema habria visto.
    """
    from app.core.config import settings

    if settings.ocr_second_pass_verification:
        return False

    logger = logging.getLogger("app")
    logger.warning("=" * 72)
    logger.warning(
        "OCR_SECOND_PASS_VERIFICATION ESTA DESACTIVADA: sin segunda pasada no "
        "hay red de seguridad, y un dato que siga a la vista tras tachar se "
        "entregara sin que nada lo tape ni lo avise."
    )
    logger.warning("=" * 72)
    return True


def warn_if_logging_pii() -> bool:
    """Avisa, bien visible, si se está registrando texto de los documentos.

    `LOG_PII` es para depurar un caso concreto y desactivarlo después.  Dejarlo
    encendido sin darse cuenta llena el registro de datos personales que luego
    nadie sabe que están ahí, así que el arranque lo dice en tres líneas que no
    se confunden con el resto.
    """
    from app.core.config import settings

    if not settings.log_pii:
        return False

    logger = logging.getLogger("app")
    logger.warning("=" * 72)
    logger.warning(
        "LOG_PII ESTA ACTIVADO: el registro incluira texto de los documentos, "
        "con datos personales. Solo para depurar; desactivelo despues."
    )
    logger.warning("=" * 72)
    return True