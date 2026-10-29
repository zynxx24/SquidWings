"""
brain.classifier.pattern_matcher — Regex pattern-based classification.
"""

from __future__ import annotations

import re

from config.constants import TaskType

# Pattern → (TaskType, confidence)
# Ordered by specificity (most specific first)
PATTERNS: list[tuple[str, TaskType, float]] = [
    # Code patterns
    (r"(?:buat|tulis|generate|create|write)\s+(?:kode|code|program|script|fungsi|function|class)", TaskType.CODE, 0.95),
    (r"(?:debug|fix|perbaiki)\s+(?:bug|error|kode|code)", TaskType.CODE, 0.9),
    (r"(?:refactor|optimize|optimasi)\s+(?:kode|code)", TaskType.CODE, 0.85),
    (r"```\w+\n", TaskType.CODE, 0.8),  # Contains code block

    # Math patterns
    (r"(?:hitung|calculate|solve)\s+.+(?:=|persamaan|equation)", TaskType.MATH, 0.9),
    (r"\d+\s*[\+\-\*\/\^]\s*\d+", TaskType.MATH, 0.7),  # Arithmetic expression
    (r"(?:integral|turunan|derivative|limit|∫|∑|∏)", TaskType.MATH, 0.9),
    (r"(?:sin|cos|tan|log|ln)\s*\(", TaskType.MATH, 0.85),

    # School task patterns
    (r"(?:jawab|kerjakan|selesaikan)\s+(?:soal|pertanyaan|tugas)", TaskType.SCHOOL_TASK, 0.9),
    (r"(?:soal\s+(?:nomor|no)?\s*\d+)", TaskType.SCHOOL_TASK, 0.85),
    (r"(?:ujian|ulangan|kuis|quiz)\s+(?:tentang|about|materi)", TaskType.SCHOOL_TASK, 0.85),

    # Image patterns
    (r"(?:buat|generate|create)\s+(?:gambar|image|foto|picture|ilustrasi)", TaskType.GENERATE_IMG, 0.95),
    (r"(?:desain|design)\s+(?:logo|banner|poster|icon)", TaskType.GENERATE_IMG, 0.85),

    # Multimodal patterns
    (r"(?:lihat|analisis|analyze)\s+(?:gambar|image|foto|picture)\s+(?:ini|this)", TaskType.MULTIMODAL, 0.9),
    (r"(?:dengar|listen|transkripsi|transcribe)\s+(?:audio|suara|rekaman)", TaskType.MULTIMODAL, 0.9),
    (r"(?:tonton|watch)\s+(?:video|rekaman)\s+(?:ini|this)", TaskType.MULTIMODAL, 0.9),

    # Reasoning patterns
    (r"(?:analisis|analyze|evaluasi|evaluate)\s+(?:mendalam|deep|detail)", TaskType.REASON, 0.85),
    (r"(?:bandingkan|compare)\s+.+(?:dan|and|dengan|with)", TaskType.REASON, 0.75),
    (r"(?:pro\s+(?:dan|and)\s+kontra|pros?\s+(?:dan|and)\s+cons?)", TaskType.REASON, 0.9),
]


def get_pattern_matches(text: str) -> list[tuple[TaskType, float]]:
    """Find all regex pattern matches in text.

    Args:
        text: Input text to check.

    Returns:
        list of (TaskType, confidence) tuples for all matches.
    """
    matches: list[tuple[TaskType, float]] = []

    for pattern, task_type, confidence in PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            matches.append((task_type, confidence))

    return matches
