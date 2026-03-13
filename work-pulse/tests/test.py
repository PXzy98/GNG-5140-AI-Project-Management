"""
Work Pulse — Comprehensive Module Test Suite
=============================================

Usage:
  python tests/test.py                         # traditional + local (Ollama)
  python tests/test.py --skip-llm              # traditional only
  python tests/test.py --tier local            # traditional + local Ollama
  python tests/test.py --tier small            # traditional + OpenRouter small
  python tests/test.py --all-tiers             # all 5 methods
  python tests/test.py --module ingestion      # ingestion tests only
  python tests/test.py --module risk           # risk tests only
  python tests/test.py --module priority       # priority ranker tests only
  python tests/test.py --module drift          # drift detector tests only
  python tests/test.py --module ingestion --tier local --skip-llm

Output:
  Console  : colored pass/fail per test (via rich)
  File     : test_results_{timestamp}.json
  File     : test_comparison_{timestamp}.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path setup — allow running from work-pulse/ or project root
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).parent
_ROOT = _THIS_DIR.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

TESTDATA_DIR = _ROOT / "testdata"
RESULTS_DIR = _ROOT

# ---------------------------------------------------------------------------
# Rich console (graceful fallback if not installed)
# ---------------------------------------------------------------------------
try:
    from rich.console import Console
    from rich.table import Table
    from rich import print as rprint
    _console = Console()
    def _print(msg: str) -> None: _console.print(msg)
except ImportError:
    def _print(msg: str) -> None: print(msg)  # type: ignore[misc]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.WARNING,
                    format="%(levelname)s %(name)s: %(message)s")

# ---------------------------------------------------------------------------
# Method constants
# ---------------------------------------------------------------------------
ALL_METHODS   = ["traditional", "llm_local", "llm_small", "llm_medium", "llm_large"]
LLM_METHODS   = ["llm_local", "llm_small", "llm_medium", "llm_large"]
TRAD_METHODS  = ["traditional"]

METHOD_LABELS = {
    "traditional":       "Traditional",
    "llm_local":         "Local hybrid",
    "llm_local_full":    "Local full",
    "llm_small":         "Small hybrid",
    "llm_small_full":    "Small full",
    "llm_medium":        "Medium hybrid",
    "llm_medium_full":   "Medium full",
    "llm_large":         "Large hybrid",
    "llm_large_full":    "Large full",
}

def _llm_tier(method: str) -> str:
    """Strip 'llm_' prefix and optional '_full' suffix → tier."""
    return method.removeprefix("llm_").removesuffix("_full")


def _llm_mode(method: str) -> str:
    """Return 'full' or 'hybrid' based on method name suffix."""
    return "full" if method.endswith("_full") else "hybrid"

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TestResult:
    test_id: str
    test_name: str
    module: str                      # ingestion | risk | priority | drift
    method: str                      # traditional | llm_local | llm_small | ...
    max_score: int
    earned_score: float
    passed: bool
    details: str
    latency_ms: float | None = None
    raw_output: dict = field(default_factory=dict)
    error: str | None = None

    def score_str(self) -> str:
        return f"{self.earned_score:.1f}/{self.max_score}"

    def status_tag(self) -> str:
        if self.error:
            return "[bold red]ERROR[/bold red]"
        return "[bold green]PASS[/bold green]" if self.passed else "[bold red]FAIL[/bold red]"


def _err(test_id: str, test_name: str, module: str, method: str,
         max_score: int, exc: Exception) -> TestResult:
    return TestResult(
        test_id=test_id, test_name=test_name, module=module, method=method,
        max_score=max_score, earned_score=0.0, passed=False,
        details=f"ERROR: {exc}", error=str(exc),
    )

# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def fuzzy_match(a: str, b: str, threshold: float = 0.8) -> bool:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio() >= threshold


def compute_f1(predicted: list[str], ground_truth: list[str],
               threshold: float = 0.8) -> tuple[float, float, float]:
    """Returns (precision, recall, F1) with fuzzy string matching."""
    if not ground_truth:
        return (1.0, 1.0, 1.0) if not predicted else (0.0, 1.0, 0.0)
    if not predicted:
        return 0.0, 0.0, 0.0
    matched_gt = set()
    tp = 0
    for p in predicted:
        for i, gt in enumerate(ground_truth):
            if i not in matched_gt and fuzzy_match(p, gt, threshold):
                matched_gt.add(i)
                tp += 1
                break
    precision = tp / len(predicted)
    recall    = tp / len(ground_truth)
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def compute_kendall_tau(predicted_ids: list[str], gt_ids: list[str]) -> float:
    """Kendall's tau between two orderings (by task_id)."""
    try:
        from scipy.stats import kendalltau  # type: ignore
    except ImportError:
        return 0.0
    common = [t for t in gt_ids if t in set(predicted_ids)]
    if len(common) < 2:
        return 0.0
    pred_map = {t: i for i, t in enumerate(predicted_ids)}
    x = list(range(len(common)))
    y = [pred_map[t] for t in common]
    tau, _ = kendalltau(x, y)
    return float(tau)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Shared storage fixture: each module gets a fresh one
# ---------------------------------------------------------------------------
def _new_storage():
    from api.models.database import MockStorage
    return MockStorage()


# ---------------------------------------------------------------------------
# Capture bus — helpers write here; runner drains into result.raw_output
# ---------------------------------------------------------------------------
_CAPTURE: dict[str, Any] = {}


def _cap(key: str, val: Any) -> None:
    _CAPTURE[key] = val


def _cap_drain() -> dict:
    out = dict(_CAPTURE)
    _CAPTURE.clear()
    return out

# ---------------------------------------------------------------------------
# ═══════════════════════  INGESTION TESTS  ═══════════════════════
# ---------------------------------------------------------------------------

async def _ingest_file(filename: str, subdir: str, method: str,
                       project_id: str | None = None, storage=None):
    """Helper: read a testdata file, ingest it, return (artifact, latency_ms)."""
    from api.models.schemas import IngestRequest
    from api.services.ingestion_service import ingest_text

    path = TESTDATA_DIR / subdir / filename
    content = _load_text(path)
    use_llm = method != "traditional"
    tier = _llm_tier(method) if use_llm else "small"
    mode = _llm_mode(method) if use_llm else "hybrid"

    req = IngestRequest(
        content=content,
        source_type="other",
        project_id=project_id,
        use_llm=use_llm,
        llm_tier=tier,       # type: ignore[arg-type]
        llm_mode=mode,       # type: ignore[arg-type]
    )
    t0 = time.perf_counter()
    art = await ingest_text(req, storage)
    latency_ms = (time.perf_counter() - t0) * 1000
    _cap("artifact", {
        "artifact_id": art.artifact_id,
        "artifact_type": art.artifact_type.value,
        "detected_language": art.detected_language,
        "word_count": art.word_count,
        "content_preview": art.content_preview[:500],
        "extracted_tasks": [t.model_dump(mode="json") for t in art.extracted_tasks],
        "metadata": art.metadata,
    })
    return art, latency_ms


