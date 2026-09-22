from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.health import router as health_router
from app.api.query_normalization import DisabledTypesQueryMiddleware
from app.api.jobs import router as jobs_router
from app.api.routes import router as anonymize_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.services.jobs import job_store


configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Lo que quedó de una ejecución anterior son documentos que nadie puede ya
    # descargar —sus identificadores murieron con el proceso— pero que siguen
    # en disco con datos personales dentro.
    job_store.purge_previous_runs()
    # Los trabajadores arrancan solos con el primer trabajo; aquí solo se
    # garantiza que paran ordenadamente al cerrar el servidor.
    yield
    job_store.shutdown()


# root_path deja la aplicación colgando de un prefijo cuando la sirve un proxy
# inverso, sin que ninguna ruta lo lleve escrito.
app = FastAPI(title=settings.app_name, root_path=settings.root_path, lifespan=lifespan)

app.include_router(health_router)
app.include_router(jobs_router)
app.include_router(anonymize_router)

# La interfaz va DESPUÉS de las rutas de la API: Starlette prueba las rutas en
# orden, así que /jobs, /health, /entity-types y /docs siguen contestando lo de
# siempre y todo lo demás lo sirve la carpeta web.  La página se sirve sin
# caché para que tras una actualización nadie mezcle una versión vieja con un
# servicio nuevo; los ficheros sueltos sí se cachean.
_WEB_DIR = Path(__file__).parent / "web"


class _NoCacheIndex(StaticFiles):
    def file_response(self, *args, **kwargs):  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


if _WEB_DIR.is_dir():
    app.mount("/", _NoCacheIndex(directory=_WEB_DIR, html=True), name="web")

# Normaliza disabled_types (mayúsculas, espacios, lista vacía) antes de que el
# Enum lo valide: hecha dentro de la ruta, la normalización llegaba tarde.
app.add_middleware(DisabledTypesQueryMiddleware)