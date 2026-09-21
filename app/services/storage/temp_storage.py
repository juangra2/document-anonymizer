from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from app.core.config import settings


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
    return TemporaryDirectory(prefix=prefix, dir=root)


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
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if settings.delete_temp_files and self._tmp:
            self._tmp.cleanup()