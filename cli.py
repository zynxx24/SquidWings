"""cli.py — thin CLI wrapper for the Agent Router.

Delegates to :func:`main.main` so the documented entry point exists
without duplicating argument parsing logic.

Usage:
    python cli.py chat
    python cli.py ask "What is Python?"
    python cli.py models
    python cli.py health
    python cli.py web --port 8080
"""

from __future__ import annotations

from main import main

__all__ = ["main"]


if __name__ == "__main__":
    main()
