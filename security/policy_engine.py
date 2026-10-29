"""
security.policy_engine - Declarative allow / ask / deny policy for tool calls.

Rules are plain data, evaluated highest-priority-first with first-match-wins
semantics. A rule matches when its tool list covers the tool name (or is empty
/ ``"*"``) *and* every ``arg_patterns`` entry matches the corresponding
argument.

Outcomes:

    ALLOW  - the call may proceed
    ASK    - the call needs explicit human confirmation
    DENY   - the call is blocked

When no rule matches, ``default`` applies. In ``strict`` mode an unmatched tool
is denied regardless of ``default``, so new tools are opt-in.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

__all__ = [
    "Decision",
    "PolicyDenied",
    "PolicyEngine",
    "PolicyError",
    "PolicyResult",
    "Rule",
    "baseline_rules",
]

# Wildcard used in a rule's ``tools`` list to mean "any tool".
ANY_TOOL = "*"


class PolicyError(Exception):
    """Base error for the policy layer."""


# N818 wants an ``Error`` suffix; the short public name is kept as an alias so
# existing imports (tests, security/__init__.py) keep working unchanged.
class PolicyDeniedError(PolicyError):
    """Raised when a policy evaluation blocks a tool call."""

    def __init__(self, tool: str, rule: str, reason: str) -> None:
        self.tool = tool
        self.rule = rule
        self.reason = reason
        super().__init__(f"policy denied {tool!r} by rule {rule!r}: {reason}")


PolicyDenied = PolicyDeniedError


class Decision(StrEnum):
    """Three-way verdict produced by the policy engine."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"

    @classmethod
    def parse(cls, value: Any) -> Decision:
        """Coerce a string or enum value into a Decision."""
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            allowed = ", ".join(d.value for d in cls)
            raise PolicyError(
                f"unknown decision {value!r} (expected one of: {allowed})"
            ) from exc


@dataclass(frozen=True, slots=True)
class Rule:
    """A single policy rule.

    Attributes:
        name: Stable identifier, surfaced in verdicts and audit records.
        action: What to do when the rule matches.
        tools: Tool names this rule applies to. Empty or ``["*"]`` means all.
        arg_patterns: Mapping of argument name -> regex the value must match.
        reason: Human-readable justification.
        priority: Higher wins. Rules are evaluated in descending order.
    """

    name: str
    action: Decision
    tools: tuple[str, ...] = ()
    arg_patterns: Mapping[str, str] = field(default_factory=dict)
    reason: str = ""
    priority: int = 0

    def __post_init__(self) -> None:
        # Normalise the action so callers may pass the raw string from YAML.
        object.__setattr__(self, "action", Decision.parse(self.action))
        object.__setattr__(self, "tools", tuple(self.tools))
        object.__setattr__(self, "arg_patterns", dict(self.arg_patterns))
        # Compile eagerly so a bad regex fails at load time, not mid-run.
        for key, pattern in self.arg_patterns.items():
            try:
                re.compile(pattern)
            except re.error as exc:
                raise PolicyError(
                    f"rule {self.name!r}: invalid regex for {key!r}: {exc}"
                ) from exc

    # -- matching ------------------------------------------------------
    def applies_to(self, tool_name: str) -> bool:
        """True when this rule covers ``tool_name``."""
        if not self.tools or ANY_TOOL in self.tools:
            return True
        return tool_name in self.tools

    def matches(self, tool_name: str, args: Mapping[str, Any]) -> bool:
        """True when the rule covers the tool *and* every pattern matches."""
        if not self.applies_to(tool_name):
            return False
        for key, pattern in self.arg_patterns.items():
            value = args.get(key)
            if value is None:
                return False
            if not re.search(pattern, str(value)):
                return False
        return True

    # -- serialisation -------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "action": self.action.value,
            "tools": list(self.tools),
            "arg_patterns": dict(self.arg_patterns),
            "reason": self.reason,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Rule:
        try:
            name = str(data["name"])
            action = data["action"]
        except KeyError as exc:
            raise PolicyError(
                f"rule is missing required key {exc.args[0]!r}"
            ) from exc
        tools = data.get("tools") or ()
        if isinstance(tools, str):
            tools = (tools,)
        patterns = data.get("arg_patterns") or {}
        if not isinstance(patterns, Mapping):
            raise PolicyError(f"rule {name!r}: arg_patterns must be a mapping")
        return cls(
            name=name,
            action=Decision.parse(action),
            tools=tuple(str(t) for t in tools),
            arg_patterns={str(k): str(v) for k, v in patterns.items()},
            reason=str(data.get("reason", "")),
            priority=int(data.get("priority", 0)),
        )


