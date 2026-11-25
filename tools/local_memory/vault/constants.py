"""Vocabulary for the layered memory architecture (L1-L3).

Single source of truth for note types, statuses, confidence levels, edge
types, folder layout, and exclusions. Every other module in the memory
layer imports from here so the index, the graph builder, and retrieval can
never disagree about what a valid value is.

Design rule from ``Grapify & Obsidian.md``: L3 (the vault) is the only
source of truth. L2 (the graph) is a cache and may be deleted at any time.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Final

# -- Note types ---------------------------------------------------------
TYPE_SESSION: Final = "session"
TYPE_CONCEPT: Final = "concept"
TYPE_DECISION: Final = "decision"
TYPE_QUESTION: Final = "question"
TYPE_PERSON: Final = "person"
TYPE_PROJECT: Final = "project"
TYPE_NOTE: Final = "note"
TYPE_META: Final = "meta"
TYPE_PLAYBOOK: Final = "playbook"

NOTE_TYPES: Final = frozenset({
    TYPE_SESSION, TYPE_CONCEPT, TYPE_DECISION, TYPE_QUESTION,
    TYPE_PERSON, TYPE_PROJECT, TYPE_NOTE, TYPE_META, TYPE_PLAYBOOK,
})

# -- Status -------------------------------------------------------------
STATUS_ACTIVE: Final = "active"
STATUS_SUPERSEDED: Final = "superseded"
STATUS_ARCHIVED: Final = "archived"
STATUS_DRAFT: Final = "draft"

NOTE_STATUSES: Final = frozenset({
    STATUS_ACTIVE, STATUS_SUPERSEDED, STATUS_ARCHIVED, STATUS_DRAFT,
})

# Statuses that must never be surfaced as current truth.
STALE_STATUSES: Final = frozenset({STATUS_SUPERSEDED, STATUS_ARCHIVED})

# -- Confidence ---------------------------------------------------------
CONFIDENCE_LOW: Final = "low"
CONFIDENCE_MEDIUM: Final = "medium"
CONFIDENCE_HIGH: Final = "high"

CONFIDENCE_LEVELS: Final = frozenset({
    CONFIDENCE_LOW, CONFIDENCE_MEDIUM, CONFIDENCE_HIGH,
})

CONFIDENCE_RANK: Final = {
    CONFIDENCE_LOW: 0,
    CONFIDENCE_MEDIUM: 1,
    CONFIDENCE_HIGH: 2,
}

# -- Edge types ---------------------------------------------------------
EDGE_LINKS: Final = "menautkan"
EDGE_DEPENDS: Final = "bergantung_pada"
EDGE_CONFLICTS: Final = "bertentangan_dengan"
EDGE_REPLACES: Final = "menggantikan"
EDGE_SUPPORTS: Final = "mendukung"
EDGE_SHARES_TAG: Final = "berbagi_tag"

EDGE_TYPES: Final = frozenset({
    EDGE_LINKS, EDGE_DEPENDS, EDGE_CONFLICTS, EDGE_REPLACES,
    EDGE_SUPPORTS, EDGE_SHARES_TAG,
})

# Frontmatter keys that declare a typed relation, mapped to an edge type.
# Both English and Indonesian spellings are accepted because the vault is
# authored in both languages.
RELATION_KEYS: Final = {
    "depends_on": EDGE_DEPENDS,
    "bergantung_pada": EDGE_DEPENDS,
    "conflicts_with": EDGE_CONFLICTS,
    "bertentangan_dengan": EDGE_CONFLICTS,
    "supersedes": EDGE_REPLACES,
    "menggantikan": EDGE_REPLACES,
    "supports": EDGE_SUPPORTS,
    "mendukung": EDGE_SUPPORTS,
    "related": EDGE_LINKS,
    "lihat": EDGE_LINKS,
    "references": EDGE_LINKS,
    "sumber": EDGE_LINKS,
}

# -- Edge provenance ----------------------------------------------------
ORIGIN_EXPLICIT: Final = "explicit"
ORIGIN_INFERRED: Final = "inferred"

# Explicit edges come from wikilinks or declared relations and are trusted.
# Inferred edges are guesses and must be labelled as such in every output.
EDGE_ORIGIN_WEIGHT: Final = {
    ORIGIN_EXPLICIT: 1.0,
    ORIGIN_INFERRED: 0.4,
}

# -- Folders ------------------------------------------------------------
FOLDER_INBOX: Final = "00-inbox"
FOLDER_SESSIONS: Final = "10-sessions"
FOLDER_CONCEPTS: Final = "20-concepts"
FOLDER_DECISIONS: Final = "30-decisions"
FOLDER_PEOPLE: Final = "40-people-projects"
FOLDER_PLAYBOOKS: Final = "50-playbooks"
FOLDER_META: Final = "90-meta"
FOLDER_GRAPH_OUT: Final = "graphify-out"

STANDARD_FOLDERS: Final = (
    FOLDER_INBOX, FOLDER_SESSIONS, FOLDER_CONCEPTS,
    FOLDER_DECISIONS, FOLDER_PEOPLE, FOLDER_PLAYBOOKS, FOLDER_META, FOLDER_GRAPH_OUT,
)

FOLDER_TYPE_MAP: Final = {
    FOLDER_INBOX: TYPE_NOTE,
    FOLDER_SESSIONS: TYPE_SESSION,
    FOLDER_CONCEPTS: TYPE_CONCEPT,
    FOLDER_DECISIONS: TYPE_DECISION,
    FOLDER_PEOPLE: TYPE_PROJECT,
    FOLDER_PLAYBOOKS: TYPE_PLAYBOOK,
    FOLDER_META: TYPE_META,
}

TYPE_FOLDER_MAP: Final = {
    TYPE_SESSION: FOLDER_SESSIONS,
    TYPE_CONCEPT: FOLDER_CONCEPTS,
    TYPE_DECISION: FOLDER_DECISIONS,
    TYPE_QUESTION: FOLDER_INBOX,
    TYPE_PERSON: FOLDER_PEOPLE,
    TYPE_PROJECT: FOLDER_PEOPLE,
    TYPE_META: FOLDER_META,
    TYPE_PLAYBOOK: FOLDER_PLAYBOOKS,
    TYPE_NOTE: FOLDER_INBOX,
}

# -- L1 boot files ------------------------------------------------------
BOOT_FILENAMES: Final = ("CLAUDE.md", "claude.md", "AGENTS.md", "BOOT.md")

# -- Exclusions ---------------------------------------------------------
# Folders never indexed: private notes stay out of L2 entirely.
DEFAULT_EXCLUDED_FOLDERS: Final = (
    ".obsidian", ".trash", ".git", "node_modules", FOLDER_GRAPH_OUT,
    # Tooling and build output: never notes, even if the vault is pointed at
    # a project root by mistake.
    ".venv", "venv", "env", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "build", "dist", ".tox", "site-packages", "vendor", "storage",
)

SENSITIVE_MARKERS: Final = (
    "private", "sensitive", "secret", "pribadi", "rahasia",
)

# -- On-disk artifacts (all inside the vault, all disposable) -----------
INDEX_DIRNAME: Final = ".squidwings"
INDEX_FILENAME: Final = "index.json"
GRAPH_FILENAME: Final = "graph.json"
REPORT_FILENAME: Final = "graph-report.md"
INDEX_VERSION: Final = 2
GRAPH_VERSION: Final = 2

# -- Limits -------------------------------------------------------------
MAX_NOTE_BYTES: Final = 5 * 1024 * 1024
MAX_INDEX_ENTRIES: Final = 10_000
MAX_SUMMARY_CHARS: Final = 240
MAX_BODY_SCAN_CHARS: Final = 20_000

# -- Graph tuning -------------------------------------------------------
HUB_MIN_DEGREE: Final = 4
HUB_TOP_N: Final = 10
COMMUNITY_MAX_ITERATIONS: Final = 12
MIN_SHARED_TAGS_FOR_EDGE: Final = 2


# -- Helpers ------------------------------------------------------------
def type_for_folder(rel_path: str) -> str:
    """Infer a note type from its top-level folder."""
    head = rel_path.replace("\\", "/").split("/", 1)[0]
    return FOLDER_TYPE_MAP.get(head, TYPE_NOTE)


def folder_for_type(note_type: str) -> str:
    """Return the canonical folder for a note type."""
    return TYPE_FOLDER_MAP.get(normalize_type(note_type), FOLDER_INBOX)


def is_excluded(rel_path: str, extra: Iterable[str] = ()) -> bool:
    """True when a path lives in an excluded or sensitive folder.

    Only directory components are checked - a file named ``secret.md`` at the
    vault root is still indexed, but anything under ``private/`` is not.
    """
    parts = [p.lower() for p in rel_path.replace("\\", "/").split("/")]
    excluded = {e.lower() for e in DEFAULT_EXCLUDED_FOLDERS}
    excluded.update(str(e).lower() for e in extra)
    for part in parts[:-1]:
        if part in excluded:
            return True
        if any(marker in part for marker in SENSITIVE_MARKERS):
            return True
    return False


def normalize_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in NOTE_TYPES else TYPE_NOTE


def normalize_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in NOTE_STATUSES else STATUS_ACTIVE


def normalize_confidence(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in CONFIDENCE_LEVELS else CONFIDENCE_MEDIUM


def edge_type_for_key(key: str) -> str | None:
    """Map a frontmatter key to an edge type, or None if not a relation."""
    return RELATION_KEYS.get(str(key or "").strip().lower())


def ensure_layout(vault: Path) -> list[str]:
    """Create the standard folder skeleton. Returns folders created."""
    created: list[str] = []
    for folder in STANDARD_FOLDERS:
        target = Path(vault) / folder
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)
            created.append(folder)
    return created


def index_dir(vault: Path) -> Path:
    return Path(vault) / INDEX_DIRNAME


def graph_path(vault: Path) -> Path:
    return Path(vault) / FOLDER_GRAPH_OUT / GRAPH_FILENAME


__all__ = [
    "TYPE_SESSION", "TYPE_CONCEPT", "TYPE_DECISION", "TYPE_QUESTION",
    "TYPE_PERSON", "TYPE_PROJECT", "TYPE_NOTE", "TYPE_META", "NOTE_TYPES",
    "STATUS_ACTIVE", "STATUS_SUPERSEDED", "STATUS_ARCHIVED", "STATUS_DRAFT",
    "NOTE_STATUSES", "STALE_STATUSES",
    "CONFIDENCE_LOW", "CONFIDENCE_MEDIUM", "CONFIDENCE_HIGH",
    "CONFIDENCE_LEVELS", "CONFIDENCE_RANK",
    "EDGE_LINKS", "EDGE_DEPENDS", "EDGE_CONFLICTS", "EDGE_REPLACES",
    "EDGE_SUPPORTS", "EDGE_SHARES_TAG", "EDGE_TYPES", "RELATION_KEYS",
    "ORIGIN_EXPLICIT", "ORIGIN_INFERRED", "EDGE_ORIGIN_WEIGHT",
    "FOLDER_INBOX", "FOLDER_SESSIONS", "FOLDER_CONCEPTS", "FOLDER_DECISIONS",
    "FOLDER_PEOPLE", "FOLDER_PLAYBOOKS", "FOLDER_META", "FOLDER_GRAPH_OUT", "STANDARD_FOLDERS",
    "FOLDER_TYPE_MAP", "TYPE_FOLDER_MAP",
    "BOOT_FILENAMES", "DEFAULT_EXCLUDED_FOLDERS", "SENSITIVE_MARKERS",
    "INDEX_DIRNAME", "INDEX_FILENAME", "GRAPH_FILENAME", "REPORT_FILENAME",
    "INDEX_VERSION", "GRAPH_VERSION",
    "MAX_NOTE_BYTES", "MAX_INDEX_ENTRIES", "MAX_SUMMARY_CHARS",
    "MAX_BODY_SCAN_CHARS",
    "HUB_MIN_DEGREE", "HUB_TOP_N", "COMMUNITY_MAX_ITERATIONS",
    "MIN_SHARED_TAGS_FOR_EDGE",
    "type_for_folder", "folder_for_type", "is_excluded",
    "normalize_type", "normalize_status", "normalize_confidence",
    "edge_type_for_key", "ensure_layout", "index_dir", "graph_path",
]
