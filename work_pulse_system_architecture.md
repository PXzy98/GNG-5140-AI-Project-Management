# Work Pulse — System Architecture & API Design

> Version 0.1 | March 2026
> Target: SSC AI-Powered Project Management Assistant
> User Stories: Daily PM (US1) · Risk Checker (US2) · Scope Creep Prevention (US3)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          CLIENT (Frontend)                              │
│   Dashboard │ Todo List │ Chatbot │ Project Explorer │ Tasks            │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │ REST / WebSocket
┌──────────────────────────────▼──────────────────────────────────────────┐
│                        API GATEWAY (FastAPI)                            │
│  ┌────────────┐ ┌──────────────┐ ┌──────────────┐ ┌────────────────┐  │
│  │ Ingestion   │ │ Priority     │ │ Risk Engine  │ │ Drift Detector │  │
│  │ Service     │ │ Ranker       │ │ Service      │ │ Service        │  │
│  └──────┬─────┘ └──────┬───────┘ └──────┬───────┘ └──────┬─────────┘  │
│         │              │                │                │             │
│  ┌──────▼──────────────▼────────────────▼────────────────▼──────────┐  │
│  │                    SHARED MIDDLEWARE LAYER                        │  │
│  │  AuditLogger · BilingualProcessor · LLMClient                    │  │
│  │  (Auth/RBAC: deferred — single-user prototype)                   │  │
│  └──────────────────────────┬───────────────────────────────────────┘  │
└─────────────────────────────┼───────────────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                    ▼
   ┌───────────┐      ┌────────────┐       ┌────────────┐
   │ PostgreSQL │      │   Redis    │       │ File Store │
   │ (primary)  │      │ (cache/    │       │ (S3/local) │
   │            │      │  queue/    │       │            │
   │            │      │  session)  │       │            │
   └───────────┘      └────────────┘       └────────────┘
```

---

## 2. Tech Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| API Server | **FastAPI** (Python) | Async support, auto OpenAPI docs, easy LLM integration |
| Database | **PostgreSQL 16** | JSONB for flexible schema, full-text search, audit-friendly |
| Cache/Queue | **Redis 7** | Session cache, rate limiting, async job queue, LLM response cache |
| LLM | **Claude API / GPT API** via abstraction layer | Model-agnostic; switchable |
| File Storage | Local filesystem → S3-compatible (MinIO) | Gov-friendly self-hosted option |
| Auth | **JWT + RBAC middleware** | Stateless, fits two-tier PMO/PM model |
| Frontend | React (existing from Report C UI) | Already designed |

---

## 3. Shared Middleware Layer

All API services share these cross-cutting concerns. Implemented as **FastAPI middleware and dependency injection**.

### 3.1 Audit Logger (Pre-wired)

```python
# middleware/audit.py
class AuditMiddleware:
    """
    Intercepts every request/response and logs to audit table.
    Pre-wired for future ITSG-33 compliance.
    """
    async def __call__(self, request, call_next):
        # BEFORE request
        audit_entry = {
            "timestamp": utcnow(),
            "action": request.method,
            "resource": request.url.path,
            "request_body_hash": hash(body),     # Not storing raw body for security
            "ip_address": request.client.host,
        }
        response = await call_next(request)
        # AFTER request
        audit_entry["status_code"] = response.status_code
        audit_entry["response_time_ms"] = elapsed
        await AuditLog.insert(audit_entry)       # Async write to PostgreSQL
        return response
```

**Audit event taxonomy** (extensible enum):
- `INGEST` — artifact ingestion
- `ANALYSIS_RUN` — LLM processing triggered
- `OUTPUT_GENERATED` — action items / risks / briefing created
- `USER_EDIT` — manual correction or override
- `ACCESS_DENIED` — RBAC enforcement event
- `EXPORT` — data export action

### 3.2 Bilingual Processor (Pre-wired)

```python
# middleware/bilingual.py
class BilingualProcessor:
    """
    Detects input language (EN/FR) and ensures LLM output matches.
    Wraps all LLM calls transparently.
    """
    async def detect_language(self, text: str) -> str:
        """Fast detection using langdetect / compact model"""
        pass

    async def ensure_bilingual_prompt(self, prompt: str, source_lang: str) -> str:
        """Injects language instruction into LLM system prompt"""
        lang_instruction = {
            "fr": "Réponds en français. All output must be in French.",
            "en": "Respond in English.",
            "mixed": "Detect the primary language and respond in that language."
        }
        return f"{lang_instruction[source_lang]}\n\n{prompt}"

    async def normalize_input(self, text: str) -> dict:
        """Returns { text, detected_lang, confidence }"""
        pass
```

### 3.3 Auth / RBAC Middleware

> **Deferred for prototype.** Single-user prototype — no auth required at this stage.
> Schema and middleware stubs are preserved for future multi-user expansion.
> When needed, implement JWT + two-tier RBAC (PMO / PM) as FastAPI dependency injection.

### 3.4 LLM Abstraction Layer

```python
# services/llm_client.py
class LLMClient:
    """
    Model-agnostic LLM interface.
    All services call this instead of directly calling Claude/GPT.
    Handles: model selection, retry, caching, token tracking, bilingual wrapping.
    """
    def __init__(self, config: LLMConfig):
        self.provider = config.provider   # "anthropic" | "openai" | "openrouter"
        self.model = config.model
        self.redis = RedisCache()
        self.bilingual = BilingualProcessor()

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        context: dict = None,        # injected project/task context
        response_format: str = "json", # "json" | "text" | "structured"
        cache_key: str = None,
        source_lang: str = "en",
    ) -> LLMResponse:
        # 1. Check Redis cache
        if cache_key and (cached := await self.redis.get(cache_key)):
            return cached

        # 2. Bilingual wrapping
        system_prompt = await self.bilingual.ensure_bilingual_prompt(
            system_prompt, source_lang
        )

        # 3. Call LLM provider
        response = await self._call_provider(system_prompt, user_prompt)

        # 4. Parse + validate structured output
        parsed = self._parse_response(response, response_format)

        # 5. Cache result
        if cache_key:
            await self.redis.set(cache_key, parsed, ttl=3600)

        # 6. Track token usage (for cost monitoring)
        await self._log_tokens(response.usage)

        return parsed
