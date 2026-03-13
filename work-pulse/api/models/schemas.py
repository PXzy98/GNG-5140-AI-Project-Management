from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from api.models.enums import (
    AlertSeverity,
    AlertStatus,
    AnalysisDepth,
    ArtifactType,
    DriftType,
    PriorityLevel,
    RiskCategory,
    RiskLevel,
    TaskStatus,
)


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

class IngestRequest(BaseModel):
    content: str
    source_type: Literal["meeting_minutes", "email", "notes", "status_report", "other"]
    project_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    use_llm: bool = False
    llm_tier: Literal["local", "small", "medium", "large"] = "small"
    llm_mode: Literal["hybrid", "full"] = "hybrid"
    # hybrid = traditional steps 1-4 + LLM task extraction (step 5 only)
    # full   = single LLM call handles all steps (type, language, metadata, tasks)


class ExtractedTask(BaseModel):
    task_id: str
    text: str
    owner: str | None = None
    deadline: str | None = None
    priority: PriorityLevel = PriorityLevel.MEDIUM
    status: TaskStatus = TaskStatus.NOT_STARTED
    source_excerpt: str | None = None
    confidence: float = 1.0


class ArtifactResponse(BaseModel):
    artifact_id: str
    status: str
    detected_language: str
    content_preview: str
    word_count: int
    extracted_tasks: list[ExtractedTask] = Field(default_factory=list)
    linked_project: str | None = None
    artifact_type: ArtifactType = ArtifactType.OTHER
    content_hash: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactResponse]
    total: int


# ---------------------------------------------------------------------------
# Risk Engine
# ---------------------------------------------------------------------------

class EvidenceLink(BaseModel):
    artifact_id: str
    excerpt: str
    location: str | None = None
    evidence_strength: float = Field(ge=0.0, le=1.0, default=0.5)


class IdentifiedRisk(BaseModel):
    risk_id: str
    description: str
    category: RiskCategory
    likelihood: int = Field(ge=1, le=5, default=3)
    impact: int = Field(ge=1, le=5, default=3)
    risk_score: float = 0.0
    risk_level: RiskLevel = RiskLevel.MEDIUM
    affected_stakeholders: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceLink] = Field(default_factory=list)
    source_artifact_id: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RiskScore(BaseModel):
    risk_id: str
    likelihood: int
    impact: int
    raw_score: float
    adjusted_score: float
    risk_level: RiskLevel
    adjustments_applied: list[str] = Field(default_factory=list)


class RiskIdentifyRequest(BaseModel):
    artifact_ids: list[str]
    project_id: str | None = None
    use_llm: bool = False
    llm_tier: Literal["local", "small", "medium", "large"] = "small"
    analysis_depth: AnalysisDepth = AnalysisDepth.STANDARD


class RiskIdentifyResponse(BaseModel):
    risks: list[IdentifiedRisk]
    project_id: str | None = None
    analysis_method: str = "traditional"
    latency_ms: float | None = None


class CrossCheckRequest(BaseModel):
    artifact_ids: list[str]
    use_llm: bool = False
    llm_tier: Literal["local", "small", "medium", "large"] = "small"


class Inconsistency(BaseModel):
    description: str
    doc_a_artifact_id: str
    doc_b_artifact_id: str
    doc_a_excerpt: str
    doc_b_excerpt: str
    severity: AlertSeverity = AlertSeverity.MEDIUM


class CrossCheckResponse(BaseModel):
    inconsistencies: list[Inconsistency]
    artifact_ids: list[str]
    latency_ms: float | None = None


class ProjectRiskSummary(BaseModel):
    project_id: str
    total_risks: int
    by_category: dict[str, int] = Field(default_factory=dict)
    by_level: dict[str, int] = Field(default_factory=dict)
    critical_risks: list[IdentifiedRisk] = Field(default_factory=list)
    trend: str = "stable"


# ---------------------------------------------------------------------------
# Priority Ranker
# ---------------------------------------------------------------------------

class TaskInput(BaseModel):
    task_id: str
    name: str
    deadline: str | None = None
    risk_level: RiskLevel | None = None
    rollover_count: int = 0
    status: TaskStatus = TaskStatus.NOT_STARTED
    project_id: str | None = None
    dependencies_blocked: int = 0
    stakeholder_priority: int = 0
    expected_rank_range: list[int] | None = None


class RankedTask(BaseModel):
    task_id: str
    name: str
    rank: int
    priority_level: PriorityLevel
    priority_score: float = Field(ge=0.0, le=100.0)
    reasoning: str = ""
    risk_factors: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    chronic_blocker: bool = False
    boost_applied: list[str] = Field(default_factory=list)


class RankingChange(BaseModel):
    task_id: str
    old_rank: int
    new_rank: int
    reason_for_change: str = ""


class PriorityRankRequest(BaseModel):
    tasks: list[TaskInput]
    context: dict[str, Any] = Field(default_factory=dict)
    use_llm: bool = False
    llm_tier: Literal["local", "small", "medium", "large"] = "small"
    previous_ranking: list[RankedTask] | None = None


class PriorityRankResponse(BaseModel):
    ranked_tasks: list[RankedTask]
    ranking_changes: list[RankingChange] = Field(default_factory=list)
    analysis_method: str = "traditional"
    latency_ms: float | None = None


# ---------------------------------------------------------------------------
# Drift Detector
# ---------------------------------------------------------------------------

class BaselineRequest(BaseModel):
    project_id: str
    artifact_id: str
    version: str = "v1"
    use_llm: bool = True
    llm_tier: Literal["local", "small", "medium", "large"] = "large"


