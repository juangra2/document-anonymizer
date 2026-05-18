from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from app.core.config import settings


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