```

---

## 4. Service APIs

### 4.1 Ingestion Gateway Service

**Purpose**: Receive multi-format inputs (email, file upload, text paste), detect format, extract content, normalize, store.

#### Endpoints

```
POST   /api/v1/ingest/text          — Paste text (meeting minutes, notes)
POST   /api/v1/ingest/file          — Upload file (docx, pdf, xlsx, eml, msg)
POST   /api/v1/ingest/email-webhook — Bot mailbox webhook (carbon copy receiver)
GET    /api/v1/ingest/artifacts      — List ingested artifacts (with filters)
GET    /api/v1/ingest/artifacts/{id} — Get single artifact with metadata
DELETE /api/v1/ingest/artifacts/{id} — Soft-delete artifact
```

#### Data Flow

```
Input (text/file/email)
  │
  ▼
Format Detector ──→ { type: "email" | "pdf" | "docx" | "text" | "xlsx" }
  │
  ▼
Content Extractor
  ├── EmailParser (subject, body, attachments, sender, date, cc, thread_id)
  ├── PDFExtractor (text extraction, OCR fallback)
  ├── DocxExtractor (paragraph text, tables)
  ├── XlsxExtractor (sheet data → structured records)
  └── PlainTextNormalizer
  │
  ▼
Bilingual Processor ──→ detect language, tag as EN/FR/mixed
  │
  ▼
Artifact Record ──→ PostgreSQL (metadata + normalized text)
                 ──→ File Store (original file preserved)
                 ──→ Redis (publish event: "artifact.ingested")
```

#### Request/Response Examples

```python
# POST /api/v1/ingest/text
# Request
{
    "content": "Meeting minutes from March 1 sprint review...",
    "source_type": "meeting_minutes",    # meeting_minutes | email | notes | other
    "project_id": "proj_001",            # optional: link to project
    "metadata": {
        "meeting_date": "2026-03-01",
        "participants": ["Alice", "Bob"]
    }
}

# Response
{
    "artifact_id": "art_20260301_001",
    "status": "processed",
    "detected_language": "en",
    "content_preview": "Meeting minutes from March 1...",
    "word_count": 342,
    "linked_project": "proj_001",
    "created_at": "2026-03-01T10:30:00Z",
    "audit_event_id": "aud_00123"         # traceability to audit log
}

# POST /api/v1/ingest/email-webhook
# Request (from bot mailbox)
{
    "from": "pm.alice@ssc-spc.gc.ca",
    "to": "workpulse-bot@ssc-spc.gc.ca",
    "cc": ["bob@ssc-spc.gc.ca"],
    "subject": "RE: NGIS Phase 3 — vendor delay update",
    "body_text": "Hi team, just got word from the vendor...",
    "body_html": "<html>...",
    "attachments": [
        { "filename": "status_update.pdf", "content_base64": "..." }
    ],
    "received_at": "2026-03-01T09:15:00Z",
    "message_id": "<abc123@mail.ssc>",
    "thread_id": "<thread456@mail.ssc>"
}

# Response
{
    "artifact_id": "art_20260301_002",
    "status": "processed",
    "detected_language": "mixed",
    "extracted_entities": {
        "sender": "pm.alice@ssc-spc.gc.ca",
        "project_ref": "NGIS Phase 3",
        "attachment_artifacts": ["art_20260301_003"]    # attachment processed separately
    },
    "audit_event_id": "aud_00124"
}
```

#### Email Bot Integration (M365 Workaround)

```
┌─────────────┐    carbon copy     ┌──────────────────┐
│ PM sends     │ ──────────────────→│ Bot Mailbox       │
│ normal email │    (CC/BCC)       │ workpulse-bot@... │
└─────────────┘                    └────────┬─────────┘
                                            │ IMAP poll / webhook
                                            ▼
                                   ┌──────────────────┐
                                   │ Ingestion Gateway │
                                   │ /email-webhook    │
                                   └──────────────────┘

Approach: No M365 API required.
PM simply CC's the bot on relevant emails.
Bot mailbox polls via IMAP or receives webhook from mail server.
```

---

### 4.2 Priority Ranker Service

**Purpose**: Given a set of tasks + project context, produce a prioritized ranking with transparent reasoning.

#### Endpoints

```
POST   /api/v1/priority/rank            — Rank a set of tasks
GET    /api/v1/priority/rank/{id}        — Get a previous ranking result
POST   /api/v1/priority/rerank          — Re-rank after user correction
```

#### Processing Pipeline

```
Input: task_ids[] + project_context
  │
  ▼
Context Assembler
  ├── Fetch tasks from DB (status, deadline, owner, dependencies)
  ├── Fetch related artifacts (recent emails, meeting notes)
  ├── Fetch project risk scores (from Risk Engine)
  └── Build context window for LLM
  │
  ▼
LLM Priority Analysis
  │  System prompt:
  │  "You are a project management assistant for Government of Canada projects.
  │   Given the following tasks and project context, rank by priority.
  │   For each task, provide:
  │   - rank (1 = highest)
  │   - priority_level: Critical / High / Medium / Low
  │   - reasoning (2-3 sentences citing specific evidence)
  │   - risk_factors contributing to priority
  │   - evidence_refs: list of artifact_ids supporting your reasoning
  │   Return as JSON array."
  │
  ▼
Post-Processing
  ├── Validate JSON structure
  ├── Verify evidence_refs exist in DB
  ├── Compute confidence score (based on context completeness)
  ├── Apply deadline proximity boost (rule-based adjustment)
  └── Store ranking result with version
  │
  ▼
Output: PriorityRanking record ──→ PostgreSQL
                                ──→ Redis (cache for Dashboard Top-K)
```

#### Request/Response

```python
# POST /api/v1/priority/rank
# Request
{
    "project_id": "proj_001",
    "task_ids": ["task_001", "task_002", "task_003", ...],  # up to 20
    "context_window": "7d",       # look back 7 days of artifacts
    "top_k": 5,                    # return top 5 with full reasoning
    "include_risk_factors": true
}

