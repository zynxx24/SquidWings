#!/usr/bin/env python3
"""Passive scanner for the dangerous code patterns cheap models leave behind.

Read-only, no network, standard library only. It greps the project for high-signal constructs
(string-built SQL, eval/exec, os.system, pickle.loads, innerHTML, hardcoded secrets, verify=False,
DEBUG=True, wide-open CORS, disabled CSRF, ...) and prints each as `severity  class  file:line`.

It is a recall aid for the redhat skill, not a verdict: a hit is a lead to confirm by reading the
code and, for a running app, by actually exploiting it. It misses whole classes a grep cannot see
(missing authz, IDOR, business-logic flaws), so it is the floor of the audit, never the ceiling.

    python3 scan_patterns.py [path]            # human-readable, whole tree (default: .)
    python3 scan_patterns.py --json [path]     # machine-readable, for the loop's ledger
    python3 scan_patterns.py --min high [path] # only high and critical

A line ending in `# nosec` or `// nosec` is skipped, so a reviewed, safe line can be silenced.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SEVERITIES = ("critical", "high", "medium", "low")
_RANK = {name: i for i, name in enumerate(SEVERITIES)}

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", "vendor",
    ".next", ".nuxt", "out", "coverage", ".cache", "site-packages", "bower_components", ".tox",
    "migrations",  # generated ORM SQL is noisy and not a user-input path
}
CODE_EXTS = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".php", ".rb", ".go", ".java",
    ".html", ".htm", ".ejs", ".erb", ".twig", ".tpl", ".blade", ".svelte", ".astro",
    ".env", ".yml", ".yaml", ".json", ".ini", ".conf", ".cfg", ".toml", ".sh", ".sql",
}
MAX_FILE_BYTES = 2_000_000
MAX_LINE_CHARS = 2_000          # a longer line is a minified bundle: skip, do not ReDoS on it
_PLACEHOLDER = re.compile(
    r"env|process\.|getenv|os\.environ|import\.meta|\{\{|\$\{|<[%A-Za-z]|your[-_]|example|changeme|"
    r"placeholder|redacted|dummy|sample|xxxx|\*\*\*|\.\.\.|^(?:none|null|true|false|test|secret|password)$",
    re.IGNORECASE,
)

PY = {".py"}
JS = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".svelte", ".astro"}
PHP = {".php", ".blade"}
WEB = JS | {".html", ".htm", ".ejs", ".erb", ".twig", ".tpl", ".vue"}


@dataclass(frozen=True)
class Rule:
    rid: str
    cls: str
    severity: str
    pattern: re.Pattern[str]
    note: str
    exts: frozenset[str] | None = None     # None: every scanned file
    kind: str = "plain"                     # "secret" gets the placeholder filter


def _rule(rid: str, cls: str, sev: str, pat: str, note: str, *, exts: set[str] | None = None,
          flags: int = re.IGNORECASE, kind: str = "plain") -> Rule:
    return Rule(rid, cls, sev, re.compile(pat, flags), note, frozenset(exts) if exts else None, kind)


RULES: tuple[Rule, ...] = (
    # ── secrets & crypto ────────────────────────────────────────────────────────
    _rule("secret-hardcoded", "Hardcoded secret", "high",
          r"""(?:api[_-]?key|secret[_-]?key|secret|password|passwd|pwd|token|access[_-]?key|"""
          r"""private[_-]?key|client[_-]?secret|auth)\s*[:=]\s*["']([^"']{6,})["']""",
          "A credential in source. Move it to the environment and rotate it.", kind="secret"),
    _rule("secret-aws", "Hardcoded AWS key", "critical", r"AKIA[0-9A-Z]{16}",
          "An AWS access key id. Rotate it now and read it from the environment.", flags=0),
    _rule("secret-privkey", "Private key in repo", "critical",
          r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
          "A private key committed to the tree. Remove, rotate, and gitignore it.", flags=0),
    _rule("weak-hash", "Weak hash for secrets", "medium", r"\b(?:hashlib\.)?(?:md5|sha1)\s*\(",
          "MD5/SHA1 is broken for passwords or signatures. Use argon2/bcrypt (passwords) or SHA-256+ (integrity)."),
    _rule("jwt-alg-none", "JWT alg none", "high", r"""["']?alg["']?\s*[:=]\s*["']none["']""",
          "JWT with alg=none accepts unsigned tokens: anyone forges any identity."),
    _rule("verify-false", "Verification disabled", "high", r"verify\s*=\s*False",
          "verify=False disables TLS checks (requests) or signature checks (jwt). Remove it."),
    _rule("tls-reject-off", "TLS check off (Node)", "high", r"rejectUnauthorized\s*:\s*false",
          "rejectUnauthorized:false turns off TLS validation. Remove it."),
    _rule("debug-on", "Debug mode on", "high", r"(?:DEBUG\s*=\s*True|debug\s*[:=]\s*True)",
          "Django DEBUG=True leaks settings and stack traces; Flask debug=True exposes an RCE console. Off in production."),
    # ── SQL injection ───────────────────────────────────────────────────────────
    _rule("sql-fstring", "SQL built with f-string", "high",
          r"""(?:execute|executemany|exec|query|raw|prepare)\s*\(\s*f["'][^"']*\b"""
          r"""(?:select|insert|update|delete|where|from|values)\b""",
          "User data interpolated straight into SQL. Use parameterised queries (placeholders + params).", exts=PY | JS),
    _rule("sql-concat", "SQL built by concatenation", "high",
          r"""(?=.*\b(?:select|insert|update|delete)\b)(?=.*(?:["']\s*\+\s*\w|\w\s*\+\s*["'])).+""",
          "SQL glued to a variable with +. Use parameters, never string building."),
    _rule("sql-percent", "SQL built with % formatting", "high",
          r"""["'][^"']*\b(?:select|insert|update|delete)\b[^"']*%[sd]?[^"']*["']\s*%""",
          "Old-style %-formatted SQL. Pass values as query parameters instead."),
    _rule("sql-template", "SQL in a template literal", "high",
          r"""(?:query|execute|raw)\s*\(\s*`[^`]*\$\{[^}]*\}[^`]*`""",
          "A ${...} inside a SQL template literal is injection. Use the driver's $1/? placeholders.", exts=JS),
    # ── command / code execution ────────────────────────────────────────────────
    _rule("os-system", "os.system", "critical", r"\bos\.system\s*\(",
          "os.system runs a shell. If any part is user data it is command injection. Use subprocess with a list and no shell.", exts=PY),
    _rule("shell-true", "subprocess shell=True", "high", r"shell\s*=\s*True",
          "shell=True with any user data is command injection. Pass an argument list and drop shell=True.", exts=PY),
    _rule("node-exec", "child_process.exec", "high", r"child_process\.(?:exec|execSync)\s*\(",
          "exec runs a shell string. Use execFile/spawn with an args array for untrusted input.", exts=JS),
    _rule("php-shell", "PHP shell execution", "critical", r"\b(?:shell_exec|passthru|proc_open|popen)\s*\(",
          "Running a shell with request data is command injection. Avoid, or escapeshellarg every argument.", exts=PHP),
    _rule("eval", "eval()", "critical", r"(?<![\w.])eval\s*\(",
          "eval on anything a user can influence is remote code execution. Remove it; parse data instead."),
    _rule("new-function", "new Function()", "high", r"\bnew\s+Function\s*\(",
          "new Function(str) is eval by another name. Remove it.", exts=JS),
    _rule("py-exec", "exec()", "high", r"(?<![\w.])exec\s*\(",
          "exec runs arbitrary code. Never build it from input.", exts=PY),
    # ── deserialization ─────────────────────────────────────────────────────────
    _rule("pickle", "pickle.load(s)", "critical", r"\b(?:pickle|cPickle)\.loads?\s*\(",
          "Unpickling attacker data is RCE. Use JSON, or sign the data and verify before loading.", exts=PY),
    _rule("yaml-load", "yaml.load without SafeLoader", "high", r"\byaml\.load\s*\((?![^)]*Safe)",
          "yaml.load builds arbitrary objects. Use yaml.safe_load.", exts=PY),
    _rule("marshal", "marshal.load(s)", "high", r"\bmarshal\.loads?\s*\(",
          "marshal on untrusted bytes is unsafe. Use a data format.", exts=PY),
    _rule("php-unserialize", "unserialize()", "high", r"\bunserialize\s*\(",
          "unserialize on request data enables object-injection chains. Use json_decode.", exts=PHP),
    _rule("node-deserialize", "node-serialize/vm", "critical", r"node-serialize|vm\.runInNewContext",
          "These run code from serialized input. Remove them.", exts=JS),
    # ── XSS / output encoding ───────────────────────────────────────────────────
    _rule("inner-html", "innerHTML sink", "high", r"\.innerHTML\s*=|\.outerHTML\s*=|insertAdjacentHTML\s*\(",
          "Assigning HTML from a variable is XSS. Use textContent, or sanitise with DOMPurify first.", exts=JS),
    _rule("react-danger", "dangerouslySetInnerHTML", "high", r"dangerouslySetInnerHTML",
          "Only with sanitised HTML. Prefer rendering text.", exts=JS),
    _rule("vue-html", "v-html", "high", r"\bv-html\b",
          "v-html renders raw HTML. Sanitise or avoid for anything user-supplied.", exts=WEB),
    _rule("doc-write", "document.write", "medium", r"document\.write(?:ln)?\s*\(",
          "document.write with input is XSS and blocks the parser. Build nodes instead.", exts=JS),
    _rule("jinja-safe", "Jinja |safe / autoescape off", "medium",
          r"\|\s*safe\b|autoescape\s*[:=]\s*(?:False|false|off)",
          "|safe and autoescape off disable HTML escaping. Only on content you produced and trust.", exts=WEB | {".py"}),
    _rule("django-mark-safe", "mark_safe", "medium", r"\bmark_safe\s*\(",
          "mark_safe tells the template not to escape. Never wrap user input in it.", exts=PY),
    _rule("blade-raw", "Blade {!! !!}", "medium", r"\{!!",
          "{!! !!} prints unescaped. Use {{ }} for anything user-supplied.", exts={".php", ".blade"}),
    _rule("jquery-html", "jQuery .html(var)", "medium", r"\.html\s*\(\s*[A-Za-z_$][\w$]*\s*\)",
          "$(x).html(variable) is an XSS sink. Use .text() or sanitise.", exts=JS),
    # ── path traversal / file access ────────────────────────────────────────────
    _rule("sendfile-var", "File served from request", "high",
          r"(?:send_file|sendFile|res\.sendFile|readFile(?:Sync)?|file_get_contents)\s*\([^)]*\b"
          r"(?:req|request|params|query|body|input|_GET|_POST|_REQUEST)\b",
          "A file path from the request without a jail is path traversal (../../etc/passwd). Resolve and confirm it stays inside the allowed root."),
    _rule("open-request", "open() on request data", "medium",
          r"\bopen\s*\([^)]*\b(?:request|params|query|body)\b",
          "Opening a path built from input can escape the directory. Validate against an allowlist or a jailed base path.", exts=PY),
    # ── SSRF ────────────────────────────────────────────────────────────────────
    _rule("ssrf", "Outbound request to a user URL", "medium",
          r"(?:requests\.(?:get|post|put|request)|urllib\.request\.urlopen|axios\.(?:get|post)|fetch)\s*\("
          r"[^)]*\b(?:req|request|params|query|body|url|target|host|endpoint)\b",
          "Fetching a URL the user controls is SSRF (reaches cloud metadata, internal services). Allowlist hosts; block private IPs and redirects."),
    # ── CORS / CSRF / cookies ───────────────────────────────────────────────────
    _rule("cors-wildcard", "CORS allow-origin *", "high",
          r"""access-control-allow-origin["']?\s*[:,]\s*["']?\*""",
          "Origin * (especially with credentials) lets any site call the API as the user. Use an allowlist."),
    _rule("cors-open", "CORS wide open", "medium", r"CORS\s*\(\s*app\s*\)|cors\s*\(\s*\)|origin\s*:\s*true",
          "A default-open CORS config trusts every origin. Pass an explicit origin allowlist."),
    _rule("csrf-off", "CSRF disabled", "high",
          r"@csrf_exempt|csrf\s*[:=]\s*(?:False|false|0|off)|WTF_CSRF_ENABLED\s*=\s*False|csrfProtection\s*:\s*false",
          "A state-changing route without CSRF protection can be triggered from another site. Keep CSRF on for cookie-auth forms."),
    _rule("cookie-insecure", "Insecure cookie flags", "medium",
          r"httponly\s*[:=]\s*(?:False|false)|secure\s*[:=]\s*(?:False|false)|samesite\s*[:=]\s*[\"']?none",
          "Session cookies need HttpOnly, Secure, and SameSite=Lax/Strict."),
    # ── randomness / exposure ───────────────────────────────────────────────────
    _rule("weak-random", "Weak RNG", "medium",
          r"\bMath\.random\s*\(|\brandom\.(?:random|randint|choice|randrange)\s*\(",
          "Only a concern for tokens/passwords/OTP. For those use secrets (Python) or crypto.randomBytes (Node), not a PRNG."),
    _rule("bind-all", "Server bound to 0.0.0.0", "low", r"0\.0\.0\.0",
          "Binding to all interfaces exposes the app on the network. Intentional for a server, a mistake for a dev box."),
)


