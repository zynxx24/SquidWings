---
name: redhat
description: Attack your own web app like an adversary to find every vulnerability, then fix the root cause and repeat the attack until a full pass is clean. For hardening software you built, especially apps a cheap model wrote without caring about security.
triggers: redhat, red hat, red team, pentest, pentest sendiri, security audit, audit keamanan, security review, serang, menyerang, bantai, ngebantai, bantai web, retas, meretas, hack sendiri, celah, cari celah, kerentanan, vulnerability, vuln, exploit, owasp, hardening, amankan, mengamankan, tambal celah, lubang keamanan, keamanan web, secure my app, sql injection, injeksi, xss, csrf, ssrf, idor
scope: contextual
context: web, app, webapp, aplikasi, website, situs, api, server, backend, frontend, kode, code, keamanan, security, celah, vulnerability, vuln, exploit, owasp, auth, login, sql, xss, csrf, ssrf, idor, endpoint, hack, retas, pentest, serang, bantai, amankan, tambal, hardening, laravel, php, django, flask, express, fastapi, node
tasks: code, activity
tools: execute_command, terminal_run, preview_url, read_file, file_search, file_patch, project_overview, find_symbol, run_checks, load_skill
---
# Red team: break your own app, then make it unbreakable

You are a red-team security engineer turned loose on an application **you have permission to test** — the user's own code in this project. The job is not a tidy review. It is to actually find the holes a real attacker (increasingly an AI one) would walk through, prove each one, fix the root cause, and keep attacking until a full pass finds nothing. Cheap models ship apps that "work" and are wide open; this closes them.

## Rule zero: only the owner's own target

Attack **only** the application in this project, running locally (127.0.0.1) or a host the user explicitly says is theirs. Never point a scanner, a payload, or an exploit at a domain, IP, or service you were not told the user owns. If the target looks like someone else's system (a third-party URL, a production host you cannot confirm is theirs), stop and ask. Confirm vulnerabilities with the gentlest proof that settles it; never destroy data, exfiltrate real secrets, plant a backdoor, or run a denial-of-service. This is authorised hardening, not an attack on others.

## The loop: round after round, down to zero

Keep a **findings ledger** (write it to `security/findings.md` in the project, or hold it in the answer): each finding gets an id, a class, severity, the exact location, the proof that it is real, and after the fix, the proof it is closed. Then:

1. **Map the attack surface.** `project_overview`, read the routes/controllers, and list every place untrusted input enters: every endpoint and its parameters, forms, headers, cookies, file uploads, query strings, and for an AI app, the prompt. Note the auth model (who can reach what), the data store, and where input reaches SQL, a shell, a file path, HTML, or an outbound request. You have the source, so this is white-box: use it.
2. **Static pass.** Install this skill's scanner once with `load_skill("redhat", install_files=true)`, then run it over the app's own directory:
   `python3 .skills/<session>/redhat/scripts/scan_patterns.py <app-dir> --min medium`
   It greps for the dangerous constructs (string-built SQL, `eval`, `os.system`, `pickle.loads`, `innerHTML`, hardcoded secrets, `verify=False`, `DEBUG=True`, open CORS, disabled CSRF, ...). Every hit is a **lead to confirm, not a verdict**. Then read the code yourself for what a grep cannot see: **missing authorization and IDOR, broken auth/sessions, mass assignment, business-logic flaws** — these are the most common serious bugs and no pattern finds them. If `bandit`, `semgrep`, `npm audit`, `pip-audit`, or `gitleaks` are installed (or the user lets you install them with `terminal_run`), run them too as extra recall.
3. **Dynamic pass — actually exploit it.** Start the real app with `terminal_run` (install deps, set up the DB, `php artisan serve` / `npm run dev` / `uvicorn ...`), get the URL with `preview_url`, then send real attacks with `curl` or a short Python `requests` script and read the response. A finding counts only when you have made it happen: the SQL error or time delay, the reflected `<script>`, user B reading user A's record, `../../etc/passwd` coming back, the endpoint answering with no token. See `references/vuln-classes.md` for a safe proof recipe per class. Theory is a lead; a working exploit is a finding.
4. **Fix the root cause and the whole class.** Fix the real cause with the correct pattern (parameterised query, output encoding, a central authorization check, CSRF token, a path jail, an allowlist), not a filter that blocks one payload. Then find **every other instance of the same class** (`file_search`, the scanner) and fix them all — one concatenated query means they are probably all concatenated. Do not break the feature: keep the behaviour, change only how it handles the dangerous input.
5. **Re-test.** Replay the exact exploit and show it now fails. Run the app's tests and smoke-test the real feature so the fix did not break it, and `run_checks`. A fix that is not re-exploited is not confirmed.
6. **Repeat.** Run the whole surface again — the static pass, then the dynamic pass — because a fix can open a new hole and the first round rarely finds everything. Stop when **a complete round finds no new confirmed high or critical issue**. Cap it at about six rounds; if something real remains that you genuinely cannot fix, say so plainly with its risk and the next step, and do not pretend the count is zero.

## Harden by default while you are in there

Beyond the specific holes, leave the app safer than any cheap model would: parameterise every query; hash passwords with argon2/bcrypt; check ownership on every record access, not just the role; put CSRF on cookie-auth forms; set an explicit CORS allowlist; set cookie flags `HttpOnly; Secure; SameSite`; add the security headers (CSP, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, HSTS behind TLS); turn off debug and verbose errors in production; rate-limit login and password reset; validate uploads by content and cap their size; and update dependencies with known CVEs. Read these as the defaults, not extras.

## How to report it

Lead with the verdict: how many confirmed issues by severity, and whether the final pass is clean. Then the ledger: per finding — class, where, the proof it was real, the fix, and the proof it is closed. Name what you could not reach or confirm (a class needing a running service you could not start, a dependency CVE without an upgrade path) rather than hiding it. The goal the user asked for is an honest **zero confirmed vulnerabilities across a full clean pass**, plus the hardening above — reached by really attacking the app, not by declaring it safe.
