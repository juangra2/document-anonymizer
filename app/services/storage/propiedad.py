"""De quien es cada carpeta de trabajo, para no borrarle el trabajo a nadie.

El problema que resuelve
------------------------
La limpieza de arranque no puede borrar TODO lo que haya bajo el directorio
temporal dando por supuesto que al arrancar no hay ningun proceso vivo.  Un
segundo servicio que ni siquiera llega a ocupar el puerto ejecuta su limpieza y
borraria la carpeta del documento que el servicio vivo esta procesando: un
`FileNotFoundError` a media pagina y un error interno sin causa entendible.

No hace falta un reinicio mal hecho.  Pasa en tres situaciones rutinarias de un
servidor: un gestor de procesos que reinicia con un
documento en curso, dos instancias levantadas a la vez aunque una falle
enseguida, y un arranque que se solapa con el apagado anterior.

Por que NO se usa el PID
------------------------
Un PID se reutiliza: si al arrancar le toca el de un proceso muerto que dejo
carpetas, las daria por vivas y no se limpiarian nunca — justo lo contrario de
lo que se busca, que es que no queden documentos con datos personales en disco.

Y en el contenedor el PID del servicio suele ser 1, que existe siempre, de modo
que "su PID esta vivo" seria cierto para cualquier carpeta huerfana.

Como se hace en su lugar
------------------------
Cada proceso se inventa un TESTIGO al arrancar —un identificador propio, sin
relacion con el sistema operativo— y mantiene una marca con ese nombre que
refresca periodicamente.  Una carpeta de trabajo anota de quien es.  La limpieza
respeta solo las carpetas cuyo testigo tiene una marca FRESCA; las demas son
restos y se borran.

En el contenedor esto se comporta como debe sin hacer nada especial: el
directorio temporal vive en memoria, al reiniciar no queda ninguna marca, y por
tanto no hay nada que respetar y se limpia todo.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from uuid import uuid4

from app.core.config import settings

logger = logging.getLogger(__name__)

#: Identificador de ESTA ejecucion.  No es el PID ni deriva de el.
TESTIGO = uuid4().hex

#: Nombre del fichero que una carpeta de trabajo usa para decir de quien es.
_MARCA_CARPETA = ".de_quien_es"

#: Subdirectorio con una marca por proceso vivo.
_DIR_PROCESOS = ".procesos"


def _dir_procesos() -> Path:
    return Path(settings.temp_root) / _DIR_PROCESOS


def _margen_frescura() -> float:
    """Cuanto puede tardar una marca en refrescarse antes de darla por muerta.

    Tres barridos, con un suelo de dos minutos: un proceso ocupado con una
    pagina escaneada larga no refresca al segundo, y darlo por muerto seria
    volver al problema de partida.
    """
    return max(120.0, settings.job_sweep_interval_s * 3.0)


def registrar_proceso() -> None:
    """Deja la marca de esta ejecucion.  Se llama al arrancar."""
    try:
        destino = _dir_procesos()
        destino.mkdir(parents=True, exist_ok=True)
        (destino / TESTIGO).write_text(
            f"pid={os.getpid()}\niniciado={time.time():.0f}\n", encoding="utf-8",
        )
    except OSError as exc:
        # Sin marca, otro proceso podria borrar el trabajo de este.  Es un
        # riesgo, no una razon para no arrancar, pero tiene que verse.
        logger.warning(
            "No se pudo dejar la marca de este proceso (%s): otra instancia "
            "podria borrar su trabajo en curso.", exc,
        )


def refrescar_proceso() -> None:
    """Dice 'sigo vivo'.  La llama el barredor en cada vuelta."""
    try:
        marca = _dir_procesos() / TESTIGO
        if marca.exists():
            os.utime(marca, None)
        else:
            registrar_proceso()
    except OSError:
        pass


def proceso_vivo(testigo: str) -> bool:
    """¿Hay una marca fresca de ese testigo?"""
    if testigo == TESTIGO:
        return True
    try:
        marca = _dir_procesos() / testigo
        return (time.time() - marca.stat().st_mtime) <= _margen_frescura()
    except OSError:
        return False


def marcar_carpeta(carpeta: Path) -> None:
    """Anota en la carpeta de trabajo que es de esta ejecucion."""
    try:
        (Path(carpeta) / _MARCA_CARPETA).write_text(TESTIGO, encoding="utf-8")
    except OSError:
        pass


def duenyo_de(carpeta: Path) -> str | None:
    """Testigo del proceso que creo la carpeta, o None si no lo dice."""
    try:
        return (Path(carpeta) / _MARCA_CARPETA).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def esta_en_uso(carpeta: Path) -> bool:
    """¿La esta usando un proceso vivo ahora mismo?

    Una carpeta sin marca de dueno no puede pertenecer a nadie vivo (todo
    proceso marca las suyas), asi que se puede limpiar.
    """
    testigo = duenyo_de(carpeta)
    return bool(testigo) and proceso_vivo(testigo)


def limpiar_marcas_muertas() -> int:
    """Quita las marcas de procesos que ya no refrescan.  Devuelve cuantas."""
    destino = _dir_procesos()
    if not destino.is_dir():
        return 0
    quitadas = 0
    for marca in list(destino.iterdir()):
        try:
            if marca.name == TESTIGO:
                continue
            if (time.time() - marca.stat().st_mtime) > _margen_frescura():
                marca.unlink()
                quitadas += 1
        except OSError:
            continue
    return quitadas
