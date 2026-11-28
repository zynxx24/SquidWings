"""Keep Codex's copy of the agent settings in step with Claude Code's.

One source: CLAUDE.md (which imports .claude/rules/*.md with ``@path`` lines) and .claude/skills/.
Generated: AGENTS.md (CLAUDE.md with every import written out, for Codex) and .codex/skills/ (a mirror).

Usage:
    .venv/bin/python scripts/sync_agent_config.py          # write the generated files
    .venv/bin/python scripts/sync_agent_config.py --check  # exit 1 if they are out of date
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "CLAUDE.md"
AGENTS = ROOT / "AGENTS.md"
CLAUDE_SKILLS = ROOT / ".claude" / "skills"
CODEX_SKILLS = ROOT / ".codex" / "skills"
IMPORT = re.compile(r"^@(\S+\.md)\s*$")
HEADER = ("<!-- Generated from CLAUDE.md and .claude/rules/ by scripts/sync_agent_config.py. "
          "Edit those files, not this one. -->\n\n")


def expand(text: str, base: Path, seen: frozenset[Path] = frozenset()) -> str:
    """CLAUDE.md with each ``@path`` import line replaced by the file it names (nested imports too)."""
    out: list[str] = []
    for line in text.splitlines():
        match = IMPORT.match(line)
        if not match:
            out.append(line)
            continue
        target = (base / match.group(1)).resolve()
        if target in seen:
            raise SystemExit(f"import cycle at {match.group(1)}")
        if not target.is_file() or ROOT not in target.parents:
            raise SystemExit(f"import not found inside the repo: {match.group(1)}")
        body = target.read_text(encoding="utf-8").strip()
        out.append(expand(body, target.parent, seen | {target}))
    return "\n".join(out)


def agents_text() -> str:
    return HEADER + expand(SOURCE.read_text(encoding="utf-8"), ROOT).rstrip() + "\n"


def skill_files(folder: Path) -> dict[str, bytes]:
    if not folder.is_dir():
        return {}
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def differences() -> list[str]:
    found: list[str] = []
    if not AGENTS.is_file() or AGENTS.read_text(encoding="utf-8") != agents_text():
        found.append("AGENTS.md")
    if skill_files(CLAUDE_SKILLS) != skill_files(CODEX_SKILLS):
        found.append(".codex/skills/")
    return found


def write() -> None:
    AGENTS.write_text(agents_text(), encoding="utf-8")
    if CODEX_SKILLS.exists():
        shutil.rmtree(CODEX_SKILLS)
    if CLAUDE_SKILLS.is_dir():
        shutil.copytree(CLAUDE_SKILLS, CODEX_SKILLS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only report whether the generated files are current")
    args = parser.parse_args()
    if args.check:
        stale = differences()
        if stale:
            print("out of date: " + ", ".join(stale) + " (run scripts/sync_agent_config.py)")
            return 1
        print("agent config in sync")
        return 0
    write()
    print("wrote AGENTS.md and .codex/skills/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