@dataclass(frozen=True, slots=True)
class PolicyResult:
    """Outcome of evaluating one tool call."""

    decision: Decision
    rule: str = ""
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW

    @property
    def blocked(self) -> bool:
        return self.decision is Decision.DENY

    @property
    def needs_approval(self) -> bool:
        return self.decision is Decision.ASK

    def raise_if_denied(self, tool_name: str) -> None:
        """Raise PolicyDenied when the decision is DENY."""
        if self.blocked:
            raise PolicyDenied(tool_name, self.rule, self.reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "rule": self.rule,
            "reason": self.reason,
        }


# -- baseline rules ----------------------------------------------------
# Mirrors security/policy.yaml. Kept in code so the engine is safe by default
# even when no YAML file is present (frozen builds, tests).
_SHELL_TOOLS = ("execute_command", "run_command", "shell")
_FILE_TOOLS = ("read_file", "upload_file", "edit_file", "discard_changes")


def baseline_rules() -> list[Rule]:
    """Return the built-in rule set, sorted highest-priority-first."""
    rules = [
        Rule(
            name="block_privilege_escalation",
            action=Decision.DENY,
            tools=_SHELL_TOOLS,
            arg_patterns={
                "command": (
                    r"(?i)\b(?:su"
                    r"do|su\s+-|doas|pkexec|ch"
                    r"own\s+root|chmod\s+[ugo]*s)\b"
                ),
            },
            reason="privilege escalation is not permitted",
            priority=100,
        ),
        Rule(
            name="block_destructive_root",
            action=Decision.DENY,
            tools=_SHELL_TOOLS,
            arg_patterns={
                "command": (
                    r"(?i)(?:r"
                    r"m\s+-[a-z]*r[a-z]*f?\s+/(?:\s|$)"
                    r"|r"
                    r"m\s+-[a-z]*f[a-z]*r?\s+/(?:\s|$)"
                    r"|mkfs\.|dd\s+if=.*of=/dev/|>\s*/dev/sd)"
                ),
            },
            reason="destructive operation on system root or block device",
            priority=100,
        ),
        Rule(
            name="block_fork_bomb",
            action=Decision.DENY,
            tools=_SHELL_TOOLS,
            arg_patterns={"command": r":\(\)\s*\{.*\};\s*:"},
            reason="fork bomb detected",
            priority=100,
        ),
        Rule(
            name="block_network_exfil",
            action=Decision.DENY,
            tools=_SHELL_TOOLS,
            arg_patterns={
                "command": (
                    r"(?i)(?:cu"
                    r"rl|wget)\b[^|;]*\|\s*(?:ba)?sh\b"
                    r"|(?:cu"
                    r"rl|wget)\b.*(?:--data|-d)\s+.*(?:\.env|id_rsa|\.ssh)"
                ),
            },
            reason="piping remote content to shell or uploading secrets",
            priority=95,
        ),
        Rule(
            name="block_secret_file_access",
            action=Decision.DENY,
            tools=_FILE_TOOLS,
            arg_patterns={
                "file_path": (
                    r"(?i)(?:^|/)(?:\.env(?:\..*)?|id_rsa|id_ed25519|\.netrc"
                    r"|\.pgpass|credentials(?:\.json)?|.*\.(?:pem|key|p12|pfx))$"
                ),
            },
            reason="access to credential material is blocked",
            priority=90,
        ),
        Rule(
            name="block_git_force_push",
            action=Decision.DENY,
            tools=_SHELL_TOOLS,
            arg_patterns={"command": r"(?i)\bgit\s+push\b.*(?:--force|-f)\b"},
            reason="force push requires explicit human approval",
            priority=80,
        ),
        Rule(
            name="ask_package_install",
            action=Decision.ASK,
            tools=_SHELL_TOOLS,
            arg_patterns={
                "command": (
                    r"(?i)\b(?:pip3?\s+install|npm\s+(?:i|install)"
                    r"|apt(?:-get)?\s+install|brew\s+install"
                    r"|cargo\s+install|gem\s+install)\b"
                ),
            },
            reason="installing dependencies modifies the environment",
            priority=60,
        ),
        Rule(
            name="allow_readonly_tools",
            action=Decision.ALLOW,
            tools=("read_file", "list_directory", "search", "grep", "glob"),
            reason="read-only operation",
            priority=10,
        ),
    ]
    return sorted(rules, key=lambda r: r.priority, reverse=True)


