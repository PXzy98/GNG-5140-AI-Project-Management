"""
Work Pulse — FastAPI application entry point.

Mounts all route modules and middleware. Run with:
    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.config import settings
from api.middleware.audit import audit_middleware
from api.routes import brief, drift, ingestion, risk

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Work Pulse",
    description="AI-Powered Project Management Assistant for SSC",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Audit middleware (request/response logging + X-Request-Id)
# ---------------------------------------------------------------------------
from starlette.middleware.base import BaseHTTPMiddleware
app.add_middleware(BaseHTTPMiddleware, dispatch=audit_middleware)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
app.include_router(ingestion.router)
app.include_router(risk.router)
app.include_router(drift.router)
app.include_router(brief.router)


# ---------------------------------------------------------------------------
# Health / root
# ---------------------------------------------------------------------------

@app.get("/health", tags=["meta"], summary="Health check")
async def health() -> dict:
    return {
        "status": "ok",
        "version": "0.1.0",
        "mock_db": settings.use_mock_db,
    }


@app.get("/", tags=["meta"], include_in_schema=False)
async def root() -> dict:
    return {"message": "Work Pulse API — see /docs for the OpenAPI spec."}
