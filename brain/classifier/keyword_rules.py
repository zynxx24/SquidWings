"""
brain.classifier.keyword_rules — Rule-based keyword classification.

Maps keywords and phrases (Bahasa Indonesia + English) to task types.
"""

from __future__ import annotations

from config.constants import TaskType

# Keyword → TaskType mapping
# Format: (keyword_or_phrase, weight)
# Higher weight = stronger signal
KEYWORD_RULES: dict[TaskType, list[tuple[str, float]]] = {
    TaskType.CODE: [
        # Indonesian
        ("buatkan kode", 0.95),
        ("buat kode", 0.95),
        ("tulis kode", 0.95),
        ("coding", 0.9),
        ("program", 0.8),
        ("script", 0.85),
        ("fungsi", 0.7),
        ("class", 0.7),
        ("debug", 0.9),
        ("fix bug", 0.9),
        ("refactor", 0.85),
        ("implementasi", 0.8),
        ("api", 0.6),
        ("database", 0.6),
        ("frontend", 0.7),
        ("backend", 0.7),
        # English
        ("write code", 0.95),
        ("create function", 0.9),
        ("implement", 0.8),
        ("code review", 0.85),
        ("algorithm", 0.8),
        ("syntax", 0.7),
        ("compile", 0.75),
        ("deploy", 0.6),
    ],
    TaskType.MATH: [
        # Indonesian
        ("hitung", 0.9),
        ("kalkulasi", 0.9),
        ("rumus", 0.85),
        ("persamaan", 0.85),
        ("matematika", 0.95),
        ("aljabar", 0.9),
        ("geometri", 0.9),
        ("statistik", 0.85),
        ("probabilitas", 0.85),
        ("integral", 0.9),
        ("turunan", 0.9),
        ("limit", 0.7),
        ("matriks", 0.85),
        ("vektor", 0.8),
        # English
        ("calculate", 0.9),
        ("equation", 0.85),
        ("formula", 0.85),
        ("derivative", 0.9),
        ("integral", 0.9),
        ("solve for x", 0.95),
        ("trigonometry", 0.9),
    ],
    TaskType.SCHOOL_TASK: [
        # Indonesian
        ("soal", 0.85),
        ("ujian", 0.9),
        ("pelajaran", 0.8),
        ("tugas sekolah", 0.95),
        ("pr ", 0.7),
        ("kerjakan", 0.6),
        ("jawab soal", 0.9),
        ("materi", 0.6),
        ("bab ", 0.5),
        ("latihan", 0.7),
        ("kuis", 0.8),
        ("ulangan", 0.85),
        # English
        ("homework", 0.9),
        ("exam", 0.85),
        ("quiz", 0.8),
        ("assignment", 0.85),
        ("school", 0.6),
        ("lesson", 0.6),
        ("textbook", 0.7),
    ],
    TaskType.GENERATE_IMG: [
        # Indonesian
        ("gambar", 0.8),
        ("buat gambar", 0.95),
        ("generate gambar", 0.95),
        ("desain", 0.7),
        ("ilustrasi", 0.85),
        ("logo", 0.7),
        ("foto", 0.6),
        # English
        ("generate image", 0.95),
        ("create image", 0.95),
        ("draw", 0.8),
        ("illustration", 0.85),
        ("design", 0.6),
        ("picture", 0.7),
    ],
    TaskType.REASON: [
        # Indonesian
        ("pikirkan", 0.8),
        ("analisis", 0.8),
        ("analisa", 0.8),
        ("evaluasi", 0.75),
        ("pertimbangkan", 0.7),
        ("bandingkan", 0.7),
        ("pro dan kontra", 0.85),
        ("mengapa", 0.5),
        ("kenapa", 0.5),
        ("argumentasi", 0.85),
        # English
        ("analyze", 0.8),
        ("evaluate", 0.75),
        ("compare", 0.7),
        ("pros and cons", 0.85),
        ("reasoning", 0.9),
        ("think about", 0.7),
        ("argue", 0.7),
    ],
    TaskType.MULTIMODAL: [
        # Indonesian
        ("lihat gambar ini", 0.95),
        ("analisis gambar", 0.9),
        ("dengarkan", 0.9),
        ("video ini", 0.9),
        ("audio ini", 0.9),
        ("transkripsi", 0.85),
        # English
        ("look at this image", 0.95),
        ("analyze this image", 0.9),
        ("transcribe", 0.85),
        ("this video", 0.8),
        ("this audio", 0.8),
        ("what do you see", 0.85),
    ],
    TaskType.ACTIVITY: [
        # Indonesian — catch-all with lower weights
        ("jelaskan", 0.5),
        ("ceritakan", 0.5),
        ("bantuan", 0.4),
        ("tolong", 0.3),
        ("bagaimana", 0.3),
        ("apa itu", 0.4),
        ("ringkasan", 0.5),
        ("terjemahkan", 0.6),
        ("resume", 0.5),
        # English
        ("explain", 0.5),
        ("help", 0.3),
        ("how to", 0.4),
        ("what is", 0.4),
        ("summarize", 0.6),
        ("translate", 0.6),
    ],
}


def get_keyword_matches(text: str) -> list[tuple[TaskType, float]]:
    """Find all keyword matches in text.

    Args:
        text: Input text to check (will be lowercased).

    Returns:
        list of (TaskType, weight) tuples for all matches.
    """
    text_lower = text.lower()
    matches: list[tuple[TaskType, float]] = []

    for task_type, keywords in KEYWORD_RULES.items():
        for keyword, weight in keywords:
            if keyword.lower() in text_lower:
                matches.append((task_type, weight))

    return matches
