"""
Bilingual middleware helpers — language detection + bilingual prompt wrapper.
"""

from __future__ import annotations

try:
    from langdetect import detect, DetectorFactory  # type: ignore
    DetectorFactory.seed = 42  # deterministic
    LANGDETECT_AVAILABLE = True
except ImportError:
    LANGDETECT_AVAILABLE = False

from api.utils.traditional_methods import detect_language_simple


def detect_language(text: str) -> str:
    """
    Detect language of text. Returns 'en', 'fr', or 'mixed'.

    Uses langdetect if available, falls back to heuristic.
    For mixed detection, runs on first/second half separately.
    """
    if not text or len(text.strip()) < 10:
        return "en"

    if LANGDETECT_AVAILABLE:
        try:
            mid = len(text) // 2
            lang_first = detect(text[:mid]) if mid > 20 else None
            lang_second = detect(text[mid:]) if mid > 20 else None
            lang_full = detect(text)

            if lang_first and lang_second and lang_first != lang_second:
                # Both halves detected different languages → mixed
                if {lang_first, lang_second} == {"en", "fr"}:
                    return "mixed"
            # Trust full-text detection
            if lang_full in ("en", "fr"):
                return lang_full
            return lang_full  # return whatever langdetect says
        except Exception:
            pass

    return detect_language_simple(text)


def ensure_bilingual_prompt(prompt: str, language: str) -> str:
    """
    Append language instruction to a prompt so the LLM responds appropriately.
    """
    if language == "fr":
        return prompt + "\n\nIMPORTANT: Respond in French (français)."
    if language == "mixed":
        return (
            prompt
            + "\n\nIMPORTANT: This document contains both English and French content. "
            "Extract information from BOTH languages. Do not ignore French-language action items or risks."
        )
    return prompt  # default: English, no extra instruction
