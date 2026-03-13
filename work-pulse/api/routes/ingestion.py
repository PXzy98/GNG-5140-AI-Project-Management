"""
Ingestion routes — /api/v1/ingest/*
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form

from api.models.schemas import (
    ArtifactListResponse,
    ArtifactResponse,
    IngestRequest,
)
from api.services.ingestion_service import (
    get_artifact,
    ingest_email_webhook,
    ingest_text,
    list_artifacts,
)

router = APIRouter(prefix="/api/v1/ingest", tags=["ingestion"])


@router.post("/text", response_model=ArtifactResponse, summary="Ingest plain text")
async def ingest_text_endpoint(request: IngestRequest) -> ArtifactResponse:
    return await ingest_text(request)


@router.post("/file", response_model=ArtifactResponse, summary="Upload a file")
async def ingest_file_endpoint(
    file: UploadFile = File(...),
    source_type: str = Form(default="other"),
    project_id: str | None = Form(default=None),
    use_llm: bool = Form(default=False),
    llm_tier: str = Form(default="small"),
) -> ArtifactResponse:
    raw_bytes = await file.read()
    try:
        content = raw_bytes.decode("utf-8", errors="replace")
    except Exception:
        raise HTTPException(status_code=422, detail="Cannot decode file as text")

    request = IngestRequest(
        content=content,
        source_type=source_type,  # type: ignore[arg-type]
        project_id=project_id,
        metadata={"filename": file.filename, "content_type": file.content_type},
        use_llm=use_llm,
        llm_tier=llm_tier,  # type: ignore[arg-type]
    )
    return await ingest_text(request)


@router.post("/email-webhook", response_model=ArtifactResponse, summary="Bot mailbox webhook")
async def email_webhook_endpoint(payload: dict[str, Any]) -> ArtifactResponse:
    return await ingest_email_webhook(payload)


@router.get("/artifacts", response_model=ArtifactListResponse, summary="List artifacts")
def list_artifacts_endpoint(
    project_id: str | None = Query(default=None),
    artifact_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> ArtifactListResponse:
    return list_artifacts(project_id=project_id, artifact_type=artifact_type, limit=limit)


@router.get("/artifacts/{artifact_id}", response_model=ArtifactResponse, summary="Get artifact")
def get_artifact_endpoint(artifact_id: str) -> ArtifactResponse:
    artifact = get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"Artifact {artifact_id} not found")
    return artifact