# Response
{
    "ranking_id": "rank_20260301_001",
    "project_id": "proj_001",
    "generated_at": "2026-03-01T11:00:00Z",
    "model_used": "claude-sonnet-4-5",
    "rankings": [
        {
            "rank": 1,
            "task_id": "task_003",
            "task_name": "Resolve vendor delay for NGIS Phase 3",
            "priority_level": "Critical",
            "reasoning": "Vendor delay directly blocks data centre migration milestone. Email from March 1 confirms 2-week slip. No mitigation plan documented yet.",
            "risk_factors": ["deadline_proximity", "dependency_blocked", "no_mitigation"],
            "evidence_refs": ["art_20260301_002", "art_20260228_005"],
            "confidence": 0.92,
            "deadline": "2026-03-10",
            "days_remaining": 9
        },
        {
            "rank": 2,
            "task_id": "task_001",
            ...
        }
    ],
    "context_summary": {
        "tasks_analyzed": 10,
        "artifacts_referenced": 8,
        "time_window": "2026-02-22 to 2026-03-01"
    },
    "audit_event_id": "aud_00130"
}
```

---

### 4.3 Risk Engine Service

**Purpose**: Identify risks from artifacts, link evidence, score risks, provide project-level risk assessment.

#### Endpoints

```
POST   /api/v1/risk/identify              — Run risk identification on artifact(s)
GET    /api/v1/risk/project/{project_id}   — Get project-level risk summary
GET    /api/v1/risk/{risk_id}              — Get single risk with full evidence chain
POST   /api/v1/risk/cross-check           — Cross-check multiple artifacts for inconsistencies
PUT    /api/v1/risk/{risk_id}/override     — Human override of risk score (with audit)
GET    /api/v1/risk/project/{id}/history   — Risk trend over time
```

#### Processing Pipeline

```
Input: artifact_ids[] + project_id
  │
  ▼
┌─────────────────────────────────────────────────────┐
│ STEP 1: Risk Identification                          │
│                                                      │
│  For each artifact:                                  │
│  LLM Prompt: "Analyze this document for project      │
│  risks. Identify: risk description, category          │
│  (schedule/budget/technical/resource/compliance),     │
│  affected stakeholders, source evidence (quote the    │
│  exact sentence)."                                   │
│                                                      │
│  Output: RawRisk[]                                   │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│ STEP 2: Cross-Document Inconsistency Detection       │
│                                                      │
│  Compare artifacts pairwise:                         │
│  LLM Prompt: "Compare Document A and Document B.     │
│  Identify any inconsistencies, contradictions, or    │
│  risks mentioned in one but missing from the other.  │
│  For each inconsistency, cite both sources."         │
│                                                      │
│  Output: Inconsistency[]                             │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│ STEP 3: Evidence Linking                             │
│                                                      │
│  For each identified risk:                           │
│  - Link to source artifact_id(s)                     │
│  - Extract specific sentence/paragraph as evidence   │
│  - Record page/paragraph location                    │
│  - Assign evidence_strength: direct / inferred       │
│                                                      │
│  Output: EvidenceChain[]                             │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│ STEP 4: Risk Scoring Matrix                          │
│                                                      │
│  For each risk, compute:                             │
│  - likelihood: 1-5 (LLM assessment + rule-based)     │
│  - impact: 1-5 (based on category & scope)           │
│  - risk_score = likelihood × impact                  │
│  - risk_level: Critical (≥20) / High (≥12) /        │
│                Medium (≥6) / Low (<6)                │
│                                                      │
│  Rule-based adjustments:                             │
│  - deadline < 7 days → likelihood +1                 │
│  - no_owner_assigned → impact +1                     │
│  - multiple_sources_confirm → likelihood +1          │
│  - human_override → use override value               │
│                                                      │
│  Output: ScoredRisk[]                                │
└──────────────────────┬──────────────────────────────┘
                       ▼
Store to PostgreSQL + Update Redis project risk cache
```

#### Request/Response

```python
# POST /api/v1/risk/identify
# Request
{
    "project_id": "proj_001",
    "artifact_ids": ["art_20260301_002", "art_20260228_010"],
    "analysis_depth": "full",        # "quick" (single-pass) | "full" (cross-check)
    "include_scoring": true
}

# Response
{
    "analysis_id": "risk_analysis_20260301_001",
    "project_id": "proj_001",
    "generated_at": "2026-03-01T11:30:00Z",
    "risks": [
        {
            "risk_id": "risk_001",
            "description": "Vendor delivery for network equipment delayed by 2 weeks, threatening NGIS Phase 3 migration timeline",
            "category": "schedule",
            "risk_score": {
                "likelihood": 4,
                "impact": 5,
                "score": 20,
                "level": "Critical"
            },
            "evidence_chain": [
                {
                    "artifact_id": "art_20260301_002",
                    "artifact_type": "email",
                    "excerpt": "Vendor confirmed 2-week delay on network switches",
                    "location": "paragraph 3",
                    "evidence_strength": "direct"
                }
            ],
            "affected_stakeholders": ["Network Team", "DC Migration Lead"],
            "suggested_mitigation": "Escalate to procurement; explore alternate vendor",
            "detected_language": "en"
        },
        {
            "risk_id": "risk_002",
            "description": "Status report does not mention vendor delay — information gap between email and official reporting",
            "category": "compliance",
            "risk_score": {
                "likelihood": 5,
                "impact": 3,
                "score": 15,
                "level": "High"
            },
            "evidence_chain": [
                {
                    "artifact_id": "art_20260301_002",
                    "source_type": "email",
                    "excerpt": "vendor delay mentioned",
                    "evidence_strength": "direct"
                },
                {
                    "artifact_id": "art_20260228_010",
                    "source_type": "status_report",
                    "excerpt": "no risks identified in current reporting period",
                    "evidence_strength": "direct"
                }
            ],
            "inconsistency_flag": true
        }
    ],
    "summary": {
        "total_risks": 2,
        "critical": 1,
        "high": 1,
        "medium": 0,
        "low": 0
    },
    "audit_event_id": "aud_00135"
}

