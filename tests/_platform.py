"""What the machine running the tests has, so a test that needs a program says so and skips instead of failing:
git, a working command shell, a real terminal (not Wine's stand-ins when the suite runs on Windows under Wine)."""

from __future__ import annotations

import functools
import os
import shutil
import subprocess


@functools.cache
def has_git() -> bool:
    return shutil.which("git") is not None


@functools.cache
def under_wine() -> bool:
    """Windows Python running under Wine (whose PowerShell, netstat and console are stubs)."""
    if os.name != "nt":
        return False
    try:
        import ctypes

        return hasattr(ctypes.WinDLL("ntdll"), "wine_get_version")
    except (OSError, AttributeError):
        return False


@functools.cache
def shell_works() -> bool:
    """Whether the shell agent commands run in really runs a command (sh; on Windows Git Bash or PowerShell)."""
    from brain.security import sandbox

    try:
        done = subprocess.run(sandbox.shell_argv("echo shell-ok"), capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return "shell-ok" in done.stdout
