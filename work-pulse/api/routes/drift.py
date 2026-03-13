"""
Drift Detector routes — /api/v1/drift/*
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.models.schemas import (
    AlertResolveRequest,
    BaselineRequest,
    DriftAlert,
    DriftCheckRequest,
    DriftCheckResponse,
    ScopeBaseline,
)
from api.services.drift_service import (
    check_drift,
    extract_baseline,
    get_project_alerts,
    resolve_alert,
)

router = APIRouter(prefix="/api/v1/drift", tags=["drift"])


@router.post("/baseline", response_model=ScopeBaseline, summary="Set/update scope baseline")
async def set_baseline_endpoint(request: BaselineRequest) -> ScopeBaseline:
    try:
        return await extract_baseline(request)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/check", response_model=DriftCheckResponse, summary="Check artifact for drift")
async def check_drift_endpoint(request: DriftCheckRequest) -> DriftCheckResponse:
    try:
        return await check_drift(request)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/project/{project_id}/alerts",
    response_model=list[DriftAlert],
    summary="Get drift alerts for project",
)
def get_alerts_endpoint(project_id: str) -> list[DriftAlert]:
    return get_project_alerts(project_id)


@router.put(
    "/alert/{alert_id}/resolve",
    response_model=DriftAlert,
    summary="Resolve or dismiss an alert",
)
def resolve_alert_endpoint(alert_id: str, request: AlertResolveRequest) -> DriftAlert:
    alert = resolve_alert(alert_id, request)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
    return alert
