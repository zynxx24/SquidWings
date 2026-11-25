"""How models are grouped on the pickers: by provider, then by the upstream behind it.

A router such as 9router serves many upstreams under one URL and names its models
``kg/claude-haiku-5-5``, ``vk/anthropic/claude-sonnet-4.6`` or ``gemini/gemini-3.8-flash``.
One group per provider would put all of them in one long list, so the first segment of the
id (after the provider's own prefix) splits it further: "Default · kg", "Default · vk".
Nothing here knows any router's naming, so a new provider with other names groups itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .rotation import family_of

FALLBACK_PROVIDER = "lain"


@dataclass(frozen=True)
class ModelGroup:
    key: str        # stable id, e.g. "default/kg"
    label: str      # what the picker shows, e.g. "Default · kg"
    provider: str   # provider id, e.g. "default"
    family: str     # upstream segment, "" when the ids carry none


def group_of(model_id: str, provider: str, names: Mapping[str, str] | None = None,
             prefixes: Mapping[str, str] | None = None) -> ModelGroup:
    """The group of one model. ``names`` and ``prefixes`` map provider id to its page name and prefix."""
    provider = provider or FALLBACK_PROVIDER
    name = (names or {}).get(provider) or provider
    prefix = (prefixes or {}).get(provider, "")
    rest = model_id[len(prefix):] if prefix and model_id.startswith(prefix) else model_id
    family = family_of(rest)
    if family:
        return ModelGroup(f"{provider}/{family}", f"{name} · {family}", provider, family)
    return ModelGroup(provider, name, provider, "")


def grouped(models: Iterable[tuple[str, str]], names: Mapping[str, str] | None = None,
            prefixes: Mapping[str, str] | None = None) -> list[tuple[ModelGroup, list[str]]]:
    """``(model_id, provider)`` pairs as sorted groups of sorted ids."""
    found: dict[str, tuple[ModelGroup, list[str]]] = {}
    for model_id, provider in models:
        group = group_of(model_id, provider, names, prefixes)
        found.setdefault(group.key, (group, []))[1].append(model_id)
    return [(g, sorted(ids)) for g, ids in sorted(found.values(), key=lambda item: item[0].label.lower())]


def provider_labels() -> tuple[dict[str, str], dict[str, str]]:
    """Names and prefixes of the providers on the router page."""
    from .store import get_store

    providers = get_store().providers()
    return {p.id: p.name for p in providers}, {p.id: p.prefix for p in providers}