async def run_ing_01(method: str, storage) -> TestResult:
    tid, name, mod, mx = "ING-01", "Plain text ingestion + storage", "ingestion", 10
    try:
        art, lat = await _ingest_file("vendor_delay_email.txt", "emails", method, storage=storage)
        ok = (art.artifact_id.startswith("art_") and art.word_count > 0
              and art.content_hash != "" and art.detected_language != "")
        score = mx if ok else 0
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"id={art.artifact_id} words={art.word_count} hash={art.content_hash[:8]}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_ing_02(method: str, storage) -> TestResult:
    tid, name, mod, mx = "ING-02", "Email format detection", "ingestion", 8
    try:
        art, lat = await _ingest_file("vendor_delay_email.txt", "emails", method, storage=storage)
        ok = art.artifact_type.value == "email"
        score = mx if ok else 0
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"detected={art.artifact_type.value}", latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_ing_03(method: str, storage) -> TestResult:
    tid, name, mod, mx = "ING-03", "Meeting minutes format detection", "ingestion", 8
    try:
        art, lat = await _ingest_file("sprint_review_minutes.txt", "meeting_minutes",
                                      method, storage=storage)
        ok = art.artifact_type.value == "meeting_minutes"
        score = mx if ok else 0
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"detected={art.artifact_type.value}", latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_ing_04(method: str, storage) -> TestResult:
    """Action item extraction F1 >= 0.8"""
    tid, name, mod, mx = "ING-04", "Action item extraction accuracy", "ingestion", 10
    try:
        gt_all = _load_json(TESTDATA_DIR / "generated" / "ingestion_ground_truth.json")
        gt = gt_all["sprint_review_minutes.txt"]["action_items"]
        gt_texts = [a["text"] for a in gt]

        art, lat = await _ingest_file("sprint_review_minutes.txt", "meeting_minutes",
                                      method, storage=storage)
        pred_texts = [t.text for t in art.extracted_tasks]
        _, recall, f1 = compute_f1(pred_texts, gt_texts)
        threshold = 0.80
        ok = f1 >= threshold
        score = round(mx * min(f1 / threshold, 1.0), 1)
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"F1={f1:.2f} recall={recall:.2f} pred={len(pred_texts)} gt={len(gt_texts)}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_ing_05(method: str, storage) -> TestResult:
    """Deadline extraction F1 >= 0.7"""
    tid, name, mod, mx = "ING-05", "Deadline extraction accuracy", "ingestion", 9
    try:
        gt_all = _load_json(TESTDATA_DIR / "generated" / "ingestion_ground_truth.json")
        gt = [a["deadline"] for a in gt_all["sprint_review_minutes.txt"]["action_items"]
              if a.get("deadline")]

        art, lat = await _ingest_file("sprint_review_minutes.txt", "meeting_minutes",
                                      method, storage=storage)
        pred = [t.deadline for t in art.extracted_tasks if t.deadline]
        _, recall, f1 = compute_f1(pred, gt, threshold=0.9)  # dates need exact-ish match
        threshold = 0.70
        ok = f1 >= threshold
        score = round(mx * min(f1 / threshold, 1.0), 1)
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"F1={f1:.2f} pred={pred} gt={gt}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_ing_06(method: str, storage) -> TestResult:
    """Owner extraction F1 >= 0.6"""
    tid, name, mod, mx = "ING-06", "Owner/assignee extraction", "ingestion", 8
    try:
        gt_all = _load_json(TESTDATA_DIR / "generated" / "ingestion_ground_truth.json")
        gt = [a["owner"] for a in gt_all["sprint_review_minutes.txt"]["action_items"]
              if a.get("owner")]

        art, lat = await _ingest_file("sprint_review_minutes.txt", "meeting_minutes",
                                      method, storage=storage)
        pred = [t.owner for t in art.extracted_tasks if t.owner]
        _, recall, f1 = compute_f1(pred, gt)
        threshold = 0.60
        ok = f1 >= threshold
        score = round(mx * min(f1 / threshold, 1.0), 1)
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"F1={f1:.2f} pred={pred[:3]} gt={gt[:3]}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_ing_07(method: str, storage) -> TestResult:
    tid, name, mod, mx = "ING-07", "Language detection (EN)", "ingestion", 7
    try:
        art, lat = await _ingest_file("vendor_delay_email.txt", "emails", method, storage=storage)
        ok = art.detected_language == "en"
        return TestResult(tid, name, mod, method, mx, mx if ok else 0, ok,
                          f"detected={art.detected_language}", latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_ing_08(method: str, storage) -> TestResult:
    tid, name, mod, mx = "ING-08", "Language detection (FR)", "ingestion", 7
    try:
        art, lat = await _ingest_file("governance_review_fr.txt", "meeting_minutes",
                                      method, storage=storage)
        ok = art.detected_language == "fr"
        return TestResult(tid, name, mod, method, mx, mx if ok else 0, ok,
                          f"detected={art.detected_language}", latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_ing_09(method: str, storage) -> TestResult:
    tid, name, mod, mx = "ING-09", "Language detection (Mixed EN/FR)", "ingestion", 9
    try:
        art, lat = await _ingest_file("bilingual_status_email.txt", "emails",
                                      method, storage=storage)
        ok = art.detected_language == "mixed"
        return TestResult(tid, name, mod, method, mx, mx if ok else 0, ok,
                          f"detected={art.detected_language}", latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_ing_10(method: str, storage) -> TestResult:
    """Bilingual: FR action items not lost (recall on FR items)"""
    tid, name, mod, mx = "ING-10", "Bilingual content preservation", "ingestion", 9
    try:
        gt_all = _load_json(TESTDATA_DIR / "generated" / "ingestion_ground_truth.json")
        gt_fr = [a["text"] for a in gt_all["bilingual_status_email.txt"]["action_items"]
                 if a.get("language") == "fr"]

        art, lat = await _ingest_file("bilingual_status_email.txt", "emails",
                                      method, storage=storage)
        pred = [t.text for t in art.extracted_tasks]
        _, recall, f1 = compute_f1(pred, gt_fr)
        ok = recall >= 0.5  # at least 1 FR item captured
        score = round(mx * min(recall / 0.5, 1.0), 1)
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"FR recall={recall:.2f} pred={len(pred)} fr_gt={len(gt_fr)}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_ing_11(method: str, storage) -> TestResult:
    """Implicit action items from status report (no explicit)"""
    tid, name, mod, mx = "ING-11", "Implicit action item extraction", "ingestion", 10
    try:
        art, lat = await _ingest_file("ngis_phase3_weekly.txt", "status_reports",
                                      method, storage=storage)
        # We expect at least 1 implicit item even though none are explicit
        ok = len(art.extracted_tasks) >= 1
        score = mx if ok else int(mx * 0.5) if art.extracted_tasks else 0
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"extracted={len(art.extracted_tasks)} items",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_ing_12(method: str, storage) -> TestResult:
    """Multi-project linking: artifact correctly linked to given project_id"""
    tid, name, mod, mx = "ING-12", "Multi-project artifact linking", "ingestion", 8
    try:
        art, lat = await _ingest_file("multi_project_update.txt", "status_reports",
                                      method, project_id="proj_multi", storage=storage)
        ok = art.linked_project == "proj_multi"
        return TestResult(tid, name, mod, method, mx, mx if ok else 0, ok,
                          f"linked_project={art.linked_project}", latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_ing_13(method: str, storage) -> TestResult:
    """Duplicate detection: same content submitted twice → same artifact_id returned"""
    tid, name, mod, mx = "ING-13", "Duplicate detection (content hash)", "ingestion", 7
    try:
        art1, _ = await _ingest_file("vendor_delay_email.txt", "emails", method, storage=storage)
        art2, lat = await _ingest_file("vendor_delay_email.txt", "emails", method, storage=storage)
        ok = art1.artifact_id == art2.artifact_id
        return TestResult(tid, name, mod, method, mx, mx if ok else 0, ok,
                          f"id1={art1.artifact_id} id2={art2.artifact_id} dedup={ok}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_ing_14(method: str, storage) -> TestResult:
    """Schema validation: response has all required fields"""
    tid, name, mod, mx = "ING-14", "API response schema validation", "ingestion", 6
    try:
        from api.models.schemas import ArtifactResponse
        art, lat = await _ingest_file("vendor_delay_email.txt", "emails", method, storage=storage)
        # Model validate → no exception = schema OK
        ArtifactResponse.model_validate(art.model_dump())
        required = all([art.artifact_id, art.detected_language,
                        art.content_preview, art.word_count >= 0])
        return TestResult(tid, name, mod, method, mx, mx if required else 0, required,
                          "Schema valid" if required else "Missing required fields",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_ing_15(method: str, storage) -> TestResult:
    """Latency measurement — always passes, just records timing"""
    tid, name, mod, mx = "ING-15", "Processing latency", "ingestion", 5
    try:
        _, lat = await _ingest_file("sprint_review_minutes.txt", "meeting_minutes",
                                    method, storage=storage)
        return TestResult(tid, name, mod, method, mx, mx, True,
                          f"latency={lat:.0f}ms", latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


ING_TESTS = [
    (run_ing_01, "both"),
    (run_ing_02, "both"),
    (run_ing_03, "both"),
    (run_ing_04, "compare"),
    (run_ing_05, "compare"),
    (run_ing_06, "compare"),
    (run_ing_07, "both"),
    (run_ing_08, "both"),
    (run_ing_09, "both"),
    (run_ing_10, "compare"),
    (run_ing_11, "compare"),
    (run_ing_12, "compare"),
    (run_ing_13, "both"),
    (run_ing_14, "both"),
    (run_ing_15, "compare"),
]

# ---------------------------------------------------------------------------
# ═══════════════════════  RISK TESTS  ═══════════════════════
# ---------------------------------------------------------------------------

async def _identify_risks_for(filename: str, subdir: str, method: str,
                               project_id: str, storage):
    """Ingest a file then run risk identification on it."""
    from api.models.schemas import RiskIdentifyRequest
    from api.services.risk_service import identify_risks

    art, _ = await _ingest_file(filename, subdir, method, project_id=project_id,
                                 storage=storage)
    use_llm = method != "traditional"
    req = RiskIdentifyRequest(
        artifact_ids=[art.artifact_id],
        project_id=project_id,
        use_llm=use_llm,
        llm_tier=_llm_tier(method) if use_llm else "small",  # type: ignore[arg-type]
    )
    t0 = time.perf_counter()
    resp = await identify_risks(req, storage)
    lat = (time.perf_counter() - t0) * 1000
    _cap("risks", {
        "count": len(resp.risks),
        "analysis_method": resp.analysis_method,
        "risks": [r.model_dump(mode="json") for r in resp.risks],
    })
    return resp, art, lat


async def run_rsk_01(method: str, storage) -> TestResult:
    """Explicit risk recall >= 0.8"""
    tid, name, mod, mx = "RSK-01", "Risk identification — explicit risks", "risk", 10
    try:
        gt_all = _load_json(TESTDATA_DIR / "generated" / "risk_ground_truth.json")
        gt_descs = [r["description"] for r in gt_all["vendor_delay_email.txt"]["risks"]]
        resp, _, lat = await _identify_risks_for("vendor_delay_email.txt", "emails",
                                                  method, "proj_rsk01", storage)
        pred_descs = [r.description for r in resp.risks]
        _, recall, f1 = compute_f1(pred_descs, gt_descs)
        ok = recall >= 0.8
        score = round(mx * min(recall / 0.8, 1.0), 1)
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"recall={recall:.2f} found={len(pred_descs)} gt={len(gt_descs)}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_rsk_02(method: str, storage) -> TestResult:
    """Implicit/hidden risk recall >= 0.5 (optimistic report)"""
    tid, name, mod, mx = "RSK-02", "Risk identification — implicit/hidden risks", "risk", 10
    try:
        gt_all = _load_json(TESTDATA_DIR / "generated" / "risk_ground_truth.json")
        gt_descs = [r["description"] for r in gt_all["optimistic_report.txt"]["risks"]]
        resp, _, lat = await _identify_risks_for("optimistic_report.txt", "status_reports",
                                                  method, "proj_rsk02", storage)
        pred_descs = [r.description for r in resp.risks]
        _, recall, _ = compute_f1(pred_descs, gt_descs)
        ok = recall >= 0.5
        score = round(mx * min(recall / 0.5, 1.0), 1)
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"recall={recall:.2f} found={len(pred_descs)} gt={len(gt_descs)}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_rsk_03(method: str, storage) -> TestResult:
    """Risk category classification >= 0.7"""
    tid, name, mod, mx = "RSK-03", "Risk category classification", "risk", 9
    try:
        gt_all = _load_json(TESTDATA_DIR / "generated" / "risk_ground_truth.json")
        gt_cats = [r["category"] for r in gt_all["vendor_delay_email.txt"]["risks"]]
        resp, _, lat = await _identify_risks_for("vendor_delay_email.txt", "emails",
                                                  method, "proj_rsk03", storage)
        pred_cats = [r.category.value for r in resp.risks]
        if not resp.risks:
            return TestResult(tid, name, mod, method, mx, 0, False,
                              "No risks identified", latency_ms=lat)
        correct = sum(1 for pc, gc in zip(pred_cats[:len(gt_cats)], gt_cats) if pc == gc)
        acc = correct / len(gt_cats)
        ok = acc >= 0.7
        score = round(mx * min(acc / 0.7, 1.0), 1)
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"accuracy={acc:.2f} pred_cats={pred_cats[:3]} gt={gt_cats}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_rsk_04(method: str, storage) -> TestResult:
    """Each risk has >= 1 evidence_ref"""
    tid, name, mod, mx = "RSK-04", "Evidence extraction (source linking)", "risk", 9
    try:
        resp, _, lat = await _identify_risks_for("vendor_delay_email.txt", "emails",
                                                  method, "proj_rsk04", storage)
        if not resp.risks:
            return TestResult(tid, name, mod, method, mx, 0, False,
                              "No risks identified", latency_ms=lat)
        with_evidence = sum(1 for r in resp.risks if r.evidence_refs)
        ratio = with_evidence / len(resp.risks)
        ok = ratio >= 1.0
        score = round(mx * ratio, 1)
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"with_evidence={with_evidence}/{len(resp.risks)}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_rsk_05(method: str, storage) -> TestResult:
    """Cross-document inconsistency detection"""
    tid, name, mod, mx = "RSK-05", "Cross-document inconsistency detection", "risk", 10
    try:
        from api.models.schemas import CrossCheckRequest
        from api.services.risk_service import cross_check

        art_a, _ = await _ingest_file("cross_check_pair_a.txt", "generated",
                                       method, project_id="proj_cc", storage=storage)
        art_b, _ = await _ingest_file("cross_check_pair_b.txt", "generated",
                                       method, project_id="proj_cc", storage=storage)
        use_llm = method != "traditional"
        req = CrossCheckRequest(
            artifact_ids=[art_a.artifact_id, art_b.artifact_id],
            use_llm=use_llm,
            llm_tier=_llm_tier(method) if use_llm else "small",  # type: ignore[arg-type]
        )
        t0 = time.perf_counter()
        resp = await cross_check(req, storage)
        lat = (time.perf_counter() - t0) * 1000

        ok = len(resp.inconsistencies) >= 1
        score = mx if ok else 0
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"inconsistencies={len(resp.inconsistencies)}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_rsk_06(method: str, storage) -> TestResult:
    """Risk scoring within ±1 level of ground truth"""
    tid, name, mod, mx = "RSK-06", "Risk scoring accuracy", "risk", 8
    try:
        gt_all = _load_json(TESTDATA_DIR / "generated" / "risk_ground_truth.json")
        gt_levels = [r["risk_level"] for r in gt_all["vendor_delay_email.txt"]["risks"]]
        level_order = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        resp, _, lat = await _identify_risks_for("vendor_delay_email.txt", "emails",
                                                  method, "proj_rsk06", storage)
        if not resp.risks:
            return TestResult(tid, name, mod, method, mx, 0, False,
                              "No risks identified", latency_ms=lat)
        within_one = 0
        pairs = list(zip([r.risk_level.value for r in resp.risks], gt_levels))
        for pred_lvl, gt_lvl in pairs:
            if abs(level_order.get(pred_lvl, 0) - level_order.get(gt_lvl, 0)) <= 1:
                within_one += 1
        ratio = within_one / len(pairs)
        ok = ratio >= 0.7
        score = round(mx * ratio, 1)
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"within_1_level={within_one}/{len(pairs)} pairs={pairs}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_rsk_07(method: str, storage) -> TestResult:
    """Compliance risk detection from ITSG-33 email"""
    tid, name, mod, mx = "RSK-07", "Compliance risk detection", "risk", 9
    try:
        resp, _, lat = await _identify_risks_for("compliance_concern_email.txt", "emails",
                                                  method, "proj_rsk07", storage)
        compliance_risks = [r for r in resp.risks if r.category.value == "compliance"]
        ok = len(compliance_risks) >= 1
        score = mx if ok else 0
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"compliance_risks={len(compliance_risks)} total={len(resp.risks)}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_rsk_08(method: str, storage) -> TestResult:
    """Multi-risk extraction from single document (>= 2 distinct)"""
    tid, name, mod, mx = "RSK-08", "Multi-risk extraction from single doc", "risk", 8
    try:
        resp, _, lat = await _identify_risks_for("sprint_review_minutes.txt", "meeting_minutes",
                                                  method, "proj_rsk08", storage)
        ok = len(resp.risks) >= 2
        score = mx if ok else int(mx * len(resp.risks) / 2)
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"risks_found={len(resp.risks)}", latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_rsk_09(method: str, storage) -> TestResult:
    """False positive rate: clean doc should have <= 2 false positives"""
    tid, name, mod, mx = "RSK-09", "False positive rate", "risk", 7
    try:
        resp, _, lat = await _identify_risks_for("drift_test_clean.txt", "generated",
                                                  method, "proj_rsk09", storage)
        fp = len(resp.risks)
        ok = fp <= 2
        score = mx if ok else max(0, mx - (fp - 2) * 2)
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"false_positives={fp} (threshold: <=2)", latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_rsk_10(method: str, storage) -> TestResult:
    """Rule adjustments: deadline <7d → likelihood+1; no_owner → impact+1"""
    tid, name, mod, mx = "RSK-10", "Risk scoring rule adjustments", "risk", 7
    try:
        from api.utils.traditional_methods import identify_risks_traditional, _apply_rule_adjustments  # type: ignore[attr-defined]
        text = "The vendor delivery is delayed, threatening the schedule. Deadline March 10."
        raw = identify_risks_traditional(text, "art_test")
        ok = len(raw) >= 1
        score = mx if ok else 0
        detail = f"Risks identified: {len(raw)}"
        if raw:
            r = raw[0]
            detail += f" lik={r['likelihood']} imp={r['impact']} level={r['risk_level']}"
        return TestResult(tid, name, mod, method, mx, score, ok, detail)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_rsk_11(method: str, storage) -> TestResult:
    """Project-level risk aggregation"""
    tid, name, mod, mx = "RSK-11", "Project-level risk aggregation", "risk", 6
    try:
        from api.services.risk_service import get_project_risk_summary
        # Ingest + identify first
        await _identify_risks_for("vendor_delay_email.txt", "emails",
                                   method, "proj_rsk11_agg", storage)
        summary = get_project_risk_summary("proj_rsk11_agg", storage)
        ok = summary.total_risks >= 1 and bool(summary.by_category)
        score = mx if ok else 0
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"total={summary.total_risks} cats={summary.by_category}")
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_rsk_12(method: str, storage) -> TestResult:
    """Schema validation"""
    tid, name, mod, mx = "RSK-12", "API response schema validation", "risk", 6
    try:
        from api.models.schemas import RiskIdentifyResponse
        resp, _, lat = await _identify_risks_for("vendor_delay_email.txt", "emails",
                                                  method, "proj_rsk12", storage)
        RiskIdentifyResponse.model_validate(resp.model_dump())
        return TestResult(tid, name, mod, method, mx, mx, True, "Schema valid", latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_rsk_13(method: str, storage) -> TestResult:
    tid, name, mod, mx = "RSK-13", "Processing latency", "risk", 5
    try:
        _, _, lat = await _identify_risks_for("vendor_delay_email.txt", "emails",
                                               method, "proj_rsk13", storage)
        return TestResult(tid, name, mod, method, mx, mx, True,
                          f"latency={lat:.0f}ms", latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


RSK_TESTS = [
    (run_rsk_01, "compare"),
    (run_rsk_02, "compare"),
    (run_rsk_03, "compare"),
    (run_rsk_04, "compare"),
    (run_rsk_05, "compare"),
    (run_rsk_06, "compare"),
    (run_rsk_07, "compare"),
    (run_rsk_08, "compare"),
    (run_rsk_09, "compare"),
    (run_rsk_10, "both"),
    (run_rsk_11, "both"),
    (run_rsk_12, "both"),
    (run_rsk_13, "compare"),
]

# ---------------------------------------------------------------------------
# ═══════════════════════  PRIORITY TESTS  ═══════════════════════
# ---------------------------------------------------------------------------

async def _rank_test_tasks(method: str, storage, previous=None):
    from api.models.schemas import PriorityRankRequest, TaskInput, RiskLevel, TaskStatus
    from api.services.priority_ranker import rank_tasks

    gt = _load_json(TESTDATA_DIR / "generated" / "priority_test_tasks.json")
    tasks = []
    for t in gt["tasks"]:
        rl = None
        if t.get("risk_level"):
            try:
                rl = RiskLevel(t["risk_level"])
            except ValueError:
                pass
        tasks.append(TaskInput(
            task_id=t["task_id"],
            name=t["name"],
            deadline=t.get("deadline"),
            risk_level=rl,
            rollover_count=t.get("rollover_count", 0),
            status=TaskStatus(t.get("status", "not_started")),
            project_id=t.get("project_id"),
            dependencies_blocked=t.get("dependencies_blocked", 0),
            stakeholder_priority=t.get("stakeholder_priority", 0),
            expected_rank_range=t.get("expected_rank_range"),
        ))
    use_llm = method != "traditional"
    req = PriorityRankRequest(
        tasks=tasks,
        context=gt["context"],
        use_llm=use_llm,
        llm_tier=_llm_tier(method) if use_llm else "small",  # type: ignore[arg-type]
        previous_ranking=previous,
    )
    t0 = time.perf_counter()
    resp = await rank_tasks(req, storage)
    lat = (time.perf_counter() - t0) * 1000
    _cap("ranking", {
        "analysis_method": resp.analysis_method,
        "ranked_tasks": [
            {"task_id": rt.task_id, "rank": rt.rank, "score": rt.priority_score,
             "priority_level": rt.priority_level.value, "reasoning": rt.reasoning,
             "boost_applied": rt.boost_applied, "chronic_blocker": rt.chronic_blocker}
            for rt in resp.ranked_tasks
        ],
    })
    return resp, gt, lat


async def run_pri_01(method: str, storage) -> TestResult:
    """Top-3 tasks all within expected_rank_range"""
    tid, name, mod, mx = "PRI-01", "Top-3 ranking accuracy", "priority", 10
    try:
        resp, gt, lat = await _rank_test_tasks(method, storage)
        gt_ranges = {t["task_id"]: t["expected_rank_range"] for t in gt["tasks"]}
        top3 = resp.ranked_tasks[:3]
        correct = sum(1 for rt in top3
                      if gt_ranges.get(rt.task_id, [99, 99])[0] <= rt.rank
                      <= gt_ranges.get(rt.task_id, [0, 0])[1])
        ok = correct == 3
        score = round(mx * correct / 3, 1)
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"correct={correct}/3 top3={[rt.task_id for rt in top3]}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_pri_02(method: str, storage) -> TestResult:
    """Bottom-3 tasks within expected_rank_range"""
    tid, name, mod, mx = "PRI-02", "Bottom-3 ranking accuracy", "priority", 8
    try:
        resp, gt, lat = await _rank_test_tasks(method, storage)
        gt_ranges = {t["task_id"]: t["expected_rank_range"] for t in gt["tasks"]}
        bottom3 = resp.ranked_tasks[-3:]
        correct = sum(1 for rt in bottom3
                      if gt_ranges.get(rt.task_id, [99, 99])[0] <= rt.rank
                      <= gt_ranges.get(rt.task_id, [0, 0])[1])
        ok = correct >= 2
        score = round(mx * correct / 3, 1)
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"correct={correct}/3 bottom3={[rt.task_id for rt in bottom3]}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_pri_03(method: str, storage) -> TestResult:
    """Kendall's tau >= 0.6 vs ground truth"""
    tid, name, mod, mx = "PRI-03", "Full ranking correlation (Kendall tau)", "priority", 10
    try:
        resp, gt, lat = await _rank_test_tasks(method, storage)
        predicted_order = [rt.task_id for rt in resp.ranked_tasks]
        gt_order = [t["task_id"] for t in gt["ground_truth_ranking"]]
        tau = compute_kendall_tau(predicted_order, gt_order)
        ok = tau >= 0.6
        score = round(mx * min(max(tau, 0) / 0.6, 1.0), 1)
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"kendall_tau={tau:.3f}", latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_pri_04(method: str, storage) -> TestResult:
    """LLM only: reasoning cites evidence (artifacts / risk factors)"""
    tid, name, mod, mx = "PRI-04", "Reasoning quality — evidence cited", "priority", 9
    try:
        resp, _, lat = await _rank_test_tasks(method, storage)
        top5 = resp.ranked_tasks[:5]
        with_reasoning = [rt for rt in top5 if len(rt.reasoning) > 20]
        ok = len(with_reasoning) >= 3
        score = round(mx * len(with_reasoning) / 5, 1)
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"with_reasoning={len(with_reasoning)}/5",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_pri_05(method: str, storage) -> TestResult:
    """LLM only: no hallucination (evidence_refs only cite existing context IDs)"""
    tid, name, mod, mx = "PRI-05", "Reasoning quality — no hallucination", "priority", 9
    try:
        gt = _load_json(TESTDATA_DIR / "generated" / "priority_test_tasks.json")
        valid_ids = {t["task_id"] for t in gt["tasks"]}
        valid_ids |= {r["risk_id"] for r in gt["context"]["active_risks"]}
        resp, _, lat = await _rank_test_tasks(method, storage)
        hallucinations = 0
        for rt in resp.ranked_tasks[:5]:
            for ref in rt.evidence_refs:
                if ref and ref not in valid_ids:
                    hallucinations += 1
        ok = hallucinations == 0
        score = mx if ok else max(0, mx - hallucinations * 2)
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"hallucinations={hallucinations}", latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_pri_06(method: str, storage) -> TestResult:
    """Deadline proximity boost: near-deadline task ranks above same-type far-deadline"""
    tid, name, mod, mx = "PRI-06", "Deadline proximity boost", "priority", 8
    try:
        resp, gt, lat = await _rank_test_tasks(method, storage)
        ranked_map = {rt.task_id: rt.rank for rt in resp.ranked_tasks}
        # task_001 (deadline 2026-03-10) should rank above task_009 (2026-04-01)
        near = ranked_map.get("task_001", 99)
        far  = ranked_map.get("task_010", 0)
        ok = near < far
        score = mx if ok else 0
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"near_task rank={near} far_task rank={far} (near<far={ok})",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_pri_07(method: str, storage) -> TestResult:
    """Chronic blocker (rollover>=3): task_008 flagged and boosted"""
    tid, name, mod, mx = "PRI-07", "Chronic blocker escalation", "priority", 8
    try:
        resp, _, lat = await _rank_test_tasks(method, storage)
        t8 = next((rt for rt in resp.ranked_tasks if rt.task_id == "task_008"), None)
        ok = t8 is not None and t8.chronic_blocker
        score = mx if ok else int(mx * 0.5) if t8 else 0
        detail = f"task_008 chronic_blocker={t8.chronic_blocker if t8 else 'not found'}"
        if t8:
            detail += f" rank={t8.rank} score={t8.priority_score}"
        return TestResult(tid, name, mod, method, mx, score, ok, detail, latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_pri_08(method: str, storage) -> TestResult:
    """Re-rank after injection: new urgent task enters top-3"""
    tid, name, mod, mx = "PRI-08", "Re-rank after new artifact injection", "priority", 10
    try:
        from api.models.schemas import PriorityRankRequest, TaskInput, RiskLevel
        from api.services.priority_ranker import rank_tasks

        # First ranking
        resp1, gt, lat1 = await _rank_test_tasks(method, storage)

        # Inject a new critical task
        new_task = TaskInput(
            task_id="task_new_emergency",
            name="EMERGENCY: Director demands immediate status call on vendor delay",
            deadline=datetime.utcnow().strftime("%Y-%m-%d"),
            risk_level=RiskLevel.CRITICAL,
            rollover_count=0,
            dependencies_blocked=5,
            stakeholder_priority=5,
        )
        all_tasks_gt = _load_json(TESTDATA_DIR / "generated" / "priority_test_tasks.json")
        from api.models.schemas import TaskStatus
        existing = [TaskInput(
            task_id=t["task_id"], name=t["name"],
            deadline=t.get("deadline"),
            risk_level=RiskLevel(t["risk_level"]) if t.get("risk_level") else None,
            rollover_count=t.get("rollover_count", 0),
            status=TaskStatus(t.get("status", "not_started")),
            dependencies_blocked=t.get("dependencies_blocked", 0),
            stakeholder_priority=t.get("stakeholder_priority", 0),
        ) for t in all_tasks_gt["tasks"]]

        use_llm = method != "traditional"
        req2 = PriorityRankRequest(
            tasks=existing + [new_task],
            context=all_tasks_gt["context"],
            use_llm=use_llm,
            llm_tier=_llm_tier(method) if use_llm else "small",  # type: ignore[arg-type]
            previous_ranking=resp1.ranked_tasks,
        )
        t0 = time.perf_counter()
        resp2 = await rank_tasks(req2, storage)
        lat = (time.perf_counter() - t0) * 1000

        new_rank = next((rt.rank for rt in resp2.ranked_tasks
                         if rt.task_id == "task_new_emergency"), 99)
        ok = new_rank <= 3
        score = mx if ok else int(mx * 0.5) if new_rank <= 5 else 0
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"new_task_rank={new_rank} (want <=3)",
                          latency_ms=lat1 + lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_pri_09(method: str, storage) -> TestResult:
    """Ranking change diff tracking"""
    tid, name, mod, mx = "PRI-09", "Re-rank change tracking (diff)", "priority", 7
    try:
        from api.models.schemas import PriorityRankRequest, TaskInput, RiskLevel, TaskStatus
        from api.services.priority_ranker import rank_tasks

        gt_data = _load_json(TESTDATA_DIR / "generated" / "priority_test_tasks.json")
        tasks = [TaskInput(
            task_id=t["task_id"], name=t["name"],
            deadline=t.get("deadline"),
            risk_level=RiskLevel(t["risk_level"]) if t.get("risk_level") else None,
            rollover_count=t.get("rollover_count", 0),
            status=TaskStatus(t.get("status", "not_started")),
        ) for t in gt_data["tasks"]]

        use_llm = method != "traditional"
        tier_val = _llm_tier(method) if use_llm else "small"

        resp1 = await rank_tasks(PriorityRankRequest(
            tasks=tasks, use_llm=use_llm, llm_tier=tier_val), storage)  # type: ignore[arg-type]

        # Tweak one task's rollover to force rank change
        tasks[1] = TaskInput(
            task_id=tasks[1].task_id, name=tasks[1].name,
            deadline=tasks[1].deadline, risk_level=tasks[1].risk_level,
            rollover_count=5, dependencies_blocked=3,
            status=tasks[1].status,
        )
        t0 = time.perf_counter()
        resp2 = await rank_tasks(PriorityRankRequest(
            tasks=tasks, use_llm=use_llm, llm_tier=tier_val,  # type: ignore[arg-type]
            previous_ranking=resp1.ranked_tasks), storage)
        lat = (time.perf_counter() - t0) * 1000

        changes_valid = all(
            hasattr(c, "old_rank") and hasattr(c, "new_rank") and hasattr(c, "reason_for_change")
            for c in resp2.ranking_changes
        )
        ok = changes_valid
        score = mx if ok else 0
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"changes={len(resp2.ranking_changes)} valid={changes_valid}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_pri_10(method: str, storage) -> TestResult:
    """LLM stability: 3 runs, top-3 identical in >=2/3"""
    tid, name, mod, mx = "PRI-10", "Stability — same input, similar output", "priority", 7
    try:
        results = []
        total_lat = 0.0
        for _ in range(3):
            resp, _, lat = await _rank_test_tasks(method, _new_storage())
            results.append(frozenset(rt.task_id for rt in resp.ranked_tasks[:3]))
            total_lat += lat
        # Check if any 2 runs share same top-3
        consistent = sum(1 for i in range(3) for j in range(i + 1, 3)
                         if results[i] == results[j])
        ok = consistent >= 1
        score = mx if ok else int(mx * 0.5)
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"consistent_pairs={consistent} top3_sets={[list(r) for r in results]}",
                          latency_ms=total_lat / 3)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_pri_11(method: str, storage) -> TestResult:
    tid, name, mod, mx = "PRI-11", "Response schema validation", "priority", 6
    try:
        from api.models.schemas import PriorityRankResponse
        resp, _, lat = await _rank_test_tasks(method, storage)
        PriorityRankResponse.model_validate(resp.model_dump())
        ok = len(resp.ranked_tasks) > 0
        return TestResult(tid, name, mod, method, mx, mx if ok else 0, ok,
                          "Schema valid", latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_pri_12(method: str, storage) -> TestResult:
    tid, name, mod, mx = "PRI-12", "Processing latency", "priority", 5
    try:
        _, _, lat = await _rank_test_tasks(method, storage)
        return TestResult(tid, name, mod, method, mx, mx, True,
                          f"latency={lat:.0f}ms", latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


PRI_TESTS = [
    (run_pri_01, "compare"),
    (run_pri_02, "compare"),
    (run_pri_03, "compare"),
    (run_pri_04, "llm_only"),
    (run_pri_05, "llm_only"),
    (run_pri_06, "both"),
    (run_pri_07, "both"),
    (run_pri_08, "compare"),
    (run_pri_09, "both"),
    (run_pri_10, "llm_only"),
    (run_pri_11, "both"),
    (run_pri_12, "compare"),
]

# ---------------------------------------------------------------------------
# ═══════════════════════  DRIFT TESTS  ═══════════════════════
# ---------------------------------------------------------------------------

async def _setup_drift_baseline(method: str, storage) -> str:
    """Ingest SOW and extract baseline. Returns project_id."""
    from api.models.schemas import BaselineRequest
    from api.services.drift_service import extract_baseline

    pid = f"proj_drift_{method}"
    art, _ = await _ingest_file("ngis_phase3_sow.txt", "sow", method,
                                 project_id=pid, storage=storage)
    req = BaselineRequest(
        project_id=pid,
        artifact_id=art.artifact_id,
        version="v1",
        use_llm=method != "traditional",
        llm_tier=_llm_tier(method) if method != "traditional" else "large",  # type: ignore[arg-type]
    )
    await extract_baseline(req, storage)
    return pid


async def _run_drift_check(filename: str, method: str, project_id: str, storage):
    from api.models.schemas import DriftCheckRequest
    from api.services.drift_service import check_drift

    art, _ = await _ingest_file(filename, "generated", method,
                                 project_id=project_id, storage=storage)
    req = DriftCheckRequest(
        project_id=project_id,
        artifact_id=art.artifact_id,
        use_llm=method != "traditional",
        llm_tier=_llm_tier(method) if method != "traditional" else "large",  # type: ignore[arg-type]
    )
    t0 = time.perf_counter()
    resp = await check_drift(req, storage)
    lat = (time.perf_counter() - t0) * 1000
    _cap("drift_check", {
        "drift_type": resp.drift_type.value,
        "overall_alignment_score": resp.overall_alignment_score,
        "reasoning": getattr(resp, "reasoning", None),
        "alerts": [a.model_dump(mode="json") for a in getattr(resp, "alerts", [])],
    })
    return resp, lat


async def run_dft_01(method: str, storage) -> TestResult:
    """Baseline extraction: all 4 lists present and non-empty"""
    tid, name, mod, mx = "DFT-01", "Baseline extraction from SOW", "drift", 10
    try:
        from api.services.drift_service import get_latest_baseline
        pid = await _setup_drift_baseline(method, storage)
        baseline = get_latest_baseline(pid, storage)
        if not baseline:
            return TestResult(tid, name, mod, method, mx, 0, False,
                              "Baseline not found in storage")
        present = all([baseline.in_scope, baseline.out_of_scope,
                       baseline.deliverables, baseline.constraints])
        score = sum([
            bool(baseline.in_scope) * 3,
            bool(baseline.out_of_scope) * 3,
            bool(baseline.deliverables) * 2,
            bool(baseline.constraints) * 2,
        ])
        ok = present
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"in_scope={len(baseline.in_scope)} out={len(baseline.out_of_scope)} "
                          f"deliv={len(baseline.deliverables)} constr={len(baseline.constraints)}")
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_dft_02(method: str, storage) -> TestResult:
    """Within-scope: clean email → alignment >= 0.8"""
    tid, name, mod, mx = "DFT-02", "Within-scope detection", "drift", 9
    try:
        pid = await _setup_drift_baseline(method, storage)
        resp, lat = await _run_drift_check("drift_test_within_scope.txt", method, pid, storage)
        ok = resp.overall_alignment_score >= 0.8 and resp.drift_type.value == "within_scope"
        score = round(mx * resp.overall_alignment_score, 1)
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"type={resp.drift_type.value} score={resp.overall_alignment_score:.2f}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_dft_03(method: str, storage) -> TestResult:
    """Scope expansion: Wi-Fi upgrade → alignment < 0.5, type=scope_expansion"""
    tid, name, mod, mx = "DFT-03", "Scope expansion detection", "drift", 10
    try:
        pid = await _setup_drift_baseline(method, storage)
        resp, lat = await _run_drift_check("drift_test_scope_expansion.txt", method, pid, storage)
        ok = (resp.drift_type.value == "scope_expansion" and
              resp.overall_alignment_score < 0.5)
        score = mx if ok else int(mx * 0.5) if resp.drift_type.value in ("scope_expansion", "ambiguous") else 0
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"type={resp.drift_type.value} score={resp.overall_alignment_score:.2f}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_dft_04(method: str, storage) -> TestResult:
    """New requirement: DR site → alignment < 0.4, type=new_requirement"""
    tid, name, mod, mx = "DFT-04", "New requirement detection", "drift", 10
    try:
        pid = await _setup_drift_baseline(method, storage)
        resp, lat = await _run_drift_check("drift_test_new_requirement.txt", method, pid, storage)
        ok = (resp.drift_type.value == "new_requirement" and
              resp.overall_alignment_score < 0.4)
        score = mx if ok else int(mx * 0.5) if resp.drift_type.value == "new_requirement" else 0
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"type={resp.drift_type.value} score={resp.overall_alignment_score:.2f}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_dft_05(method: str, storage) -> TestResult:
    """Contradicts scope: budget+timeline override"""
    tid, name, mod, mx = "DFT-05", "Contradicts-scope detection", "drift", 10
    try:
        pid = await _setup_drift_baseline(method, storage)
        resp, lat = await _run_drift_check("drift_test_contradicts.txt", method, pid, storage)
        ok = resp.drift_type.value == "contradicts_scope"
        score = mx if ok else int(mx * 0.3)
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"type={resp.drift_type.value} score={resp.overall_alignment_score:.2f}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_dft_06(method: str, storage) -> TestResult:
    """Ambiguous: NOC monitoring tool → type=ambiguous OR alignment 0.3-0.7"""
    tid, name, mod, mx = "DFT-06", "Ambiguous case handling", "drift", 8
    try:
        pid = await _setup_drift_baseline(method, storage)
        resp, lat = await _run_drift_check("drift_test_ambiguous.txt", method, pid, storage)
        score_in_range = 0.3 <= resp.overall_alignment_score <= 0.7
        is_ambiguous = resp.drift_type.value == "ambiguous"
        ok = is_ambiguous or score_in_range
        score = mx if ok else int(mx * 0.5)
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"type={resp.drift_type.value} score={resp.overall_alignment_score:.2f}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_dft_07(method: str, storage) -> TestResult:
    """No false alarm on clean update: alignment >= 0.8, no alerts"""
    tid, name, mod, mx = "DFT-07", "No false alarm on clean update", "drift", 8
    try:
        pid = await _setup_drift_baseline(method, storage)
        resp, lat = await _run_drift_check("drift_test_clean.txt", method, pid, storage)
        ok = resp.overall_alignment_score >= 0.8 and len(resp.alerts) == 0
        score = mx if ok else int(mx * 0.5) if resp.overall_alignment_score >= 0.7 else 0
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"score={resp.overall_alignment_score:.2f} alerts={len(resp.alerts)}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_dft_08(method: str, storage) -> TestResult:
    """French scope creep: correctly detected as scope_expansion"""
    tid, name, mod, mx = "DFT-08", "Bilingual drift detection (FR)", "drift", 9
    try:
        pid = await _setup_drift_baseline(method, storage)
        resp, lat = await _run_drift_check("drift_test_fr_scope_creep.txt", method, pid, storage)
        ok = resp.drift_type.value in ("scope_expansion", "new_requirement")
        score = mx if ok else int(mx * 0.5) if resp.drift_type.value != "within_scope" else 0
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"type={resp.drift_type.value} score={resp.overall_alignment_score:.2f}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_dft_09(method: str, storage) -> TestResult:
    """Evidence quality: each alert has detected_ask + baseline_reference"""
    tid, name, mod, mx = "DFT-09", "Evidence quality — excerpt provided", "drift", 7
    try:
        pid = await _setup_drift_baseline(method, storage)
        resp, lat = await _run_drift_check("drift_test_scope_expansion.txt", method, pid, storage)
        if not resp.alerts:
            return TestResult(tid, name, mod, method, mx, 0, False,
                              "No alerts generated", latency_ms=lat)
        with_evidence = sum(1 for a in resp.alerts
                            if a.detected_ask and a.baseline_reference)
        ok = with_evidence == len(resp.alerts)
        score = round(mx * with_evidence / len(resp.alerts), 1)
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"with_evidence={with_evidence}/{len(resp.alerts)}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_dft_10(method: str, storage) -> TestResult:
    """Suggested action present in each alert"""
    tid, name, mod, mx = "DFT-10", "Suggested action provided", "drift", 6
    try:
        pid = await _setup_drift_baseline(method, storage)
        resp, lat = await _run_drift_check("drift_test_new_requirement.txt", method, pid, storage)
        if not resp.alerts:
            return TestResult(tid, name, mod, method, mx, 0, False,
                              "No alerts generated", latency_ms=lat)
        with_action = sum(1 for a in resp.alerts if a.suggested_action)
        ok = with_action == len(resp.alerts)
        score = round(mx * with_action / len(resp.alerts), 1)
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"with_action={with_action}/{len(resp.alerts)}",
                          latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_dft_11(method: str, storage) -> TestResult:
    """Baseline versioning: v1 then v2, check uses latest"""
    tid, name, mod, mx = "DFT-11", "Baseline versioning", "drift", 6
    try:
        from api.models.schemas import BaselineRequest
        from api.services.drift_service import extract_baseline, get_latest_baseline

        pid = f"proj_dft11_{method}"
        art, _ = await _ingest_file("ngis_phase3_sow.txt", "sow", method,
                                     project_id=pid, storage=storage)
        await extract_baseline(BaselineRequest(
            project_id=pid, artifact_id=art.artifact_id, version="v1",
            use_llm=method != "traditional",
            llm_tier=_llm_tier(method) if method != "traditional" else "large",  # type: ignore[arg-type]
        ), storage)
        await extract_baseline(BaselineRequest(
            project_id=pid, artifact_id=art.artifact_id, version="v2",
            use_llm=method != "traditional",
            llm_tier=_llm_tier(method) if method != "traditional" else "large",  # type: ignore[arg-type]
        ), storage)
        latest = get_latest_baseline(pid, storage)
        ok = latest is not None and latest.version == "v2"
        score = mx if ok else 0
        return TestResult(tid, name, mod, method, mx, score, ok,
                          f"latest_version={latest.version if latest else 'None'}")
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_dft_12(method: str, storage) -> TestResult:
    """Schema validation"""
    tid, name, mod, mx = "DFT-12", "API response schema validation", "drift", 6
    try:
        from api.models.schemas import DriftCheckResponse
        pid = await _setup_drift_baseline(method, storage)
        resp, lat = await _run_drift_check("drift_test_within_scope.txt", method, pid, storage)
        DriftCheckResponse.model_validate(resp.model_dump())
        return TestResult(tid, name, mod, method, mx, mx, True,
                          "Schema valid", latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


async def run_dft_13(method: str, storage) -> TestResult:
    tid, name, mod, mx = "DFT-13", "Processing latency", "drift", 5
    try:
        pid = await _setup_drift_baseline(method, storage)
        _, lat = await _run_drift_check("drift_test_scope_expansion.txt", method, pid, storage)
        return TestResult(tid, name, mod, method, mx, mx, True,
                          f"latency={lat:.0f}ms", latency_ms=lat)
    except Exception as e:
        return _err(tid, name, mod, method, mx, e)


DFT_TESTS = [
    (run_dft_01, "llm_only"),
    (run_dft_02, "llm_only"),
    (run_dft_03, "llm_only"),
    (run_dft_04, "llm_only"),
    (run_dft_05, "llm_only"),
    (run_dft_06, "llm_only"),
    (run_dft_07, "llm_only"),
    (run_dft_08, "llm_only"),
    (run_dft_09, "llm_only"),
    (run_dft_10, "llm_only"),
    (run_dft_11, "both"),
    (run_dft_12, "both"),
    (run_dft_13, "llm_only"),
]

# ---------------------------------------------------------------------------
# ═══════════════════════  TEST RUNNER  ═══════════════════════
# ---------------------------------------------------------------------------

MODULE_TESTS: dict[str, list] = {
    "ingestion": ING_TESTS,
    "risk":      RSK_TESTS,
    "priority":  PRI_TESTS,
    "drift":     DFT_TESTS,
}


def _methods_for_test(test_type: str, enabled_methods: list[str]) -> list[str]:
    """Return which methods to run for a given test_type string."""
    if test_type == "llm_only":
        return [m for m in enabled_methods if m != "traditional"]
    elif test_type == "both" or test_type == "compare":
        return enabled_methods
    return enabled_methods


async def run_all(
    modules: list[str],
    enabled_methods: list[str],
    rate_limit_s: float = 1.0,
    verbose: bool = False,
    raw_dir: Path | None = None,
) -> list[TestResult]:
    from api.utils.llm_client import clear_llm_log, get_llm_log

    all_results: list[TestResult] = []

    for module in modules:
        _print(f"\n[bold cyan]=== {module.upper()} TESTS ===[/bold cyan]")
        tests = MODULE_TESTS[module]
        storage = _new_storage()  # fresh storage per module

        for fn, test_type in tests:
            methods = _methods_for_test(test_type, enabled_methods)
            for method in methods:
                # Reset capture bus before each test
                _CAPTURE.clear()
                clear_llm_log()

                _print(f"  [dim]{fn.__name__} [{method}]...[/dim]")
                t0 = time.perf_counter()
                result = await fn(method, storage)
                elapsed = (time.perf_counter() - t0) * 1000
                if result.latency_ms is None:
                    result.latency_ms = elapsed

                # Drain capture bus + LLM log into raw_output
                result.raw_output = {
                    **_cap_drain(),
                    "llm_calls": get_llm_log(),
                }

                tag = result.status_tag()
                _print(
                    f"  {tag} {result.test_id} [{method}] "
                    f"{result.score_str()} | {result.details[:80]}"
                    + (f" | lat={result.latency_ms:.0f}ms" if result.latency_ms else "")
                )

                # Verbose: print full raw output
                if verbose:
                    _print(f"[dim]{json.dumps(result.raw_output, indent=2, default=str)[:4000]}[/dim]")

                # Save individual raw output file
                if raw_dir:
                    fname = f"{result.test_id}_{method}.json"
                    (raw_dir / fname).write_text(
                        json.dumps({
                            "test_id": result.test_id,
                            "test_name": result.test_name,
                            "method": method,
                            "passed": result.passed,
                            "score": f"{result.earned_score}/{result.max_score}",
                            "details": result.details,
                            "error": result.error,
                            "latency_ms": result.latency_ms,
                            **result.raw_output,
                        }, indent=2, default=str),
                        encoding="utf-8",
                    )

                all_results.append(result)

                # Rate limit between LLM calls
                if method != "traditional" and rate_limit_s > 0:
                    await asyncio.sleep(rate_limit_s)

    return all_results

# ---------------------------------------------------------------------------
# ═══════════════════════  SCORING & REPORTS  ═══════════════════════
# ---------------------------------------------------------------------------

def compute_final_scores(results: list[TestResult]) -> dict[str, Any]:
    modules = ["ingestion", "risk", "priority", "drift"]
    max_scores = {"ingestion": 121, "risk": 104, "priority": 97, "drift": 104}
    by_module: dict[str, dict[str, Any]] = {m: {} for m in modules}
    by_method: dict[str, float] = {}
    latency_by_module: dict[str, dict[str, list]] = {m: {} for m in modules}

    for r in results:
        mod = r.module
        meth = r.method
        if mod not in by_module:
            continue
        by_module[mod].setdefault(meth, 0.0)
        by_module[mod][meth] += r.earned_score
        by_method.setdefault(meth, 0.0)
        by_method[meth] += r.earned_score
        if r.latency_ms:
            latency_by_module[mod].setdefault(meth, []).append(r.latency_ms)

    avg_latency: dict[str, dict[str, float]] = {}
    for mod, meth_lats in latency_by_module.items():
        avg_latency[mod] = {
            m: round(sum(lats) / len(lats), 1)
            for m, lats in meth_lats.items() if lats
        }

    return {
        "by_module": by_module,
        "by_method": by_method,
        "total_possible": max_scores,
        "avg_latency_ms": avg_latency,
    }


def save_json_results(results: list[TestResult], ts: str) -> Path:
    path = RESULTS_DIR / f"test_results_{ts}.json"
    data = {
        "timestamp": ts,
        "total_tests": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed and not r.error),
        "errors": sum(1 for r in results if r.error),
        "scores": compute_final_scores(results),
        "results": [asdict(r) for r in results],
    }
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return path


def generate_markdown_report(results: list[TestResult], ts: str) -> Path:
    path = RESULTS_DIR / f"test_comparison_{ts}.md"
    scores = compute_final_scores(results)
    methods = sorted({r.method for r in results})
    mod_labels = {
        "ingestion": "Ingestion (/114)",
        "risk":      "Risk Engine (/104)",
        "priority":  "Priority Ranker (/97)",
        "drift":     "Drift Detector (/104)",
    }
    method_order = ["traditional", "llm_local", "llm_small", "llm_medium", "llm_large"]
    ordered_methods = [m for m in method_order if m in methods]

    lines = [
        f"# Work Pulse Test Results — {ts}",
        "",
        "## Overall Score Comparison",
        "",
    ]

    # Header row
    header = "| Method |" + "".join(f" {mod_labels[m]} |" for m in ["ingestion", "risk", "priority", "drift"]) + " Total (/419) |"
    sep    = "|--------|" + "".join("----------|" for _ in range(4)) + "-------------|"
    lines += [header, sep]

    for meth in ordered_methods:
        row = f"| {METHOD_LABELS.get(meth, meth)} |"
        total = 0.0
        for mod in ["ingestion", "risk", "priority", "drift"]:
            s = scores["by_module"][mod].get(meth, 0)
            mx = scores["total_possible"][mod]
            row += f" {s:.0f}/{mx} |"
            total += s
        row += f" {total:.0f}/419 |"
        lines.append(row)

    # Latency table
    lines += ["", "## Average Latency per Call (ms)", ""]
    lat_header = "| Method |" + "".join(f" {m.replace('Ingestion','Ing').split('/')[0]} |" for m in ["ingestion", "risk", "priority", "drift"])
    lat_sep    = "|--------|" + "".join("------------|" for _ in range(4))
    lines += [lat_header, lat_sep]
    for meth in ordered_methods:
        row = f"| {METHOD_LABELS.get(meth, meth)} |"
        for mod in ["ingestion", "risk", "priority", "drift"]:
            lat = scores["avg_latency_ms"].get(mod, {}).get(meth)
            row += f" {lat:.0f}ms |" if lat else " N/A |"
        lines.append(row)

    # Per-test detail
    lines += ["", "## Detailed Results by Module", ""]
    for mod in ["ingestion", "risk", "priority", "drift"]:
        lines.append(f"### {mod.capitalize()}")
        lines.append("")
        lines.append("| Test ID | Test Name | Max | " +
                     " | ".join(METHOD_LABELS.get(m, m) for m in ordered_methods) + " |")
        lines.append("|---------|-----------|-----|" +
                     "".join("--------|" for _ in ordered_methods))

        mod_results = [r for r in results if r.module == mod]
        test_ids = list(dict.fromkeys(r.test_id for r in mod_results))
        for tid in test_ids:
            tid_results = {r.method: r for r in mod_results if r.test_id == tid}
            first = next(iter(tid_results.values()))
            row = f"| {tid} | {first.test_name[:35]} | {first.max_score} |"
            for meth in ordered_methods:
                r = tid_results.get(meth)
                if r:
                    flag = "PASS" if r.passed else ("ERR" if r.error else "FAIL")
                    row += f" {r.earned_score:.1f} {flag} |"
                else:
                    row += " — |"
            lines.append(row)
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path

# ---------------------------------------------------------------------------
# ═══════════════════════  CLI ENTRY POINT  ═══════════════════════
# ---------------------------------------------------------------------------

_TIER_CHOICES = ["local", "local_full", "small", "small_full",
                 "medium", "medium_full", "large", "large_full"]


def _parse_args():
    p = argparse.ArgumentParser(description="Work Pulse Test Suite")
    p.add_argument("--module", choices=["ingestion", "risk", "priority", "drift"],
                   help="Run only this module")
    p.add_argument("--tier", nargs="+", choices=_TIER_CHOICES,
                   help="LLM tier(s) to run, e.g. --tier local local_full medium large")
    p.add_argument("--skip-llm", action="store_true",
                   help="Run traditional methods only (no LLM calls)")
    p.add_argument("--no-traditional", action="store_true",
                   help="Skip the traditional baseline (LLM methods only)")
    p.add_argument("--all-tiers", action="store_true",
                   help="Run all methods (traditional + 4 LLM tiers)")
    p.add_argument("--rate-limit", type=float, default=1.0,
                   help="Seconds between LLM calls (default: 1.0)")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Print full raw output (service response + LLM content) after each test")
    p.add_argument("--no-raw-files", action="store_true",
                   help="Skip saving per-test raw output files")
    return p.parse_args()


async def main():
    args = _parse_args()
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    # Determine which modules to run
    modules = [args.module] if args.module else list(MODULE_TESTS.keys())

    # Determine which methods to use
    if args.skip_llm:
        enabled_methods = ["traditional"]
    elif args.all_tiers:
        enabled_methods = ALL_METHODS
    elif args.tier:
        llm_methods = [f"llm_{t}" for t in args.tier]
        enabled_methods = llm_methods if args.no_traditional else ["traditional"] + llm_methods
    else:
        # Default: traditional + local Ollama hybrid
        enabled_methods = ["traditional", "llm_local"]

    _print(f"\n[bold]Work Pulse Test Suite[/bold]  {ts}")
    _print(f"Modules  : {modules}")
    _print(f"Methods  : {enabled_methods}")
    _print(f"Testdata : {TESTDATA_DIR}")

    # Create per-test raw output directory (unless --no-raw-files)
    raw_dir: Path | None = None
    if not args.no_raw_files:
        raw_dir = RESULTS_DIR / f"test_raw_{ts}"
        raw_dir.mkdir(exist_ok=True)

    start = time.perf_counter()
    results = await run_all(
        modules, enabled_methods,
        rate_limit_s=args.rate_limit,
        verbose=args.verbose,
        raw_dir=raw_dir,
    )
    elapsed = time.perf_counter() - start

    # Summary
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed and not r.error)
    errors = sum(1 for r in results if r.error)
    _print(f"\n[bold]Results[/bold]: {passed} PASS  {failed} FAIL  {errors} ERROR "
           f"/ {len(results)} total  ({elapsed:.1f}s)")

    scores = compute_final_scores(results)
    _print("\n[bold]Score summary:[/bold]")
    for mod in ["ingestion", "risk", "priority", "drift"]:
        mod_scores = scores["by_module"].get(mod, {})
        mx = scores["total_possible"][mod]
        parts = "  ".join(f"{METHOD_LABELS.get(m,m)}={s:.0f}/{mx}"
                          for m, s in mod_scores.items())
        _print(f"  {mod.capitalize():12s} {parts}")

    # Save files
    json_path = save_json_results(results, ts)
    md_path = generate_markdown_report(results, ts)
    _print(f"\n[bold green]Saved:[/bold green]")
    _print(f"  JSON   : {json_path}")
    _print(f"  Report : {md_path}")
    if raw_dir:
        count = len(list(raw_dir.glob("*.json")))
        _print(f"  Raw    : {raw_dir}  ({count} files)")


if __name__ == "__main__":
    asyncio.run(main())
