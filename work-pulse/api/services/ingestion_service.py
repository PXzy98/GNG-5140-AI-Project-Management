"""
Ingestion Service — Pipeline: format detection → content extraction →
language detection → metadata extraction → task extraction → hash → store.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from api.middleware.bilingual import detect_language, ensure_bilingual_prompt
from api.models.database import MockStorage, get_storage
from api.models.enums import ArtifactType, TaskStatus, PriorityLevel
from api.models.schemas import (
    ArtifactListResponse,
    ArtifactResponse,
    ExtractedTask,
    IngestRequest,
)
from api.utils import llm_client
from api.utils.traditional_methods import extract_tasks_traditional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Format detection helpers
# ---------------------------------------------------------------------------

_EMAIL_SIGNALS = re.compile(
    r"(^From:\s|^To:\s|^Subject:\s|^Date:\s|^Cc:\s|MIME-Version:|Content-Type:)",
    re.MULTILINE | re.IGNORECASE,
)
_MEETING_SIGNALS = re.compile(
    r"(minutes|attendees|agenda|action items|meeting called to order|adjourned|présents|ordre du jour)",
    re.IGNORECASE,
)
_STATUS_SIGNALS = re.compile(
    r"(status report|weekly update|project update|accomplishments|risks and issues|milestones)",
    re.IGNORECASE,
)
_SOW_SIGNALS = re.compile(
    r"(statement of work|scope of work|deliverables|out of scope|in scope|terms and conditions)",
    re.IGNORECASE,
)

_SOURCE_TYPE_MAP = {
    "email": ArtifactType.EMAIL,
    "meeting_minutes": ArtifactType.MEETING_MINUTES,
    "status_report": ArtifactType.STATUS_REPORT,
    "notes": ArtifactType.NOTES,
    "other": ArtifactType.OTHER,
}


def _detect_artifact_type(content: str, hint: str) -> ArtifactType:
    if hint in _SOURCE_TYPE_MAP and hint != "other":
        return _SOURCE_TYPE_MAP[hint]
    if _EMAIL_SIGNALS.search(content):
        return ArtifactType.EMAIL
    if _MEETING_SIGNALS.search(content):
        return ArtifactType.MEETING_MINUTES
    if _SOW_SIGNALS.search(content):
        return ArtifactType.SOW
    if _STATUS_SIGNALS.search(content):
        return ArtifactType.STATUS_REPORT
    return ArtifactType.OTHER


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------

_FROM_RE = re.compile(r"^From:\s*(.+)", re.MULTILINE | re.IGNORECASE)
_TO_RE = re.compile(r"^To:\s*(.+)", re.MULTILINE | re.IGNORECASE)
_SUBJECT_RE = re.compile(r"^Subject:\s*(.+)", re.MULTILINE | re.IGNORECASE)
_DATE_HDR_RE = re.compile(r"^Date:\s*(.+)", re.MULTILINE | re.IGNORECASE)
_ATTENDEES_RE = re.compile(r"Attendees?[:\s]+(.+?)(?:\n\n|\Z)", re.IGNORECASE | re.DOTALL)


def _extract_metadata(content: str, artifact_type: ArtifactType, extra: dict) -> dict[str, Any]:
    meta: dict[str, Any] = dict(extra)
    if artifact_type == ArtifactType.EMAIL:
        for label, rx in [("sender", _FROM_RE), ("recipients", _TO_RE),
                          ("subject", _SUBJECT_RE), ("date", _DATE_HDR_RE)]:
            m = rx.search(content)
            if m:
                meta[label] = m.group(1).strip()
    elif artifact_type == ArtifactType.MEETING_MINUTES:
        m = _ATTENDEES_RE.search(content)
        if m:
            meta["attendees"] = [a.strip() for a in re.split(r"[,;\n]", m.group(1)) if a.strip()]
    return meta


# ---------------------------------------------------------------------------
# Task extraction paths
# ---------------------------------------------------------------------------

def _tasks_from_traditional(content: str, artifact_id: str) -> list[ExtractedTask]:
    raw = extract_tasks_traditional(content)
    tasks = []
    for i, t in enumerate(raw):
        tasks.append(
            ExtractedTask(
                task_id=f"{artifact_id}_t{i+1:03d}",
                text=t["text"],
                owner=t.get("owner"),
                deadline=t.get("deadline"),
                confidence=t.get("confidence", 0.7),
                source_excerpt=t.get("source_excerpt"),
            )
        )
    return tasks


async def _tasks_from_llm(
    content: str, artifact_id: str, tier: str, language: str
) -> list[ExtractedTask]:
    prompt = (
        "Extract all action items from the following document.\n\n"
        "Rules:\n"
        "- 'text': Use the exact or near-verbatim phrasing from the document. "
        "Do NOT paraphrase or summarize — copy the task description as closely as possible, "
        "omitting only the owner prefix (e.g. 'Alice Tran to ...' → 'Contact ...').\n"
        "- 'owner': Person responsible, as written in the document (full name if available), or null.\n"
        "- 'deadline': Normalize ALL dates to YYYY-MM-DD format (e.g. 'March 5, 2026' → '2026-03-05'), or null.\n"
        "- 'priority': critical/high/medium/low.\n"
        "- 'source_excerpt': Short direct quote from the document.\n\n"
        "Extract action items in ALL languages present. "
        "If the document contains French action items, include them in French exactly as written.\n\n"
        "Return ONLY a JSON array of objects with keys: text, owner, deadline, priority, source_excerpt. "
        "No explanation outside the JSON.\n\n"
        f"DOCUMENT:\n{content[:4000]}"
    )
    prompt = ensure_bilingual_prompt(prompt, language)
    resp = await llm_client.complete(
        messages=[{"role": "user", "content": prompt}],
        tier=tier,
        expect_json=True,
    )
    if resp.error or not resp.parsed_json:
        logger.warning("LLM task extraction failed: %s", resp.error)
        return []
    items = resp.parsed_json if isinstance(resp.parsed_json, list) else []
    tasks = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        prio_str = str(item.get("priority", "medium")).lower()
        try:
            priority = PriorityLevel(prio_str)
        except ValueError:
            priority = PriorityLevel.MEDIUM
        tasks.append(
            ExtractedTask(
                task_id=f"{artifact_id}_t{i+1:03d}",
                text=str(item.get("text", ""))[:300],
                owner=item.get("owner"),
                deadline=item.get("deadline"),
                priority=priority,
                confidence=0.9,
                source_excerpt=str(item.get("source_excerpt", ""))[:150],
            )
        )
    return tasks


@dataclass
class _FullIngestResult:
    artifact_type: ArtifactType
    language: str
    metadata: dict
    tasks: list[ExtractedTask]


async def _ingest_with_llm_full(
    content: str, artifact_id: str, tier: str, extra_metadata: dict
) -> _FullIngestResult:
    """Single LLM call that handles all ingestion steps: type detection, language
    detection, metadata extraction, and task extraction."""
    prompt = (
        "Analyze the following document and return a single JSON object with these keys:\n\n"
        "\"artifact_type\": one of email | meeting_minutes | status_report | notes | other\n"
        "\"language\": one of en | fr | mixed\n"
        "\"metadata\": object with relevant fields:\n"
        "  - for email: sender, recipients, subject, date\n"
        "  - for meeting_minutes: attendees (list of names), date (YYYY-MM-DD)\n"
        "  - for status_report: title, date (YYYY-MM-DD)\n"
        "  - otherwise: {}\n"
        "\"action_items\": array of objects, each with:\n"
        "  - \"text\": exact or near-verbatim phrasing from the document, "
        "omitting only the owner prefix (e.g. 'Alice to ...' → 'Contact ...')\n"
        "  - \"owner\": full name as written, or null\n"
        "  - \"deadline\": normalized to YYYY-MM-DD, or null\n"
        "  - \"priority\": critical | high | medium | low\n"
        "  - \"source_excerpt\": short direct quote\n\n"
        "Rules:\n"
        "- Extract action items in ALL languages present; keep French items in French.\n"
        "- Do NOT paraphrase task text — copy it closely from the document.\n"
        "- Normalize all dates to YYYY-MM-DD.\n"
        "- Return ONLY the JSON object. No explanation outside the JSON.\n\n"
        f"DOCUMENT:\n{content[:5000]}"
    )
    resp = await llm_client.complete(
        messages=[{"role": "user", "content": prompt}],
        tier=tier,
        max_tokens=4000,
        expect_json=True,
    )

    if resp.error or not isinstance(resp.parsed_json, dict):
        logger.warning("LLM full ingest failed: %s", resp.error)
        # Fall back to empty/defaults so caller can still build a valid artifact
        return _FullIngestResult(
            artifact_type=ArtifactType.OTHER,
            language="en",
            metadata=dict(extra_metadata),
            tasks=[],
        )

    data = resp.parsed_json

    # --- artifact_type ---
    raw_type = str(data.get("artifact_type", "other")).lower()
    type_map = {
        "email": ArtifactType.EMAIL,
        "meeting_minutes": ArtifactType.MEETING_MINUTES,
        "status_report": ArtifactType.STATUS_REPORT,
        "notes": ArtifactType.NOTES,
    }
    artifact_type = type_map.get(raw_type, ArtifactType.OTHER)

    # --- language ---
    language = str(data.get("language", "en")).lower()
    if language not in ("en", "fr", "mixed"):
        language = "en"

    # --- metadata ---
    llm_meta = data.get("metadata") or {}
    if isinstance(llm_meta, dict):
        metadata = {**extra_metadata, **llm_meta}
    else:
        metadata = dict(extra_metadata)

    # --- tasks ---
    raw_items = data.get("action_items") or []
    tasks: list[ExtractedTask] = []
    for i, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        prio_str = str(item.get("priority", "medium")).lower()
        try:
            priority = PriorityLevel(prio_str)
        except ValueError:
            priority = PriorityLevel.MEDIUM
        tasks.append(ExtractedTask(
            task_id=f"{artifact_id}_t{i+1:03d}",
            text=str(item.get("text", ""))[:300],
            owner=item.get("owner"),
            deadline=item.get("deadline"),
            priority=priority,
            confidence=0.95,
            source_excerpt=str(item.get("source_excerpt", ""))[:150],
        ))

    return _FullIngestResult(
        artifact_type=artifact_type,
        language=language,
        metadata=metadata,
        tasks=tasks,
    )


# ---------------------------------------------------------------------------
# Content hash
# ---------------------------------------------------------------------------

def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

async def ingest_text(
    request: IngestRequest,
    storage: MockStorage | None = None,
) -> ArtifactResponse:
    storage = storage or get_storage()
    t0 = time.perf_counter()

    content = request.content.strip()
    artifact_id = f"art_{uuid.uuid4().hex[:10]}"

    # 1. Format detection
    artifact_type = _detect_artifact_type(content, request.source_type)

    # 2. Language detection
    language = detect_language(content)

    # 3. Metadata extraction
    metadata = _extract_metadata(content, artifact_type, request.metadata)

    # 4. Content hash — dedup check
    # Dedup key is method-aware so different modes cache independently,
    # while repeated submissions of the same content+mode still deduplicate.
    chash = _content_hash(content)
    if not request.use_llm:
        dedup_key = chash
    elif request.llm_mode == "full":
        dedup_key = f"{chash}_{request.llm_tier}_full"
    else:
        dedup_key = f"{chash}_{request.llm_tier}"
    existing = storage.find_by_field("artifacts", "content_hash", dedup_key)
    if existing:
        logger.info("Duplicate content detected, returning existing artifact")
        stored = existing[0]
        if isinstance(stored, dict):
            return ArtifactResponse.model_validate(stored)
        return stored

    # 5. Extraction — three paths
    if request.use_llm and request.llm_mode == "full":
        # Full LLM: one call handles type detection, language, metadata, and tasks
        full = await _ingest_with_llm_full(
            content, artifact_id, request.llm_tier, request.metadata
        )
        artifact_type = full.artifact_type
        language = full.language
        metadata = full.metadata
        extracted_tasks = full.tasks
        method = f"llm_{request.llm_tier}_full"

    elif request.use_llm:
        # Hybrid: traditional steps 1-3 + LLM task extraction
        extracted_tasks = await _tasks_from_llm(content, artifact_id, request.llm_tier, language)
        method = f"llm_{request.llm_tier}"

    else:
        extracted_tasks = _tasks_from_traditional(content, artifact_id)
        method = "traditional"

    latency_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "Ingested artifact %s lang=%s tasks=%d method=%s latency=%.0fms",
        artifact_id, language, len(extracted_tasks), method, latency_ms,
    )

    artifact = ArtifactResponse(
        artifact_id=artifact_id,
        status="processed",
        detected_language=language,
        content_preview=content[:8000],
        word_count=len(content.split()),
        extracted_tasks=extracted_tasks,
        linked_project=request.project_id,
        artifact_type=artifact_type,
        content_hash=dedup_key,
        created_at=datetime.utcnow(),
        metadata=metadata,
    )

    # 6. Store
    storage.put("artifacts", artifact_id, artifact.model_dump())
    # Also index by project
    if request.project_id:
        proj_arts = storage.get("project_artifacts", request.project_id) or []
        proj_arts.append(artifact_id)
        storage.put("project_artifacts", request.project_id, proj_arts)

    return artifact


async def ingest_email_webhook(
    payload: dict[str, Any],
    storage: MockStorage | None = None,
) -> ArtifactResponse:
    """Process an email webhook payload (mailbox bot)."""
    content_parts = []
    for field in ("subject", "from", "body", "text", "content"):
        if field in payload:
            content_parts.append(f"{field.capitalize()}: {payload[field]}")
    content = "\n".join(content_parts) or str(payload)

    request = IngestRequest(
        content=content,
        source_type="email",
        project_id=payload.get("project_id"),
        metadata={k: v for k, v in payload.items() if k not in ("body", "text", "content")},
    )
    return await ingest_text(request, storage)


def list_artifacts(
    project_id: str | None = None,
    artifact_type: str | None = None,
    limit: int = 50,
    storage: MockStorage | None = None,
) -> ArtifactListResponse:
    storage = storage or get_storage()
    filters: dict[str, Any] = {}
    if project_id:
        filters["linked_project"] = project_id
    if artifact_type:
        filters["artifact_type"] = artifact_type

    items = storage.list("artifacts", filters=filters if filters else None)
    artifacts = []
    for item in items[:limit]:
        if isinstance(item, dict):
            artifacts.append(ArtifactResponse.model_validate(item))
        else:
            artifacts.append(item)
    return ArtifactListResponse(artifacts=artifacts, total=len(artifacts))


def get_artifact(artifact_id: str, storage: MockStorage | None = None) -> ArtifactResponse | None:
    storage = storage or get_storage()
    item = storage.get("artifacts", artifact_id)
    if item is None:
        return None
    if isinstance(item, dict):
        return ArtifactResponse.model_validate(item)
    return item