# GET /api/v1/risk/project/proj_001
# Response — Project-level risk dashboard data
{
    "project_id": "proj_001",
    "project_name": "NGIS Phase 3",
    "overall_risk_level": "High",
    "risk_breakdown": {
        "schedule": { "count": 3, "max_score": 20 },
        "budget": { "count": 1, "max_score": 8 },
        "technical": { "count": 2, "max_score": 12 },
        "resource": { "count": 1, "max_score": 6 },
        "compliance": { "count": 1, "max_score": 15 }
    },
    "risk_trend": [
        { "date": "2026-02-15", "avg_score": 8.2 },
        { "date": "2026-02-22", "avg_score": 10.5 },
        { "date": "2026-03-01", "avg_score": 14.1 }
    ],
    "top_risks": [ /* top 3 risks by score */ ],
    "last_analysis": "2026-03-01T11:30:00Z"
}
```

---

### 4.4 Drift Detection Service (Scope Creep Prevention)

**Purpose**: Compare incoming communications against project baseline (SOW/scope) to detect deviations.

#### Endpoints

```
POST   /api/v1/drift/baseline              — Set/update project scope baseline
POST   /api/v1/drift/check                 — Check artifact(s) against baseline
GET    /api/v1/drift/project/{id}/alerts    — Get drift alerts for a project
GET    /api/v1/drift/alert/{alert_id}       — Get single alert with evidence
PUT    /api/v1/drift/alert/{id}/resolve     — Mark alert as resolved/false-positive
GET    /api/v1/drift/project/{id}/history   — Drift trend over time
```

#### Processing Pipeline

```
                    ┌────────────────────────┐
                    │  Baseline Setup (once)  │
                    │  POST /drift/baseline   │
                    │                         │
                    │  Input: SOW document,   │
                    │  project charter, scope │
                    │  statement              │
                    │           │              │
                    │           ▼              │
                    │  LLM: Extract scope     │
                    │  boundaries as          │
                    │  structured items:      │
                    │  - in_scope[]           │
                    │  - out_of_scope[]       │
                    │  - deliverables[]       │
                    │  - constraints[]        │
                    │           │              │
                    │           ▼              │
                    │  Store + Vectorize      │
                    │  (for semantic search)  │
                    └────────────────────────┘

                    ┌────────────────────────┐
                    │  Drift Check (ongoing)  │
                    │  POST /drift/check      │
                    │  (or triggered by       │
                    │   Ingestion webhook)    │
                    │           │              │
                    │           ▼              │
                    │  For each new artifact: │
                    │                         │
                    │  Step 1: Extract        │
                    │  "asks" / requests      │
                    │  / new requirements     │
                    │  from the text          │
                    │           │              │
                    │           ▼              │
                    │  Step 2: Compare each   │
                    │  "ask" against baseline │
                    │  - Semantic similarity  │
                    │    (vector search)      │
                    │  - LLM judgment:        │
                    │    "Does this ask fall  │
                    │    within the defined   │
                    │    scope?"              │
                    │           │              │
                    │           ▼              │
                    │  Step 3: Score drift    │
                    │  - alignment_score 0-1  │
                    │  - drift_type:          │
                    │    new_requirement |    │
                    │    scope_expansion |    │
                    │    contradicts_scope |  │
                    │    ambiguous            │
                    │           │              │
                    │           ▼              │
                    │  Step 4: Alert if       │
                    │  alignment_score < 0.6  │
                    │  or drift_type !=       │
                    │  "within_scope"         │
                    └────────────────────────┘
```

#### Request/Response

```python
# POST /api/v1/drift/baseline
# Request
{
    "project_id": "proj_001",
    "baseline_artifacts": ["art_sow_001"],     # SOW / project charter
    "additional_scope_text": "Optional manual scope boundaries...",
    "version_note": "Initial baseline from approved SOW v1.0"
}

# Response
{
    "baseline_id": "baseline_proj001_v1",
    "project_id": "proj_001",
    "extracted_scope": {
        "in_scope": [
            "Data centre consolidation for 3 facilities",
            "Network equipment migration",
            "Active Directory migration"
        ],
        "out_of_scope": [
            "End-user device refresh",
            "Application modernization"
        ],
        "deliverables": [
            "Migration plan document",
            "Risk assessment report",
            "Post-migration validation report"
        ],
        "constraints": [
            "Must complete by Q2 2026",
            "Budget cap: $2.4M"
        ]
    },
    "vector_index_status": "indexed",
    "created_at": "2026-03-01T12:00:00Z",
    "audit_event_id": "aud_00140"
}

# POST /api/v1/drift/check
# Request
{
    "project_id": "proj_001",
    "artifact_ids": ["art_20260301_002"],    # new email to check
    "baseline_id": "baseline_proj001_v1"     # optional, defaults to latest
}

# Response
{
    "check_id": "drift_check_20260301_001",
    "project_id": "proj_001",
    "alerts": [
        {
            "alert_id": "drift_alert_001",
            "severity": "warning",
            "drift_type": "scope_expansion",
            "description": "Email requests adding Wi-Fi infrastructure upgrade to NGIS Phase 3 scope — not included in original SOW",
            "detected_ask": "Can we also upgrade the Wi-Fi access points while we're doing the network migration?",
            "alignment_score": 0.23,
            "baseline_reference": {
                "closest_scope_item": "Network equipment migration",
                "similarity": 0.67,
                "judgment": "Related to network but constitutes new scope not in SOW"
            },
            "evidence": {
                "artifact_id": "art_20260301_002",
                "excerpt": "Can we also upgrade the Wi-Fi access points...",
                "sender": "stakeholder_x@ssc-spc.gc.ca"
            },
            "suggested_action": "Review with project sponsor; if approved, update SOW and baseline"
        }
    ],
    "no_drift_items": [
        "Vendor delay discussion — within scope of existing risk management"
    ],
    "audit_event_id": "aud_00141"
}
```

#### Event-Driven Integration

```
Ingestion Gateway
  │
  │  Redis pub/sub: "artifact.ingested"
  │
  ▼
Drift Detection Service (subscriber)
  │
  │  Auto-checks new artifacts against baseline
  │
  ▼
If drift detected → Redis pub/sub: "drift.alert.created"
  │
  ▼
Dashboard notification panel (subscriber)
```

---

### 4.5 Daily Planner Service (Task List + Brief Report + Day Rollover)

**Purpose**: Maintain a per-day task list and daily brief report. At end-of-day, detect unfinished tasks and roll them over to the next day's list automatically.

#### Endpoints

```
POST   /api/v1/daily/generate              — Generate today's task list + brief from recent artifacts
GET    /api/v1/daily/{date}                 — Get daily plan for a specific date (YYYY-MM-DD)
GET    /api/v1/daily/today                  — Shortcut: get today's plan
PUT    /api/v1/daily/{date}/tasks/{task_id} — Update task status within daily list
POST   /api/v1/daily/{date}/tasks           — Manually add a task to daily list
DELETE /api/v1/daily/{date}/tasks/{task_id} — Remove task from daily list

POST   /api/v1/daily/{date}/close           — End-of-day: close the day, trigger rollover
GET    /api/v1/daily/{date}/report           — Get the daily brief report
POST   /api/v1/daily/{date}/report/regenerate — Re-generate brief with updated info

GET    /api/v1/daily/history                 — List recent daily plans (paginated)
GET    /api/v1/daily/streak                  — Completion stats (tasks done vs rolled over)
```

#### Data Flow — Morning: Generate Daily Plan

```
Trigger: User opens Dashboard / calls POST /daily/generate
  │
  ▼
Step 1: Collect Inputs
  ├── Rolled-over tasks from previous day (status = 'rolled_over')
  ├── Tasks from DB where deadline = today or overdue
  ├── New artifacts ingested since last daily plan
  └── Active risks with level >= High
  │
  ▼
