---
name: clean-code
description: Avoid the patterns AI agents leave behind, based on the aislop rules, and fix what aislop reports.
triggers: slop, aislop, rapikan, bersihkan, clean, cleanup, review, refactor, kualitas, quality
---
# Writing code without AI slop

These are the habits that make generated code recognisable. Call `aislop_scan` when it is available: it scores the project from 0 to 100 and lists each finding as `file:line rule`. Fix errors first, then `ai-slop/*` warnings. Leave formatting to the project's formatter.

**Comments**
- Do not restate the code (`# return the value`) or narrate it (`# Step 1: load the data`, decorative separators).
- Do not mention your process ("as requested", "updated to fix").
- Write a comment only for what the code cannot say: why, a constraint, a surprising case.

**Errors**
- Never leave an empty `except` or `catch`, or one that only logs and carries on. Handle it, add context and re-raise, or let it propagate.
- Catch the specific error you can handle, not `Exception` with `pass`.
- Do not hide a failure behind a default. `x or {}`, `.get("a", {}).get("b")`, and a fallback that turns missing data into a normal-looking value make bugs invisible. If the value is required, fail loudly.
- Do not add a `try` that only rethrows the same error.

**Imports and dependencies**
- Only import what exists in the project's manifest. An invented package is the most common agent bug, so check `package.json` or `pyproject.toml` first.
- Remove unused imports and merge duplicates.

**Structure**
- No wrappers that only forward their arguments, no helpers used once, and no copies of a type or function that already exists. Search first with `find_symbol`.
- Remove dead code, code after `return`, and conditions that are always true.
- Split functions that have grown too long instead of adding another branch.
- Replace long `if`/`elif` chains on one value with a table or a map.

**Leftovers**
- Remove `console.log`, debug `print`, `TODO` stubs, and placeholder names like `data2`, `temp1`, or `helper_1`.
- Python: no mutable default arguments, and no `range(len(x))` where `enumerate` works.
- TypeScript: no `as any`, `as unknown as X`, or `@ts-ignore` unless you explain why.

**Tests**
- A test must be able to fail. `assert True` or comparing two fixed literals proves nothing.

**Security**
- No hardcoded secrets or environment-specific URLs: read them from configuration.
- No `eval`, no SQL built by concatenating strings, no user input in shell commands, and no unsanitised `innerHTML`.

If a finding is a false positive, suppress that one line with `aislop-ignore-next-line <rule> -- reason`, and say why.
