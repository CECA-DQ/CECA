from fastapi import FastAPI

from src.core.logging import setup_logging
from src.routes.projects import router as projects_router

setup_logging()

app = FastAPI(title="CECA API", version="0.1.0")

app.include_router(projects_router)


@app.get("/healthz", tags=["ops"])
async def health_check() -> dict:
    return {"status": "ok"}