Step 2: LLM Daily Brief Generation
  │  System prompt:
  │  "You are a daily planning assistant for a Government of Canada PM.
  │   Given the following tasks, rolled-over items, new communications,
  │   and active risks, generate:
  │   1. A prioritized daily task list (ordered by urgency)
  │   2. A brief report summarizing:
  │      - Key items requiring attention today
  │      - Risks that have changed since yesterday
  │      - Rolled-over tasks that need immediate action
  │      - New items extracted from overnight communications
  │   Format: JSON { tasks: [...], brief_report: { summary, highlights, warnings } }"
  │
  ▼
Step 3: Store Daily Plan
  ├── daily_plans table (the plan record)
  ├── daily_plan_tasks table (individual task entries for the day)
  └── Redis cache: "daily:{date}:plan" (for fast Dashboard access)
```

#### Data Flow — End of Day: Close & Rollover

```
Trigger: User clicks "Close Day" / Scheduled cron at 23:59 / POST /daily/{date}/close
  │
  ▼
Step 1: Snapshot Current State
  ├── Mark daily plan status = 'closed'
  ├── For each task in today's list:
  │     completed     → status = 'completed', completion_time recorded
  │     in_progress   → status = 'rolled_over', rollover_count += 1
  │     not_started   → status = 'rolled_over', rollover_count += 1
  │
  ▼
Step 2: Generate End-of-Day Summary
  │  LLM: "Summarize what was accomplished today vs what was planned.
  │        Highlight: completed items, items rolled over (and why),
  │        any new risks or drift alerts that appeared today."
  │
  │  Append to daily brief report as "end_of_day_summary"
  │
  ▼
Step 3: Seed Next Day's Plan
  ├── Copy rolled-over tasks → create entries for tomorrow
  │   with flag: is_rollover = true, original_date, rollover_count
  ├── If rollover_count >= 3 → flag as "chronic_blocker",
  │   elevate priority automatically
  └── Tomorrow's plan status = 'draft' (finalized when user opens it)
  │
  ▼
Step 4: Notify
  └── Redis publish "events:daily.closed" → Dashboard update
```

#### Request/Response

```python
# POST /api/v1/daily/generate
# Request
{
    "date": "2026-03-01",                # optional, defaults to today
    "include_risk_summary": true,
    "lookback_hours": 24                  # how far back to check for new artifacts
}

# Response
{
    "plan_id": "daily_20260301",
    "date": "2026-03-01",
    "status": "active",
    "tasks": [
        {
            "daily_task_id": "dt_001",
            "task_id": "task_003",              # linked to master task
            "name": "Resolve vendor delay for NGIS Phase 3",
            "priority": "critical",
            "source": "rolled_over",             # rolled_over | scheduled | new_extraction | manual
            "original_date": "2026-02-28",
            "rollover_count": 1,
            "deadline": "2026-03-10",
            "status": "not_started",
            "risk_level": "critical",
            "notes": "Rolled over from yesterday — no progress recorded"
        },
        {
            "daily_task_id": "dt_002",
            "task_id": "task_010",
            "name": "Review email re: GC-Cloud Wave 4 budget revision",
            "priority": "high",
            "source": "new_extraction",
            "original_date": null,
            "rollover_count": 0,
            "deadline": "2026-03-03",
            "status": "not_started",
            "risk_level": "medium",
            "notes": "Extracted from email ingested at 08:15 today"
        },
        {
            "daily_task_id": "dt_003",
            "task_id": null,                    # no master task yet
            "name": "Prepare slide deck for Friday governance review",
            "priority": "medium",
            "source": "manual",
            "original_date": null,
            "rollover_count": 0,
            "deadline": "2026-03-05",
            "status": "not_started",
            "risk_level": null,
            "notes": "Manually added by user"
        }
    ],
    "brief_report": {
        "summary": "3 items on today's agenda. 1 critical item rolled over from yesterday (vendor delay). 1 new item extracted from overnight email. 1 manually added.",
        "highlights": [
            "Vendor delay for NGIS Phase 3 is now 1 day overdue for follow-up",
            "New budget revision request for GC-Cloud Wave 4 received overnight"
        ],
        "warnings": [
            "Vendor delay task has been rolled over once — requires action today"
        ],
        "active_risks_summary": {
            "critical": 1,
            "high": 1,
            "total": 5
        }
    },
    "generated_at": "2026-03-01T08:00:00Z",
    "audit_event_id": "aud_00150"
}

# POST /api/v1/daily/2026-03-01/close
# Request
{
    "task_updates": [
        { "daily_task_id": "dt_001", "status": "in_progress", "notes": "Called vendor, awaiting callback" },
        { "daily_task_id": "dt_002", "status": "completed" },
        { "daily_task_id": "dt_003", "status": "not_started" }
    ]
}

# Response
{
    "plan_id": "daily_20260301",
    "date": "2026-03-01",
    "status": "closed",
    "closed_at": "2026-03-01T23:59:00Z",
    "summary": {
        "total_tasks": 3,
        "completed": 1,
        "rolled_over": 2,
        "completion_rate": 0.33
    },
    "end_of_day_report": {
        "accomplished": "Reviewed and responded to GC-Cloud Wave 4 budget revision email.",
        "rolled_over": [
            {
                "daily_task_id": "dt_001",
                "name": "Resolve vendor delay for NGIS Phase 3",
                "rollover_count": 2,
                "flag": "chronic_blocker",
                "reason": "In progress — vendor callback pending"
            },
            {
                "daily_task_id": "dt_003",
                "name": "Prepare slide deck for Friday governance review",
                "rollover_count": 1,
                "flag": null,
                "reason": "Not started"
            }
        ],
        "new_risks_today": 0,
        "drift_alerts_today": 0
    },
    "next_day_seeded": {
        "date": "2026-03-02",
        "seeded_tasks": 2,
        "chronic_blockers": 1
    },
    "audit_event_id": "aud_00160"
}

