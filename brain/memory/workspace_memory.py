"""
brain.memory.workspace_memory — Smart workspace memory + graphify integration.

Gabungan memory + file tracking + truncation buffer yang tersimpan
DI DALAM folder workspace (.agent_memory/).

Fitur utama:
1. FILE HASHING — Track hash setiap file → kirim hanya yg berubah ke AI
2. TRUNCATION BUFFER — Simpan output terakhir yg terpotong → continue tepat
3. COMPACT MEMORY — Progress, decisions, file state → format super hemat token
4. SMART CONTEXT — Hanya kirim file yg relevan, bukan semua

Struktur di workspace:
  workspace/nama-proyek/
    .agent_memory/
      state.json       → memory utama (progress, decisions, files, dll)
      truncated.txt    → buffer output terpotong terakhir
      file_index.json  → hash semua file (detect perubahan)

Format state.json dibuat seminimal mungkin:
- Setiap entry max 1 baris
- Tidak ada redundansi
- Token cost ~200-500 tokens untuk seluruh context
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, cast

from utils.logger import get_logger

logger = get_logger(__name__)

# Dirs/files to ignore when scanning workspace
_IGNORE_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "node_modules",
    ".pytest_cache", ".mypy_cache", ".agent_memory",
    "dist", "build", ".egg-info", ".idea", ".vscode",
    # Cache & tools
    ".ruff_cache", ".ruff", ".cache", ".tox", ".nox",
    # Virtual envs (any name)
    ".venv_ruff", "env", ".env_*",
    # Build artifacts
    ".next", ".nuxt", ".output", "target", "out",
    # Package dirs & framework storage/vendor
    "site-packages", ".yarn", ".pnpm", "vendor", "storage",
    "bower_components", "Pods", ".gradle", ".m2", ".bundle",
    ".terraform", ".turbo", ".parcel-cache",
    # Coverage & test
    "htmlcov", ".coverage", "coverage",
}

# Prefix patterns — any dir starting with these is skipped
_IGNORE_DIR_PREFIXES = (".venv", "venv_", ".env_", ".cache")
_IGNORE_FILES = {".DS_Store", "Thumbs.db", ".gitkeep"}

# Text extensions AI can read
_TEXT_EXT = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".yaml", ".yml",
    ".toml", ".md", ".txt", ".csv", ".html", ".css", ".xml", ".sql",
    ".sh", ".env", ".ini", ".cfg", ".rs", ".go", ".java", ".c",
    ".cpp", ".h", ".rb", ".php", ".dart", ".lua", ".r", ".R",
}


class WorkspaceMemory:
    """Smart workspace memory — lives inside the workspace folder.

    Menyimpan semua state di <workspace>/.agent_memory/ sehingga
    memory ikut workspace project dan survive restart/model switch.

    Usage:
        wm = WorkspaceMemory(workspace_path)

        # Build smart context (hemat token)
        ctx = wm.build_smart_context(user_input)

        # Save truncated output for continue
        wm.save_truncation(partial_content, messages)

        # Get continue context when user says "continue"
        ctx = wm.get_continue_context()

        # Record progress
        wm.record_progress("Phase 1", "done", "Created all modules")

        # Record file edit
        wm.record_file_edit("app.py", "edited", "Added login route")
    """

    def __init__(self, workspace_path: str | Path) -> None:
        self._ws = Path(workspace_path)
        self._mem_dir = self._ws / ".agent_memory"
        self._mem_dir.mkdir(parents=True, exist_ok=True)

        self._state_path = self._mem_dir / "state.json"
        self._trunc_path = self._mem_dir / "truncated.txt"
        self._trunc_meta_path = self._mem_dir / "truncated_meta.json"
        self._index_path = self._mem_dir / "file_index.json"

        self._state = self._load_state()
        self._file_index = self._load_file_index()

    # ─── State Management ────────────────────────────────────────────

    def _load_state(self) -> dict[str, Any]:
        if self._state_path.exists():
            try:
                data = json.loads(self._state_path.read_text("utf-8"))
                if isinstance(data, dict):
                    return cast(dict[str, Any], data)
            except Exception:
                pass
        return {
            "project": self._ws.name,
            "created": time.time(),
            "updated": time.time(),
            "progress": [],      # [{phase, status, detail, ts}]
            "decisions": [],     # [{what, why, ts}]
            "files": {},         # {rel_path: {action, hash, lines, summary, ts}}
            "facts": [],         # [{fact, ts}]
            "model_history": [], # [{model, ts}]
            "errors": [],        # [{err, ts}]
            "turn_count": 0,
        }

    def _save_state(self) -> None:
        self._state["updated"] = time.time()
        self._state_path.write_text(
            json.dumps(self._state, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    def _load_file_index(self) -> dict[str, Any]:
        if self._index_path.exists():
            try:
                data = json.loads(self._index_path.read_text("utf-8"))
                if isinstance(data, dict):
                    return cast(dict[str, Any], data)
            except Exception:
                pass
        return {}

    def _save_file_index(self) -> None:
        self._index_path.write_text(
            json.dumps(self._file_index, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    # ─── File Hashing & Tracking ─────────────────────────────────────

    def _hash_file(self, path: Path) -> str:
        """Quick MD5 hash (first 12 chars)."""
        try:
            return hashlib.md5(path.read_bytes()).hexdigest()[:12]
        except Exception:
            return ""

    def scan_workspace_files(self) -> dict[str, dict[str, Any]]:
        """Scan all files, detect changes vs last known state.

        Returns:
            {rel_path: {size, hash, ext, changed: bool, status: "new"|"modified"|"unchanged"|"deleted"}}
        """
        current: dict[str, dict[str, Any]] = {}

        # os.walk with in-place pruning: ignored dirs (.venv, node_modules, …)
        # are never descended into, unlike rglob which walks everything first.
        for root, dirs, filenames in os.walk(self._ws):
            dirs[:] = sorted(
                d for d in dirs
                if d not in _IGNORE_DIRS
                and not d.endswith(".egg-info")
                and not d.startswith(_IGNORE_DIR_PREFIXES)
            )
            root_path = Path(root)
            for name in sorted(filenames):
                if name in _IGNORE_FILES:
                    continue
                p = root_path / name
                try:
                    st = p.stat()
                except OSError:
                    continue
                rel = str(p.relative_to(self._ws))
                prev = self._file_index.get(rel, {})
                prev_hash = prev.get("hash", "")

                # Skip re-hashing when size+mtime are unchanged (cheap stat check)
                if (
                    prev_hash
                    and prev.get("size") == st.st_size
                    and prev.get("mtime") == st.st_mtime_ns
                ):
                    h = prev_hash
                else:
                    h = self._hash_file(p)

                if not prev_hash:
                    status = "new"
                elif h != prev_hash:
                    status = "modified"
                else:
                    status = "unchanged"

                current[rel] = {
                    "size": st.st_size,
                    "mtime": st.st_mtime_ns,
                    "hash": h,
                    "ext": p.suffix.lower(),
                    "changed": status != "unchanged",
                    "status": status,
                }

        # Detect deleted
        for rel in self._file_index:
            if rel not in current:
                current[rel] = {"status": "deleted", "changed": True, "size": 0, "hash": "", "ext": ""}

        # Update index
        new_index = {
            rel: {"hash": info["hash"], "size": info["size"], "mtime": info["mtime"]}
            for rel, info in current.items()
            if info["status"] != "deleted"
        }
        # Only write file_index.json when something actually changed
        if new_index != self._file_index:
            self._file_index = new_index
            self._save_file_index()

        return current

    # ─── Smart Context Builder ───────────────────────────────────────

    def build_smart_context(
        self,
        user_input: str = "",
        max_file_bytes: int = 8_000,
        max_total_bytes: int = 30_000,
        include_unchanged: bool = False,
    ) -> str:
        """Build token-efficient workspace context.

        Strategi hemat token:
        1. Kirim MEMORY SUMMARY (progress, decisions, facts) — ~200 tokens
        2. Kirim FILE LIST dengan status (new/modified/unchanged) — ~100 tokens
        3. Kirim CONTENT hanya file yg CHANGED atau yg relevantdengan input — hemat!
        4. Untuk file unchanged, hanya kirim nama + hash (AI tahu isinya dari memory)

        Args:
            user_input: Current user message (untuk detect file relevance).
            max_file_bytes: Max bytes per file content to include.
            max_total_bytes: Max total context bytes.
            include_unchanged: Force include all file contents.

        Returns:
            Compact context string for injection into prompt.
        """
        files = self.scan_workspace_files()
        parts: list[str] = []
        total_bytes = 0

        # ── Part 1: Memory Summary ──
        mem_summary = self._build_memory_summary()
        if mem_summary:
            parts.append(mem_summary)
            total_bytes += len(mem_summary)

        # ── Part 2: File Index ──
        parts.append(f"\n[WORKSPACE: {self._ws.name}]")
        parts.append(f"Dir: {self._ws}")

        if not files:
            parts.append("Files: (empty)")
        else:
            parts.append("Files:")
            changed_items = [
                (rel, info) for rel, info in sorted(files.items())
                if info.get("status") in ("new", "modified")
            ]
            unchanged_items = [
                (rel, info) for rel, info in sorted(files.items())
                if info.get("status") == "unchanged"
            ]

            max_files_in_listing = 60
            shown = 0
            for rel, info in changed_items:
                sz = self._fmt_size(info["size"])
                tag = "🆕" if info["status"] == "new" else "📝"
                parts.append(f"  {tag}{rel} ({sz})")
                shown += 1
                if shown >= max_files_in_listing:
                    break

            if shown < max_files_in_listing:
                remaining_slots = max_files_in_listing - shown
                for rel, info in unchanged_items[:remaining_slots]:
                    sz = self._fmt_size(info["size"])
                    parts.append(f"  {rel} ({sz})")
                    shown += 1

            total_remaining = len(files) - shown
            if total_remaining > 0:
                parts.append(f"  ... (+{total_remaining} files hidden)")

        # ── Part 3: File Contents (smart — hanya yg berubah/relevan) ──
        content_budget = max_total_bytes - total_bytes - 500  # Reserve for instructions

        for rel, info in sorted(files.items()):
            if content_budget <= 0:
                break
            if info["status"] == "deleted":
                continue
            if info["ext"] not in _TEXT_EXT:
                continue
            if info["size"] > max_file_bytes:
                continue

            # Include if: changed, or file mentioned in user input, or include_unchanged
            should_include = (
                info["changed"]
                or include_unchanged
                or self._is_relevant(rel, user_input)
            )
            if not should_include:
                continue

            filepath = self._ws / rel
            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")
                if len(content) > max_file_bytes:
                    content = content[:max_file_bytes] + "\n... [truncated]"

                parts.append(f"\n--- {rel} ---")
                parts.append(content)
                parts.append("---")
                content_budget -= len(content)
            except Exception:
                pass

        # ── Part 4: Instructions ──
        parts.append(self._build_instructions())

        return "\n".join(parts)

    def _is_relevant(self, rel_path: str, user_input: str) -> bool:
        """Check if a file is relevant to the user's message."""
        if not user_input:
            return False
        input_lower = user_input.lower()
        name = Path(rel_path).name.lower()
        stem = Path(rel_path).stem.lower()
        # Check if filename/stem appears in input
        return name in input_lower or stem in input_lower

    def _build_memory_summary(self) -> str:
        """Build compact memory summary (~200-500 tokens)."""
        s = self._state
        if not s.get("progress") and not s.get("decisions") and not s.get("files"):
            return ""

        parts = ["[MEMORY]"]

        # Progress
        if s.get("progress"):
            parts.append("Progress:")
            for p in s["progress"][-10:]:
                icon = {"done": "✅", "in_progress": "🔄", "pending": "⏳", "failed": "❌"}.get(p.get("status", ""), "·")
                detail = f" — {p['detail']}" if p.get("detail") else ""
                parts.append(f"  {icon} {p.get('phase', '?')}: {p.get('status', '?')}{detail}")

        # Decisions (last 5)
        if s.get("decisions"):
            parts.append("Decisions:")
            for d in s["decisions"][-5:]:
                why = f" ({d['why']})" if d.get("why") else ""
                parts.append(f"  • {d.get('what', '?')}{why}")

        # Facts (last 5)
        if s.get("facts"):
            parts.append("Facts:")
            for f in s["facts"][-5:]:
                parts.append(f"  • {f.get('fact', '?')}")

        # File edit history (last 10, compact)
        if s.get("files"):
            parts.append("File edits:")
            for rel, info in list(s["files"].items())[-10:]:
                summary = f" — {info['summary']}" if info.get("summary") else ""
                parts.append(f"  [{info.get('action', '?')}] {rel}{summary}")

        # Errors (last 3)
        if s.get("errors"):
            parts.append("Recent errors:")
            for e in s["errors"][-3:]:
                parts.append(f"  ⚠ {e.get('err', '?')[:80]}")

        # Last model
        if s.get("model_history"):
            last = s["model_history"][-1]
            parts.append(f"Last model: {last.get('model', '?')}")

        parts.append(f"Turns: {s.get('turn_count', 0)}")
        parts.append("[/MEMORY]")

        return "\n".join(parts)

    def _build_instructions(self) -> str:
        """Build file instruction block for AI."""
        return (
            "\n[INSTRUCTIONS]\n"
            "Kamu punya akses penuh ke workspace ini.\n"
            "Untuk CREATE/UPDATE file:\n"
            "### File: <path_relatif>\n"
            "```<bahasa>\n<isi lengkap file>\n```\n\n"
            "Untuk EDIT sebagian (hemat token!):\n"
            "```replace:<path_relatif>\n"
            "<<<<<<< SEARCH\n<kode lama>\n=======\n<kode baru>\n>>>>>>>\n```\n\n"
            "PENTING: Jika output terpotong, user akan bilang 'continue'.\n"
            "Saat itu, LANJUTKAN TEPAT dari baris terakhir yang terputus.\n"
            "JANGAN ulangi dari awal. JANGAN tulis ulang file dari baris 1.\n"
            "Langsung sambungkan output dari titik terputus.\n"
            "[/INSTRUCTIONS]"
        )

    # ─── Truncation Buffer ───────────────────────────────────────────

    def save_truncation(
        self,
        partial_content: str,
        messages: list[dict] | None = None,
        model_id: str = "",
    ) -> None:
        """Save truncated output for smart continue.

        Disimpan di .agent_memory/truncated.txt sehingga
        continue bisa sambung tepat.

        Args:
            partial_content: The truncated AI output.
            messages: Full message history at point of truncation.
            model_id: Model yang generate output.
        """
        # Save the partial content
        self._trunc_path.write_text(partial_content, encoding="utf-8")

        # Save metadata
        meta = {
            "model": model_id,
            "timestamp": time.time(),
            "content_length": len(partial_content),
            "last_100_chars": partial_content[-100:] if partial_content else "",
        }
        self._trunc_meta_path.write_text(
            json.dumps(meta, ensure_ascii=False),
            encoding="utf-8",
        )

        logger.info(
            f"[wm] Saved truncation buffer: {len(partial_content)} chars"
        )

    def has_truncation(self) -> bool:
        """Check if there's a saved truncation."""
        return self._trunc_path.exists() and self._trunc_path.stat().st_size > 0

    def get_continue_context(self) -> str:
        """Get smart continue context from truncation buffer.

        Returns context string yang tells AI exactly where to continue.
        AI TIDAK AKAN restart dari awal karena kita kirim:
        1. Potongan terakhir output (last ~2000 chars)
        2. Instruksi eksplisit untuk sambung

        Returns:
            Context string for continue, or "" if no truncation.
        """
        if not self.has_truncation():
            return ""

        try:
            content = self._trunc_path.read_text(encoding="utf-8")
        except Exception:
            return ""

        # Ambil potongan terakhir (hemat token — ga perlu kirim semua)
        # Kirim cukup banyak biar AI tahu konteksnya
        tail_size = min(3000, len(content))
        tail = content[-tail_size:]

        # Detect jika output terakhir adalah kode (cari ``` block)
        in_code_block = tail.count("```") % 2 == 1  # Odd = masih di dalam code block

        context = (
            "[CONTINUE FROM TRUNCATION]\n"
            "Output sebelumnya TERPOTONG. Ini adalah bagian TERAKHIR yang sudah ditulis:\n"
            "----LAST OUTPUT START----\n"
            f"{tail}\n"
            "----LAST OUTPUT END----\n\n"
        )

        if in_code_block:
            context += (
                "⚠️ Output terputus DI TENGAH CODE BLOCK.\n"
                "LANJUTKAN TEPAT dari baris terakhir kode di atas.\n"
                "JANGAN tulis ulang dari awal file. JANGAN buka ``` baru.\n"
                "Langsung tulis baris kode selanjutnya.\n"
            )
        else:
            context += (
                "LANJUTKAN TEPAT dari bagian terakhir di atas.\n"
                "JANGAN ulangi apa yang sudah ditulis.\n"
                "Langsung sambungkan.\n"
            )

        context += "[/CONTINUE]\n"
        return context

    def clear_truncation(self) -> None:
        """Clear the truncation buffer (after successful continue)."""
        if self._trunc_path.exists():
            self._trunc_path.unlink()
        if self._trunc_meta_path.exists():
            self._trunc_meta_path.unlink()

    def save_last_response(self, content: str, model_id: str = "") -> None:
        """Save the last AI response summary for cross-model continuity.

        Saat user chat ulang atau model ganti, AI bisa baca ini dan
        langsung lanjut tanpa ngulang dari awal. Hemat token.

        Hanya simpan tail (2000 chars terakhir) + metadata.
        """
        if not content.strip():
            return

        resp_path = self._mem_dir / "last_response.json"
        tail = content[-2000:] if len(content) > 2000 else content

        # Detect apakah output terakhir mengandung kode
        has_code = "```" in content
        # Detect apakah ada file yang dibuat/diedit
        has_file_ops = any(
            marker in content
            for marker in ("### File:", "```replace:", "💾 Saved:", "✏️  Replaced")
        )

        data = {
            "model": model_id,
            "timestamp": time.time(),
            "total_length": len(content),
            "tail": tail,
            "has_code": has_code,
            "has_file_ops": has_file_ops,
        }

        try:
            resp_path.write_text(
                json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[wm] Failed to save last response: {e}")

    def get_continuity_context(self) -> str:
        """Build context yang bikin AI langsung lanjut, bukan ngulang.

        Dipanggil saat user chat baru atau model ganti. AI baca ini
        dan tahu persis apa yang sudah dikerjakan sebelumnya.

        Returns:
            Context string, atau "" jika tidak ada history.
        """
        resp_path = self._mem_dir / "last_response.json"
        if not resp_path.exists():
            return ""

        try:
            data = json.loads(resp_path.read_text("utf-8"))
        except Exception:
            return ""

        tail = data.get("tail", "")
        if not tail.strip():
            return ""

        model = data.get("model", "unknown")
        has_code = data.get("has_code", False)
        has_file_ops = data.get("has_file_ops", False)

        parts = [
            "[PREVIOUS AI OUTPUT — DO NOT REPEAT]",
            f"Model sebelumnya: {model}",
            f"Total output: {data.get('total_length', 0)} chars",
        ]

        if has_file_ops:
            parts.append("Status: AI sedang mengerjakan file operations (create/edit)")
        if has_code:
            parts.append("Status: AI sedang menulis kode")

        parts.append("")
        parts.append("Bagian TERAKHIR output sebelumnya:")
        parts.append("---")
        parts.append(tail)
        parts.append("---")
        parts.append("")
        parts.append(
            "INSTRUKSI: Kamu HARUS melanjutkan dari titik di atas. "
            "DILARANG mengulang analisis, penjelasan, atau kode yang sudah "
            "ditulis sebelumnya. Langsung lanjutkan ke bagian berikutnya."
        )
        parts.append("[/PREVIOUS AI OUTPUT]")

        return "\n".join(parts)

    # ─── Recording Methods ───────────────────────────────────────────

    def record_progress(
        self, phase: str, status: str = "done", detail: str = ""
    ) -> None:
        """Record task progress."""
        # Update existing or append
        for p in self._state["progress"]:
            if p.get("phase") == phase:
                p["status"] = status
                p["detail"] = detail
                p["ts"] = time.time()
                self._save_state()
                return

        self._state["progress"].append({
            "phase": phase, "status": status, "detail": detail,
            "ts": time.time(),
        })
        # Keep last 20
        if len(self._state["progress"]) > 20:
            self._state["progress"] = self._state["progress"][-20:]
        self._save_state()

    def record_decision(self, what: str, why: str = "") -> None:
        """Record a design decision."""
        self._state["decisions"].append({
            "what": what, "why": why, "ts": time.time(),
        })
        if len(self._state["decisions"]) > 20:
            self._state["decisions"] = self._state["decisions"][-20:]
        self._save_state()

    def record_fact(self, fact: str) -> None:
        """Record an important fact."""
        self._state["facts"].append({"fact": fact, "ts": time.time()})
        if len(self._state["facts"]) > 30:
            self._state["facts"] = self._state["facts"][-30:]
        self._save_state()

    def record_file_edit(
        self, rel_path: str, action: str = "edited", summary: str = ""
    ) -> None:
        """Record a file edit."""
        self._state["files"][rel_path] = {
            "action": action,
            "summary": summary,
            "ts": time.time(),
        }
        # Keep last 50 files
        if len(self._state["files"]) > 50:
            items = sorted(self._state["files"].items(), key=lambda x: x[1].get("ts", 0))
            self._state["files"] = dict(items[-50:])
        self._save_state()

    def record_model(self, model_id: str) -> None:
        """Record which model is being used."""
        self._state["model_history"].append({
            "model": model_id, "ts": time.time(),
        })
        if len(self._state["model_history"]) > 10:
            self._state["model_history"] = self._state["model_history"][-10:]
        self._save_state()

    def get_last_model(self) -> str | None:
        """Get the model ID that was previously used."""
        history = self._state.get("model_history", [])
        if len(history) >= 2:
            return str(history[-2]["model"])
        elif len(history) == 1:
            return str(history[-1]["model"])
        return None

    def record_error(self, error: str) -> None:
        """Record an error."""
        self._state["errors"].append({"err": error[:200], "ts": time.time()})
        if len(self._state["errors"]) > 10:
            self._state["errors"] = self._state["errors"][-10:]
        self._save_state()

    def increment_turn(self) -> None:
        """Increment the turn counter."""
        self._state["turn_count"] = self._state.get("turn_count", 0) + 1
        self._save_state()

    # ─── Utils ───────────────────────────────────────────────────────

    @staticmethod
    def _fmt_size(size: int) -> str:
        if size >= 1_048_576:
            return f"{size / 1_048_576:.1f}MB"
        if size >= 1024:
            return f"{size / 1024:.1f}KB"
        return f"{size}B"

    @property
    def workspace_path(self) -> Path:
        return self._ws

    @property
    def state(self) -> dict[str, Any]:
        return self._state
