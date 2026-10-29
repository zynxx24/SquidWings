"""
security.guard — Composed facade wiring every security layer together.

Call sites should depend on `Guard` rather than reaching into individual
modules. One `Guard` instance owns:

    policy  -> decides allow / ask / deny for a tool call
    jail    -> canonicalises filesystem arguments (symlink-safe)
    shield  -> inspects untrusted text for prompt injection
    redactor-> scrubs secrets from anything leaving the process
    audit   -> hash-chained record of every decision

Ordering matters and is deliberate:

    1. policy      cheapest + authoritative; a denial short-circuits
    2. path jail   only for tools that touch the filesystem
    3. prompt shield for untrusted inbound text
    4. audit       written after the verdict, before the executor runs

The executor itself is never invoked by `check()`; use `execute()` which
enforces the verdict and records the outcome.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from security.audit_chain import AuditChain, get_chain
from security.path_jail import PathJail, PathJailError
from security.policy_engine import Decision, PolicyEngine, PolicyResult
from security.prompt_shield import PromptShield
from security.redactor import Redactor

# Tool arguments that carry a filesystem path and must pass the jail.
_PATH_ARGS: tuple[str, ...] = ("file_path", "path", "directory", "dir_path", "target")

# Tools whose arguments are untrusted inbound text worth screening.
_TEXT_ARGS: tuple[str, ...] = ("query", "prompt", "content", "text", "input")


class GuardError(RuntimeError):
    """Raised when a guarded operation is blocked."""

    def __init__(self, tool_name: str, reason: str, rule: str = "") -> None:
        self.tool_name = tool_name
        self.reason = reason
        self.rule = rule
        detail = f"rule={rule}: {reason}" if rule else reason
        super().__init__(f"blocked {tool_name!r}: {detail}")


@dataclass(frozen=True, slots=True)
class GuardVerdict:
    """Outcome of a guard check.

    Attributes:
        allowed: True only when the call may proceed.
        decision: Underlying policy decision.
        rule: Rule (or layer) that produced the verdict.
        reason: Human-readable justification.
    """

    allowed: bool
    decision: Decision
    rule: str = ""
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed

    def raise_if_blocked(self, tool_name: str) -> None:
        """Raise GuardError when the verdict is not permissive."""
        if not self.allowed:
            raise GuardError(tool_name, self.reason, self.rule)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "decision": self.decision.value,
            "rule": self.rule,
            "reason": self.reason,
        }


@dataclass(slots=True)
class GuardContext:
    """Caller identity and correlation data attached to audit records."""

    actor: str = "system"
    session_id: str = ""
    request_id: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"actor": self.actor}
        if self.session_id:
            out["session_id"] = self.session_id
        if self.request_id:
            out["request_id"] = self.request_id
        if self.metadata:
            out["metadata"] = dict(self.metadata)
        return out


class Guard:
    """Facade over policy, jail, shield, redactor and audit.

    Args:
        policy: Policy engine. Defaults to the baseline rule set.
        jail: Filesystem jail. When omitted, path checking is skipped.
        shield: Prompt-injection detector. Defaults to the standard shield.
        redactor: Secret scrubber. Defaults to the standard pattern set.
        audit: Audit chain. Defaults to the process-wide chain.
    """

    __slots__ = ("policy", "jail", "shield", "redactor", "audit")

    def __init__(
        self,
        *,
        policy: PolicyEngine | None = None,
        jail: PathJail | None = None,
        shield: PromptShield | None = None,
        redactor: Redactor | None = None,
        audit: AuditChain | None = None,
    ) -> None:
        self.policy = policy if policy is not None else PolicyEngine()
        self.jail = jail
        self.shield = shield if shield is not None else PromptShield()
        self.redactor = redactor if redactor is not None else Redactor()
        self.audit = audit if audit is not None else get_chain()

    # ── evaluation ──────────────────────────────────────────────────────
    def check(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        context: GuardContext | None = None,
    ) -> GuardVerdict:
        """Evaluate a tool call without running it.

        Layers are consulted in cost order; the first blocking layer wins.
        The verdict is always audited.
        """
        args = dict(arguments or {})

        result: PolicyResult = self.policy.evaluate(tool_name, args)
        if result.decision is Decision.DENY:
            verdict = GuardVerdict(
                allowed=False,
                decision=Decision.DENY,
                rule=result.rule,
                reason=result.reason,
            )
            self._record(tool_name, args, verdict, context)
            return verdict

        jail_verdict = self._check_paths(tool_name, args)
        if jail_verdict is not None:
            self._record(tool_name, args, jail_verdict, context)
            return jail_verdict

        shield_verdict = self._check_untrusted(tool_name, args)
        if shield_verdict is not None:
            self._record(tool_name, args, shield_verdict, context)
            return shield_verdict

        verdict = GuardVerdict(
            allowed=True,
            decision=result.decision,
            rule=result.rule,
            reason=result.reason,
        )
        self._record(tool_name, args, verdict, context)
        return verdict

    def _check_paths(self, tool_name: str, args: Mapping[str, Any]) -> GuardVerdict | None:
        """Reject path arguments that escape the jail."""
        if self.jail is None:
            return None
        for key in _PATH_ARGS:
            raw = args.get(key)
            if not isinstance(raw, (str, os.PathLike)) or not str(raw):
                continue
            try:
                self.jail.resolve(str(raw))
            except PathJailError as exc:
                return GuardVerdict(
                    allowed=False,
                    decision=Decision.DENY,
                    rule="path_jail",
                    reason=str(exc),
                )
        return None

    def _check_untrusted(self, tool_name: str, args: Mapping[str, Any]) -> GuardVerdict | None:
        """Reject arguments carrying a high-confidence injection."""
        for key in _TEXT_ARGS:
            raw = args.get(key)
            if not isinstance(raw, str) or not raw:
                continue
            verdict = self.shield.inspect(raw)
            if verdict.should_block:
                return GuardVerdict(
                    allowed=False,
                    decision=Decision.DENY,
                    rule="prompt_shield",
                    reason=f"prompt injection detected in {key!r}",
                )
        return None

    # ── execution ───────────────────────────────────────────────────────
    def execute(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None,
        func: Callable[..., Any],
        *,
        context: GuardContext | None = None,
    ) -> Any:
        """Enforce the verdict, then run `func(**arguments)`.

        Raises:
            GuardError: when the call is blocked.
        """
        args = dict(arguments or {})
        verdict = self.check(tool_name, args, context=context)
        verdict.raise_if_blocked(tool_name)
        try:
            result = func(**args)
        except Exception as exc:
            self._record(
                tool_name,
                args,
                GuardVerdict(
                    allowed=False,
                    decision=Decision.DENY,
                    rule="executor_error",
                    reason=f"{type(exc).__name__}: {exc}",
                ),
                context,
            )
            raise
        self.audit.append(
            "tool.result",
            actor=(context.actor if context else "system"),
            tool=tool_name,
            ok=True,
        )
        return result

    # ── output hygiene ──────────────────────────────────────────────────
    def scrub(self, value: Any) -> Any:
        """Redact secrets from a structured payload or string."""
        return self.redactor.scrub_obj(value)

    def sanitize_text(self, text: str) -> str:
        """Redact secrets from a plain string."""
        return self.redactor.scrub(text)

    # ── introspection ───────────────────────────────────────────────────
    def status(self) -> dict[str, Any]:
        """Serialisable snapshot for health endpoints and diagnostics."""
        chain = self.audit.verify()
        return {
            "policy_rules": len(self.policy.rules),
            "policy_default": self.policy.evaluate("__probe__", {}).decision.value,
            "jail_root": str(self.jail.root) if self.jail is not None else None,
            "audit": {
                "path": str(self.audit.path),
                "records": chain.records,
                "valid": chain.valid,
            },
        }

    def _record(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        verdict: GuardVerdict,
        context: GuardContext | None,
    ) -> None:
        """Append a redacted decision record to the audit chain."""
        payload: dict[str, Any] = {
            "tool": tool_name,
            "decision": verdict.decision.value,
            "rule": verdict.rule,
            "reason": self.redactor.scrub(verdict.reason),
            "arguments": self.redactor.scrub_obj(dict(args)),
        }
        if context is not None:
            payload["context"] = context.to_dict()
        self.audit.append(
            "guard.check",
            actor=(context.actor if context else "system"),
            **payload,
        )

    def __repr__(self) -> str:
        return (
            f"Guard(rules={len(self.policy.rules)}, "
            f"jail={'on' if self.jail is not None else 'off'})"
        )


_facade: Guard | None = None


def get_guard_facade() -> Guard:
    """Return the process-wide default guard."""
    global _facade
    if _facade is None:
        _facade = Guard()
    return _facade


def configure(**kwargs: Any) -> Guard:
    """Replace the process-wide default guard and return it."""
    global _facade
    _facade = Guard(**kwargs)
    return _facade
