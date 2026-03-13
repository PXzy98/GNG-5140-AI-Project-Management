"""
Risk Engine routes — /api/v1/risk/*
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.models.schemas import (
    CrossCheckRequest,
    CrossCheckResponse,
    IdentifiedRisk,
    ProjectRiskSummary,
    RiskIdentifyRequest,
    RiskIdentifyResponse,
)
from api.services.risk_service import (
    cross_check,
    get_project_risk_summary,
    get_risk,
    identify_risks,
)

router = APIRouter(prefix="/api/v1/risk", tags=["risk"])


@router.post("/identify", response_model=RiskIdentifyResponse, summary="Identify risks in artifacts")
async def identify_risks_endpoint(request: RiskIdentifyRequest) -> RiskIdentifyResponse:
    return await identify_risks(request)


@router.get(
    "/project/{project_id}",
    response_model=ProjectRiskSummary,
    summary="Project-level risk summary",
)
def project_risk_summary(project_id: str) -> ProjectRiskSummary:
    return get_project_risk_summary(project_id)


@router.get("/{risk_id}", response_model=IdentifiedRisk, summary="Get single risk")
def get_risk_endpoint(risk_id: str) -> IdentifiedRisk:
    risk = get_risk(risk_id)
    if risk is None:
        raise HTTPException(status_code=404, detail=f"Risk {risk_id} not found")
    return risk


@router.post("/cross-check", response_model=CrossCheckResponse, summary="Cross-check artifacts")
async def cross_check_endpoint(request: CrossCheckRequest) -> CrossCheckResponse:
    return await cross_check(request)
