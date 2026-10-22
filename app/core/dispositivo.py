"""Una sola decision de dispositivo para todo el servicio: GPU si se pide y la hay.

Los motores no deciden por su cuenta con el ajuste de configuracion: pedir
"cuda" sin CUDA hace fallar el primer documento escaneado.  En el contenedor sin
tarjeta (o con la tarjeta mal pasada) el servicio tiene que arrancar en CPU y
decirlo claro en el log.

Que tarjeta se usa no se decide aqui: la elige quien arranca el contenedor
(GPU_DEVICE en docker-compose, o --gpus device=N), y dentro solo se ve esa.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from app.core.config import settings

logger = logging.getLogger(__name__)


def _cuda() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 — sin torch o sin driver: CPU
        return False


@lru_cache(maxsize=1)
def cuda_disponible() -> bool:
    return _cuda()


def usar_gpu(pedida: bool) -> bool:
    """GPU solo si la configuracion la pide y CUDA esta disponible."""
    return bool(pedida) and cuda_disponible()


def avisar_al_arrancar() -> None:
    """Deja escrito en el log, al arrancar, en que se va a procesar."""
    pedida = settings.ocr_use_gpu or settings.ner_use_gpu
    if not pedida:
        logger.info("GPU desactivada por configuracion (OCR_USE_GPU/NER_USE_GPU): se procesa en CPU.")
        return
    if cuda_disponible():
        import torch

        logger.info("GPU en uso: %s (CUDA %s, torch %s).",
                    torch.cuda.get_device_name(0), torch.version.cuda, torch.__version__)
        return
    logger.warning(
        "*** SIN GPU *** Se pidio GPU pero CUDA no esta disponible: el servicio funciona en "
        "CPU, mucho mas despacio (un escaneado puede tardar minutos por pagina).  "
        "Compruebe que el contenedor se arranco con la tarjeta (GPU_DEVICE en "
        "docker-compose, o --gpus), el driver de NVIDIA del servidor y "
        "nvidia-container-toolkit.  /health/ready lo indica en 'gpu' e 'issues'."
    )
