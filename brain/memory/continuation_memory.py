"""Compact activity and truncated-code continuation memory."""

from __future__ import annotations

import contextlib
import json
import re
import time
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

_MAX_FILES = 12
_MAX_TAIL_LINES = 80
_MAX_SUMMARY_CHARS = 160
_MAX_CONTEXT_CHARS = 12_000
_MAX_PATH_CHARS = 240


class ContinuationMemory:
    """Persist compact activity and code-continuation state for a workspace."""

    def __init__(self, workspace_path: str | Path) -> None:
        self._workspace = Path(workspace_path)
        self._directory = self._workspace / ".agent_memory" / "continuation"
        self._directory.mkdir(parents=True, exist_ok=True)

        self._activity_path = self._directory / "last_activity.json"
        self._buffer_path = self._directory / "code_buffer.json"

    def record_activity(
        self,
        action: str,
        target: str = "",
        summary: str = "",
        model: str = "",
        code_state: str = "complete",
        files_changed: list[str] | None = None,
    ) -> None:
        """Save one compact activity record; storage failures are non-fatal."""
        files: list[str] = []
        seen: set[str] = set()

        for item in files_changed or []:
            path = self._clean_path(item)
            if path and path not in seen:
                seen.add(path)
                files.append(path)

        self._atomic_write(
            self._activity_path,
            {
                "action": str(action)[:32],
                "target": self._clean_path(target),
                "summary": self._compact(summary, _MAX_SUMMARY_CHARS),
                "model": str(model)[:120],
                "code_state": str(code_state)[:20],
                "files": files[-_MAX_FILES:],
                "ts": time.time(),
            },
        )

    def get_last_activity(self) -> dict[str, Any] | None:
        """Return the last activity record, or ``None`` if unavailable."""
        data = self._read_json(self._activity_path)
        return data if isinstance(data, dict) else None

    def save_code_buffer(
        self,
        file_path: str,
        partial_code: str,
        language: str = "",
        context_before: str = "",
        total_expected_lines: int = 0,
        written_lines: int = 0,
        in_code_block: bool = False,
    ) -> None:
        """Save only the tail needed to continue a truncated response."""
        code = str(partial_code or "")
        lines = code.splitlines()
        tail_lines = lines[-_MAX_TAIL_LINES:]
        path = self._clean_path(file_path)

        try:
            expected_lines = max(0, int(total_expected_lines or 0))
            written = max(0, int(written_lines or len(lines)))
        except (TypeError, ValueError):
            expected_lines = 0
            written = len(lines)

        self._atomic_write(
            self._buffer_path,
            {
                "file": path,
                "lang": language or self._detect_language(path),
                "tail": "\n".join(tail_lines),
                "tail_start_line": max(0, len(lines) - len(tail_lines)),
                "total_written": written,
                "total_expected": expected_lines,
                "last_scope": self._detect_last_scope(lines),
                "context_hint": self._compact(context_before, 200),
                "in_code_block": bool(in_code_block),
                "ts": time.time(),
            },
        )

    def get_code_buffer(self) -> dict[str, Any] | None:
        """Return a valid continuation buffer, ignoring corrupt/empty data."""
        data = self._read_json(self._buffer_path)
        if not isinstance(data, dict):
            return None
        if not str(data.get("tail", "")).strip():
            return None
        return data

    def has_code_buffer(self) -> bool:
        """Return whether a usable continuation buffer exists."""
        return self.get_code_buffer() is not None

    def clear_code_buffer(self) -> None:
        """Delete the continuation buffer after successful completion."""
        try:
            self._buffer_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("[continuation] failed to clear code buffer: %s", exc)

    def build_context(self, for_continue: bool = False) -> str:
        """Build a bounded context string for normal or continuation prompts."""
        parts: list[str] = []
        activity = self.get_last_activity()

        if activity:
            line = f"[LAST] {activity.get('action', 'work')}"
            target = str(activity.get("target", ""))
            summary = str(activity.get("summary", ""))
            state = str(activity.get("code_state", "complete"))

            if target:
                line += f" {target}"
            if summary:
                line += f" — {summary}"
            if state != "complete":
                line += f" ({state})"
            parts.append(line)

            files = activity.get("files", [])
            if isinstance(files, list) and files:
                parts.append(
                    "[FILES] " + ", ".join(str(item) for item in files[-8:])
                )

        if for_continue:
            buffer = self.get_code_buffer()
            if buffer:
                file_path = str(buffer.get("file", "unknown"))
                language = str(buffer.get("lang", ""))
                written = self._safe_int(buffer.get("total_written"))
                expected = self._safe_int(buffer.get("total_expected"))
                scope = str(buffer.get("last_scope", ""))
                tail = str(buffer.get("tail", ""))

                parts.extend(("[CONTINUE_CODE]", f"File: {file_path} ({language})"))
                parts.append(
                    f"Progress: {written}/{expected} lines"
                    if expected
                    else f"Progress: {written} lines"
                )

                if scope:
                    parts.append(f"Scope: {scope}")
                if tail:
                    parts.extend(("LAST_CODE:", f"```{language}", tail, "```"))

                if buffer.get("in_code_block"):
                    parts.append(
                        "Output terputus di tengah code block. "
                        "Lanjutkan tepat setelah baris terakhir. "
                        "Jangan membuka fence baru dan jangan mengulang kode."
                    )
                else:
                    parts.append(
                        "Lanjutkan tepat dari baris terakhir. "
                        "Jangan mengulang kode yang sudah ada."
                    )
                parts.append("[/CONTINUE_CODE]")

        return "\n".join(parts)[:_MAX_CONTEXT_CHARS]

    def analyze_and_save(
        self,
        response_text: str,
        model_id: str = "",
        is_truncated: bool = False,
    ) -> None:
        """Extract compact activity and, when needed, a continuation buffer."""
        if not response_text or not response_text.strip():
            return

        files = self._detect_files(response_text)
        summary = self._extract_summary(response_text)
        self.record_activity(
            action=self._detect_action(response_text),
            target=files[0] if files else "",
            summary=summary,
            model=model_id,
            code_state="truncated" if is_truncated else "complete",
            files_changed=files,
        )

        if not is_truncated:
            self.clear_code_buffer()
            return

        block = self._extract_last_code_block(response_text)
        if not block:
            return

        file_path, language, code, is_closed = block
        self.save_code_buffer(
            file_path=file_path or (files[0] if files else ""),
            partial_code=code,
            language=language,
            context_before=summary,
            in_code_block=not is_closed,
        )

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _atomic_write(path: Path, data: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            temporary.write_text(
                json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(path)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("[continuation] failed to save %s: %s", path.name, exc)
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _compact(value: str, limit: int) -> str:
        return " ".join(str(value or "").split())[:limit]

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _clean_path(value: Any) -> str:
        path = str(value or "").strip().strip("`* ")
        if not path or len(path) > _MAX_PATH_CHARS:
            return ""

        invalid_markers = (
            "(?P<",
            "[^",
            "\\n",
            "<path>",
            '",',
            "before[-",
            "text):",
        )
        if any(marker in path for marker in invalid_markers):
            return ""
        if path.startswith(("http://", "https://")):
            return ""
        return path

    @staticmethod
    def _detect_action(text: str) -> str:
        sample = text[:2500].lower()
        if "```" in sample or "### file:" in sample:
            if any(word in sample for word in ("bug", "error", "traceback", "fix", "perbaiki")):
                return "debugging"
            if "refactor" in sample:
                return "refactoring"
            return "coding"
        if any(word in sample for word in ("plan", "rencana", "langkah")):
            return "planning"
        if any(word in sample for word in ("penjelasan", "explain", "because", "karena")):
            return "explaining"
        return "chatting"

    @classmethod
    def _detect_files(cls, text: str) -> list[str]:
        patterns = (
            r"(?im)^\s*###\s*File:\s*([^\n`]+)",
            r"(?im)^\s*```(?:file|replace):([^\n`]+)",
            r"(?im)^\s*💾\s*Saved:\s*([^\n(]+)",
            r"(?im)^\s*✏️\s*Replaced(?:\s+in)?:\s*([^\n(]+)",
        )
        result: list[str] = []
        seen: set[str] = set()
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                path = cls._clean_path(match.group(1))
                if path and path not in seen:
                    seen.add(path)
                    result.append(path)
                if len(result) >= _MAX_FILES:
                    return result
        return result

    @staticmethod
    def _extract_summary(text: str) -> str:
        for raw_line in text.splitlines()[:40]:
            line = raw_line.strip().lstrip("#").strip()
            if not line or line.startswith(("```", "💾 Saved:", "✏️", "<<<<<<<", "=======")):
                continue
            if len(line) >= 8:
                return ContinuationMemory._compact(line, _MAX_SUMMARY_CHARS)
        return ""

    @staticmethod
    def _detect_last_scope(lines: list[str]) -> str:
        patterns = (
            (re.compile(r"^\s*class\s+([A-Za-z_]\w*)"), "class"),
            (re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)"), "def"),
            (re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"), "function"),
        )
        for line in reversed(lines):
            for pattern, kind in patterns:
                match = pattern.search(line)
                if match:
                    return f"{kind} {match.group(1)}"
        return ""

    @staticmethod
    def _detect_language(file_path: str) -> str:
        languages = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".jsx": "jsx", ".tsx": "tsx", ".go": "go", ".rs": "rust",
            ".java": "java", ".rb": "ruby", ".php": "php", ".html": "html",
            ".css": "css", ".sql": "sql", ".sh": "bash", ".json": "json",
            ".yaml": "yaml", ".yml": "yaml", ".md": "markdown", ".toml": "toml",
        }
        return languages.get(Path(file_path).suffix.lower(), "")

    @classmethod
    def _extract_last_code_block(
        cls,
        text: str,
    ) -> tuple[str, str, str, bool] | None:
        """Extract the last complete block or an unterminated final block."""
        pattern = re.compile(
            r"```(?P<header>[^\r\n]*)\r?\n(?P<code>.*?)(?P<close>```|$)",
            re.DOTALL,
        )
        candidates: list[tuple[int, str, str, str, bool]] = []

        for match in pattern.finditer(text):
            header = match.group("header").strip()
            file_path = ""
            language = ""
            lowered = header.lower()

            if lowered.startswith("replace:"):
                file_path = header[8:].strip()
            elif lowered.startswith("file:"):
                file_path = header[5:].strip()
            elif header:
                language = header.split()[0]

            if not file_path:
                before = text[:match.start()][-1200:]
                markers = list(re.finditer(r"(?im)^\s*###\s*File:\s*([^\n`]+)", before))
                if markers:
                    file_path = markers[-1].group(1).strip()

            code = match.group("code")
            if code.strip():
                candidates.append(
                    (
                        match.start(),
                        cls._clean_path(file_path),
                        language,
                        code,
                        bool(match.group("close")),
                    )
                )

        if not candidates:
            return None

        _, file_path, language, code, is_closed = max(candidates, key=lambda item: item[0])
        return file_path, language, code, is_closed
