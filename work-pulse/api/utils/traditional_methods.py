"""
Traditional (non-LLM) baselines for task extraction, risk identification,
priority ranking, and text similarity.

spaCy is optional — imported inside try/except; methods degrade gracefully.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

# ---------------------------------------------------------------------------
# Optional imports
# ---------------------------------------------------------------------------

try:
    import spacy  # type: ignore
    _nlp = spacy.load("en_core_web_sm")
    SPACY_AVAILABLE = True
except Exception:
    _nlp = None
    SPACY_AVAILABLE = False

try:
    from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
    from sklearn.metrics.pairwise import cosine_similarity  # type: ignore
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

# ---------------------------------------------------------------------------
# Action item / task extraction
# ---------------------------------------------------------------------------

# Patterns that signal an explicit action item
_ACTION_PATTERNS = [
    r"\baction\s*(?:item|required|needed)?\s*[:\-]?\s*(.+)",
    r"\btodo\s*[:\-]?\s*(.+)",
    r"\bfollow[\s\-]?up\s*[:\-]?\s*(.+)",
    r"\bplease\s+(?:ensure|confirm|review|update|send|provide|schedule|contact|coordinate)\b.{5,100}",
    r"\b(?:must|should|need to|needs to|will|shall)\s+\w.{5,80}",
    r"\bdeadline\s*[:\-]?\s*(.+)",
    r"\bassigned\s+to\s*[:\-]?\s*(.+)",
    r"\bowner\s*[:\-]?\s*(.+)",
    r"^\s*[-*•]\s+(?!.*\bhttps?\b).{10,120}$",
]
_ACTION_RE = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in _ACTION_PATTERNS]

# Date patterns (ISO, slash, written month)
_DATE_PATTERNS = [
    r"\b(\d{4}[-/]\d{1,2}[-/]\d{1,2})\b",
    r"\b(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b",
    r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s*\d{4})\b",
    r"\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?,?\s*\d{4})\b",
]
_DATE_RE = [re.compile(p, re.IGNORECASE) for p in _DATE_PATTERNS]

# Common name patterns for owner extraction (fallback without spaCy)
_OWNER_PATTERNS = [
    r"(?:assigned\s+to|owner|contact|by)\s*[:\-]?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:will|should|must|to)\s+\w",
]
_OWNER_RE = [re.compile(p) for p in _OWNER_PATTERNS]


def extract_tasks_traditional(text: str, source_excerpt_chars: int = 100) -> list[dict[str, Any]]:
    """
    Extract action items from text using regex + optional spaCy NER.

    Returns a list of dicts with keys:
        text, owner, deadline, confidence, source_excerpt
    """
    tasks: list[dict[str, Any]] = []
    seen_texts: set[str] = set()

    for pattern in _ACTION_RE:
        for m in pattern.finditer(text):
            snippet = m.group(0).strip()
            if len(snippet) < 10:
                continue
            key = hashlib.md5(snippet.lower().encode()).hexdigest()
            if key in seen_texts:
                continue
            seen_texts.add(key)

            # Try to find a date near the match
            deadline = None
            surrounding = text[max(0, m.start() - 50): m.end() + 100]
            for drx in _DATE_RE:
                dm = drx.search(surrounding)
                if dm:
                    deadline = dm.group(1)
                    break

            # Try to find an owner near the match
            owner = None
            if SPACY_AVAILABLE and _nlp:
                doc = _nlp(surrounding)
                persons = [ent.text for ent in doc.ents if ent.label_ == "PERSON"]
                if persons:
                    owner = persons[0]
            if not owner:
                for orx in _OWNER_RE:
                    om = orx.search(surrounding)
                    if om:
                        owner = om.group(1)
                        break

            tasks.append(
                {
                    "text": snippet[:200],
                    "owner": owner,
                    "deadline": deadline,
                    "confidence": 0.7,
                    "source_excerpt": snippet[:source_excerpt_chars],
                }
            )

    return tasks


def extract_dates(text: str) -> list[str]:
    """Return all date strings found in text."""
    dates: list[str] = []
    for drx in _DATE_RE:
        for m in drx.finditer(text):
            dates.append(m.group(1))
    return list(dict.fromkeys(dates))  # deduplicate preserving order


# ---------------------------------------------------------------------------
# Risk identification
# ---------------------------------------------------------------------------

RISK_KEYWORDS: dict[str, list[str]] = {
    "schedule": [
        "delay", "delayed", "overdue", "behind schedule", "slippage", "slip",
        "timeline at risk", "deadline missed", "late delivery", "behind plan",
        "weeks behind", "months behind", "adjustment", "slight adjustment",
        "schedule optimization", "revised timeline",
    ],
    "budget": [
        "overrun", "over budget", "cost increase", "budget exceeded",
        "additional funding", "unplanned cost", "variance", "overspend",
        "financial risk", "budget pressure", "budget revision", "revised figures",
        "above the approved envelope", "above approved", "additional cost",
        "remediation requirements", "unplanned cost",
    ],
    "technical": [
        "technical risk", "deprecated", "legacy system", "dependency",
        "integration failure", "compatibility", "scalability", "security vulnerability",
        "performance issue", "architecture concern",
    ],
    "resource": [
        "resource shortage", "understaffed", "key person", "single point of failure",
        "no backup", "leaving", "resignation", "contractor", "capacity",
        "unavailable", "bandwidth", "knowledge transfer",
        "single-sourced", "single sourced", "alternative routing", "no alternative",
        "exploring external opportunities",
    ],
    "compliance": [
        "compliance", "audit", "regulatory", "ITSG", "PBMM", "policy violation",
        "non-compliant", "GC-TIP", "security assessment", "privacy impact",
        "ATIP", "breach", "violation",
    ],
}

# Words that suggest things are actually fine (negative evidence)
_MITIGATION_WORDS = ["resolved", "mitigated", "addressed", "closed", "no issue", "on track", "no risk"]


def identify_risks_traditional(text: str, artifact_id: str = "") -> list[dict[str, Any]]:
    """
    Identify risks from text via keyword matching + density scoring.

    Returns a list of dicts with keys:
        description, category, likelihood, impact, risk_score, risk_level,
        evidence_excerpt, source_artifact_id
    """
    text_lower = text.lower()
    risks: list[dict[str, Any]] = []

    for category, keywords in RISK_KEYWORDS.items():
        matched_kws: list[str] = []
        excerpts: list[str] = []

        for kw in keywords:
            idx = text_lower.find(kw.lower())
            while idx != -1:
                # Check if this match is negated by mitigation words in nearby context
                context = text_lower[max(0, idx - 60): idx + 60]
                if any(mw in context for mw in _MITIGATION_WORDS):
                    idx = text_lower.find(kw.lower(), idx + 1)
                    continue
                matched_kws.append(kw)
                excerpts.append(text[max(0, idx - 40): idx + len(kw) + 40].strip())
                idx = text_lower.find(kw.lower(), idx + 1)

        if not matched_kws:
            continue

        # Score: base likelihood from match density
        density = len(matched_kws) / (len(text.split()) / 100 + 1)
        likelihood = min(5, max(1, int(density * 1.5) + 1))
        impact = 3  # default; service layer applies adjustments

        raw_score = likelihood * impact
        risk_level = _score_to_level(raw_score)

        # Build description from first matching keyword + category
        desc = f"{category.capitalize()} risk detected: {matched_kws[0]} mentioned"
        if len(matched_kws) > 1:
            desc += f" (also: {', '.join(matched_kws[1:3])})"

        risks.append(
            {
                "description": desc,
                "category": category,
                "likelihood": likelihood,
                "impact": impact,
                "risk_score": float(raw_score),
                "risk_level": risk_level,
                "evidence_excerpt": excerpts[0][:200] if excerpts else "",
                "source_artifact_id": artifact_id,
            }
        )

    return risks


def _score_to_level(score: float) -> str:
    if score >= 20:
        return "critical"
    if score >= 12:
        return "high"
    if score >= 6:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Priority ranking
# ---------------------------------------------------------------------------

# Weights for scoring formula
_WEIGHTS = {
    "deadline_urgency": 0.35,
    "risk_level": 0.30,
    "dependency_count": 0.20,
    "rollover_count": 0.15,
}

_RISK_LEVEL_MAP = {"critical": 5, "high": 4, "medium": 3, "low": 1}


def _deadline_urgency(deadline_str: str | None) -> float:
    """Return a 0–5 urgency score based on how soon the deadline is."""
    if not deadline_str:
        return 0.0
    from datetime import datetime, timezone
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            dl = datetime.strptime(deadline_str, fmt)
            days = (dl - datetime.now()).days
            if days < 0:
                return 5.0  # overdue
            if days <= 1:
                return 5.0
            if days <= 3:
                return 4.0
            if days <= 7:
                return 3.5
            if days <= 14:
                return 2.5
            if days <= 30:
                return 1.5
            return 0.5
        except ValueError:
            continue
    return 0.0


def rank_tasks_traditional(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Rank tasks by weighted formula. Returns the list sorted by descending score,
    each item augmented with: priority_score, rank, priority_level, boost_applied.
    """
    scored = []
    for task in tasks:
        du = _deadline_urgency(task.get("deadline"))
        rl = _RISK_LEVEL_MAP.get(str(task.get("risk_level") or "").lower(), 0)
        dep = min(5, task.get("dependencies_blocked", 0))
        roll = min(5, task.get("rollover_count", 0))

        raw = (
            _WEIGHTS["deadline_urgency"] * du * 20
            + _WEIGHTS["risk_level"] * rl * 20
            + _WEIGHTS["dependency_count"] * dep * 20
            + _WEIGHTS["rollover_count"] * roll * 20
        )

        boosts: list[str] = []
        # Rule-based post-processing
        if du >= 5.0:
            raw += 20
            boosts.append("deadline_overdue_+20")
        elif du >= 4.0:
            raw += 10
            boosts.append("deadline_1d_+10")
        elif du >= 3.5:
            raw += 5
            boosts.append("deadline_3d_+5")

        if roll >= 3:
            raw += 15
            boosts.append("chronic_blocker_+15")

        if rl >= 4:
            raw += 8
            boosts.append("high_risk_+8")

        score = min(100.0, raw)
        scored.append(
            {
                **task,
                "priority_score": round(score, 2),
                "boost_applied": boosts,
                "chronic_blocker": roll >= 3,
            }
        )

    scored.sort(key=lambda x: x["priority_score"], reverse=True)

    for i, item in enumerate(scored, start=1):
        item["rank"] = i
        item["priority_level"] = _score_to_priority(item["priority_score"])

    return scored


