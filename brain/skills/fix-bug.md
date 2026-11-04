---
name: fix-bug
description: Find the real cause of a bug and fix it with the smallest change.
triggers: bug, error, perbaiki, fix, gagal, crash, traceback, exception, rusak, tidak jalan, ga jalan, nggak jalan, salah, debug, failing, broken
---
# Fixing a bug

Work in this order. Do not skip to the edit.

1. **Reproduce.** Get the exact error text, or run the failing case with `run_checks` or `execute_command`. If you cannot reproduce it, say so before guessing.
2. **Locate.** Start at the last line of the stack trace that is in this project. Use `find_symbol` on the failing function and `code_outline` on large files. Use `read_file` with `start_line` and `end_line` instead of reading whole files.
3. **Explain the cause in one sentence** before you edit: "X fails because Y". If you cannot write that sentence, keep reading. A fix without a known cause usually moves the bug.
4. **Fix the cause, not the symptom.** Do not add a `try/except` that hides the error, a special case for one input, or a default that masks bad data.
5. **Make the smallest change.** Do not refactor, rename, or reformat nearby code in the same fix.
6. **Check other callers.** If you changed a function's behaviour, run `find_symbol` on it and confirm its callers still work.
7. **Verify.** Run the original failing case again, then `run_checks`. Report what you ran and what it printed.

If two attempts fail, stop and re-read the error. Your assumption is probably wrong: check the path, the argument names, the version, and whether the code you are reading is the code that runs.

Common causes worth checking first: `None` or empty input, an off-by-one, a wrong import or path, a missing `await`, a mutated shared value, stale state or cache, and a type or format mismatch at a boundary (JSON, database, API).

## How to think this through

Hold three things in your head at once: what should happen, what happens instead, and where the two split. Write the split point down before you read more code. Every step should shrink the area where the bug can live. If a step does not shrink it, you are browsing, not debugging.

Treat your first theory as a suspect, not a conclusion. Before you act on it, ask what you would expect to see if it were false, and go and look. A fix that passes once proves little if you never saw the failure. Check earlier in this chat too: if a similar change was made in a recent turn, that change is the first suspect.