class ScopeBaseline(BaseModel):
    baseline_id: str
    project_id: str
    version: str = "v1"
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    source_artifact_id: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DriftCheckRequest(BaseModel):
    project_id: str
    artifact_id: str
    use_llm: bool = True
    llm_tier: Literal["local", "small", "medium", "large"] = "large"


class DriftAlert(BaseModel):
    alert_id: str
    project_id: str
    artifact_id: str
    drift_type: DriftType
    alignment_score: float = Field(ge=0.0, le=1.0)
    detected_ask: str = ""
    baseline_reference: str = ""
    suggested_action: str = ""
    severity: AlertSeverity = AlertSeverity.MEDIUM
    status: AlertStatus = AlertStatus.OPEN
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DriftCheckResponse(BaseModel):
    project_id: str
    artifact_id: str
    alerts: list[DriftAlert] = Field(default_factory=list)
    overall_alignment_score: float = Field(ge=0.0, le=1.0, default=1.0)
    drift_type: DriftType = DriftType.WITHIN_SCOPE
    latency_ms: float | None = None


class AlertResolveRequest(BaseModel):
    resolution: Literal["resolved", "false_positive"]
    notes: str = ""


# ---------------------------------------------------------------------------
# Daily Brief
# ---------------------------------------------------------------------------

class BriefGenerateRequest(BaseModel):
    project_ids: list[str] = Field(default_factory=list)
    date: str | None = None
    use_llm: bool = False
    llm_tier: Literal["local", "small", "medium", "large"] = "large"
    context_window_days: int = 7
    top_k: int = 10


class MorningBrief(BaseModel):
    summary: str = ""
    sections: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RankingMetadata(BaseModel):
    ranking_id: str
    model_used: str = "traditional"
    tasks_analyzed: int = 0
    artifacts_referenced: int = 0
    version: int = 1
    trigger: str = "morning_generation"
    trigger_ref: str | None = None


class DailyPlanTask(BaseModel):
    daily_task_id: str
    task_id: str
    date: str
    name: str
    status: TaskStatus = TaskStatus.NOT_STARTED
    rank: int = 0
    priority_score: float = 0.0
    priority_level: PriorityLevel = PriorityLevel.MEDIUM
    reasoning: str = ""
    risk_factors: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    deadline: str | None = None
    owner: str | None = None
    rollover_count: int = 0
    is_rollover: bool = False
    chronic_blocker: bool = False
    boost_applied: list[str] = Field(default_factory=list)
    source: str = "manual"  # manual | rolled_over | new_extraction
    project_id: str | None = None


class DailyPlanResponse(BaseModel):
    brief_id: str
    date: str
    status: str = "draft"  # draft | active | closed
    project_ids: list[str] = Field(default_factory=list)
    morning_brief: MorningBrief | None = None
    ranked_tasks: list[DailyPlanTask] = Field(default_factory=list)
    ranking_metadata: RankingMetadata | None = None
    active_risks: list[IdentifiedRisk] = Field(default_factory=list)
    drift_alerts: list[DriftAlert] = Field(default_factory=list)
    generated_at: datetime | None = None
    closed_at: datetime | None = None
    eod_summary: str | None = None
    rerank_count: int = 0


class InjectTaskRequest(BaseModel):
    name: str
    project_id: str | None = None
    deadline: str | None = None
    priority_hint: PriorityLevel = PriorityLevel.MEDIUM
    notes: str = ""
    owner: str | None = None
    trigger_rerank: bool = True
    use_llm: bool = False
    llm_tier: Literal["local", "small", "medium", "large"] = "small"


class ReRankRequest(BaseModel):
    trigger: str = "manual"
    reason: str = ""
    trigger_artifact_id: str | None = None
    use_llm: bool = False
    llm_tier: Literal["local", "small", "medium", "large"] = "small"


class ReRankResponse(BaseModel):
    brief_id: str
    date: str
    reranked_at: datetime
    ranking_metadata: RankingMetadata
    ranking_changes: list[RankingChange] = Field(default_factory=list)
    updated_ranked_tasks: list[DailyPlanTask] = Field(default_factory=list)


class TaskUpdateRequest(BaseModel):
    status: TaskStatus | None = None
    priority_level: PriorityLevel | None = None
    deadline: str | None = None
    owner: str | None = None
    notes: str | None = None


class RankingHistoryEntry(BaseModel):
    version: int
    ranking_id: str
    trigger: str
    trigger_ref: str | None = None
    changes: list[RankingChange] = Field(default_factory=list)
    snapshot_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CloseDayResponse(BaseModel):
    brief_id: str
    date: str
    closed_at: datetime
    completed_tasks: int
    rolled_over_tasks: int
    eod_summary: str = ""
    next_day_seeded: bool = False


class BriefHistoryItem(BaseModel):
    brief_id: str
    date: str
    status: str
    tasks_count: int = 0
    completed_count: int = 0
    generated_at: datetime | None = None


class StreakResponse(BaseModel):
    current_streak: int = 0
    total_days: int = 0
    completion_rate: float = 0.0
    by_date: list[dict[str, Any]] = Field(default_factory=list)


class DailyBriefResponse(BaseModel):
    """Legacy alias kept for backward-compat; new code uses DailyPlanResponse."""
    brief_id: str
    date: str
    project_ids: list[str]
    top_tasks: list[DailyPlanTask] = Field(default_factory=list)
    active_risks: list[IdentifiedRisk] = Field(default_factory=list)
    drift_alerts: list[DriftAlert] = Field(default_factory=list)
    summary: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
