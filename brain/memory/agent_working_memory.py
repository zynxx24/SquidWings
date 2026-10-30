"""brain.memory.agent_working_memory — Compact working-memory checkpoint.

Persists the agent's *in-progress brain state* between fallbacks, model
switches, and session restarts so the next model always knows:

  * What stage the pipeline is at (PLAN / SEARCH / COMPILE / EXECUTE)
  * What the original goal was
  * What the plan said
  * What web searches returned
  * What tools were executed and their results
  * What thinking the previous model did

Cached cleanly per (session_id, turn_id) so prompt-caching can reuse it.
Storage is deliberately compact — only what the next model *needs*.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

_MAX_THINKING_CHARS = 2_000   # keep most recent slice of thinking
_MAX_WEB_SNIPPET   = 400      # per result
_MAX_TOOL_RESULT   = 600      # per tool call result
_MAX_PLAN_CHARS    = 1_200
_MAX_COMPILE_CHARS = 3_000


class AgentStage(StrEnum):
    """Pipeline stage — what the agent was doing when it checkpointed."""
    IDLE     = "idle"
    PLAN     = "plan"
    SEARCH   = "search"
    COMPILE  = "compile"
    EXECUTE  = "execute"
    DONE     = "done"


@dataclass
class WebResult:
    query: str
    title: str
    url: str
    snippet: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "query":   self.query,
            "title":   self.title,
            "url":     self.url,
            "snippet": self.snippet[:_MAX_WEB_SNIPPET],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WebResult:
        return cls(
            query=d.get("query", ""),
            title=d.get("title", ""),
            url=d.get("url", ""),
            snippet=d.get("snippet", ""),
        )


@dataclass
class ToolCheckpoint:
    name:      str
    arguments: dict[str, Any]
    result:    str            # stringified, truncated
    iteration: int
    success:   bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":      self.name,
            "arguments": self.arguments,
            "result":    self.result[:_MAX_TOOL_RESULT],
            "iteration": self.iteration,
            "success":   self.success,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ToolCheckpoint:
        return cls(
            name=d.get("name", ""),
            arguments=d.get("arguments", {}),
            result=d.get("result", ""),
            iteration=d.get("iteration", 0),
            success=d.get("success", True),
        )


@dataclass
class WorkingMemoryState:
    """Full checkpoint for one agent turn."""
    session_id:   str
    turn_id:      str
    goal:         str                        = ""
    stage:        AgentStage                 = AgentStage.IDLE
    plan:            str                     = ""   # bullet plan text
    plan_steps:      list[str]              = field(default_factory=list)
    plan_done:       list[str]              = field(default_factory=list)
    search_queries:  list[str]              = field(default_factory=list)  # PLAN output
    web_results:     list[WebResult]        = field(default_factory=list)
    compiled:     str                        = ""   # plan + web synthesis
    tools_done:   list[ToolCheckpoint]       = field(default_factory=list)
    thinking:     str                        = ""   # last model thinking slice
    model_used:   str                        = ""
    fallback_count: int                      = 0
    ts:           float                      = field(default_factory=time.time)

    # --- serialisation ---

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id":     self.session_id,
            "turn_id":        self.turn_id,
            "goal":           self.goal[:500],
            "stage":          self.stage.value,
            "plan":            self.plan[:_MAX_PLAN_CHARS],
            "plan_steps":     self.plan_steps[:20],
            "plan_done":      self.plan_done[:20],
            "search_queries": self.search_queries[:8],
            "web_results":    [r.to_dict() for r in self.web_results[:6]],
            "compiled":       self.compiled[:_MAX_COMPILE_CHARS],
            "tools_done":     [t.to_dict() for t in self.tools_done[-12:]],
            "thinking":       self.thinking[-_MAX_THINKING_CHARS:],
            "model_used":     self.model_used[:80],
            "fallback_count": self.fallback_count,
            "ts":             self.ts,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkingMemoryState:
        return cls(
            session_id=d.get("session_id", ""),
            turn_id=d.get("turn_id", ""),
            goal=d.get("goal", ""),
            stage=AgentStage(d.get("stage", "idle")),
            plan=d.get("plan", ""),
            plan_steps=d.get("plan_steps", []),
            plan_done=d.get("plan_done", []),
            search_queries=d.get("search_queries", []),
            web_results=[WebResult.from_dict(r) for r in d.get("web_results", [])],
            compiled=d.get("compiled", ""),
            tools_done=[ToolCheckpoint.from_dict(t) for t in d.get("tools_done", [])],
            thinking=d.get("thinking", ""),
            model_used=d.get("model_used", ""),
            fallback_count=d.get("fallback_count", 0),
            ts=d.get("ts", 0.0),
        )

    # --- helpers ---

    def mark_step_done(self, step: str) -> None:
        if step in self.plan_steps and step not in self.plan_done:
            self.plan_done.append(step)

    def pending_steps(self) -> list[str]:
        return [s for s in self.plan_steps if s not in self.plan_done]

    def add_web(self, query: str, results: list[dict[str, Any]]) -> None:
        for r in results[:3]:
            self.web_results.append(WebResult(
                query=query,
                title=r.get("title", ""),
                url=r.get("url", r.get("href", "")),
                snippet=r.get("body", r.get("snippet", r.get("text", "")))[:_MAX_WEB_SNIPPET],
            ))

    def add_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        result: Any,
        iteration: int,
        success: bool = True,
    ) -> None:
        result_str = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        self.tools_done.append(ToolCheckpoint(
            name=name,
            arguments=arguments,
            result=result_str[:_MAX_TOOL_RESULT],
            iteration=iteration,
            success=success,
        ))

    def append_thinking(self, text: str) -> None:
        combined = self.thinking + text
        self.thinking = combined[-_MAX_THINKING_CHARS:]

    def to_cache_block(self) -> str:
        """Compact text injected into the system prompt (cache-friendly).

        Stable once stage doesn't change → prompt-caching reuses this block
        on fallback retries within the same stage.
        """
        lines: list[str] = []
        lines.append(
            f"[WORKING MEMORY | stage={self.stage.value} "
            f"| model={self.model_used} | fallbacks={self.fallback_count}]"
        )
        lines.append(f"Goal: {self.goal[:300]}")

        if self.plan_steps:
            lines.append("\nPlan:")
            for i, step in enumerate(self.plan_steps, 1):
                tick = "✓" if step in self.plan_done else "○"
                lines.append(f"  {tick} {i}. {step}")

        if self.thinking:
            lines.append(f"\nPrevious thinking (tail):\n{self.thinking[-800:]}")

        if self.web_results:
            lines.append("\nWeb research:")
            for r in self.web_results[:4]:
                lines.append(f"  [{r.query}] {r.title} — {r.snippet[:200]}")
                lines.append(f"   source: {r.url}")

        if self.compiled:
            lines.append(f"\nCompiled insight:\n{self.compiled[:1000]}")

        if self.tools_done:
            lines.append("\nTools already executed:")
            for t in self.tools_done[-6:]:
                status = "✓" if t.success else "✗"
                lines.append(
                    f"  {status} {t.name}({list(t.arguments.keys())}) "
                    f"→ {t.result[:150]}"
                )

        if self.plan_done:
            lines.append(f"\nCompleted steps: {', '.join(self.plan_done)}")
        pending = self.pending_steps()
        if pending:
            lines.append(f"Remaining steps: {', '.join(pending)}")

        lines.append("[/WORKING MEMORY]")
        return "\n".join(lines)


class AgentWorkingMemory:
    """Persist/load WorkingMemoryState to disk per (session_id, turn_id).

    One file per turn — old turns kept for audit, never loaded automatically.
    """

    def __init__(self, workspace_path: str | Path) -> None:
        self._dir = Path(workspace_path) / ".agent_memory" / "working"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._current: dict[str, WorkingMemoryState] = {}  # "sess:turn" → state

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def get_or_create(
        self, session_id: str, turn_id: str, goal: str = ""
    ) -> WorkingMemoryState:
        """Return in-memory state, or load from disk, or create fresh."""
        key = f"{session_id}:{turn_id}"
        if key in self._current:
            return self._current[key]

        path = self._path(session_id, turn_id)
        if path.exists():
            try:
                data = json.loads(path.read_text("utf-8"))
                state = WorkingMemoryState.from_dict(data)
                self._current[key] = state
                logger.info(
                    "[working_mem] Resumed state session=%s turn=%s stage=%s",
                    session_id, turn_id, state.stage.value,
                )
                return state
            except Exception as e:
                logger.warning("[working_mem] Failed to load checkpoint: %s", e)

        state = WorkingMemoryState(session_id=session_id, turn_id=turn_id, goal=goal)
        self._current[key] = state
        return state

    def save(self, state: WorkingMemoryState) -> None:
        """Persist state atomically (non-fatal on failure)."""
        state.ts = time.time()
        path = self._path(state.session_id, state.turn_id)
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(state.to_dict(), ensure_ascii=False, indent=2), "utf-8"
            )
            tmp.replace(path)
        except Exception as e:
            logger.warning("[working_mem] Save failed: %s", e)

    def finish(self, state: WorkingMemoryState) -> None:
        """Mark turn done and persist final state."""
        state.stage = AgentStage.DONE
        self.save(state)
        self._current.pop(f"{state.session_id}:{state.turn_id}", None)

    def get_active(self, session_id: str) -> WorkingMemoryState | None:
        """Return the most recently active (non-DONE) state for this session."""
        for key, state in reversed(list(self._current.items())):
            if key.startswith(f"{session_id}:") and state.stage != AgentStage.DONE:
                return state
        return self._load_latest_from_disk(session_id)

    # ------------------------------------------------------------------ #
    #  Internal                                                            #
    # ------------------------------------------------------------------ #

    def _path(self, session_id: str, turn_id: str) -> Path:
        # Both ids come from callers and end up in a file name.
        name = re.sub(r"[^A-Za-z0-9._-]", "_", f"{session_id}-{turn_id}").lstrip(".")
        return self._dir / f"{name}.json"

    def _load_latest_from_disk(self, session_id: str) -> WorkingMemoryState | None:
        files = sorted(
            self._dir.glob(f"{re.sub(r'[^A-Za-z0-9._-]', '_', session_id).lstrip('.')}-*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for f in files:
            try:
                data = json.loads(f.read_text("utf-8"))
                state = WorkingMemoryState.from_dict(data)
                if state.stage != AgentStage.DONE:
                    return state
            except Exception:
                continue
        return None
