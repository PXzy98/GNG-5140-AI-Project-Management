"""
Drift Detector Service — LLM-only concept validation.

Pipeline:
  1. Baseline Setup — ingest SOW/charter → extract scope boundaries
  2. Drift Check   — extract asks → compare vs baseline → score → classify
  3. Alert         — generate alert if alignment_score < 0.6 or type != within_scope

Supports 3 LLM tiers. Traditional TF-IDF similarity used as supplemental signal.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Any

from api.models.database import MockStorage, get_storage
from api.models.enums import AlertSeverity, AlertStatus, DriftType
from api.models.schemas import (
    AlertResolveRequest,
    BaselineRequest,
    DriftAlert,
    DriftCheckRequest,
    DriftCheckResponse,
    ScopeBaseline,
)
from api.utils import llm_client
from api.utils.traditional_methods import compute_text_similarity

logger = logging.getLogger(__name__)

_DRIFT_ALERT_THRESHOLD = 0.6
_SEVERITY_MAP = {
    DriftType.CONTRADICTS_SCOPE: AlertSeverity.CRITICAL,
    DriftType.SCOPE_EXPANSION: AlertSeverity.HIGH,
    DriftType.NEW_REQUIREMENT: AlertSeverity.HIGH,
    DriftType.AMBIGUOUS: AlertSeverity.MEDIUM,
    DriftType.WITHIN_SCOPE: AlertSeverity.LOW,
}


# ---------------------------------------------------------------------------
# Baseline extraction
# ---------------------------------------------------------------------------

async def extract_baseline(
    request: BaselineRequest,
    storage: MockStorage | None = None,
) -> ScopeBaseline:
    storage = storage or get_storage()

    artifact = storage.get("artifacts", request.artifact_id)
    if not artifact:
        raise ValueError(f"Artifact {request.artifact_id} not found")

    content = artifact.get("content_preview", "") if isinstance(artifact, dict) else ""

    prompt = (
        "You are a scope analyst. Read the following project document (SOW or project charter) "
        "and extract the scope boundaries. Return a JSON object with keys:\n"
        "  in_scope: list of strings (activities/items explicitly in scope)\n"
        "  out_of_scope: list of strings (activities/items explicitly excluded)\n"
        "  deliverables: list of strings (concrete deliverables committed)\n"
        "  constraints: list of strings (budget caps, deadlines, regulatory constraints)\n"
        "Return ONLY the JSON object.\n\n"
        f"DOCUMENT:\n{content[:5000]}"
    )

    resp = await llm_client.complete(
        messages=[{"role": "user", "content": prompt}],
        tier=request.llm_tier,
        expect_json=True,
        max_tokens=2000,
    )

    parsed = resp.parsed_json if (resp.parsed_json and isinstance(resp.parsed_json, dict)) else {}

    baseline = ScopeBaseline(
        baseline_id=f"bsl_{uuid.uuid4().hex[:8]}",
        project_id=request.project_id,
        version=request.version,
        in_scope=parsed.get("in_scope", []),
        out_of_scope=parsed.get("out_of_scope", []),
        deliverables=parsed.get("deliverables", []),
        constraints=parsed.get("constraints", []),
        source_artifact_id=request.artifact_id,
        created_at=datetime.utcnow(),
    )

    # Store; always use project_id as key so later versions overwrite
    storage_key = f"{request.project_id}::{request.version}"
    storage.put("baselines", storage_key, baseline.model_dump())
    # Also track latest
    storage.put("baselines_latest", request.project_id, storage_key)

    logger.info(
        "Baseline extracted: project=%s version=%s in_scope=%d out_of_scope=%d",
        request.project_id, request.version,
        len(baseline.in_scope), len(baseline.out_of_scope),
    )
    return baseline


def get_latest_baseline(
    project_id: str, storage: MockStorage
) -> ScopeBaseline | None:
    key = storage.get("baselines_latest", project_id)
    if not key:
        return None
    data = storage.get("baselines", key)
    if not data:
        return None
    return ScopeBaseline.model_validate(data) if isinstance(data, dict) else data


# ---------------------------------------------------------------------------
# Drift check
# ---------------------------------------------------------------------------

async def _llm_drift_check(
    content: str,
    baseline: ScopeBaseline,
    tier: str,
) -> dict[str, Any]:
    """Ask the LLM to classify drift. Returns dict with alignment_score, drift_type, etc."""
    baseline_summary = (
        f"IN SCOPE: {'; '.join(baseline.in_scope[:10])}\n"
        f"OUT OF SCOPE: {'; '.join(baseline.out_of_scope[:10])}\n"
        f"DELIVERABLES: {'; '.join(baseline.deliverables[:10])}\n"
        f"CONSTRAINTS: {'; '.join(baseline.constraints[:10])}"
    )

    prompt = (
        "You are a project scope governance analyst. Compare the following document against "
        "the project scope baseline and classify whether it represents scope drift.\n\n"
        "Return a JSON object with keys:\n"
        "  alignment_score: float 0.0-1.0 (1.0 = perfectly within scope)\n"
        "  drift_type: one of within_scope / new_requirement / scope_expansion / "
        "contradicts_scope / ambiguous\n"
        "  detected_ask: string (the specific request or statement that triggered drift, or '')\n"
        "  baseline_reference: string (which baseline item this conflicts with, or '')\n"
        "  suggested_action: string (recommended next step for project manager)\n"
        "  reasoning: string (brief explanation)\n\n"
        f"SCOPE BASELINE:\n{baseline_summary}\n\n"
        f"NEW DOCUMENT:\n{content[:3000]}"
    )

    resp = await llm_client.complete(
        messages=[{"role": "user", "content": prompt}],
        tier=tier,
        expect_json=True,
        max_tokens=1500,
    )

    if resp.error or not isinstance(resp.parsed_json, dict):
        logger.warning("LLM drift check failed: %s", resp.error)
        # Fallback to TF-IDF similarity as rough signal
        baseline_text = " ".join(baseline.in_scope + baseline.deliverables)
        sim = compute_text_similarity(content, baseline_text)
        return {
            "alignment_score": sim,
            "drift_type": "within_scope" if sim > 0.6 else "ambiguous",
            "detected_ask": "",
            "baseline_reference": "",
            "suggested_action": "Manual review recommended (LLM unavailable).",
            "reasoning": "Computed via TF-IDF similarity fallback.",
        }

    return resp.parsed_json


async def check_drift(
    request: DriftCheckRequest,
    storage: MockStorage | None = None,
) -> DriftCheckResponse:
    storage = storage or get_storage()
    t0 = time.perf_counter()

    artifact = storage.get("artifacts", request.artifact_id)
    if not artifact:
        raise ValueError(f"Artifact {request.artifact_id} not found")
    content = artifact.get("content_preview", "") if isinstance(artifact, dict) else ""

    baseline = get_latest_baseline(request.project_id, storage)
    if not baseline:
        raise ValueError(f"No baseline found for project {request.project_id}")

    result = await _llm_drift_check(content, baseline, request.llm_tier)

    # Parse result
    raw_score = float(result.get("alignment_score", 1.0))
    alignment_score = max(0.0, min(1.0, raw_score))

    drift_type_str = str(result.get("drift_type", "within_scope")).lower()
    try:
        drift_type = DriftType(drift_type_str)
    except ValueError:
        drift_type = DriftType.AMBIGUOUS

    alerts: list[DriftAlert] = []
    if alignment_score < _DRIFT_ALERT_THRESHOLD or drift_type != DriftType.WITHIN_SCOPE:
        alert = DriftAlert(
            alert_id=f"alt_{uuid.uuid4().hex[:8]}",
            project_id=request.project_id,
            artifact_id=request.artifact_id,
            drift_type=drift_type,
            alignment_score=alignment_score,
            detected_ask=str(result.get("detected_ask", ""))[:300],
            baseline_reference=str(result.get("baseline_reference", ""))[:300],
            suggested_action=str(result.get("suggested_action", ""))[:300],
            severity=_SEVERITY_MAP.get(drift_type, AlertSeverity.MEDIUM),
            status=AlertStatus.OPEN,
            created_at=datetime.utcnow(),
        )
        alerts.append(alert)
        storage.put("drift_alerts", alert.alert_id, alert.model_dump())

        # Index by project
        proj_alerts = storage.get("project_drift_alerts", request.project_id) or []
        proj_alerts.append(alert.alert_id)
        storage.put("project_drift_alerts", request.project_id, proj_alerts)

    latency_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "Drift check: project=%s artifact=%s type=%s score=%.2f alerts=%d latency=%.0fms",
        request.project_id, request.artifact_id, drift_type.value,
        alignment_score, len(alerts), latency_ms,
    )

    return DriftCheckResponse(
        project_id=request.project_id,
        artifact_id=request.artifact_id,
        alerts=alerts,
        overall_alignment_score=alignment_score,
        drift_type=drift_type,
        latency_ms=latency_ms,
    )


# ---------------------------------------------------------------------------
# Alert management
# ---------------------------------------------------------------------------

def get_project_alerts(
    project_id: str, storage: MockStorage | None = None
) -> list[DriftAlert]:
    storage = storage or get_storage()
    alert_ids: list[str] = storage.get("project_drift_alerts", project_id) or []
    alerts: list[DriftAlert] = []
    for aid in alert_ids:
        data = storage.get("drift_alerts", aid)
        if data:
            alerts.append(
                DriftAlert.model_validate(data) if isinstance(data, dict) else data
            )
    return alerts


def resolve_alert(
    alert_id: str,
    request: AlertResolveRequest,
    storage: MockStorage | None = None,
) -> DriftAlert | None:
    storage = storage or get_storage()
    data = storage.get("drift_alerts", alert_id)
    if not data:
        return None
    alert = DriftAlert.model_validate(data) if isinstance(data, dict) else data
    new_status = (
        AlertStatus.RESOLVED
        if request.resolution == "resolved"
        else AlertStatus.FALSE_POSITIVE
    )
    updated = alert.model_copy(update={"status": new_status})
    storage.put("drift_alerts", alert_id, updated.model_dump())
    return updated
