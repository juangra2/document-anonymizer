from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from app.core.config import settings
from app.services.storage import propiedad


def comprobar_carpeta_de_trabajos() -> None:
    """Al arrancar: que se pueda escribir donde van los documentos.

    El error tipico del primer despliegue es montar en /data/trabajos una
    carpeta del servidor que pertenece a root: el servicio, que no corre como
    root (UID 10001), arrancaba y fallaba con el primer documento.  Ahora se
    para al arrancar y dice como arreglarlo.
    """
    root = Path(settings.temp_root)
    prueba = root / f".prueba_escritura_{uuid4().hex}"
    try:
        root.mkdir(parents=True, exist_ok=True)
        prueba.write_bytes(b"ok")
        prueba.unlink()
    except OSError as exc:
        raise RuntimeError(
            f"No se puede escribir en la carpeta de trabajos {root} ({exc.__class__.__name__}).  "
            "El servicio corre con el usuario 10001, no como root.  Si se ha montado una carpeta "
            f"del servidor en {root}, hay que darle permiso: en el servidor, "
            "'sudo chown -R 10001:10001 <carpeta>' (y 'chmod 700 <carpeta>').  Con el volumen "
            "con nombre del docker-compose no hace falta.  Se configura con TEMP_ROOT."
        ) from exc


def scratch_dir(prefix: str) -> "TemporaryDirectory[str]":
    """Carpeta temporal de trabajo, bajo el directorio del servicio.

    Las imágenes de las páginas llevan TODO lo que lleva el documento, así que
    tienen que caer donde el despliegue sabe borrar —en el contenedor, la
    memoria temporal que se monta para eso— y no en el /tmp del sistema, que
    nadie vigila.  El borrado normal lo hace el propio contexto al salir;
    esto es para que lo que quede tras un corte brusco quede en un solo sitio.
    """
    root = Path(settings.temp_root)
    root.mkdir(parents=True, exist_ok=True)
    tmp = TemporaryDirectory(prefix=prefix, dir=root)
    # Deja dicho de quien es: la limpieza de arranque de otra instancia respeta
    # las carpetas de los procesos vivos y borra solo los restos.
    propiedad.marcar_carpeta(Path(tmp.name))
    return tmp


class TempJobDirectory:
    def __init__(self) -> None:
        self.job_id = uuid4().hex
        self._tmp: TemporaryDirectory[str] | None = None
        self.path: Path | None = None

    def __enter__(self) -> "TempJobDirectory":
        root = Path(settings.temp_root)
        root.mkdir(parents=True, exist_ok=True)

        self._tmp = TemporaryDirectory(
            prefix=f"{self.job_id}_",
            dir=root,
        )
        self.path = Path(self._tmp.name)
        propiedad.marcar_carpeta(self.path)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if settings.delete_temp_files and self._tmp:
            self._tmp.cleanup()