"""
Daily Brief Service — Main orchestrator.

Responsibilities:
  A. Morning Brief Generation (Steps 1-5 from spec)
  B. Mid-day Re-Rank (manual or artifact-triggered)
  C. Task inject + re-rank
  D. End-of-Day Close + Rollover
  E. History, streak, report queries

Priority Ranker is used as an internal component (not REST-exposed here).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

from api.models.database import MockStorage, get_storage
from api.models.enums import PriorityLevel, TaskStatus
from api.models.schemas import (
    BriefGenerateRequest,
    BriefHistoryItem,
    CloseDayResponse,
    DailyPlanResponse,
    DailyPlanTask,
    InjectTaskRequest,
    MorningBrief,
    PriorityRankRequest,
    RankingChange,
    RankingHistoryEntry,
    RankingMetadata,
    ReRankRequest,
    ReRankResponse,
    StreakResponse,
    TaskInput,
    TaskUpdateRequest,
)
from api.services.priority_ranker import rank_tasks
from api.utils import llm_client

logger = logging.getLogger(__name__)

_DATE_FMT = "%Y-%m-%d"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _today() -> str:
    return datetime.utcnow().strftime(_DATE_FMT)


def _yesterday(date: str) -> str:
    d = datetime.strptime(date, _DATE_FMT) - timedelta(days=1)
    return d.strftime(_DATE_FMT)


def _tomorrow(date: str) -> str:
    d = datetime.strptime(date, _DATE_FMT) + timedelta(days=1)
    return d.strftime(_DATE_FMT)


def _make_daily_task_id() -> str:
    return f"dt_{uuid.uuid4().hex[:8]}"


def _make_brief_id(date: str) -> str:
    return f"brief_{date.replace('-', '')}"


def _plan_tasks_key(date: str) -> str:
    return f"plan_tasks_{date}"


def _load_plan(date: str, storage: MockStorage) -> dict[str, Any] | None:
    return storage.get("daily_plans", date)


def _save_plan(date: str, plan: dict[str, Any], storage: MockStorage) -> None:
    storage.put("daily_plans", date, plan)


def _load_tasks(date: str, storage: MockStorage) -> list[dict[str, Any]]:
    return storage.get(_plan_tasks_key(date), "__all__") or []


def _save_tasks(date: str, tasks: list[dict[str, Any]], storage: MockStorage) -> None:
    storage.put(_plan_tasks_key(date), "__all__", tasks)


def _ranked_task_to_plan_task(rt: Any, date: str, source: str = "manual") -> dict[str, Any]:
    """Convert a RankedTask to a DailyPlanTask dict."""
    return {
        "daily_task_id": _make_daily_task_id(),
        "task_id": rt.task_id,
        "date": date,
        "name": rt.name,
        "status": TaskStatus.NOT_STARTED.value,
        "rank": rt.rank,
        "priority_score": rt.priority_score,
        "priority_level": rt.priority_level.value if hasattr(rt.priority_level, 'value') else rt.priority_level,
        "reasoning": rt.reasoning,
        "risk_factors": rt.risk_factors,
        "evidence_refs": rt.evidence_refs,
        "deadline": None,
        "owner": None,
        "rollover_count": 0,
        "is_rollover": source == "rolled_over",
        "chronic_blocker": rt.chronic_blocker,
        "boost_applied": rt.boost_applied,
        "source": source,
        "project_id": None,
    }


def _task_inputs_from_plan_tasks(plan_tasks: list[dict[str, Any]]) -> list[TaskInput]:
    inputs = []
    for t in plan_tasks:
        rl_str = t.get("risk_level")
        from api.models.enums import RiskLevel
        risk_level = None
        if rl_str:
            try:
                risk_level = RiskLevel(rl_str)
            except ValueError:
                pass
        try:
            status = TaskStatus(t.get("status", "not_started"))
        except ValueError:
            status = TaskStatus.NOT_STARTED
        inputs.append(TaskInput(
            task_id=t.get("task_id", t.get("daily_task_id", "")),
            name=t.get("name", ""),
            deadline=t.get("deadline"),
            risk_level=risk_level,
            rollover_count=t.get("rollover_count", 0),
            status=status,
            project_id=t.get("project_id"),
            dependencies_blocked=t.get("dependencies_blocked", 0),
            stakeholder_priority=t.get("stakeholder_priority", 0),
        ))
    return inputs


def _make_ranking_metadata(
    ranked_tasks: list[Any],
    version: int,
    method: str,
    trigger: str = "morning_generation",
    trigger_ref: str | None = None,
) -> RankingMetadata:
    from api.utils.llm_client import MODEL_TIERS
    tier = method.replace("llm_", "") if method.startswith("llm_") else None
    model = MODEL_TIERS.get(tier, "traditional") if tier else "traditional"
    return RankingMetadata(
        ranking_id=f"rank_{uuid.uuid4().hex[:8]}",
        model_used=model,
        tasks_analyzed=len(ranked_tasks),
        artifacts_referenced=0,
        version=version,
        trigger=trigger,
        trigger_ref=trigger_ref,
    )


async def _generate_morning_brief_llm(
    tasks: list[dict[str, Any]],
    active_risks: list[Any],
    drift_alerts: list[Any],
    tier: str,
) -> MorningBrief:
    """Ask LLM to generate a narrative morning brief from ranked data."""
    task_summary = json.dumps(
        [{"rank": t.get("rank"), "name": t.get("name"),
          "priority_level": t.get("priority_level"), "deadline": t.get("deadline"),
          "rollover_count": t.get("rollover_count", 0), "source": t.get("source")}
         for t in tasks[:10]],
        default=str,
    )
    risk_summary = json.dumps(
        [{"description": r.description if hasattr(r, "description") else str(r),
          "risk_level": r.risk_level.value if hasattr(r, "risk_level") else "unknown"}
         for r in active_risks[:5]],
        default=str,
    )
    prompt = (
        "You are a project management assistant for the Government of Canada (SSC). "
        "Generate a concise daily brief based on the ranked tasks and risks below. "
        "Return a JSON object with keys:\n"
        "  summary: string (2-3 sentences overview of today's priorities)\n"
        "  sections: list of {title, items: list[string]} — include: "
        "'Top Priority Items', 'Rolled-Over Warnings' (if any), 'Risk Alerts' (if any), 'New Items' (if any)\n"
        "  warnings: list of strings (critical issues requiring immediate attention)\n"
        "Return ONLY the JSON object.\n\n"
        f"RANKED TASKS:\n{task_summary}\n\nACTIVE RISKS:\n{risk_summary}"
    )
    resp = await llm_client.complete(
        messages=[{"role": "user", "content": prompt}],
        tier=tier,
        expect_json=True,
        max_tokens=1500,
    )
    if resp.error or not isinstance(resp.parsed_json, dict):
        return _generate_morning_brief_traditional(tasks, active_risks, drift_alerts)
    p = resp.parsed_json
    return MorningBrief(
        summary=str(p.get("summary", "")),
        sections=p.get("sections", []),
        warnings=p.get("warnings", []),
    )


def _generate_morning_brief_traditional(
    tasks: list[dict[str, Any]],
    active_risks: list[Any],
    drift_alerts: list[Any],
) -> MorningBrief:
    top = tasks[:5]
    rolled = [t for t in tasks if t.get("is_rollover")]
    critical = [t for t in tasks if t.get("priority_level") in ("critical",)]
    new_items = [t for t in tasks if t.get("source") == "new_extraction"]

    summary = (
        f"Today: {len(tasks)} task(s) ranked. "
        + (f"{len(critical)} critical. " if critical else "")
        + (f"{len(rolled)} rolled over from previous day. " if rolled else "")
        + (f"{len(active_risks)} active risk(s)." if active_risks else "No active risks flagged.")
    )

    sections: list[dict[str, Any]] = [
        {
            "title": "Top Priority Items",
            "items": [f"#{t['rank']} {t['name']} [{t.get('priority_level','?')}]" for t in top],
        }
    ]
    if rolled:
        sections.append({
            "title": "Rolled-Over Warnings",
            "items": [f"{t['name']} (rolled over {t.get('rollover_count',1)} time(s))" for t in rolled],
        })
    if active_risks:
        sections.append({
            "title": "Risk Alerts",
            "items": [
                f"{getattr(r, 'risk_level', '?').upper() if hasattr(r, 'risk_level') else '?'}: "
                f"{getattr(r, 'description', str(r))[:100]}"
                for r in active_risks[:3]
            ],
        })
    if new_items:
        sections.append({
            "title": "New Items",
            "items": [f"{t['name']} (newly extracted)" for t in new_items],
        })

    warnings = [
        f"{t['name']} rolled over {t.get('rollover_count',1)} time(s) — needs attention"
        for t in rolled if t.get("rollover_count", 1) >= 2
    ]
    return MorningBrief(summary=summary, sections=sections, warnings=warnings)


async def _generate_eod_summary_llm(
    tasks: list[dict[str, Any]], tier: str
) -> str:
    completed = [t for t in tasks if t.get("status") == "completed"]
    rolled = [t for t in tasks if t.get("status") == "rolled_over"]
    prompt = (
        "Summarize today's project management day in 2-4 sentences. "
        f"Completed tasks: {[t['name'] for t in completed]}. "
        f"Rolled-over tasks: {[t['name'] for t in rolled]}. "
        "Be concise and factual. No JSON needed."
    )
    resp = await llm_client.complete(
        messages=[{"role": "user", "content": prompt}],
        tier=tier,
        expect_json=False,
        max_tokens=300,
    )
    if resp.error:
        completed_n = len(completed)
        rolled_n = len(rolled)
        return (
            f"Day closed. {completed_n} task(s) completed, {rolled_n} rolled over to tomorrow."
        )
    return resp.content.strip()


# ---------------------------------------------------------------------------
# Core service functions
# ---------------------------------------------------------------------------

async def generate_brief(
    request: BriefGenerateRequest,
    storage: MockStorage | None = None,
) -> DailyPlanResponse:
    """
    Step 1: Collect context (rolled-over tasks, active risks, drift alerts, artifacts).
    Step 2: Rank all candidate tasks via Priority Ranker.
    Step 3: Generate morning brief narrative.
    Step 4: Persist and return.
    """
    storage = storage or get_storage()
    date = request.date or _today()
    brief_id = _make_brief_id(date)

    # -- Step 1: Collect existing/rolled-over tasks from yesterday --
    yesterday = _yesterday(date)
    prev_tasks = _load_tasks(yesterday, storage)
    rolled_over: list[dict[str, Any]] = []
    for t in prev_tasks:
        if t.get("status") not in (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value):
            rolled = dict(t)
            rolled["rollover_count"] = rolled.get("rollover_count", 0) + 1
            rolled["is_rollover"] = True
            rolled["source"] = "rolled_over"
            rolled["status"] = TaskStatus.ROLLED_OVER.value
            rolled["date"] = date
            rolled["daily_task_id"] = _make_daily_task_id()
            rolled_over.append(rolled)

    # Collect active risks from storage for requested projects
    from api.services.risk_service import get_project_risk_summary
    from api.services.drift_service import get_project_alerts
    from api.models.schemas import IdentifiedRisk, DriftAlert

    active_risks: list[IdentifiedRisk] = []
    drift_alerts: list[DriftAlert] = []
    for pid in request.project_ids:
        summary = get_project_risk_summary(pid, storage)
        active_risks.extend(summary.critical_risks)
        drift_alerts.extend(get_project_alerts(pid, storage))

    # -- Step 2: Build task inputs from rolled-over + any due-today tasks --
    candidate_tasks = list(rolled_over)
    # Add any tasks explicitly queued for this date
    todays_tasks = _load_tasks(date, storage)
    existing_ids = {t["task_id"] for t in candidate_tasks}
    for t in todays_tasks:
        if t["task_id"] not in existing_ids:
            candidate_tasks.append(t)

    # Run priority ranker if we have tasks
    ranked_result = None
    if candidate_tasks:
        task_inputs = _task_inputs_from_plan_tasks(candidate_tasks)
        context: dict[str, Any] = {
            "active_risks": [
                {"risk_id": r.risk_id, "description": r.description,
                 "risk_level": r.risk_level.value}
                for r in active_risks
            ],
            "drift_alerts": [a.alert_id for a in drift_alerts],
            "date": date,
        }
        rank_req = PriorityRankRequest(
            tasks=task_inputs,
            context=context,
            use_llm=request.use_llm,
            llm_tier=request.llm_tier,
        )
        ranked_result = await rank_tasks(rank_req, storage)
        method = ranked_result.analysis_method

        # Merge ranked output back into candidate tasks
        ranked_map = {rt.task_id: rt for rt in ranked_result.ranked_tasks}
        final_tasks: list[dict[str, Any]] = []
        for ct in candidate_tasks:
            rt = ranked_map.get(ct["task_id"])
            if rt:
                ct["rank"] = rt.rank
                ct["priority_score"] = rt.priority_score
                ct["priority_level"] = rt.priority_level.value
                ct["reasoning"] = rt.reasoning
                ct["risk_factors"] = rt.risk_factors
                ct["evidence_refs"] = rt.evidence_refs
                ct["chronic_blocker"] = rt.chronic_blocker
                ct["boost_applied"] = rt.boost_applied
            final_tasks.append(ct)
        final_tasks.sort(key=lambda x: x.get("rank", 999))
    else:
        final_tasks = []
        method = "traditional"

    # -- Step 3: Generate morning brief narrative --
    if request.use_llm and final_tasks:
        morning_brief = await _generate_morning_brief_llm(
            final_tasks, active_risks, drift_alerts, request.llm_tier
        )
    else:
        morning_brief = _generate_morning_brief_traditional(
            final_tasks, active_risks, drift_alerts
        )

    # -- Step 4: Build metadata and persist --
    ranking_meta = _make_ranking_metadata(
        final_tasks, version=1, method=method
    ) if final_tasks else None

    plan: dict[str, Any] = {
        "brief_id": brief_id,
        "date": date,
        "status": "active",
        "project_ids": request.project_ids,
        "morning_brief": morning_brief.model_dump(),
        "ranking_metadata": ranking_meta.model_dump() if ranking_meta else None,
        "active_risks": [r.model_dump() for r in active_risks],
        "drift_alerts": [a.model_dump() for a in drift_alerts],
        "generated_at": datetime.utcnow().isoformat(),
        "closed_at": None,
        "eod_summary": None,
        "rerank_count": 0,
        "ranking_history": [],
    }
    _save_plan(date, plan, storage)
    _save_tasks(date, final_tasks, storage)

    logger.info(
        "Brief generated: date=%s tasks=%d risks=%d method=%s",
        date, len(final_tasks), len(active_risks), method,
    )
    return _build_response(plan, final_tasks)


def _build_response(
    plan: dict[str, Any], tasks: list[dict[str, Any]]
) -> DailyPlanResponse:
    from api.models.schemas import IdentifiedRisk, DriftAlert

    ranked_tasks = [DailyPlanTask.model_validate(t) for t in tasks]

    mb_data = plan.get("morning_brief") or {}
    morning_brief = MorningBrief.model_validate(mb_data) if mb_data else None

    rm_data = plan.get("ranking_metadata")
    ranking_meta = RankingMetadata.model_validate(rm_data) if rm_data else None

    active_risks = []
    for r in plan.get("active_risks", []):
        try:
            active_risks.append(IdentifiedRisk.model_validate(r))
        except Exception:
            pass

    drift_alerts = []
    for a in plan.get("drift_alerts", []):
        try:
            drift_alerts.append(DriftAlert.model_validate(a))
        except Exception:
            pass

    return DailyPlanResponse(
        brief_id=plan["brief_id"],
        date=plan["date"],
        status=plan.get("status", "draft"),
        project_ids=plan.get("project_ids", []),
        morning_brief=morning_brief,
        ranked_tasks=ranked_tasks,
        ranking_metadata=ranking_meta,
        active_risks=active_risks,
        drift_alerts=drift_alerts,
        generated_at=datetime.fromisoformat(plan["generated_at"]) if plan.get("generated_at") else None,
        closed_at=datetime.fromisoformat(plan["closed_at"]) if plan.get("closed_at") else None,
        eod_summary=plan.get("eod_summary"),
        rerank_count=plan.get("rerank_count", 0),
    )


def get_brief(date: str, storage: MockStorage | None = None) -> DailyPlanResponse | None:
    storage = storage or get_storage()
    plan = _load_plan(date, storage)
    if not plan:
        return None
    tasks = _load_tasks(date, storage)
    return _build_response(plan, tasks)


# ---------------------------------------------------------------------------
# Task management
# ---------------------------------------------------------------------------

def get_tasks(date: str, storage: MockStorage | None = None) -> list[DailyPlanTask]:
    storage = storage or get_storage()
    raw = _load_tasks(date, storage)
    return [DailyPlanTask.model_validate(t) for t in raw]


def add_task(
    date: str,
    name: str,
    project_id: str | None = None,
    deadline: str | None = None,
    owner: str | None = None,
    priority_hint: PriorityLevel = PriorityLevel.MEDIUM,
    source: str = "manual",
    rollover_count: int = 0,
    storage: MockStorage | None = None,
) -> DailyPlanTask:
    storage = storage or get_storage()
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    tasks = _load_tasks(date, storage)
    new_task: dict[str, Any] = {
        "daily_task_id": _make_daily_task_id(),
        "task_id": task_id,
        "date": date,
        "name": name,
        "status": TaskStatus.NOT_STARTED.value,
        "rank": len(tasks) + 1,
        "priority_score": 50.0,
        "priority_level": priority_hint.value,
        "reasoning": "",
        "risk_factors": [],
        "evidence_refs": [],
        "deadline": deadline,
        "owner": owner,
        "rollover_count": rollover_count,
        "is_rollover": source == "rolled_over",
        "chronic_blocker": rollover_count >= 3,
        "boost_applied": [],
        "source": source,
        "project_id": project_id,
    }
    tasks.append(new_task)
    _save_tasks(date, tasks, storage)
    return DailyPlanTask.model_validate(new_task)


def update_task(
    date: str,
    task_id: str,
    request: TaskUpdateRequest,
    storage: MockStorage | None = None,
) -> DailyPlanTask | None:
    storage = storage or get_storage()
    tasks = _load_tasks(date, storage)
    for t in tasks:
        if t["task_id"] == task_id or t["daily_task_id"] == task_id:
            if request.status is not None:
                t["status"] = request.status.value
            if request.priority_level is not None:
                t["priority_level"] = request.priority_level.value
            if request.deadline is not None:
                t["deadline"] = request.deadline
            if request.owner is not None:
                t["owner"] = request.owner
            _save_tasks(date, tasks, storage)
            return DailyPlanTask.model_validate(t)
    return None


def delete_task(
    date: str, task_id: str, storage: MockStorage | None = None
) -> bool:
    storage = storage or get_storage()
    tasks = _load_tasks(date, storage)
    new_tasks = [
        t for t in tasks
        if t["task_id"] != task_id and t["daily_task_id"] != task_id
    ]
    if len(new_tasks) == len(tasks):
        return False
    _save_tasks(date, new_tasks, storage)
    return True


# ---------------------------------------------------------------------------
# Re-rank
# ---------------------------------------------------------------------------

async def rerank(
    date: str,
    request: ReRankRequest,
    storage: MockStorage | None = None,
) -> ReRankResponse:
    storage = storage or get_storage()
    plan = _load_plan(date, storage)
    if not plan:
        raise ValueError(f"No brief found for date {date}")

    tasks = _load_tasks(date, storage)
    if not tasks:
        raise ValueError("No tasks to rank")

    old_ranks = {t["task_id"]: t.get("rank", 0) for t in tasks}
    task_inputs = _task_inputs_from_plan_tasks(tasks)

    rank_req = PriorityRankRequest(
        tasks=task_inputs,
        context={"trigger": request.trigger, "reason": request.reason},
        use_llm=request.use_llm,
        llm_tier=request.llm_tier,
    )
    ranked_result = await rank_tasks(rank_req, storage)

    # Merge updated ranks back
    ranked_map = {rt.task_id: rt for rt in ranked_result.ranked_tasks}
    for t in tasks:
        rt = ranked_map.get(t["task_id"])
        if rt:
            t["rank"] = rt.rank
            t["priority_score"] = rt.priority_score
            t["priority_level"] = rt.priority_level.value
            t["reasoning"] = rt.reasoning
            t["risk_factors"] = rt.risk_factors
            t["evidence_refs"] = rt.evidence_refs
            t["chronic_blocker"] = rt.chronic_blocker
            t["boost_applied"] = rt.boost_applied
    tasks.sort(key=lambda x: x.get("rank", 999))

    # Compute changes
    changes: list[RankingChange] = []
    for t in tasks:
        old = old_ranks.get(t["task_id"], 0)
        new = t.get("rank", 0)
        if old != new:
            delta = old - new
            changes.append(RankingChange(
                task_id=t["task_id"],
                old_rank=old,
                new_rank=new,
                reason_for_change=(
                    f"Moved {'up' if delta > 0 else 'down'} {abs(delta)} position(s). "
                    f"Trigger: {request.trigger}."
                ),
            ))

    # Update version
    version = plan.get("rerank_count", 0) + 2  # starts at v1 (morning), v2 = first rerank
    method = ranked_result.analysis_method
    meta = _make_ranking_metadata(
        tasks, version=version, method=method,
        trigger=request.trigger,
        trigger_ref=request.trigger_artifact_id,
    )

    # Save ranking history entry
    history_entry = {
        "version": version,
        "ranking_id": meta.ranking_id,
        "trigger": request.trigger,
        "trigger_ref": request.trigger_artifact_id,
        "changes": [c.model_dump() for c in changes],
        "snapshot_count": len(tasks),
        "created_at": datetime.utcnow().isoformat(),
    }
    ranking_history = plan.get("ranking_history", [])
    ranking_history.append(history_entry)
    plan["ranking_history"] = ranking_history
    plan["ranking_metadata"] = meta.model_dump()
    plan["rerank_count"] = plan.get("rerank_count", 0) + 1
    _save_plan(date, plan, storage)
    _save_tasks(date, tasks, storage)

    logger.info(
        "Re-rank complete: date=%s version=%d changes=%d trigger=%s",
        date, version, len(changes), request.trigger,
    )

    return ReRankResponse(
        brief_id=plan["brief_id"],
        date=date,
        reranked_at=datetime.utcnow(),
        ranking_metadata=meta,
        ranking_changes=changes,
        updated_ranked_tasks=[DailyPlanTask.model_validate(t) for t in tasks],
    )


# ---------------------------------------------------------------------------
# Inject + re-rank
# ---------------------------------------------------------------------------

async def inject_task(
    date: str,
    request: InjectTaskRequest,
    storage: MockStorage | None = None,
) -> dict[str, Any]:
    storage = storage or get_storage()
    plan = _load_plan(date, storage)
    if not plan:
        raise ValueError(f"No brief found for date {date}")

    new_task = add_task(
        date=date,
        name=request.name,
        project_id=request.project_id,
        deadline=request.deadline,
        owner=request.owner,
        priority_hint=request.priority_hint,
        source="manual",
        storage=storage,
    )
    result: dict[str, Any] = {"injected_task": new_task.model_dump()}

    if request.trigger_rerank:
        rerank_resp = await rerank(
            date=date,
            request=ReRankRequest(
                trigger="task_injected",
                reason=f"New task injected: {request.name}",
                use_llm=request.use_llm,
                llm_tier=request.llm_tier,
            ),
            storage=storage,
        )
        result["rerank_result"] = rerank_resp.model_dump(mode="json")

    return result


# ---------------------------------------------------------------------------
# End-of-day close
# ---------------------------------------------------------------------------

async def close_day(
    date: str,
    use_llm: bool = False,
    llm_tier: str = "small",
    storage: MockStorage | None = None,
) -> CloseDayResponse:
    storage = storage or get_storage()
    plan = _load_plan(date, storage)
    if not plan:
        raise ValueError(f"No brief found for date {date}")

    tasks = _load_tasks(date, storage)
    completed_count = 0
    rolled_count = 0

    for t in tasks:
        status = t.get("status", TaskStatus.NOT_STARTED.value)
        if status == TaskStatus.COMPLETED.value:
            completed_count += 1
        elif status != TaskStatus.CANCELLED.value:
            t["status"] = TaskStatus.ROLLED_OVER.value
            t["rollover_count"] = t.get("rollover_count", 0) + 1
            rolled_count += 1

    _save_tasks(date, tasks, storage)

    # EOD summary
    if use_llm:
        eod_summary = await _generate_eod_summary_llm(tasks, llm_tier)
    else:
        eod_summary = (
            f"Day closed. {completed_count} completed, {rolled_count} rolled over."
        )

    # Seed next day with rolled-over tasks
    tomorrow = _tomorrow(date)
    next_day_tasks = _load_tasks(tomorrow, storage)
    existing_ids = {t["task_id"] for t in next_day_tasks}
    seeded = 0
    for t in tasks:
        if t.get("status") == TaskStatus.ROLLED_OVER.value and t["task_id"] not in existing_ids:
            seeded_task = dict(t)
            seeded_task["date"] = tomorrow
            seeded_task["daily_task_id"] = _make_daily_task_id()
            seeded_task["is_rollover"] = True
            seeded_task["status"] = TaskStatus.NOT_STARTED.value
            seeded_task["source"] = "rolled_over"
            seeded_task["chronic_blocker"] = seeded_task.get("rollover_count", 0) >= 3
            next_day_tasks.append(seeded_task)
            seeded += 1
    _save_tasks(tomorrow, next_day_tasks, storage)

    # Update plan
    now = datetime.utcnow()
    plan["status"] = "closed"
    plan["closed_at"] = now.isoformat()
    plan["eod_summary"] = eod_summary
    _save_plan(date, plan, storage)

    logger.info(
        "Day closed: date=%s completed=%d rolled_over=%d seeded=%d",
        date, completed_count, rolled_count, seeded,
    )

    return CloseDayResponse(
        brief_id=plan["brief_id"],
        date=date,
        closed_at=now,
        completed_tasks=completed_count,
        rolled_over_tasks=rolled_count,
        eod_summary=eod_summary,
        next_day_seeded=seeded > 0,
    )


# ---------------------------------------------------------------------------
# Report + history + streak
# ---------------------------------------------------------------------------

def get_report(date: str, storage: MockStorage | None = None) -> dict[str, Any]:
    storage = storage or get_storage()
    plan = _load_plan(date, storage)
    if not plan:
        return {}
    tasks = _load_tasks(date, storage)
    completed = [t for t in tasks if t.get("status") == TaskStatus.COMPLETED.value]
    rolled = [t for t in tasks if t.get("status") == TaskStatus.ROLLED_OVER.value]
    return {
        "brief_id": plan.get("brief_id"),
        "date": date,
        "status": plan.get("status"),
        "morning_brief": plan.get("morning_brief"),
        "eod_summary": plan.get("eod_summary"),
        "stats": {
            "total_tasks": len(tasks),
            "completed": len(completed),
            "rolled_over": len(rolled),
            "completion_rate": round(len(completed) / len(tasks), 2) if tasks else 0.0,
        },
        "ranked_tasks": tasks,
    }


def get_ranking_history(
    date: str, storage: MockStorage | None = None
) -> list[RankingHistoryEntry]:
    storage = storage or get_storage()
    plan = _load_plan(date, storage)
    if not plan:
        return []
    history = plan.get("ranking_history", [])
    entries = []
    for h in history:
        changes = [RankingChange.model_validate(c) for c in h.get("changes", [])]
        entries.append(RankingHistoryEntry(
            version=h["version"],
            ranking_id=h.get("ranking_id", ""),
            trigger=h.get("trigger", ""),
            trigger_ref=h.get("trigger_ref"),
            changes=changes,
            snapshot_count=h.get("snapshot_count", 0),
            created_at=datetime.fromisoformat(h["created_at"]) if h.get("created_at") else datetime.utcnow(),
        ))
    return entries


def list_briefs(
    limit: int = 30,
    offset: int = 0,
    storage: MockStorage | None = None,
) -> list[BriefHistoryItem]:
    storage = storage or get_storage()
    all_plans = storage.list("daily_plans")
    items: list[BriefHistoryItem] = []
    for plan in all_plans:
        if not isinstance(plan, dict):
            continue
        date = plan.get("date", "")
        tasks = _load_tasks(date, storage)
        completed = sum(1 for t in tasks if t.get("status") == TaskStatus.COMPLETED.value)
        items.append(BriefHistoryItem(
            brief_id=plan.get("brief_id", ""),
            date=date,
            status=plan.get("status", "unknown"),
            tasks_count=len(tasks),
            completed_count=completed,
            generated_at=datetime.fromisoformat(plan["generated_at"]) if plan.get("generated_at") else None,
        ))
    items.sort(key=lambda x: x.date, reverse=True)
    return items[offset: offset + limit]


def get_streak(storage: MockStorage | None = None) -> StreakResponse:
    storage = storage or get_storage()
    all_plans = storage.list("daily_plans")
    closed = [p for p in all_plans if isinstance(p, dict) and p.get("status") == "closed"]
    closed.sort(key=lambda p: p.get("date", ""), reverse=True)

    by_date = []
    current_streak = 0
    prev_date: str | None = None

    for plan in closed:
        date = plan["date"]
        tasks = _load_tasks(date, storage)
        total = len(tasks)
        completed = sum(1 for t in tasks if t.get("status") == TaskStatus.COMPLETED.value)
        rate = round(completed / total, 2) if total else 0.0
        by_date.append({"date": date, "total": total, "completed": completed, "rate": rate})

        # Streak: consecutive days with at least 1 completion
        if completed > 0:
            if prev_date is None:
                current_streak = 1
            else:
                try:
                    gap = (
                        datetime.strptime(prev_date, _DATE_FMT)
                        - datetime.strptime(date, _DATE_FMT)
                    ).days
                    current_streak = current_streak + 1 if gap == 1 else 1
                except ValueError:
                    current_streak = 1
            prev_date = date
        else:
            break

    avg_rate = round(
        sum(d["rate"] for d in by_date) / len(by_date), 2
    ) if by_date else 0.0

    return StreakResponse(
        current_streak=current_streak,
        total_days=len(closed),
        completion_rate=avg_rate,
        by_date=by_date,
    )