# GET /api/v1/daily/2026-03-01/report
# Response
{
    "plan_id": "daily_20260301",
    "date": "2026-03-01",
    "morning_brief": {
        "summary": "3 items on today's agenda...",
        "highlights": [...],
        "warnings": [...]
    },
    "end_of_day_summary": {
        "accomplished": "...",
        "rolled_over": [...],
        "completion_rate": 0.33
    },
    "full_narrative": "March 1, 2026 — Daily Report\n\nToday's plan included 3 tasks. The critical vendor delay for NGIS Phase 3 remains unresolved (vendor callback pending, now rolled over twice — flagged as chronic blocker). The GC-Cloud Wave 4 budget revision was reviewed and completed. Governance slide deck preparation was not started and rolls to March 2.\n\nRisk posture: 1 critical, 1 high. No new drift alerts.",
    "generated_at": "2026-03-01T23:59:00Z"
}
```

---

## 5. Database Design (PostgreSQL)

### 5.1 Core Tables

```sql
-- ============================================================
-- PROJECTS
-- ============================================================
CREATE TABLE projects (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    owner_id        UUID REFERENCES users(id),
    start_date      DATE,
    end_date        DATE,
    status          VARCHAR(50) DEFAULT 'active',  -- active | completed | on_hold
    metadata        JSONB DEFAULT '{}',             -- flexible extension
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ                     -- soft delete
);

-- ============================================================
-- TASKS
-- ============================================================
CREATE TABLE tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id),
    name            VARCHAR(500) NOT NULL,
    description     TEXT,
    status          VARCHAR(50) DEFAULT 'not_started',
    priority        VARCHAR(20),                    -- critical | high | medium | low
    priority_score  FLOAT,                          -- numeric score from ranker
    owner_id        UUID REFERENCES users(id),
    start_date      DATE,
    deadline        DATE,
    risk_level      VARCHAR(20),                    -- critical | high | medium | low
    risk_score      FLOAT,
    source_artifact_id UUID REFERENCES artifacts(id), -- where this task was extracted from
    ai_generated    BOOLEAN DEFAULT false,          -- extracted by AI vs manual
    human_verified  BOOLEAN DEFAULT false,          -- human confirmed
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX idx_tasks_project ON tasks(project_id);
CREATE INDEX idx_tasks_deadline ON tasks(deadline);
CREATE INDEX idx_tasks_priority ON tasks(priority_score DESC);
CREATE INDEX idx_tasks_owner ON tasks(owner_id);

-- ============================================================
-- ARTIFACTS (ingested documents)
-- ============================================================
CREATE TABLE artifacts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id),
    artifact_type   VARCHAR(50) NOT NULL,           -- email | meeting_minutes | document | text | spreadsheet
    source_mode     VARCHAR(20) NOT NULL,           -- manual | automated (bot)
    original_filename VARCHAR(500),
    file_path       VARCHAR(1000),                  -- path in file store
    content_text    TEXT,                            -- extracted normalized text
    content_hash    VARCHAR(64),                     -- SHA-256 for dedup
    detected_language VARCHAR(10),                  -- en | fr | mixed
    word_count      INTEGER,
    metadata        JSONB DEFAULT '{}',             -- sender, recipients, thread_id, etc.
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX idx_artifacts_project ON artifacts(project_id);
CREATE INDEX idx_artifacts_type ON artifacts(artifact_type);
CREATE INDEX idx_artifacts_content_hash ON artifacts(content_hash);   -- dedup
-- Full-text search index for content
CREATE INDEX idx_artifacts_fts ON artifacts USING gin(to_tsvector('english', content_text));

-- ============================================================
-- RISKS
-- ============================================================
CREATE TABLE risks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id),
    analysis_id     UUID REFERENCES risk_analyses(id),
    description     TEXT NOT NULL,
    category        VARCHAR(50),                    -- schedule | budget | technical | resource | compliance
    likelihood      INTEGER CHECK (likelihood BETWEEN 1 AND 5),
    impact          INTEGER CHECK (impact BETWEEN 1 AND 5),
    risk_score      INTEGER GENERATED ALWAYS AS (likelihood * impact) STORED,
    risk_level      VARCHAR(20),                    -- critical | high | medium | low
    suggested_mitigation TEXT,
    inconsistency_flag  BOOLEAN DEFAULT false,
    human_override      JSONB,                      -- { overridden_by, original_score, new_score, reason }
    status          VARCHAR(50) DEFAULT 'open',     -- open | mitigated | accepted | closed
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_risks_project ON risks(project_id);
CREATE INDEX idx_risks_score ON risks(risk_score DESC);

-- ============================================================
-- EVIDENCE LINKS (traceability)
-- ============================================================
CREATE TABLE evidence_links (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type     VARCHAR(50) NOT NULL,           -- risk | task | priority_ranking | drift_alert
    source_id       UUID NOT NULL,                  -- FK to risks.id, tasks.id, etc.
    artifact_id     UUID REFERENCES artifacts(id),
    excerpt         TEXT,                            -- relevant quote from artifact
    location        VARCHAR(200),                   -- page/paragraph/line reference
    evidence_strength VARCHAR(20),                  -- direct | inferred
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_evidence_source ON evidence_links(source_type, source_id);
CREATE INDEX idx_evidence_artifact ON evidence_links(artifact_id);

-- ============================================================
-- RISK ANALYSES (batch analysis records)
-- ============================================================
CREATE TABLE risk_analyses (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id),
    artifact_ids    UUID[] NOT NULL,
    model_used      VARCHAR(100),
    analysis_depth  VARCHAR(20),                    -- quick | full
    summary         JSONB,                          -- { total, critical, high, medium, low }
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- DRIFT BASELINES
-- ============================================================
CREATE TABLE drift_baselines (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id),
    version         INTEGER DEFAULT 1,
    source_artifact_ids UUID[],
    extracted_scope JSONB NOT NULL,                  -- { in_scope[], out_of_scope[], deliverables[], constraints[] }
    scope_embedding VECTOR(1536),                   -- pgvector for semantic search
    version_note    TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    is_active       BOOLEAN DEFAULT true
);

CREATE INDEX idx_baselines_project ON drift_baselines(project_id, is_active);

-- ============================================================
-- DRIFT ALERTS
-- ============================================================
CREATE TABLE drift_alerts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id),
    baseline_id     UUID REFERENCES drift_baselines(id),
    check_artifact_id UUID REFERENCES artifacts(id),
    severity        VARCHAR(20),                    -- critical | warning | info
    drift_type      VARCHAR(50),                    -- new_requirement | scope_expansion | contradicts_scope | ambiguous
    description     TEXT,
    detected_ask    TEXT,
    alignment_score FLOAT,                          -- 0.0 (total drift) to 1.0 (within scope)
    baseline_reference JSONB,                       -- { closest_scope_item, similarity, judgment }
    status          VARCHAR(50) DEFAULT 'open',     -- open | reviewed | resolved | false_positive
    resolved_by     UUID REFERENCES users(id),
    resolution_note TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX idx_drift_alerts_project ON drift_alerts(project_id, status);

