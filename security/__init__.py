"""
security - Defense-in-depth primitives for the agent runtime.

Layers (cheapest first):
    redactor        - outbound secret scrubbing (logs, audit, API payloads)
    prompt_shield   - prompt-injection / jailbreak detection
    path_jail       - canonical path containment (symlink-safe)
    rate_guard      - token-bucket rate limiting per principal
    policy_engine   - declarative allow/deny policy for tool execution
    secrets_vault   - encrypted-at-rest credential storage + rotation
    integrity       - file hash manifests to detect tampering
    audit_chain     - tamper-evident hash-chained audit log
    headers         - HTTP security headers + CSP middleware
    guard           - composed facade that wires all of the above

`guard.Guard` is the recommended entry point for call sites; the individual
modules remain importable for tests and specialised use.
"""

from security.audit_chain import AuditChain, AuditRecord, ChainStatus, audit, get_chain
from security.guard import (
    Guard,
    GuardContext,
    GuardError,
    GuardVerdict,
    configure,
    get_guard_facade,
)
from security.headers import (
    DEFAULT_CSP,
    DEFAULT_HEADERS,
    SecurityHeadersMiddleware,
    apply_headers,
    build_csp,
)
from security.integrity import Diff, FileRecord, Manifest, hash_file, verify
from security.path_jail import PathJail, PathJailError
from security.policy_engine import (
    Decision,
    PolicyDenied,
    PolicyEngine,
    PolicyError,
    PolicyResult,
    Rule,
)
from security.prompt_shield import PromptShield, Severity, ShieldVerdict
from security.rate_guard import RateGuard, RateLimitExceeded, TokenBucket
from security.redactor import Redactor, redact, redact_text
from security.secrets_vault import (
    SecretsVault,
    VaultAuthError,
    VaultError,
    VaultLocked,
    generate_passphrase,
    resolve_secret,
)

__all__ = [
    # audit
    "AuditChain",
    "AuditRecord",
    "ChainStatus",
    "audit",
    "get_chain",
    # guard
    "Guard",
    "GuardContext",
    "GuardError",
    "GuardVerdict",
    "configure",
    "get_guard_facade",
    # headers
    "DEFAULT_CSP",
    "DEFAULT_HEADERS",
    "SecurityHeadersMiddleware",
    "apply_headers",
    "build_csp",
    # integrity
    "Diff",
    "FileRecord",
    "Manifest",
    "hash_file",
    "verify",
    # path jail
    "PathJail",
    "PathJailError",
    # policy
    "Decision",
    "PolicyDenied",
    "PolicyEngine",
    "PolicyError",
    "PolicyResult",
    "Rule",
    # prompt shield
    "PromptShield",
    "Severity",
    "ShieldVerdict",
    # rate guard
    "RateGuard",
    "RateLimitExceeded",
    "TokenBucket",
    # redactor
    "Redactor",
    "redact",
    "redact_text",
    # vault
    "SecretsVault",
    "VaultAuthError",
    "VaultError",
    "VaultLocked",
    "generate_passphrase",
    "resolve_secret",
]
