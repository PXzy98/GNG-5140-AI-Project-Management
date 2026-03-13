"""
Risk Engine Service — 4-step pipeline:
  1. Risk Identification
  2. Cross-Document Inconsistency Detection
  3. Evidence Linking
  4. Risk Scoring Matrix

Two paths: Traditional (keyword) and LLM (OpenRouter, 3 tiers).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import Counter
from datetime import datetime
from typing import Any

from api.models.database import MockStorage, get_storage
from api.models.enums import AlertSeverity, RiskCategory, RiskLevel
from api.models.schemas import (
    CrossCheckRequest,
    CrossCheckResponse,
    EvidenceLink,
    IdentifiedRisk,
    Inconsistency,
    ProjectRiskSummary,
    RiskIdentifyRequest,
    RiskIdentifyResponse,
)
from api.utils import llm_client
from api.utils.traditional_methods import identify_risks_traditional, compute_text_similarity

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

_LEVEL_MAP = {"low": RiskLevel.LOW, "medium": RiskLevel.MEDIUM,
              "high": RiskLevel.HIGH, "critical": RiskLevel.CRITICAL}
_LEVEL_ORDER = {RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2,
                RiskLevel.HIGH: 3, RiskLevel.CRITICAL: 4}


def _score_to_level(score: float) -> RiskLevel:
    if score >= 20:
        return RiskLevel.CRITICAL
    if score >= 12:
        return RiskLevel.HIGH
    if score >= 6:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _apply_rule_adjustments(
    likelihood: int, impact: int, artifact_text: str, has_owner: bool
) -> tuple[int, int, list[str]]:
    """Apply rule-based score adjustments. Returns adjusted likelihood, impact, and reasons."""
    adjustments: list[str] = []
    # Deadline proximity signal: look for near-term dates
    from api.utils.traditional_methods import extract_dates
    from datetime import datetime as dt
    dates = extract_dates(artifact_text)
    for d in dates:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                days = (dt.strptime(d, fmt) - dt.now()).days
                if 0 <= days < 7:
                    likelihood = min(5, likelihood + 1)
                    adjustments.append("deadline_<7d: likelihood+1")
                break
            except ValueError:
                continue

    if not has_owner:
        impact = min(5, impact + 1)
        adjustments.append("no_owner: impact+1")

    return likelihood, impact, adjustments


# ---------------------------------------------------------------------------
# Traditional path
# ---------------------------------------------------------------------------

def _identify_risks_traditional(
    artifact_ids: list[str],
    project_id: str | None,
    storage: MockStorage,
) -> list[IdentifiedRisk]:
    risks: list[IdentifiedRisk] = []
    for art_id in artifact_ids:
        artifact = storage.get("artifacts", art_id)
        if not artifact:
            continue
        content = artifact.get("content_preview", "") if isinstance(artifact, dict) else ""
        raw_risks = identify_risks_traditional(content, art_id)
        for r in raw_risks:
            risk_id = f"rsk_{uuid.uuid4().hex[:8]}"
            lik, imp, adj = _apply_rule_adjustments(
                r["likelihood"], r["impact"], content, has_owner=False
            )
            raw_score = lik * imp
            level = _score_to_level(raw_score)

            try:
                cat = RiskCategory(r["category"])
            except ValueError:
                cat = RiskCategory.TECHNICAL

            risk = IdentifiedRisk(
                risk_id=risk_id,
                description=r["description"],
                category=cat,
                likelihood=lik,
                impact=imp,
                risk_score=float(raw_score),
                risk_level=level,
                evidence_refs=[
                    EvidenceLink(
                        artifact_id=art_id,
                        excerpt=r.get("evidence_excerpt", "")[:200],
                        evidence_strength=min(1.0, lik * imp / 25),
                    )
                ],
                source_artifact_id=art_id,
            )
            risks.append(risk)
    return risks


# ---------------------------------------------------------------------------
# LLM path
# ---------------------------------------------------------------------------

async def _identify_risks_llm(
    artifact_ids: list[str],
    project_id: str | None,
    tier: str,
    storage: MockStorage,
) -> list[IdentifiedRisk]:
    risks: list[IdentifiedRisk] = []
    for art_id in artifact_ids:
        artifact = storage.get("artifacts", art_id)
        if not artifact:
            continue
        content = artifact.get("content_preview", "") if isinstance(artifact, dict) else ""

        prompt = (
            "You are a project risk analyst. Identify only genuine problems or risks that are "
            "explicitly stated or strongly implied. Do NOT flag activities described as "
            "completed, successful, tested, or on-track — those are NOT risks. "
            "If no real risks exist, return [].\n\n"
            "Return at most 5 risks. Each \"description\" must:\n"
            "- Be under 20 words\n"
            "- Start with the risk subject using generic terms (e.g. 'vendor', 'supplier', 'project') "
            "not specific product/company names\n"
            "- Include a specific verbatim phrase, number, or percentage from the document\n"
            "- If a risk is buried or hidden in an appendix or footnote, state where it appears\n"
            "- If the document uses minimizing language for a real problem "
            "(e.g. 'slight adjustment' for a 3-week slip), quote that phrase in single-quotes\n"
            "- For single-vendor/supplier dependency risks, ALWAYS use format: "
            "'No backup [resource] identified for [context] — single-sourced risk'\n"
            "- For key-person dependency risks, use format: "
            "'No backup [role] identified — [person] single point of failure'\n"
            "- Follow this style: '<subject> <verb phrase>, <consequence>'\n"
            "  Example: 'Vendor delivery delayed by 2 weeks, threatening NGIS Phase 3 migration timeline'\n"
            "  Example: 'No backup supplier identified for critical network equipment — single-sourced risk'\n"
            "  Example: 'Budget overrun of $340,000 (14%) buried in appendix — not in executive summary'\n"
            "  Example: 'Three-week slip acknowledged as \\'slight adjustment\\' — timeline at risk'\n\n"
            "Category definitions:\n"
            "- schedule: deadline slippage, delivery delays, timeline risks\n"
            "- budget: cost overruns, unplanned expenses, financial risks\n"
            "- technical: system failures, integration issues, technology risks\n"
            "- resource: staffing gaps, single-vendor/supplier dependency, key-person risks\n"
            "- compliance: regulatory, audit, policy, or governance risks\n\n"
            "For each risk return a JSON object with keys: "
            "description (≤20 words, includes verbatim phrase), category, likelihood (1-5), impact (1-5), "
            "evidence_excerpt (short direct quote), affected_stakeholders (list of strings). "
            "Return ONLY a JSON array. No explanation outside the JSON.\n\n"
            f"DOCUMENT (artifact_id={art_id}):\n{content[:4000]}"
        )
        resp = await llm_client.complete(
            messages=[{"role": "user", "content": prompt}],
            tier=tier,
            max_tokens=4000,
            expect_json=True,
        )
        if resp.error or not resp.parsed_json:
            logger.warning("LLM risk identification failed for %s: %s", art_id, resp.error)
            continue

        items = resp.parsed_json if isinstance(resp.parsed_json, list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            lik = int(item.get("likelihood", 3))
            imp = int(item.get("impact", 3))
            lik_adj, imp_adj, adj = _apply_rule_adjustments(lik, imp, content, has_owner=False)
            raw_score = lik_adj * imp_adj

            try:
                cat = RiskCategory(str(item.get("category", "technical")).lower())
            except ValueError:
                cat = RiskCategory.TECHNICAL

            risk = IdentifiedRisk(
                risk_id=f"rsk_{uuid.uuid4().hex[:8]}",
                description=str(item.get("description", ""))[:500],
                category=cat,
                likelihood=lik_adj,
                impact=imp_adj,
                risk_score=float(raw_score),
                risk_level=_score_to_level(raw_score),
                affected_stakeholders=item.get("affected_stakeholders", []),
                evidence_refs=[
                    EvidenceLink(
                        artifact_id=art_id,
                        excerpt=str(item.get("evidence_excerpt", ""))[:200],
                        evidence_strength=min(1.0, raw_score / 25),
                    )
                ],
                source_artifact_id=art_id,
            )
            risks.append(risk)
    return risks


# ---------------------------------------------------------------------------
# Cross-check
# ---------------------------------------------------------------------------

# Contradiction keyword pairs
_CONTRADICTION_PAIRS = [
    (["on track", "no concerns", "no risk", "on budget", "ahead of schedule"],
     ["delay", "overrun", "behind", "at risk", "budget exceeded", "timeline slip"]),
    (["no budget concerns", "within budget"],
     ["budget overrun", "cost increase", "over budget", "financial risk"]),
]


def _cross_check_traditional(
    artifact_ids: list[str], storage: MockStorage
) -> list[Inconsistency]:
    inconsistencies: list[Inconsistency] = []
    contents: dict[str, str] = {}
    for art_id in artifact_ids:
        art = storage.get("artifacts", art_id)
        if art:
            contents[art_id] = art.get("content_preview", "") if isinstance(art, dict) else ""

    ids = list(contents.keys())
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            id_a, id_b = ids[i], ids[j]
            text_a = contents[id_a].lower()
            text_b = contents[id_b].lower()

            for positive_set, negative_set in _CONTRADICTION_PAIRS:
                a_positive = any(p in text_a for p in positive_set)
                b_negative = any(n in text_b for n in negative_set)
                b_positive = any(p in text_b for p in positive_set)
                a_negative = any(n in text_a for n in negative_set)

                if (a_positive and b_negative) or (b_positive and a_negative):
                    # Find excerpts
                    exc_a = next((s for s in positive_set if s in text_a), "")
                    exc_b = next((s for s in negative_set if s in text_b), "")
                    if not exc_a:
                        exc_a = next((s for s in negative_set if s in text_a), "")
                    if not exc_b:
                        exc_b = next((s for s in positive_set if s in text_b), "")

                    inconsistencies.append(Inconsistency(
                        description=(
                            f"Document {id_a} and {id_b} contain contradicting statements "
                            "about project status or budget."
                        ),
                        doc_a_artifact_id=id_a,
                        doc_b_artifact_id=id_b,
                        doc_a_excerpt=exc_a,
                        doc_b_excerpt=exc_b,
                        severity=AlertSeverity.HIGH,
                    ))
                    break  # one inconsistency per pair

    return inconsistencies


async def _cross_check_llm(
    artifact_ids: list[str], tier: str, storage: MockStorage
) -> list[Inconsistency]:
    contents: dict[str, str] = {}
    for art_id in artifact_ids:
        art = storage.get("artifacts", art_id)
        if art:
            contents[art_id] = art.get("content_preview", "") if isinstance(art, dict) else ""

    if len(contents) < 2:
        return []

    docs_text = "\n\n---\n\n".join(
        f"[{aid}]:\n{txt[:1500]}" for aid, txt in contents.items()
    )
    prompt = (
        "You are a project audit analyst. Review the following documents and identify any "
        "contradictions, inconsistencies, or conflicting claims between them. "
        "Return a JSON array of objects with keys: "
        "description, doc_a_artifact_id, doc_b_artifact_id, doc_a_excerpt, doc_b_excerpt, "
        "severity (critical/high/medium/low). "
        "Return empty array [] if no inconsistencies found.\n\n"
        f"DOCUMENTS:\n{docs_text}"
    )
    resp = await llm_client.complete(
        messages=[{"role": "user", "content": prompt}],
        tier=tier,
        expect_json=True,
    )
    if resp.error or not resp.parsed_json:
        return []

    items = resp.parsed_json if isinstance(resp.parsed_json, list) else []
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            sev = AlertSeverity(str(item.get("severity", "medium")).lower())
        except ValueError:
            sev = AlertSeverity.MEDIUM
        result.append(Inconsistency(
            description=str(item.get("description", ""))[:500],
            doc_a_artifact_id=str(item.get("doc_a_artifact_id", "")),
            doc_b_artifact_id=str(item.get("doc_b_artifact_id", "")),
            doc_a_excerpt=str(item.get("doc_a_excerpt", ""))[:200],
            doc_b_excerpt=str(item.get("doc_b_excerpt", ""))[:200],
            severity=sev,
        ))
    return result


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

async def identify_risks(
    request: RiskIdentifyRequest,
    storage: MockStorage | None = None,
) -> RiskIdentifyResponse:
    storage = storage or get_storage()
    t0 = time.perf_counter()

    if request.use_llm:
        risks = await _identify_risks_llm(
            request.artifact_ids, request.project_id, request.llm_tier, storage
        )
        method = f"llm_{request.llm_tier}"
    else:
        risks = _identify_risks_traditional(
            request.artifact_ids, request.project_id, storage
        )
        method = "traditional"

    # Persist risks
    for risk in risks:
        storage.put("risks", risk.risk_id, risk.model_dump())
        if request.project_id:
            proj_risks = storage.get("project_risks", request.project_id) or []
            proj_risks.append(risk.risk_id)
            storage.put("project_risks", request.project_id, proj_risks)

    latency_ms = (time.perf_counter() - t0) * 1000
    logger.info("Risk identification: method=%s risks=%d latency=%.0fms",
                method, len(risks), latency_ms)

    return RiskIdentifyResponse(
        risks=risks,
        project_id=request.project_id,
        analysis_method=method,
        latency_ms=latency_ms,
    )


async def cross_check(
    request: CrossCheckRequest,
    storage: MockStorage | None = None,
) -> CrossCheckResponse:
    storage = storage or get_storage()
    t0 = time.perf_counter()

    if request.use_llm:
        inconsistencies = await _cross_check_llm(
            request.artifact_ids, request.llm_tier, storage
        )
    else:
        inconsistencies = _cross_check_traditional(request.artifact_ids, storage)

    latency_ms = (time.perf_counter() - t0) * 1000
    return CrossCheckResponse(
        inconsistencies=inconsistencies,
        artifact_ids=request.artifact_ids,
        latency_ms=latency_ms,
    )


def get_risk(risk_id: str, storage: MockStorage | None = None) -> IdentifiedRisk | None:
    storage = storage or get_storage()
    item = storage.get("risks", risk_id)
    if item is None:
        return None
    return IdentifiedRisk.model_validate(item) if isinstance(item, dict) else item


def get_project_risk_summary(
    project_id: str, storage: MockStorage | None = None
) -> ProjectRiskSummary:
    storage = storage or get_storage()
    risk_ids: list[str] = storage.get("project_risks", project_id) or []
    risks: list[IdentifiedRisk] = []
    for rid in risk_ids:
        r = get_risk(rid, storage)
        if r:
            risks.append(r)

    by_cat: Counter = Counter(r.category.value for r in risks)
    by_level: Counter = Counter(r.risk_level.value for r in risks)
    critical = [r for r in risks if r.risk_level in (RiskLevel.CRITICAL, RiskLevel.HIGH)]

    return ProjectRiskSummary(
        project_id=project_id,
        total_risks=len(risks),
        by_category=dict(by_cat),
        by_level=dict(by_level),
        critical_risks=critical[:5],
        trend="stable",
    )
