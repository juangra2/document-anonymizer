import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.health import router as health_router
from app.api.query_normalization import DisabledTypesQueryMiddleware
from app.api.jobs import router as jobs_router
from app.api.routes import router as anonymize_router
from app.core.config import settings
from app.core.dispositivo import avisar_al_arrancar
from app.core.logging import configure_logging
from app.services.storage.temp_storage import comprobar_carpeta_de_trabajos
from app.services.jobs import job_store


configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Antes de nada: si no se puede escribir en la carpeta de trabajos, parar
    # aqui con el motivo, no con el primer documento.
    try:
        comprobar_carpeta_de_trabajos()
    except RuntimeError as exc:
        # Salida directa: con SystemExit, uvicorn imprimia 50 lineas de traza
        # y el motivo quedaba enterrado en medio.
        logging.getLogger("app").critical("%s", exc)
        logging.shutdown()
        os._exit(1)
    avisar_al_arrancar()
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
# siempre y todo lo demás lo sirve la carpeta web.
_WEB_DIR = Path(__file__).parent / "web"


class _SinCache(StaticFiles):
    """Sirve la interfaz obligando a revalidar SIEMPRE, fichero a fichero.

    Con la página sin caché pero los estilos y los módulos cacheados, una
    actualización dejaba a la gente con el HTML nuevo y el CSS viejo: la
    pantalla salía descuadrada y nadie entendía por qué.  `no-cache` no
    significa «no guardes», significa «pregunta antes de usarlo»: el navegador
    conserva el fichero y recibe un 304 vacío mientras no cambie, que en una
    intranet con ficheros de kilobytes no cuesta nada.
    """

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


if _WEB_DIR.is_dir():
    app.mount("/", _SinCache(directory=_WEB_DIR, html=True), name="web")

# Normaliza disabled_types (mayúsculas, espacios, lista vacía) antes de que el
# Enum lo valide: hecha dentro de la ruta, llegaría tarde.
app.add_middleware(DisabledTypesQueryMiddleware)