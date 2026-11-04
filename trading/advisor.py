"""Builds the brief an LLM reviews before a trade. It does not call a model and does not decide.

The LLM is asked for a second opinion on one proposal, given the rules' reasons, the news
around it, and the loss report. Its answer is text for the user. It cannot place an order;
the order still needs a preview and a confirm from the user.

Calling a model is not wired in yet. ``brief`` produces the exact text that would be sent.
"""

from __future__ import annotations

from typing import Any


def brief(proposal: dict[str, Any], loss_report_md: str, news_titles: list[str]) -> str:
    lines = [
        "You are reviewing ONE proposed trade. You do not place orders. Say whether the setup",
        "looks sound, name the single biggest risk, and say what would make you skip it.",
        "Be brief. Do not promise profit. If the data is thin, say so.",
        "",
        f"Symbol: {proposal.get('symbol')}",
        f"Proposed side: {proposal.get('side') or 'none'}",
        f"Entry (last close): {proposal.get('entry')}",
        f"Stop loss: {proposal.get('sl')}   Take profit: {proposal.get('tp')}",
        f"Volatility regime: {proposal.get('regime')} (ATR {proposal.get('atr')})",
        "Rule reasons: " + ("; ".join(proposal.get("reasons") or []) or "none"),
        f"Blocked by rule: {proposal.get('blocked') or 'no'}",
        "",
        "Recent high-impact news:",
    ]
    lines += [f"- {t}" for t in news_titles] or ["- none listed"]
    lines += ["", "Loss report (from the journal):", loss_report_md.strip()]
    return "\n".join(lines)


__all__ = ["brief"]