def _score_to_priority(score: float) -> str:
    if score >= 75:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 25:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Text similarity (TF-IDF cosine)
# ---------------------------------------------------------------------------

def compute_text_similarity(text_a: str, text_b: str) -> float:
    """
    Return cosine similarity (0–1) between two texts using TF-IDF.
    Falls back to Jaccard similarity if scikit-learn is unavailable.
    """
    if SKLEARN_AVAILABLE:
        try:
            vectorizer = TfidfVectorizer(stop_words="english", min_df=1)
            matrix = vectorizer.fit_transform([text_a, text_b])
            sim = cosine_similarity(matrix[0:1], matrix[1:2])[0][0]
            return float(sim)
        except Exception:
            pass
    # Jaccard fallback
    tokens_a = set(text_a.lower().split())
    tokens_b = set(text_b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def _apply_rule_adjustments(
    likelihood: int, impact: int, artifact_text: str, has_owner: bool = False
) -> tuple[int, int, list[str]]:
    """Rule-based score adjustments (re-exported from traditional_methods for tests)."""
    adjustments: list[str] = []
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


def detect_language_simple(text: str) -> str:
    """
    Simple heuristic language detection without langdetect.
    Returns 'fr', 'en', or 'mixed'.
    """
    french_words = {
        "le", "la", "les", "de", "du", "des", "est", "sont", "avec", "pour",
        "une", "nous", "vous", "ils", "elles", "dans", "sur", "par", "que",
        "qui", "cette", "ces", "mais", "ou", "donc", "or", "ni", "car",
    }
    tokens = set(text.lower().split())
    fr_count = len(tokens & french_words)
    ratio = fr_count / max(len(tokens), 1)
    if ratio > 0.15:
        return "fr" if ratio > 0.25 else "mixed"
    return "en"
