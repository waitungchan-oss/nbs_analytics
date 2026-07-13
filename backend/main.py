from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers import dashboard, decisions, exports, health, insights, stability, upload


def create_app() -> FastAPI:
    app = FastAPI(
        title="NBS Analytics API",
        version="0.1.0",
        description="Read-only API baseline for the NBS Analytics dashboard.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    def root() -> dict:
        return {
            "service": "nbs-analytics-api",
            "status": "ok",
            "frontend": "http://127.0.0.1:5173/",
            "docs": "/docs",
            "health": "/api/health",
        }

    app.include_router(health.router)
    app.include_router(dashboard.router)
    app.include_router(decisions.router)
    app.include_router(exports.router)
    app.include_router(stability.router)
    app.include_router(upload.router)
    app.include_router(insights.router)
    return app


app = create_app()
