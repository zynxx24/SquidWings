"""What the agent remembers about one project.

Memory has three layers, and each kind of fact has exactly one home:

* Session: ``ConversationStore``, the chat transcript.
* Project: this class. Files touched, decisions, facts, errors, and output that
  was cut off. Stored in ``<workspace>/.agent_memory`` so it travels with the
  project and survives restarts and model switches.
* Knowledge: the vault. Notes and playbooks that outlive any project.

State lives on disk, not in the instance. Agents share a workspace, and a tool
can write in the middle of a turn, so every change reads the latest state first.
Otherwise whichever copy saved last would silently erase the other's facts.

Nothing a turn depends on may raise: memory must never be able to fail a turn.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from brain.classifier.continuation import continuation_hint
from utils.logger import get_logger

logger = get_logger(__name__)

_IGNORE_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "node_modules", ".pytest_cache", ".mypy_cache",
    ".agent_memory", ".vault", "graphify-out", ".aislop", "dist", "build", ".idea", ".vscode", ".ruff_cache", ".cache", ".tox", ".nox",
    "env", ".next", ".nuxt", ".output", "target", "out", "site-packages", ".yarn", ".pnpm",
    "vendor", "storage", "bower_components", "Pods", ".gradle", ".m2", ".bundle", ".terraform",
    ".turbo", ".parcel-cache", "htmlcov", ".coverage", "coverage",
}
_IGNORE_DIR_PREFIXES = (".venv", "venv_", ".env_", ".cache")
_IGNORE_FILES = {".DS_Store", "Thumbs.db", ".gitkeep"}
_TEXT_EXT = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".yaml", ".yml", ".toml", ".md", ".txt", ".csv",
    ".html", ".css", ".xml", ".sql", ".sh", ".env", ".ini", ".cfg", ".rs", ".go", ".java", ".c",
    ".cpp", ".h", ".rb", ".php", ".dart", ".lua", ".r",
}
_LANGUAGES = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".jsx": "jsx", ".tsx": "tsx",
    ".go": "go", ".rs": "rust", ".java": "java", ".rb": "ruby", ".php": "php", ".html": "html",
    ".css": "css", ".sql": "sql", ".sh": "bash", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".md": "markdown", ".toml": "toml",
}
_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "have", "will", "yang", "dan", "untuk",
    "dengan", "dari", "ini", "itu", "ada", "bisa", "tidak", "juga", "saya", "kamu", "kita", "atau",
    "pada", "buat", "buatkan", "tolong", "mohon", "please", "make", "build", "add", "file", "code",
    "kode", "function", "class", "def", "return",
}
_LIMITS = {"progress": 20, "decisions": 20, "facts": 30, "errors": 10, "model_history": 10}
_DECISION_PREFIXES = ("decision:", "keputusan:", "we will", "kita akan")
_FACT_PREFIXES = ("fact:", "fakta:", "catatan:", "note:")

# The agent can read any file with a tool, so the prompt only needs a head start.
_MAX_FILE_BYTES = 4_000
_MAX_TOTAL_BYTES = 12_000
_MAX_LISTED_FILES = 60
_SUMMARY_TOKENS = 400
_MAX_FILE_EDITS = 50
_HALF_LIFE_SECONDS = 3 * 24 * 3600
_ERROR_HALF_LIFE_SECONDS = 12 * 3600
_DEDUP_SIMILARITY = 0.82
_TAIL_CODE_LINES = 80
_TAIL_TEXT_CHARS = 3_000
_LAST_RESPONSE_CHARS = 2_000

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


@dataclass
class ScoredFile:
    """A file ranked by how likely it matters to a request."""

    rel_path: str
    score: float
    reason: str
    status: str = "unchanged"
    size: int = 0


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", (text or "").lower())
    return {word for word in words if word not in _STOPWORDS}


def _similar(a: set[str], b: set[str]) -> bool:
    return bool(a and b) and len(a & b) / len(a | b) >= _DEDUP_SIMILARITY


def _decay(timestamp: float, half_life: float, now: float) -> float:
    return math.pow(0.5, max(0.0, now - timestamp) / half_life)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _last_code_block(text: str) -> tuple[str, str, str, bool] | None:
    """The final fenced block as ``(file, language, code, is_closed)``."""
    pattern = re.compile(r"```(?P<header>[^\r\n]*)\r?\n(?P<code>.*?)(?P<close>```|$)", re.DOTALL)
    last = None
    for match in pattern.finditer(text):
        if match.group("code").strip():
            last = match
    if last is None:
        return None
    header = last.group("header").strip()
    prefix, _, rest = header.partition(":")
    if prefix.lower() in {"file", "replace"}:
        file_path, language = rest.strip(), ""
    else:
        file_path, language = "", header.split()[0] if header else ""
    if not file_path:
        markers = re.findall(r"(?im)^\s*###\s*File:\s*([^\n`]+)", text[: last.start()][-1200:])
        if markers:
            file_path = markers[-1].strip()
    return file_path, language, last.group("code"), bool(last.group("close"))


def _last_scope(lines: list[str]) -> str:
    patterns = (
        (r"^\s*class\s+([A-Za-z_]\w*)", "class"),
        (r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)", "def"),
        (r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", "function"),
    )
    for line in reversed(lines):
        for pattern, kind in patterns:
            match = re.search(pattern, line)
            if match:
                return f"{kind} {match.group(1)}"
    return ""


class ProjectMemory:
    """Per-workspace memory: read before a turn, written after it."""

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)
        self._dir = self.workspace / ".agent_memory"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self._dir / "state.json"
        self._index_path = self._dir / "file_index.json"
        self._cutoff_path = self._dir / "cutoff.json"
        self._last_response_path = self._dir / "last_response.json"
        with _locks_guard:
            self._lock = _locks.setdefault(str(self._dir.resolve()), threading.Lock())

    @classmethod
    def for_path(cls, path: str | Path | None) -> ProjectMemory | None:
        """Open memory for a directory, or None when there is no usable one."""
        if not path:
            return None
        try:
            directory = Path(path)
            return cls(directory) if directory.is_dir() else None
        except OSError as exc:
            logger.warning(f"[project_memory] unavailable for {path}: {exc}")
            return None

    # ── State ────────────────────────────────────────────────────────

    @property
    def state(self) -> dict[str, Any]:
        """The latest saved state. Same file format as earlier versions."""
        return _read_json(self._state_path) or {
            "project": self.workspace.name,
            "created": time.time(),
            "updated": time.time(),
            "progress": [],
            "decisions": [],
            "files": {},
            "facts": [],
            "model_history": [],
            "errors": [],
            "turn_count": 0,
        }

    def _update(self, change: Callable[[dict[str, Any]], None]) -> None:
        """Apply a change to the latest state, so concurrent writers keep their work."""
        with self._lock:
            state = self.state
            change(state)
            for key, limit in _LIMITS.items():
                if len(state.get(key, [])) > limit:
                    state[key] = state[key][-limit:]
            files = state.get("files", {})
            if len(files) > _MAX_FILE_EDITS:
                newest = sorted(files.items(), key=lambda item: item[1].get("ts", 0))[-_MAX_FILE_EDITS:]
                state["files"] = dict(newest)
            state["updated"] = time.time()
            _write_json(self._state_path, state)

    # ── Files ────────────────────────────────────────────────────────

    def scan_files(self) -> dict[str, dict[str, Any]]:
        """Every file with its status against the last scan: new, modified, unchanged, deleted."""
        index = _read_json(self._index_path) or {}
        current: dict[str, dict[str, Any]] = {}
        for root, dirs, names in os.walk(self.workspace):
            dirs[:] = sorted(
                d for d in dirs
                if d not in _IGNORE_DIRS
                and not d.endswith(".egg-info")
                and not d.startswith(_IGNORE_DIR_PREFIXES)
            )
            for name in sorted(names):
                if name in _IGNORE_FILES:
                    continue
                path = Path(root) / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                relative = str(path.relative_to(self.workspace))
                previous = index.get(relative, {})
                known_hash = previous.get("hash", "")
                unchanged_on_disk = previous.get("size") == stat.st_size and previous.get("mtime") == stat.st_mtime_ns
                digest = known_hash if known_hash and unchanged_on_disk else self._hash(path)
                status = "new" if not known_hash else "modified" if digest != known_hash else "unchanged"
                current[relative] = {
                    "size": stat.st_size,
                    "mtime": stat.st_mtime_ns,
                    "hash": digest,
                    "ext": path.suffix.lower(),
                    "changed": status != "unchanged",
                    "status": status,
                }
        new_index = {r: {k: i[k] for k in ("hash", "size", "mtime")} for r, i in current.items()}
        if new_index != index:
            _write_json(self._index_path, new_index)
        for relative in index:
            if relative not in current:
                current[relative] = {"status": "deleted", "changed": True, "size": 0, "hash": "", "ext": ""}
        return current

    @staticmethod
    def _hash(path: Path) -> str:
        try:
            return hashlib.md5(path.read_bytes()).hexdigest()[:12]
        except OSError:
            return ""

    def score_files(
        self, query: str = "", *, limit: int = 20, prefer_ext: Iterable[str] | None = None
    ) -> list[ScoredFile]:
        """Rank files by recent change, past edits, and overlap with the query."""
        files = self.scan_files()
        edited = self.state.get("files", {})
        keywords = _tokenize(query)
        prefer = {ext.lower() for ext in prefer_ext} if prefer_ext else None
        recent_dirs = {str(Path(r).parent) for r, i in files.items() if i.get("changed")}

        scored: list[ScoredFile] = []
        for relative, info in files.items():
            extension = str(info.get("ext", "")).lower()
            if info.get("status") == "deleted" or extension not in _TEXT_EXT:
                continue
            score = 0.0
            reasons: list[str] = []
            if info.get("changed"):
                score += 2.0
                reasons.append("changed")
            overlap = keywords & _tokenize(relative.replace("/", " ").replace(".", " "))
            if overlap:
                score += 2.5 * len(overlap)
                reasons.append(f"path:{','.join(sorted(overlap)[:3])}")
            elif keywords and info.get("size", 0) < 200_000:
                try:
                    body = (self.workspace / relative).read_text(encoding="utf-8", errors="replace")[:20_000]
                except OSError:
                    body = ""
                in_body = keywords & _tokenize(body)
                if in_body:
                    score += 1.5 * len(in_body)
                    reasons.append(f"body:{','.join(sorted(in_body)[:3])}")
            if relative in edited:
                score += 1.5
                reasons.append("edited-before")
            if str(Path(relative).parent) in recent_dirs:
                score += 0.8
                reasons.append("near-recent")
            if prefer and extension in prefer:
                score += 1.0
                reasons.append(f"ext:{extension}")
            if score > 0:
                scored.append(ScoredFile(
                    rel_path=relative,
                    score=round(score, 2),
                    reason="; ".join(reasons),
                    status=str(info.get("status", "unchanged")),
                    size=int(info.get("size", 0)),
                ))
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]

    # ── Reading memory into a prompt ─────────────────────────────────

    def summary(self, token_budget: int = _SUMMARY_TOKENS, query: str = "") -> str:
        """Compact digest, most relevant to the query first, trimmed to a budget."""
        state = self.state
        now = time.time()
        lines: list[str] = []
        wanted = _tokenize(query)

        def ranked(kind: str, key: str, half_life: float) -> list[dict[str, Any]]:
            def weight(item: dict[str, Any]) -> float:
                # An old decision about what is being asked beats a fresh one about something else.
                overlap = len(wanted & _tokenize(str(item.get(key, ""))))
                return _decay(item.get("ts", 0), half_life, now) + overlap
            return sorted(state.get(kind, []), key=weight, reverse=True)

        if state.get("progress"):
            lines.append("Progress:")
            for item in state["progress"][-8:]:
                detail = f" - {item['detail'][:80]}" if item.get("detail") else ""
                lines.append(f"  {item.get('phase', '?')}: {item.get('status', '?')}{detail}")
        if state.get("decisions"):
            lines.append("Decisions:")
            for item in ranked("decisions", "what", _HALF_LIFE_SECONDS)[:5]:
                why = f" ({item['why'][:60]})" if item.get("why") else ""
                lines.append(f"  * {item.get('what', '?')[:100]}{why}")
        if state.get("facts"):
            lines.append("Facts:")
            lines.extend(f"  * {item.get('fact', '?')[:120]}" for item in ranked("facts", "fact", _HALF_LIFE_SECONDS)[:6])
        if state.get("errors"):
            lines.append("Recent errors:")
            lines.extend(f"  ! {item.get('err', '?')[:90]}" for item in ranked("errors", "err", _ERROR_HALF_LIFE_SECONDS)[:3])
        if state.get("files"):
            lines.append("File edits:")
            ranked_files = sorted(state["files"].items(), key=lambda kv: kv[1].get("ts", 0), reverse=True)
            for relative, info in ranked_files[:8]:
                note = f" - {info['summary'][:60]}" if info.get("summary") else ""
                lines.append(f"  [{info.get('action', '?')}] {relative}{note}")
        if not lines:
            return ""

        while len(lines) > 1 and len("\n".join(lines)) // 4 > token_budget:
            lines.pop()
        return "\n".join(["[MEMORY]", *lines, "[/MEMORY]"])

    def _workspace_context(self, user_input: str) -> str:
        """Files in the project, with the contents of those likely to matter."""
        files = {r: i for r, i in self.scan_files().items() if i["status"] != "deleted"}
        if not files:
            return ""

        ordered = sorted(files.items(), key=lambda item: (not item[1]["changed"], item[0]))
        parts = [f"[WORKSPACE: {self.workspace.name}]", "Files:"]
        for relative, info in ordered[:_MAX_LISTED_FILES]:
            marker = {"new": "(new) ", "modified": "(changed) "}.get(info["status"], "")
            parts.append(f"  {marker}{relative} ({self._format_size(info['size'])})")
        if len(ordered) > _MAX_LISTED_FILES:
            parts.append(f"  ... (+{len(ordered) - _MAX_LISTED_FILES} more)")

        budget = _MAX_TOTAL_BYTES
        for relative, info in sorted(files.items()):
            if budget <= 0:
                break
            if info["ext"] not in _TEXT_EXT or info["size"] > _MAX_FILE_BYTES:
                continue
            if not info["changed"] and not self._mentioned(relative, user_input):
                continue
            try:
                content = (self.workspace / relative).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            parts.extend((f"\n--- {relative} ---", content, "---"))
            budget -= len(content)
        return "\n".join(parts)

    @staticmethod
    def _mentioned(relative: str, text: str) -> bool:
        """Whether a request names this file. Whole words, so a.py is not "a"."""
        if not text:
            return False
        path = Path(relative)
        candidates = [path.name, path.stem] if len(path.stem) >= 3 else [path.name]
        return any(
            re.search(rf"(?<![\w.-]){re.escape(c.lower())}(?![\w-])", text.lower()) for c in candidates
        )

    @staticmethod
    def _format_size(size: int) -> str:
        if size >= 1_048_576:
            return f"{size / 1_048_576:.1f}MB"
        return f"{size / 1024:.1f}KB" if size >= 1024 else f"{size}B"

    def build_input(self, user_input: str, is_continue: bool) -> str:
        """The user's message with what this project remembers around it.

        A message in an empty project is returned untouched, so a greeting costs
        nothing and the model is not told about a workspace with nothing in it.
        """
        try:
            self._update(lambda state: state.update(turn_count=state.get("turn_count", 0) + 1))
            if is_continue:
                return self._continue_input(user_input)
            parts = [part for part in (self.summary(query=user_input), self._workspace_context(user_input)) if part]
            if not parts:
                return user_input
            return "\n\n".join([*parts, f"[USER REQUEST]\n{user_input}"])
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[project_memory] context failed: {exc}")
            return user_input

    def _continue_input(self, user_input: str) -> str:
        """Resume exactly where output stopped, or else from the last answer."""
        cutoff = self._cutoff_context()
        if cutoff:
            return f"{cutoff}\n\n[USER REQUEST]\n{user_input}"
        parts = [part for part in (self.summary(), self._last_response_context()) if part]
        return "\n\n".join([*parts, f"[USER REQUEST]\n{user_input} {continuation_hint()}"])

    def _cutoff_context(self) -> str:
        data = _read_json(self._cutoff_path)
        tail = str((data or {}).get("tail", ""))
        if not data or not tail.strip():
            return ""
        parts = ["[CONTINUE]"]
        if data.get("file"):
            parts.append(f"File: {data['file']}")
        if data.get("scope"):
            parts.append(f"Scope: {data['scope']}")
        parts.extend(("Your previous output was cut off. It ended with:", "----", tail, "----"))
        if data.get("in_code_block"):
            parts.append(
                "It stopped inside a code block. Continue from the very next line. "
                "Do not open a new fence or repeat any code."
            )
        else:
            parts.append("Continue from exactly that point. Do not repeat what is already written.")
        parts.append("[/CONTINUE]")
        return "\n".join(parts)

    def _last_response_context(self) -> str:
        data = _read_json(self._last_response_path)
        tail = str((data or {}).get("tail", ""))
        if not tail.strip():
            return ""
        return f"[PREVIOUS OUTPUT]\nYour last answer ended with:\n----\n{tail}\n----\nDo not repeat it.\n[/PREVIOUS OUTPUT]"

    # ── Writing memory after a turn ──────────────────────────────────

    def finish_turn(
        self,
        answer: str,
        model_id: str,
        changed_files: list[Path],
        truncated: bool,
    ) -> None:
        """Record a completed turn."""
        try:
            now = time.time()
            decisions = self._prefixed_lines(answer, _DECISION_PREFIXES)
            facts = self._prefixed_lines(answer, _FACT_PREFIXES)

            def change(state: dict[str, Any]) -> None:
                state["model_history"].append({"model": model_id, "ts": now})
                for path in changed_files:
                    state["files"][self._relative(path)] = {"action": "edited", "summary": "", "ts": now}
                for what in decisions:
                    self._merge(state, "decisions", "what", what)
                for fact in facts:
                    self._merge(state, "facts", "fact", fact)

            self._update(change)
            if answer.strip():
                _write_json(self._last_response_path, {
                    "model": model_id, "ts": now, "tail": answer[-_LAST_RESPONSE_CHARS:],
                })
            if truncated:
                self._save_cutoff(answer, model_id)
            else:
                self._cutoff_path.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[project_memory] finish_turn failed: {exc}")

    def _save_cutoff(self, answer: str, model_id: str) -> None:
        block = _last_code_block(answer)
        # A closed block means the cut fell in the prose after it.
        if block and not block[3]:
            file_path, language, code, _ = block
            lines = code.splitlines()
            data = {
                "file": file_path,
                "lang": language or _LANGUAGES.get(Path(file_path).suffix.lower(), ""),
                "tail": "\n".join(lines[-_TAIL_CODE_LINES:]),
                "scope": _last_scope(lines),
                "in_code_block": True,
            }
        else:
            data = {"file": "", "lang": "", "tail": answer[-_TAIL_TEXT_CHARS:], "scope": "", "in_code_block": False}
        _write_json(self._cutoff_path, {**data, "model": model_id, "ts": time.time()})

    def has_cutoff(self) -> bool:
        return self._cutoff_path.exists()

    @staticmethod
    def _prefixed_lines(text: str, prefixes: tuple[str, ...]) -> list[str]:
        found = []
        for line in text.splitlines():
            stripped = line.strip()
            for prefix in prefixes:
                if stripped.lower().startswith(prefix):
                    value = stripped[len(prefix):].strip(" :-")
                    if value:
                        found.append(value)
                    break
        return found

    @staticmethod
    def _merge(state: dict[str, Any], kind: str, key: str, text: str, **extra: Any) -> bool:
        """Add an entry unless a near-duplicate exists, which is refreshed instead."""
        tokens = _tokenize(text)
        for existing in state[kind]:
            if _similar(tokens, _tokenize(existing.get(key, ""))):
                existing["ts"] = time.time()
                return False
        state[kind].append({key: text, **extra, "ts": time.time()})
        return True

    def remember(self, kind: str, text: str, why: str = "") -> bool | None:
        """Save something on purpose. Returns whether it was new, or None for an unknown kind."""
        added = [False]

        def change(state: dict[str, Any]) -> None:
            if kind == "decision":
                added[0] = self._merge(state, "decisions", "what", text, why=why)
            elif kind == "fact":
                added[0] = self._merge(state, "facts", "fact", text)
            elif kind == "error":
                added[0] = self._merge(state, "errors", "err", text[:200])
            else:
                for item in state["progress"]:
                    if item.get("phase") == text:
                        item.update(status="done", detail=why, ts=time.time())
                        break
                else:
                    state["progress"].append({"phase": text, "status": "done", "detail": why, "ts": time.time()})
                added[0] = True

        if kind not in {"decision", "fact", "error", "progress"}:
            return None
        self._update(change)
        return added[0]

    def forget(self, text: str) -> int:
        """Remove decisions, facts, and errors matching the text. Returns how many.

        Memory that turned out to be wrong must be removable, or it keeps
        misleading every later turn.
        """
        needle = text.strip().lower()
        tokens = _tokenize(text)
        removed = [0]

        def matches(value: str) -> bool:
            other = _tokenize(value)
            overlap = len(tokens & other) / len(tokens | other) if tokens and other else 0.0
            return needle in value.lower() or overlap >= 0.5

        def change(state: dict[str, Any]) -> None:
            for kind, key in (("decisions", "what"), ("facts", "fact"), ("errors", "err")):
                kept = [item for item in state.get(kind, []) if not matches(str(item.get(key, "")))]
                removed[0] += len(state.get(kind, [])) - len(kept)
                state[kind] = kept

        if needle:
            self._update(change)
        return removed[0]

    def record_file_actions(self, actions: list[str]) -> None:
        """Record files written from fenced blocks in a reply (CLI workspaces)."""
        try:
            def change(state: dict[str, Any]) -> None:
                for action in actions:
                    _, _, path = action.partition(": ")
                    if path:
                        state["files"][path.strip()] = {
                            "action": "created" if "💾" in action else "edited",
                            "summary": action,
                            "ts": time.time(),
                        }

            self._update(change)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[project_memory] record_file_actions failed: {exc}")

    def record_error(self, model_id: str, error: str) -> None:
        try:
            self.remember("error", f"{model_id}: {error[:150]}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[project_memory] record_error failed: {exc}")

    def _relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.workspace.resolve()))
        except ValueError:
            return str(path)
