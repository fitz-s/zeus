# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: operator "Zeus Data Ingest + Collection Efficiency Refactor" spec
#   §"Source contracts", RESHAPED per operator directive 2026-05-24 ("don't reinvent
#   what we have, drop or reshape if needed") + Phase-0 audit: the spec's standalone
#   SourceContract would be a 4th parallel registry duplicating data_sources_registry,
#   forecast_source_registry, and source_release_calendar. This module is instead a
#   read-through COMPOSING VIEW over those three existing authorities — zero re-declared facts.
"""Composed source-contract view — PR1 of the Data Temporal Kernel program.

The refactor spec asked for a unified ``SourceContract`` so downstream layers (job
registry, frontier, watermarks) have one typed handle per source. A standalone contract
registry, however, would re-declare facts already owned by three BINDING authorities:

  * ``config/source_release_calendar.yaml``                  — temporal facts (via TemporalPolicy)
  * ``architecture/data_sources_registry_2026_05_08.yaml``   — family / publisher / provenance
  * ``src/data/forecast_source_registry.py``                 — forecast tier / roles / gates

Re-declaring those would create a fourth source of truth that drifts — the wrapper-layer
anti-pattern the project methodology forbids. So ``SourceContract`` here is a *view*: it
holds the ``TemporalPolicy`` and reads family/publisher/tier/roles THROUGH to the existing
registries. ``live_authorization`` and ``backfill_only`` are properties delegating to the
``TemporalPolicy`` — there is no independent authority field to drift.

Where the registries disagree (e.g. calendar ``source_id='tigge'`` vs registry id
``'tigge_mars'``), the view surfaces the gap as ``family=None`` rather than inventing an
alias map; resolving that alias is the job of ``scripts/source_contract_lint.py``
(assertion 1) and a later registry-fix PR, not of this composer.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml

from src.data.source_time import TemporalPolicy, load_temporal_policy

_REGISTRY_PATH = (
    Path(__file__).resolve().parents[2]
    / "architecture"
    / "data_sources_registry_2026_05_08.yaml"
)


@lru_cache(maxsize=1)
def _registry_by_id() -> dict[str, dict[str, Any]]:
    """Index data_sources_registry sources by their ``id`` (cached read-through)."""
    with _REGISTRY_PATH.open() as f:
        data = yaml.safe_load(f)
    return {src["id"]: src for src in data.get("sources", [])}


@dataclass(frozen=True)
class SourceContract:
    """A read-through view binding one calendar entry to its registry facts.

    No temporal/authority fact is stored independently here: ``temporal`` carries the
    calendar facts; ``family``/``publisher``/``forecast_tier``/``forecast_roles`` are
    resolved from the existing registries at construction; ``live_authorization`` and
    ``backfill_only`` delegate to ``temporal``. Nothing to drift.
    """

    calendar_id: str
    temporal: TemporalPolicy

    # Read THROUGH from data_sources_registry (None when the registry lacks coverage,
    # e.g. the tigge/tigge_mars alias gap — surfaced, not papered over).
    # ACTIVATION BARRIER (PR review #329 K): a downstream consumer (job registry / frontier /
    # rate-limit manager) MUST treat family=None as an explicit unresolved-alias finding and
    # refuse to derive routing from it — resolve the alias in the authoritative registry first.
    family: Optional[str]
    publisher: Optional[str]

    # Read THROUGH from forecast_source_registry (None/() for non-forecast families).
    forecast_tier: Optional[str]
    forecast_roles: tuple[str, ...]

    @property
    def source_id(self) -> str:
        return self.temporal.source_id

    @property
    def live_authorization(self) -> bool:
        """Delegates to TemporalPolicy — single authority for live eligibility."""
        return self.temporal.live_authorization

    @property
    def backfill_only(self) -> bool:
        """Delegates to TemporalPolicy — single authority for backfill status."""
        return self.temporal.backfill_only


def _forecast_tier_and_roles(source_id: str) -> tuple[Optional[str], tuple[str, ...]]:
    """Read tier/roles through to forecast_source_registry; ({}, ()) when absent.

    Imported lazily: forecast_source_registry pulls ingest clients, and this view must
    be importable in contexts that do not need the forecast runtime.
    """
    try:
        from src.data.forecast_source_registry import SOURCES
    except Exception:  # pragma: no cover - registry import is environment-dependent
        return None, ()
    spec = SOURCES.get(source_id)
    if spec is None:
        return None, ()
    roles = tuple(getattr(spec, "allowed_roles", ()) or ())
    return getattr(spec, "tier", None), roles


def load_source_contract(calendar_id: str) -> SourceContract:
    """Compose a :class:`SourceContract` view for ``calendar_id``.

    Reads one calendar entry (via TemporalPolicy) and resolves the matching registry
    facts. Re-declares nothing. Raises ``KeyError`` if the calendar entry is absent.
    """
    temporal = load_temporal_policy(calendar_id)
    registry = _registry_by_id().get(temporal.source_id)
    family = registry.get("category") if registry else None
    publisher = registry.get("publisher") if registry else None
    forecast_tier, forecast_roles = _forecast_tier_and_roles(temporal.source_id)
    return SourceContract(
        calendar_id=calendar_id,
        temporal=temporal,
        family=family,
        publisher=publisher,
        forecast_tier=forecast_tier,
        forecast_roles=forecast_roles,
    )