-- ============================================================
-- PRIORITY RANKINGS
-- ============================================================
CREATE TABLE priority_rankings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id),
    task_ids        UUID[] NOT NULL,
    model_used      VARCHAR(100),
    context_window  VARCHAR(20),
    rankings        JSONB NOT NULL,                 -- full ranking array
    context_summary JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- DAILY PLANS
-- ============================================================
CREATE TABLE daily_plans (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_date       DATE NOT NULL UNIQUE,            -- one plan per day
    status          VARCHAR(20) DEFAULT 'active',    -- draft | active | closed
    morning_brief   JSONB,                           -- { summary, highlights, warnings, active_risks_summary }
    end_of_day_summary JSONB,                        -- { accomplished, rolled_over[], completion_rate, new_risks, drift_alerts }
    full_narrative  TEXT,                             -- LLM-generated daily report text
    model_used      VARCHAR(100),
    total_tasks     INTEGER DEFAULT 0,
    completed_tasks INTEGER DEFAULT 0,
    rolled_over_tasks INTEGER DEFAULT 0,
    completion_rate FLOAT,
    generated_at    TIMESTAMPTZ,
    closed_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_daily_plans_date ON daily_plans(plan_date);

-- ============================================================
-- DAILY PLAN TASKS (per-day task entries)
-- ============================================================
CREATE TABLE daily_plan_tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id         UUID REFERENCES daily_plans(id) ON DELETE CASCADE,
    task_id         UUID REFERENCES tasks(id),       -- nullable: manual tasks may not have a master task yet
    name            VARCHAR(500) NOT NULL,
    priority        VARCHAR(20),                     -- critical | high | medium | low
    status          VARCHAR(50) DEFAULT 'not_started', -- not_started | in_progress | completed | rolled_over
    source          VARCHAR(30) NOT NULL,            -- rolled_over | scheduled | new_extraction | manual
    original_date   DATE,                            -- first date this task appeared (for rollover tracking)
    rollover_count  INTEGER DEFAULT 0,               -- how many times rolled over
    is_chronic_blocker BOOLEAN DEFAULT false,         -- flagged if rollover_count >= 3
    risk_level      VARCHAR(20),
    deadline        DATE,
    notes           TEXT,
    completion_time TIMESTAMPTZ,                     -- when marked completed
    sort_order      INTEGER DEFAULT 0,               -- display order in daily list
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_daily_tasks_plan ON daily_plan_tasks(plan_id);
CREATE INDEX idx_daily_tasks_status ON daily_plan_tasks(status);
CREATE INDEX idx_daily_tasks_rollover ON daily_plan_tasks(rollover_count DESC);
CREATE INDEX idx_daily_tasks_source ON daily_plan_tasks(source);

-- ============================================================
-- USERS & RBAC
-- ============================================================
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           VARCHAR(255) UNIQUE NOT NULL,
    name            VARCHAR(255),
    role            VARCHAR(50) NOT NULL,            -- PMO | PM
    department      VARCHAR(255),
    phone           VARCHAR(50),
    is_active       BOOLEAN DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- AUDIT LOG (append-only, tamper-resistant)
-- ============================================================
CREATE TABLE audit_log (
    id              BIGSERIAL PRIMARY KEY,           -- sequential for ordering
    event_id        UUID DEFAULT gen_random_uuid(),
    timestamp       TIMESTAMPTZ DEFAULT NOW(),
    user_id         UUID REFERENCES users(id),
    role            VARCHAR(50),
    action          VARCHAR(50) NOT NULL,            -- INGEST | ANALYSIS_RUN | OUTPUT_GENERATED | USER_EDIT | ACCESS_DENIED | EXPORT
    resource_type   VARCHAR(100),
    resource_id     UUID,
    request_path    VARCHAR(500),
    request_method  VARCHAR(10),
    request_body_hash VARCHAR(64),
    status_code     INTEGER,
    response_time_ms INTEGER,
    ip_address      INET,
    session_id      VARCHAR(100),
    details         JSONB DEFAULT '{}',             -- additional context
    prev_hash       VARCHAR(64)                      -- chain hash for tamper detection
);

-- Append-only: no UPDATE or DELETE permissions granted
-- Partitioned by month for performance
CREATE INDEX idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX idx_audit_user ON audit_log(user_id);
CREATE INDEX idx_audit_action ON audit_log(action);
CREATE INDEX idx_audit_resource ON audit_log(resource_type, resource_id);
```

### 5.2 Entity Relationship Summary

```
users ──────────────────┐
  │                     │
  │ owns                │ assigned_to
  ▼                     ▼
projects ────────── tasks ◄──────── daily_plan_tasks
  │                  │  │                  │
  │ contains         │  │ extracted_from   │ belongs_to
  ▼                  │  ▼                  ▼
artifacts ◄──────────┘ evidence_links   daily_plans
  │                         ▲              │
  │ analyzed_by             │ supports     │ generates
  ▼                         │              ▼
risk_analyses ──────── risks          (morning_brief +
                            │          end_of_day_summary)
drift_baselines             │
  │                         │
  │ checked_against         │
  ▼                         │
drift_alerts ───────────────┘
                            │
priority_rankings ──────────┘

audit_log ← (records all operations across all tables)
```

---

## 6. Redis Usage Design

### 6.1 Use Cases

| Use Case | Redis Feature | Key Pattern | TTL |
|----------|--------------|-------------|-----|
| **LLM Response Cache** | String/Hash | `llm:cache:{prompt_hash}` | 1h |
| **Dashboard Top-K Tasks** | Sorted Set | `dashboard:top_tasks` | 15min |
| **Project Risk Summary** | Hash | `risk:summary:{project_id}` | 30min |
| **Daily Plan Cache** | String | `daily:{date}:plan` | Until day closed |
| **Daily Brief Cache** | String | `daily:{date}:brief` | Until regenerated |
| **Rate Limiting** | String + INCR | `ratelimit:{endpoint}` | 1min |
| **Async Job Queue** | List (LPUSH/BRPOP) | `queue:ingestion`, `queue:analysis` | — |
| **Pub/Sub Events** | Pub/Sub channels | `events:artifact.ingested`, `events:drift.alert`, `events:daily.closed` | — |
| **Processing Lock** | String + NX | `lock:analysis:{project_id}` | 5min |

### 6.2 Cache Strategy

```python
# Example: Dashboard Top-K cache + Daily Plan cache

async def get_dashboard_top_tasks(k: int = 5) -> list:
    cache_key = "dashboard:top_tasks"

    # Try cache first
    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached)

    # Cache miss → query DB
    tasks = await db.query("""
        SELECT t.*, r.risk_score
        FROM tasks t
        LEFT JOIN risks r ON r.id = (
            SELECT id FROM risks WHERE project_id = t.project_id
            ORDER BY risk_score DESC LIMIT 1
        )
        WHERE t.status != 'completed'
        ORDER BY t.priority_score DESC NULLS LAST, t.deadline ASC
        LIMIT $1
    """, k)

    # Cache for 15 minutes
    await redis.set(cache_key, json.dumps(tasks), ex=900)
    return tasks

