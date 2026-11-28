# SquidWings: notes for Claude Code

SquidWings is an AI agent harness (terminal + web) in **one language, Python 3.12+**, that runs on Windows, macOS and Linux. The UI, prompts and docs are in **Indonesian**; keep new user-facing text in Indonesian, code and comments in English.

## How to work (always)

@.claude/rules/plan-first.md

@.claude/rules/quality-bar.md

@.claude/rules/video-script-first.md

@.claude/rules/lessons.md

## Agent settings: `.claude/` and `.codex/`

| Path | For | What |
|---|---|---|
| `CLAUDE.md` + `.claude/rules/*.md` | both | The rules. Edit these; they are the one source. |
| `.claude/settings.json` | Claude Code | Shared permissions: the check commands are allowed, reading `.env`, credentials and router keys is denied. Personal overrides go in `.claude/settings.local.json` (git-ignored). |
| `.claude/skills/` | Claude Code | `super-plan`, `quality-gate`, `video-script` |
| `.claude/agents/` | Claude Code | `quality-reviewer`: independent six-aspect review of a diff |
| `.codex/config.toml` | Codex | Reasoning effort and room for `AGENTS.md` |
| `AGENTS.md`, `.codex/skills/` | Codex | **Generated**: run `.venv/bin/python scripts/sync_agent_config.py` after editing the rules or skills. A test fails when they drift. |

The harness has the same rules for the models it runs: `brain/system_prompt/` (plan first, six-aspect bar) and the built-in skills `bar-kualitas`, `video-audio` and `voice-over`.

## Run it

```bash
python scripts/setup.py                 # .venv + requirements (uses uv when present)
./run.sh                                # web UI, no venv activation (Windows: run.bat); args go to main.py
.venv/bin/python main.py web            # http://127.0.0.1:8080  (Windows: .venv\Scripts\python)
.venv/bin/python main.py chat           # terminal chat
.venv/bin/python -m pytest -q           # offline test suite, ~3 min
bash scripts/check_all.sh               # ruff, mypy, compile, pytest, import
```

Pass `--no-update` to `main.py` while developing, or it fast-forwards from GitHub on start. Do not run long live-model sessions to test a change: the suite is offline on purpose.

## Layout (each folder has its own README.md)

| Folder | What |
|---|---|
| `brain/` | The brain and harness. `agent/` (orchestrator, agent loop, sessions, teams), `harness/` (autopilot, tool and skill selection, tiers, verifier, guards, relevance ranking, model errors), `classifier/`, `memory/`, `model/` (provider client, registry, router), `system_prompt/`, `config/` (settings, paths, add-on switch), `utils/` (cache, platform_compat, workspace, ...), `security/` (sandbox, policy) |
| `input/` | What reaches the model: context assembly, token limits, think prompts, design styles |
| `skills/` | `builtin/` own skills, `sources.json` GitHub skills (cloned to `.sources/`, never edited), `adapters/` our adjustments |
| `tools/` | Every AI tool, one folder per segment; `toolbox/` holds the hidden on-demand tools |
| `web/` | FastAPI server and chat page |
| `addons/` | Optional, **off by default**: `trading/`, `studio/` (`python main.py addon install <name>`) |
| `data/` | Runtime output (logs, audit chain, reports), git-ignored |

## Rules that keep it working

- **Model modes.** A model the user picks (`model_locked=True`) is used as it is; when it fails, `brain/harness/model_errors.py` explains why and the turn ends. Never add a fallback, advisor or recovery model for a locked pick. **Auto** goes through `brain/harness/autopilot.py` (`ROUTING_QUALITY=smart`): cheapest reliable model for the tier, one tier up after a bad turn.
- **No artificial limits.** The owner's rule: the harness puts no cap on what a model sees; only the provider's window (1M tokens, `DEFAULT_CONTEXT_WINDOW` for unreported models) limits history and context. Do not add caps on history, tools, tool results or skills.
- **Scale.** Skills and tools will keep growing. Rank with `brain/harness/relevance.py` (IDF-weighted triggers, BM25) so the most relevant come first; hidden toolbox tools stay on demand (`find_tools`).
- **Caches** go through `brain/utils/cache.py` (named, bounded, TTL, `clear_session` on chat delete, stats at `/api/cache`). Anything built from files uses `FileWatch` so edits apply without a restart. No new unbounded module-level dicts.
- **Per-chat isolation.** State, caches and installed kits are keyed by the session (`get_active_session()`); a chat never sees another chat's data.
- **Cross-platform.** POSIX-only calls (`termios`, `pty`, `os.killpg`, `preexec_fn`, `os.nice`) go through `brain/utils/platform_compat.py`. Runtime files go through `brain/config/paths.py`, never the repo root.
- **GitHub skills stay original.** Adjust one with `skills/adapters/<name>.md`, not by editing `skills/.sources/`.
- **Add-ons** register their tools, skills and pages only when switched on (`brain/config/edition.py`). New optional features become an `addons/<name>/` with an `addon.json`.
- **Tests** must stay offline (`tests/conftest.py` blocks the network and points data at temp folders). Add a test for new logic.
- **Never commit** `.env`, `data/`, `workspace/`, `vault/`, chat stores, or anything in `.gitignore`.
- **Web turns** run as tasks independent of the WebSocket (`web/live_turns.py`): a dropped page never cancels an answer; only `{"type": "stop"}` does.
- **Changelog:** note every user-visible change in `docs/CHANGELOG.md`.
- Writing: no em dashes in user-facing text; plain words over marketing tone.
