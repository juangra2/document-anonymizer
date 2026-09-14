from contextlib import asynccontextmanager

from fastapi import FastAPI

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

# Normaliza disabled_types (mayúsculas, espacios, lista vacía) antes de que el
# Enum lo valide: hecha dentro de la ruta, la normalización llegaba tarde.
app.add_middleware(DisabledTypesQueryMiddleware)