import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.core.logging import setup_logging
from src.routes.archivo import router as archivo_router
from src.routes.assignments import router as assignments_router
from src.routes.audio_mix import router as audio_mix_router
from src.routes.cesta import router as cesta_router
from src.routes.cola import router as cola_router
from src.routes.grafismo import router as grafismo_router
from src.routes.highlights import router as highlights_router
from src.routes.kpis import router as kpis_router
from src.routes.generar_pieza import router as generar_pieza_router
from src.routes.montaje import router as montaje_router
from src.routes.projects import router as projects_router

setup_logging()

app = FastAPI(title="CECA API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects_router)
app.include_router(assignments_router)
app.include_router(kpis_router)
app.include_router(cesta_router)
app.include_router(grafismo_router)
app.include_router(audio_mix_router)
app.include_router(archivo_router)
app.include_router(highlights_router)
app.include_router(cola_router)
app.include_router(montaje_router)
app.include_router(generar_pieza_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logging.getLogger(__name__).error("Unhandled exception: %s\n%s", exc, traceback.format_exc())
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.get("/healthz", tags=["ops"])
async def health_check() -> dict:
    return {"status": "ok"}
