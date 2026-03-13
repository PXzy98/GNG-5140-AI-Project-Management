"""
Daily Brief routes — /api/v1/brief/*
Also hosts the test-only priority ranker endpoint.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from api.models.schemas import (
    BriefGenerateRequest,
    BriefHistoryItem,
    CloseDayResponse,
    DailyPlanResponse,
    DailyPlanTask,
    InjectTaskRequest,
    PriorityRankRequest,
    PriorityRankResponse,
    RankingHistoryEntry,
    ReRankRequest,
    ReRankResponse,
    StreakResponse,
    TaskUpdateRequest,
)
from api.services.brief_service import (
    add_task,
    close_day,
    delete_task,
    generate_brief,
    get_brief,
    get_ranking_history,
    get_report,
    get_streak,
    get_tasks,
    inject_task,
    list_briefs,
    rerank,
    update_task,
)
from api.services.priority_ranker import rank_tasks

router = APIRouter(tags=["brief"])

# ---------------------------------------------------------------------------
# Morning Brief generation
# ---------------------------------------------------------------------------

@router.post(
    "/api/v1/brief/generate",
    response_model=DailyPlanResponse,
    summary="Generate today's daily brief",
)
async def generate_brief_endpoint(request: BriefGenerateRequest) -> DailyPlanResponse:
    return await generate_brief(request)


@router.get(
    "/api/v1/brief/today",
    response_model=DailyPlanResponse,
    summary="Get today's brief (shortcut)",
)
def get_today_brief() -> DailyPlanResponse:
    from api.services.brief_service import _today
    brief = get_brief(_today())
    if brief is None:
        raise HTTPException(status_code=404, detail="No brief generated for today yet")
    return brief


@router.get(
    "/api/v1/brief/history",
    response_model=list[BriefHistoryItem],
    summary="List historical briefs",
)
def list_briefs_endpoint(
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[BriefHistoryItem]:
    return list_briefs(limit=limit, offset=offset)


@router.get(
    "/api/v1/brief/streak",
    response_model=StreakResponse,
    summary="Completion streak statistics",
)
def get_streak_endpoint() -> StreakResponse:
    return get_streak()


# ---------------------------------------------------------------------------
# Date-specific brief endpoints
# NOTE: fixed-path routes (/history, /streak, /today) must come BEFORE
#       the parameterised /{date} routes so FastAPI matches them first.
# ---------------------------------------------------------------------------

@router.get(
    "/api/v1/brief/{date}",
    response_model=DailyPlanResponse,
    summary="Get brief for a specific date",
)
def get_brief_endpoint(date: str) -> DailyPlanResponse:
    brief = get_brief(date)
    if brief is None:
        raise HTTPException(status_code=404, detail=f"No brief found for {date}")
    return brief


@router.post(
    "/api/v1/brief/{date}/regenerate",
    response_model=DailyPlanResponse,
    summary="Regenerate brief with latest data",
)
async def regenerate_brief_endpoint(date: str, request: BriefGenerateRequest) -> DailyPlanResponse:
    request.date = date
    return await generate_brief(request)


# ---------------------------------------------------------------------------
# Task management within a brief
# ---------------------------------------------------------------------------

@router.get(
    "/api/v1/brief/{date}/tasks",
    response_model=list[DailyPlanTask],
    summary="Get ranked task list for date",
)
def get_tasks_endpoint(date: str) -> list[DailyPlanTask]:
    return get_tasks(date)


@router.post(
    "/api/v1/brief/{date}/tasks",
    response_model=DailyPlanTask,
    summary="Manually add a task to brief",
)
def add_task_endpoint(date: str, body: InjectTaskRequest) -> DailyPlanTask:
    return add_task(
        date=date,
        name=body.name,
        project_id=body.project_id,
        deadline=body.deadline,
        owner=body.owner,
        priority_hint=body.priority_hint,
        source="manual",
    )


@router.put(
    "/api/v1/brief/{date}/tasks/{task_id}",
    response_model=DailyPlanTask,
    summary="Update task status / attributes",
)
def update_task_endpoint(date: str, task_id: str, request: TaskUpdateRequest) -> DailyPlanTask:
    task = update_task(date, task_id, request)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found in {date}")
    return task


@router.delete(
    "/api/v1/brief/{date}/tasks/{task_id}",
    summary="Remove task from brief",
)
def delete_task_endpoint(date: str, task_id: str) -> dict[str, bool]:
    ok = delete_task(date, task_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found in {date}")
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Mid-day re-rank
# ---------------------------------------------------------------------------

@router.post(
    "/api/v1/brief/{date}/rerank",
    response_model=ReRankResponse,
    summary="Trigger re-ranking for the day",
)
async def rerank_endpoint(date: str, request: ReRankRequest) -> ReRankResponse:
    try:
        return await rerank(date, request)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/api/v1/brief/{date}/inject",
    summary="Inject new task and (optionally) trigger re-rank",
)
async def inject_endpoint(date: str, request: InjectTaskRequest) -> dict[str, Any]:
    try:
        return await inject_task(date, request)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/api/v1/brief/{date}/ranking-history",
    response_model=list[RankingHistoryEntry],
    summary="View ranking change history for date",
)
def ranking_history_endpoint(date: str) -> list[RankingHistoryEntry]:
    return get_ranking_history(date)


# ---------------------------------------------------------------------------
# End-of-day
# ---------------------------------------------------------------------------

@router.post(
    "/api/v1/brief/{date}/close",
    response_model=CloseDayResponse,
    summary="Close the day, roll over incomplete tasks",
)
async def close_day_endpoint(
    date: str,
    use_llm: bool = Query(default=False),
    llm_tier: str = Query(default="small"),
) -> CloseDayResponse:
    try:
        return await close_day(date, use_llm=use_llm, llm_tier=llm_tier)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/api/v1/brief/{date}/report",
    summary="Full day report (morning brief + EOD summary)",
)
def get_report_endpoint(date: str) -> dict[str, Any]:
    report = get_report(date)
    if not report:
        raise HTTPException(status_code=404, detail=f"No report found for {date}")
    return report


# ---------------------------------------------------------------------------
# Test-only: direct priority ranker invocation
# ---------------------------------------------------------------------------

@router.post(
    "/api/v1/_test/priority/rank",
    response_model=PriorityRankResponse,
    summary="[Test] Directly invoke priority ranker",
)
async def test_priority_rank(request: PriorityRankRequest) -> PriorityRankResponse:
    return await rank_tasks(request)
