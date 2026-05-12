from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.core.logging import setup_logging
from src.routes.assignments import router as assignments_router
from src.routes.kpis import router as kpis_router
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


@app.get("/healthz", tags=["ops"])
async def health_check() -> dict:
    return {"status": "ok"}
