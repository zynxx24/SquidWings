"""Execution harness: makes the loop, not the model, responsible for discipline.

A cheap model usually fails from lack of discipline rather than lack of
knowledge: it edits files it never read, repeats a failing call, or announces
success without checking. Everything here runs in code and costs no tokens,
except the advisor, which is only called once a run is demonstrably stuck.
"""

from .runtime import Harness, HarnessStats
from .tiers import Scaffold, Tier, scaffold_for, tier_for

__all__ = ["Harness", "HarnessStats", "Scaffold", "Tier", "scaffold_for", "tier_for"]
