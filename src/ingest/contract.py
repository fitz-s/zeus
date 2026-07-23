# Created: 2026-07-08
# Authority basis: docs/rebuild/EXECUTION_MASTER_2026-07-07.md §E R3 (ingest 契约化);
#   docs/rebuild/whole_system_first_principles_2026-07-07.md §2.1 (ingest verdict);
#   docs/operations/current/plans/data_temporal_kernel/PLAN.md (PR1 SourceContract precursor).
"""SourceContract — the target-namespace declarative registry for R3 (ingest contractualization).

This module is the sole source-contract binding
law for this name: a standalone contract registry that re-declares temporal/family/tier facts
already owned by ``config/source_release_calendar.yaml`` / ``architecture/data_sources_registry_
2026_05_08.yaml`` / ``src/data/forecast_source_registry.py`` would be a FOURTH source of truth
(operator directive 2026-05-24, "don't reinvent what we have, drop or reshape if needed";
enforced by its contract tests). This module builds
INTO the target namespace (``src/ingest/``) by relocating that composing view here and extending
it with exactly the fields the R3 packet asks for and the old view did not yet carry: ``clock_law``
(gridded-ceiling vs station-own-clock — the dichotomy ``_replacement_cycle_availability_poll_if_
needed`` hard-coded per call site instead of declaring), ``dependents`` (the downstream reactions
a source's arrival triggers), and ``fetch_ref`` / ``parse_ref`` / ``clock_check_ref`` (dotted-path
pointers into the existing fetchers — this table does NOT re-implement fetch/parse, it POINTS at
the fetchers that already exist elsewhere, honoring the same "compose, don't duplicate" law).
No compatibility re-export or second contract surface exists.

CLOCK LAW (notepad law, verbatim): station sources (cwa/hko) carry their own provider cycle
clock — they must NEVER be gated behind the gridded freshness ceiling. Concretely:

  * ``"gridded_ceiling"`` — the row's fetch is sequenced behind a shared gridded-model cycle-
    availability probe (``src/data/replacement_cycle_availability.py``): numerical weather
    models publish on a shared ~00Z/06Z/12Z/18Z schedule and the anchor leg's own availability
    gates when the whole basket is safe to fetch.
  * ``"own_clock"`` — the row's ``clock_check`` consults ONLY that source's own cursor / provider
    metadata. Station forecast adapters (``hko_fnd``, ``cwa_township`` — see
    ``src/data/station_forecast_adapter.py``) and every non-forecast family (observation, solar,
    market_topology, ...) fall here: they poll on their own native cadence and must never be
    blocked on another source's gridded-model publish state.

FIVE-CONCERN DISSECTION of ``_replacement_cycle_availability_poll_if_needed`` (src/data/
replacement_forecast_production.py:876-1066) — each incident-patched concern's destination:

  1. Anchor gridded-cycle availability poll + leg fetch (2026-06-11 K4.0b(a))
     -> SourceContract row ``openmeteo_ecmwf_ifs_9km`` (clock_law=gridded_ceiling;
        clock_check_ref -> replacement_cycle_availability.resolve_anchor_cycle_availability;
        fetch_ref -> download_replacement_forecast_current_targets.download_current_target_
        openmeteo_inputs [reference only — that script is HARD-BOUNDARY, not edited]).
  2. Source-clock probe delegation (probe_openmeteo_source_clock_updates call inside the tick)
     -> REDUNDANT under the generic scheduler: this *is* the generic scheduler's own clock_check
        step (src/ingest/scheduler.py:run_source_contract_tick). No separate concern remains once
        a row's clock_check is scheduler-owned; source_clock_update_probe.py's cursor-diff shape
        is what src/ingest/scheduler.py generalizes.
  3. bayes_precision_fusion extras fan-out gate (coverage-probe + fixpoint latch, 2026-06-13/16)
     -> SourceContract row ``bayes_precision_fusion_extras`` (clock_law=gridded_ceiling; its own
        coverage-based clock_check, independent cadence from the anchor leg's cursor).
  4. Fusion-upgrade reseed trigger (Task #32, partial-fusion upgrade detection)
     -> ``dependents`` entry ``"fusion_upgrade_reseed"`` on the ``openmeteo_ecmwf_ifs_9km`` row,
        dispatched by the generic scheduler on ARRIVED (src/data/replacement_fusion_upgrade_
        trigger.enqueue_fusion_upgrade_reseeds).
  5. Cycle-advance rematerialization trigger (U5 step 2a, sister of #4)
     -> ``dependents`` entry ``"cycle_advance_reseed"`` on the ``openmeteo_ecmwf_ifs_9km`` row,
        dispatched by the generic scheduler on ARRIVED (src/data/replacement_cycle_advance_
        trigger.enqueue_cycle_advance_reseeds -- module name inferred from call site; see done-
        claim for the exact function this packet did NOT flip live wiring to call).

SCOPE NOTE: this packet builds and unit-tests the generalized mechanism (this module +
src/ingest/scheduler.py). It deliberately does NOT flip ``_replacement_cycle_availability_poll_
if_needed``'s live call site to the new scheduler — that is a seam transplant on the live
forecast-download path and the rebuild constitution (EXECUTION_MASTER §E2.2) requires replay
evidence before a money-path-adjacent seam flips, which this single packet does not carry. The
mapping above is the destination design; the flip is named as remaining work.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional

import yaml

from src.data.source_time import TemporalPolicy, load_temporal_policy

ClockLaw = Literal["gridded_ceiling", "own_clock"]

_REGISTRY_PATH = (
    Path(__file__).resolve().parents[2]
    / "architecture"
    / "data_sources_registry_2026_05_08.yaml"
)

# Station provider prefixes that carry their own publish cycle (read-through mirror of
# src/strategy/live_inference/source_clock_vnext.PROVIDER_FAMILY_PREFIXES' hko_/cwa_ entries —
# NOT re-declared independently; kept as a local tuple because importing source_clock_vnext here
# would pull q-authority modules into every SourceContract consumer, including pure registry
# readers like scripts/source_contract_lint.py).
_STATION_OWN_CLOCK_PREFIXES: tuple[str, ...] = ("hko_", "cwa_")

# Families with no gridded-model concept at all (observation/solar/market_topology/...): a
# "gridded ceiling" only exists within the forecast family, so every non-forecast family is
# own_clock by construction, not by exemption.
_GRIDDED_CEILING_FAMILY = "forecast"


def clock_law_for(source_id: str, family: Optional[str]) -> ClockLaw:
    """The notepad law as executable code: station providers and non-forecast families are
    ``own_clock``; everything else in the forecast family defaults to ``gridded_ceiling``."""
    if any(source_id.startswith(prefix) for prefix in _STATION_OWN_CLOCK_PREFIXES):
        return "own_clock"
    if family != _GRIDDED_CEILING_FAMILY:
        return "own_clock"
    return "gridded_ceiling"


@lru_cache(maxsize=1)
def _registry_by_id() -> dict[str, dict[str, Any]]:
    """Index data_sources_registry sources by their ``id`` (cached read-through)."""
    with _REGISTRY_PATH.open() as f:
        data = yaml.safe_load(f)
    return {src["id"]: src for src in data.get("sources", [])}


@dataclass(frozen=True)
class SourceContract:
    """One typed row per data source: identity + clock law + where its mechanics live.

    Temporal/family/tier facts are read-through (``temporal`` is the sole authority for
    ``live_authorization``/``backfill_only`` when a calendar entry exists; ``None`` when it does
    not — honestly absent, never invented). ``clock_law``/``dependents``/``fetch_ref``/
    ``parse_ref``/``clock_check_ref`` are genuinely NEW facts this table is the first place to
    declare (the R3 packet's contribution) — they are stored fields, not read-through properties,
    because no other registry owns them today.
    """

    source_id: str
    clock_law: ClockLaw

    # Read-through (None when the source has no calendar_id / calendar entry — see module note).
    calendar_id: Optional[str] = None
    temporal: Optional[TemporalPolicy] = None
    family: Optional[str] = None
    publisher: Optional[str] = None
    forecast_tier: Optional[str] = None
    forecast_roles: tuple[str, ...] = ()

    # New R3 facts: dotted "module:function" pointers into the fetchers/parsers that already
    # exist elsewhere (this table POINTS, it does not re-implement — anti-duplication law).
    clock_check_ref: Optional[str] = None
    fetch_ref: Optional[str] = None
    parse_ref: Optional[str] = None
    # Downstream reactions to fire when this row's clock_check reports a new cycle (symbolic
    # names resolved by src/ingest/scheduler.py's dependents_dispatch; see five-concern map above).
    dependents: tuple[str, ...] = ()
    notes: str = ""

    @property
    def live_authorization(self) -> Optional[bool]:
        return self.temporal.live_authorization if self.temporal is not None else None

    @property
    def backfill_only(self) -> Optional[bool]:
        return self.temporal.backfill_only if self.temporal is not None else None


def _forecast_tier_and_roles(source_id: str) -> tuple[Optional[str], tuple[str, ...]]:
    """Read tier/roles through to forecast_source_registry; (None, ()) when absent.

    Imported lazily: forecast_source_registry pulls ingest clients, and this view must be
    importable in contexts that do not need the forecast runtime (e.g. the lint script)."""
    try:
        from src.data.forecast_source_registry import SOURCES  # noqa: PLC0415
    except Exception:  # pragma: no cover - registry import is environment-dependent
        return None, ()
    spec = SOURCES.get(source_id)
    if spec is None:
        return None, ()
    roles = tuple(getattr(spec, "allowed_roles", ()) or ())
    return getattr(spec, "tier", None), roles


def load_source_contract(calendar_id: str) -> SourceContract:
    """Compose a calendar-backed :class:`SourceContract` for ``calendar_id``.

    Reads one calendar entry (via TemporalPolicy) and resolves the matching registry facts.
    Re-declares nothing. Raises ``KeyError`` if the calendar entry is absent."""
    temporal = load_temporal_policy(calendar_id)
    registry = _registry_by_id().get(temporal.source_id)
    family = registry.get("category") if registry else None
    publisher = registry.get("publisher") if registry else None
    forecast_tier, forecast_roles = _forecast_tier_and_roles(temporal.source_id)
    return SourceContract(
        source_id=temporal.source_id,
        clock_law=clock_law_for(temporal.source_id, family),
        calendar_id=calendar_id,
        temporal=temporal,
        family=family,
        publisher=publisher,
        forecast_tier=forecast_tier,
        forecast_roles=forecast_roles,
    )


# ---------------------------------------------------------------------------
# Explicit rows for live sources the release calendar does not (yet) cover. The calendar
# (config/source_release_calendar.yaml) carries only 4 entries today; extending it to cover
# every live source is a config-authoring effort beyond this packet's scope (see done-claim
# "what remains"). These rows are honestly calendar-less (temporal=None) rather than inventing
# calendar facts — they still carry the clock_law/dependents/fetch_ref facts this table exists
# to declare.
# ---------------------------------------------------------------------------

_EXPLICIT_ROWS: tuple[SourceContract, ...] = (
    # Gridded anchor leg driving _replacement_cycle_availability_poll_if_needed concern #1.
    SourceContract(
        source_id="openmeteo_ecmwf_ifs_9km",
        clock_law="gridded_ceiling",
        family="forecast",
        clock_check_ref="src.data.replacement_cycle_availability:resolve_anchor_cycle_availability",
        fetch_ref=(
            "scripts.download_replacement_forecast_current_targets:"
            "download_current_target_openmeteo_inputs"
        ),
        dependents=("fusion_upgrade_reseed", "cycle_advance_reseed"),
        notes="five-concern dissection #1/#4/#5 (see module docstring)",
    ),
    # bayes_precision_fusion extras fan-out — concern #3, own coverage-based clock_check,
    # deliberately NOT gated on the anchor row's cursor (its own cycle-completeness probe).
    SourceContract(
        source_id="bayes_precision_fusion_extras",
        clock_law="gridded_ceiling",
        family="forecast",
        clock_check_ref="src.data.replacement_forecast_production:_extras_cycle_incomplete",
        fetch_ref=(
            "src.data.replacement_forecast_production:"
            "_download_bayes_precision_fusion_extra_raw_inputs_if_needed"
        ),
        notes="five-concern dissection #3 (see module docstring)",
    ),
    # Station forecast adapters — the notepad-law antibody rows: own_clock, never gated behind
    # the gridded ceiling above, per src/data/station_forecast_adapter.py + the
    # _ingest_station_forecasts_live docstring ("Runs on the download lane [...] refreshes at
    # the same ~2x/day cadence as the gridded raw inputs [...]" -- own cron, not gated on
    # anchor cycle availability).
    SourceContract(
        source_id="hko_fnd",
        clock_law="own_clock",
        family="forecast",
        fetch_ref="src.data.station_forecast_adapter:ingest_hko_fnd_live",
        parse_ref="src.data.station_forecast_adapter:parse_hko_fnd_payload",
        notes="station-own-clock antibody row (notepad law)",
    ),
    SourceContract(
        source_id="cwa_township",
        clock_law="own_clock",
        family="forecast",
        fetch_ref="src.data.station_forecast_adapter:ingest_cwa_township_live",
        parse_ref="src.data.station_forecast_adapter:parse_cwa_township_payload",
        notes="station-own-clock antibody row (notepad law)",
    ),
)


_CALENDAR_IDS_PATH = Path(__file__).resolve().parents[2] / "config" / "source_release_calendar.yaml"


def _calendar_backed_rows() -> tuple[SourceContract, ...]:
    """Every SourceContract the release calendar currently covers (best-effort: a malformed or
    incomplete calendar entry is skipped, never crashes the registry build)."""
    rows: list[SourceContract] = []
    try:
        with _CALENDAR_IDS_PATH.open() as f:
            data = yaml.safe_load(f)
    except Exception:  # pragma: no cover - defensive; calendar is a tracked config file
        return ()
    for entry in data.get("entries", ()):
        calendar_id = entry.get("calendar_id") if isinstance(entry, dict) else None
        if not calendar_id:
            continue
        try:
            rows.append(load_source_contract(calendar_id))
        except Exception:  # pragma: no cover - a malformed entry must not break the registry
            continue
    return tuple(rows)


@lru_cache(maxsize=1)
def source_contracts() -> dict[str, SourceContract]:
    """The full SourceContract registry: calendar-backed rows plus the explicit rows above,
    keyed by ``source_id`` (last writer wins if a source_id appears in both — explicit rows are
    applied second so a hand-authored row can refine a calendar-derived one)."""
    rows: dict[str, SourceContract] = {}
    for row in _calendar_backed_rows():
        rows[row.source_id] = row
    for row in _EXPLICIT_ROWS:
        rows[row.source_id] = row
    return rows
