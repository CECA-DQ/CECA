from fastapi import FastAPI

from src.core.logging import setup_logging

setup_logging()

app = FastAPI(title="CECA API", version="0.1.0")

# Routes are registered here as each domain is implemented:
# from src.routes import search, content, basket, ai
# app.include_router(search.router, prefix="/search", tags=["search"])
# app.include_router(content.router, prefix="/content", tags=["content"])
# app.include_router(basket.router, prefix="/basket", tags=["basket"])
# app.include_router(ai.router, prefix="/ai", tags=["ai"])


@app.get("/healthz", tags=["ops"])
async def health_check() -> dict:
    return {"status": "ok"}
