"""
Priority Ranker — internal component of the Daily Brief Service.

Two paths:
  Traditional: weighted formula (no LLM, no reasoning)
  LLM: full ranking with reasoning + evidence refs + rule-based post-adjustments

Rule-based post-processing (applied on top of LLM output):
  deadline ≤1d  → +20
  deadline ≤3d  → +10
  deadline ≤7d  → +5
  rollover ≥3   → +15  (chronic_blocker flag)
  critical/high risk → +8
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any

from api.models.database import MockStorage, get_storage
from api.models.enums import PriorityLevel, RiskLevel
from api.models.schemas import (
    PriorityRankRequest,
    PriorityRankResponse,
    RankedTask,
    RankingChange,
    TaskInput,
)
from api.utils import llm_client
from api.utils.traditional_methods import rank_tasks_traditional

logger = logging.getLogger(__name__)

_RISK_LEVEL_ORDER = {
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}


def _deadline_days(deadline_str: str | None) -> float | None:
    if not deadline_str:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return (datetime.strptime(deadline_str, fmt) - datetime.now()).days
        except ValueError:
            continue
    return None


def _apply_post_adjustments(score: float, task: TaskInput) -> tuple[float, list[str], bool]:
    boosts: list[str] = []
    chronic_blocker = False
    days = _deadline_days(task.deadline)

    if days is not None:
        if days <= 1:
            score += 20
            boosts.append("deadline_le1d:+20")
        elif days <= 3:
            score += 10
            boosts.append("deadline_le3d:+10")
        elif days <= 7:
            score += 5
            boosts.append("deadline_le7d:+5")

    if task.rollover_count >= 3:
        score += 15
        boosts.append("rollover_ge3:+15")
        chronic_blocker = True

    if task.risk_level in (RiskLevel.CRITICAL, RiskLevel.HIGH):
        score += 8
        boosts.append("high_risk:+8")

    return min(100.0, score), boosts, chronic_blocker


def _score_to_priority(score: float) -> PriorityLevel:
    if score >= 75:
        return PriorityLevel.CRITICAL
    if score >= 50:
        return PriorityLevel.HIGH
    if score >= 25:
        return PriorityLevel.MEDIUM
    return PriorityLevel.LOW


# ---------------------------------------------------------------------------
# Traditional path
# ---------------------------------------------------------------------------

def _rank_traditional(tasks: list[TaskInput]) -> list[RankedTask]:
    raw = [t.model_dump(mode="json") for t in tasks]
    scored = rank_tasks_traditional(raw)
    ranked: list[RankedTask] = []
    for item in scored:
        task_in = next((t for t in tasks if t.task_id == item["task_id"]), None)
        score = item.get("priority_score", 0.0)
        boost_applied = item.get("boost_applied", [])
        chronic = item.get("chronic_blocker", False)

        # Apply spec adjustments on top
        if task_in:
            score, extra_boosts, chronic = _apply_post_adjustments(score, task_in)
            boost_applied = boost_applied + extra_boosts

        ranked.append(RankedTask(
            task_id=item["task_id"],
            name=item.get("name", ""),
            rank=0,  # re-assigned below after re-sort
            priority_level=_score_to_priority(score),
            priority_score=round(score, 2),
            reasoning="",
            chronic_blocker=chronic,
            boost_applied=boost_applied,
            confidence=0.85,
        ))

    # Re-sort by adjusted score and assign final ranks
    ranked.sort(key=lambda r: r.priority_score, reverse=True)
    for i, r in enumerate(ranked, start=1):
        r.rank = i
    return ranked


# ---------------------------------------------------------------------------
# LLM path
# ---------------------------------------------------------------------------

async def _rank_llm(
    tasks: list[TaskInput],
    context: dict[str, Any],
    tier: str,
) -> list[RankedTask]:
    tasks_json = json.dumps(
        [t.model_dump(exclude={"expected_rank_range"}) for t in tasks],
        default=str,
        indent=2,
    )
    ctx_json = json.dumps(context, default=str, indent=2)

    prompt = (
        "You are a senior project manager at a government IT department. "
        "Rank the following tasks by priority, from highest to lowest. "
        "Consider: deadline urgency, risk level, number of dependencies blocked, rollover count, "
        "and any context provided. "
        "Return a JSON array of objects with keys: "
        "task_id, rank (integer starting at 1), priority_level (critical/high/medium/low), "
        "priority_score (0-100 float), reasoning (2-3 sentences referencing specific factors), "
        "risk_factors (list of strings), evidence_refs (list of artifact_ids from context, or []), "
        "confidence (0.0-1.0). "
        "Return ONLY the JSON array.\n\n"
        f"TASKS:\n{tasks_json}\n\nCONTEXT:\n{ctx_json}"
    )

    resp = await llm_client.complete(
        messages=[{"role": "user", "content": prompt}],
        tier=tier,
        expect_json=True,
        max_tokens=3000,
    )

    if resp.error or not resp.parsed_json:
        logger.warning("LLM ranking failed: %s — falling back to traditional", resp.error)
        return _rank_traditional(tasks)

    items = resp.parsed_json if isinstance(resp.parsed_json, list) else []
    task_map = {t.task_id: t for t in tasks}
    ranked: list[RankedTask] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("task_id", ""))
        task_in = task_map.get(tid)

        try:
            prio = PriorityLevel(str(item.get("priority_level", "medium")).lower())
        except ValueError:
            prio = PriorityLevel.MEDIUM

        score = float(item.get("priority_score", 50.0))
        boosts: list[str] = []
        chronic = False

        if task_in:
            score, boosts, chronic = _apply_post_adjustments(score, task_in)

        ranked.append(RankedTask(
            task_id=tid,
            name=task_in.name if task_in else str(item.get("name", tid)),
            rank=int(item.get("rank", 99)),
            priority_level=_score_to_priority(score),
            priority_score=round(score, 2),
            reasoning=str(item.get("reasoning", ""))[:500],
            risk_factors=item.get("risk_factors", []),
            evidence_refs=item.get("evidence_refs", []),
            confidence=float(item.get("confidence", 0.9)),
            chronic_blocker=chronic,
            boost_applied=boosts,
        ))

    # Sort and re-assign ranks by score
    ranked.sort(key=lambda x: x.priority_score, reverse=True)
    for i, rt in enumerate(ranked, start=1):
        rt.rank = i

    return ranked


# ---------------------------------------------------------------------------
# Diff tracking
# ---------------------------------------------------------------------------

def _compute_changes(
    old: list[RankedTask] | None, new: list[RankedTask]
) -> list[RankingChange]:
    if not old:
        return []
    old_map = {rt.task_id: rt.rank for rt in old}
    changes: list[RankingChange] = []
    for rt in new:
        old_rank = old_map.get(rt.task_id)
        if old_rank is not None and old_rank != rt.rank:
            delta = old_rank - rt.rank
            reason = (
                f"Moved {'up' if delta > 0 else 'down'} {abs(delta)} position(s)."
                + (f" Boosts applied: {', '.join(rt.boost_applied)}." if rt.boost_applied else "")
            )
            changes.append(RankingChange(
                task_id=rt.task_id,
                old_rank=old_rank,
                new_rank=rt.rank,
                reason_for_change=reason,
            ))
    return changes


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def rank_tasks(
    request: PriorityRankRequest,
    storage: MockStorage | None = None,
) -> PriorityRankResponse:
    t0 = time.perf_counter()

    if request.use_llm:
        ranked = await _rank_llm(request.tasks, request.context, request.llm_tier)
        method = f"llm_{request.llm_tier}"
    else:
        ranked = _rank_traditional(request.tasks)
        method = "traditional"

    changes = _compute_changes(request.previous_ranking, ranked)
    latency_ms = (time.perf_counter() - t0) * 1000

    logger.info("Priority ranking: method=%s tasks=%d latency=%.0fms",
                method, len(ranked), latency_ms)

    return PriorityRankResponse(
        ranked_tasks=ranked,
        ranking_changes=changes,
        analysis_method=method,
        latency_ms=latency_ms,
    )