class PolicyEngine:
    """Evaluates tool calls against an ordered rule set."""

    __slots__ = ("rules", "default", "strict")

    def __init__(
        self,
        rules: Iterable[Rule] | None = None,
        *,
        default: Decision = Decision.ALLOW,
        strict: bool = False,
    ) -> None:
        """Build an engine.

        Args:
            rules: Rules to evaluate. ``None`` loads baseline_rules();
                pass ``[]`` for an empty set.
            default: Decision when no rule matches.
            strict: When True, unmatched tools are denied outright.
        """
        source = baseline_rules() if rules is None else list(rules)
        self.rules: list[Rule] = sorted(
            source, key=lambda r: r.priority, reverse=True
        )
        self.default = Decision.parse(default)
        self.strict = strict

    # -- evaluation ----------------------------------------------------
    def evaluate(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> PolicyResult:
        """Return the verdict for ``tool_name`` called with ``arguments``."""
        args = dict(arguments or {})
        for rule in self.rules:
            if rule.matches(tool_name, args):
                return PolicyResult(
                    decision=rule.action,
                    rule=rule.name,
                    reason=rule.reason,
                )
        if self.strict:
            return PolicyResult(
                decision=Decision.DENY,
                rule="strict_default",
                reason=(
                    f"tool {tool_name!r} is not covered by any rule "
                    "(strict mode)"
                ),
            )
        return PolicyResult(
            decision=self.default,
            rule="default",
            reason="no rule matched",
        )

    def is_allowed(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> bool:
        """Convenience predicate for the common allow/not-allow question."""
        return self.evaluate(tool_name, arguments).allowed

    # -- construction from data ----------------------------------------
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PolicyEngine:
        """Build an engine from a parsed policy document."""
        raw_rules = data.get("rules") or []
        if not isinstance(raw_rules, Sequence) or isinstance(
            raw_rules, (str, bytes)
        ):
            raise PolicyError("policy 'rules' must be a list")
        rules = [Rule.from_dict(item) for item in raw_rules]
        return cls(
            rules=rules,
            default=Decision.parse(data.get("default", Decision.ALLOW)),
            strict=bool(data.get("strict", False)),
        )

    @classmethod
    def load(cls, path: str | Path) -> PolicyEngine:
        """Load a YAML policy file.

        Raises:
            PolicyError: when the file is missing or PyYAML is unavailable.
        """
        target = Path(path)
        if not target.exists():
            raise PolicyError(f"policy file not found: {target}")
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover
            raise PolicyError(
                "PyYAML is required to load a YAML policy file"
            ) from exc
        try:
            data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise PolicyError(f"invalid YAML in {target}: {exc}") from exc
        if not isinstance(data, Mapping):
            raise PolicyError(
                f"policy root must be a mapping, got {type(data).__name__}"
            )
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "default": self.default.value,
            "strict": self.strict,
            "rules": [r.to_dict() for r in self.rules],
        }

    def __len__(self) -> int:
        return len(self.rules)

    def __repr__(self) -> str:
        return (
            f"PolicyEngine(rules={len(self.rules)}, "
            f"default={self.default.value!r}, strict={self.strict})"
        )