def _severity_filter(name: str) -> set[str]:
    if name not in _RANK:
        return set(SEVERITIES)
    return {s for s in SEVERITIES if _RANK[s] <= _RANK[name]}


def iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        suffix = ".blade" if path.name.endswith(".blade.php") else path.suffix.lower()
        if suffix not in CODE_EXTS:
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        yield path, suffix


def scan_file(path: Path, suffix: str, root: Path) -> list[dict]:
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    if b"\x00" in raw[:4096]:
        return []
    text = raw.decode("utf-8", errors="replace")
    rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
    out: list[dict] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if len(line) > MAX_LINE_CHARS:
            continue
        stripped = line.rstrip()
        if stripped.endswith("# nosec") or stripped.endswith("// nosec"):
            continue
        for rule in RULES:
            if rule.exts is not None and suffix not in rule.exts:
                continue
            match = rule.pattern.search(line)
            if not match:
                continue
            if rule.kind == "secret":
                value = match.group(1) if match.groups() else ""
                if _PLACEHOLDER.search(value) or len(set(value)) <= 2:
                    continue
            out.append({
                "severity": rule.severity, "class": rule.cls, "rule": rule.rid,
                "file": rel, "line": lineno, "code": line.strip()[:200], "note": rule.note,
            })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan for dangerous code patterns (redhat skill).")
    parser.add_argument("path", nargs="?", default=".", help="Directory or file to scan (default: .)")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument("--min", default="low", choices=SEVERITIES, help="Lowest severity to report.")
    args = parser.parse_args()

    root = Path(args.path).resolve()
    wanted = _severity_filter(args.min)
    findings: list[dict] = []
    if root.is_file():
        suffix = ".blade" if root.name.endswith(".blade.php") else root.suffix.lower()
        findings = scan_file(root, suffix, root.parent)
    else:
        for path, suffix in iter_files(root):
            findings.extend(scan_file(path, suffix, root))
    findings = [f for f in findings if f["severity"] in wanted]
    findings.sort(key=lambda f: (_RANK[f["severity"]], f["file"], f["line"]))

    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES}
    if args.json:
        print(json.dumps({"total": len(findings), "counts": counts, "findings": findings}, ensure_ascii=False, indent=1))
        return 0

    if not findings:
        print("No dangerous patterns matched. The grep floor is clean — now do the parts a grep cannot see:")
        print("  missing authorization / IDOR, broken auth and sessions, business-logic flaws, dependency CVEs.")
        return 0
    width = max(len(f["class"]) for f in findings)
    last = ""
    for f in findings:
        if f["severity"] != last:
            print(f"\n== {f['severity'].upper()} ==")
            last = f["severity"]
        print(f"  {f['class']:<{width}}  {f['file']}:{f['line']}")
        print(f"  {'':<{width}}  {f['code']}")
    summary = ", ".join(f"{counts[s]} {s}" for s in SEVERITIES if counts[s])
    print(f"\n{len(findings)} lead(s): {summary}.")
    print("Each is a lead, not a verdict: confirm by reading the code and exploiting the running app. "
          "A grep cannot see missing authz, IDOR, or logic flaws — check those by hand.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
