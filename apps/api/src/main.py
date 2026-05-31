import logging
import os
import traceback

# Ensure ffmpeg/ffprobe are on PATH regardless of how the process was launched.
# On macOS with Homebrew the binaries live here but may not be symlinked globally.
_FFMPEG_BIN = "/usr/local/opt/ffmpeg/bin"
if os.path.isdir(_FFMPEG_BIN) and _FFMPEG_BIN not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _FFMPEG_BIN + ":" + os.environ.get("PATH", "")

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
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

_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
    "http://localhost:3002",
    "http://127.0.0.1:3002",
]

app = FastAPI(title="CECA API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
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


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    body = await request.body()
    logging.getLogger(__name__).warning(
        "422 on %s %s — body: %s — errors: %s",
        request.method, request.url.path, body.decode(errors="replace"), exc.errors(),
    )
    origin = request.headers.get("origin", "")
    headers = {"Access-Control-Allow-Origin": origin} if origin in _CORS_ORIGINS else {}
    return JSONResponse(status_code=422, content={"detail": exc.errors()}, headers=headers)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logging.getLogger(__name__).error("Unhandled exception: %s\n%s", exc, traceback.format_exc())
    # Starlette does not guarantee CORS headers on responses generated inside
    # exception handlers, so we add them manually here.
    origin = request.headers.get("origin", "")
    headers = {"Access-Control-Allow-Origin": origin} if origin in _CORS_ORIGINS else {}
    return JSONResponse(status_code=500, content={"detail": str(exc)}, headers=headers)


@app.get("/healthz", tags=["ops"])
async def health_check() -> dict:
    return {"status": "ok"}
