"""Set up SquidWings on Windows, macOS, or Linux with one command, using only Python.

    python scripts/setup.py              # .venv + requirements.txt
    python scripts/setup.py --addon trading --addon studio

It makes ``.venv`` next to main.py (with uv when it is installed, otherwise ``python -m venv``),
installs requirements.txt into it, and prints how to start. Add-ons stay off unless named:
``--addon`` runs ``main.py addon install <name>`` with the new environment.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
MIN_PYTHON = (3, 12)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def run(argv: list[str]) -> None:
    print("  $", " ".join(argv))
    subprocess.run(argv, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--addon", action="append", default=[], help="Add-on to install and switch on (trading, studio)")
    args = parser.parse_args()
    if sys.version_info < MIN_PYTHON:
        print(f"Butuh Python {'.'.join(map(str, MIN_PYTHON))} atau lebih baru; ini {sys.version.split()[0]}.")
        return 1
    uv = shutil.which("uv")
    if not venv_python().exists():
        print("==> Membuat .venv")
        run([uv, "venv", "--seed", "--python", f"{sys.version_info[0]}.{sys.version_info[1]}", str(VENV)] if uv
            else [sys.executable, "-m", "venv", str(VENV)])
    print("==> Memasang requirements.txt")
    py = str(venv_python())
    # requirements.lock holds every package to a tested version, so a new upload upstream cannot slip in.
    held = ["-c", "requirements.lock"] if (ROOT / "requirements.lock").is_file() else []
    run([uv, "pip", "install", "--python", py, "-r", "requirements.txt", *held] if uv
        else [py, "-m", "pip", "install", "-r", "requirements.txt", *held])
    for name in args.addon:
        print(f"==> Add-on {name}")
        run([py, "main.py", "--no-update", "addon", "install", name])
    activate = r".venv\Scripts\activate" if sys.platform == "win32" else "source .venv/bin/activate"
    print(f"\nSelesai. Mulai dengan:\n  {activate}\n  python main.py web\n"
          "Add-on (trading, studio) mati bawaan: python main.py addon list")
    return 0


if __name__ == "__main__":
    sys.exit(main())