async def get_daily_plan_cached(date: str) -> dict:
    cache_key = f"daily:{date}:plan"

    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached)

    # Cache miss → query DB
    plan = await db.fetch_daily_plan(date)
    if plan:
        # Cache until day is closed (no TTL, invalidated on close)
        await redis.set(cache_key, json.dumps(plan))
    return plan

async def invalidate_daily_cache(date: str):
    """Called when day is closed or plan is updated"""
    await redis.delete(f"daily:{date}:plan")
    await redis.delete(f"daily:{date}:brief")
```

### 6.3 Async Job Queue

```python
# Producer (Ingestion Gateway)
async def enqueue_analysis(artifact_id: str):
    job = {
        "job_id": str(uuid4()),
        "type": "risk_analysis",
        "artifact_id": artifact_id,
        "queued_at": utcnow().isoformat()
    }
    await redis.lpush("queue:analysis", json.dumps(job))
    await redis.publish("events:artifact.ingested", json.dumps({
        "artifact_id": artifact_id
    }))

# Consumer (Risk Engine worker)
async def process_analysis_queue():
    while True:
        _, job_data = await redis.brpop("queue:analysis")
        job = json.loads(job_data)
        # Acquire lock to prevent duplicate processing
        lock_key = f"lock:analysis:{job['artifact_id']}"
        if await redis.set(lock_key, "1", nx=True, ex=300):
            try:
                await run_risk_analysis(job["artifact_id"])
            finally:
                await redis.delete(lock_key)
```

### 6.4 Pub/Sub Event Flow

```
Ingestion Gateway ──publish──→ "events:artifact.ingested"
                                    │
                   ┌────────────────┼────────────────┐
                   ▼                ▼                ▼
            Risk Engine       Drift Detector   Daily Planner
            (subscriber)      (subscriber)     (subscriber:
                   │                │           updates today's
                   ▼                ▼           task list if new
            ──publish──→    ──publish──→        items extracted)
      "events:risk.updated"  "events:drift.alert"    │
                   │                │                 │
                   └────────┬───────┘                 │
                            ▼                         │
                     Dashboard UI  ◄──────────────────┘
                     (WebSocket subscriber)

Daily Planner ──publish──→ "events:daily.closed"
                                │
                                ▼
                         Dashboard UI (refresh next day view)
```

---

## 7. Data Export & Visualization (Pre-wired)

### 7.1 Export Endpoints

```
GET /api/v1/export/project/{id}/report     — Full project report (PDF/DOCX)
GET /api/v1/export/project/{id}/risks      — Risk register (CSV/XLSX)
GET /api/v1/export/project/{id}/tasks      — Task list (CSV/XLSX)
GET /api/v1/export/daily/{date}/report     — Daily brief report (PDF/Markdown)
GET /api/v1/export/daily/range             — Daily reports for date range (PDF)
GET /api/v1/export/audit                   — Audit log export (CSV)
```

### 7.2 Visualization Data Endpoints

```
GET /api/v1/viz/project/{id}/risk-matrix    — Risk scatter plot data (likelihood × impact)
GET /api/v1/viz/project/{id}/risk-trend     — Risk score over time (line chart)
GET /api/v1/viz/project/{id}/task-status    — Task status distribution (pie/bar)
GET /api/v1/viz/project/{id}/drift-timeline — Drift alerts over time
GET /api/v1/viz/daily/completion-trend      — Daily completion rate over time (line chart)
GET /api/v1/viz/daily/rollover-heatmap      — Chronic blocker visualization
GET /api/v1/viz/dashboard/summary           — Cross-project summary stats
```

Response format for all viz endpoints:

```python
{
    "chart_type": "scatter",            # scatter | line | bar | pie | heatmap
    "title": "Risk Matrix — NGIS Phase 3",
    "data": [ ... ],                    # chart-ready data points
    "axes": { "x": "Likelihood", "y": "Impact" },
    "generated_at": "2026-03-01T12:00:00Z"
}
```

---

## 8. File Structure

```
work-pulse/
├── api/
│   ├── main.py                         # FastAPI app entry
│   ├── config.py                       # Environment config
│   ├── routes/
│   │   ├── ingestion.py                # /api/v1/ingest/*
│   │   ├── priority.py                 # /api/v1/priority/*
│   │   ├── risk.py                     # /api/v1/risk/*
│   │   ├── drift.py                    # /api/v1/drift/*
│   │   ├── daily.py                    # /api/v1/daily/*
│   │   ├── export.py                   # /api/v1/export/*
│   │   └── viz.py                      # /api/v1/viz/*
│   ├── middleware/
│   │   ├── audit.py                    # Audit logging middleware
│   │   ├── auth.py                     # JWT + RBAC
│   │   └── bilingual.py               # Language detection + wrapping
│   ├── services/
│   │   ├── llm_client.py              # Model-agnostic LLM interface
│   │   ├── ingestion_service.py       # Format detection, extraction, normalization
│   │   ├── priority_service.py        # Priority ranking logic
│   │   ├── risk_service.py            # Risk identification + scoring + evidence
│   │   ├── drift_service.py           # Baseline management + drift detection
│   │   ├── daily_service.py           # Daily plan generation + rollover logic
│   │   └── export_service.py          # Report generation
│   ├── models/
│   │   ├── database.py                # SQLAlchemy / asyncpg models
│   │   ├── schemas.py                 # Pydantic request/response schemas
│   │   └── enums.py                   # Status, priority, risk level enums
│   └── utils/
│       ├── redis_client.py            # Redis connection + helpers
│       ├── file_store.py              # File storage abstraction
│       └── hash.py                    # Content hashing, audit chain hashing
├── workers/
│   ├── analysis_worker.py             # Async risk analysis consumer
│   ├── drift_worker.py                # Async drift check consumer
│   └── daily_rollover_worker.py       # Scheduled end-of-day close + rollover
├── migrations/                         # Alembic DB migrations
├── tests/
├── docker-compose.yml
├── Dockerfile
└── README.md
```
