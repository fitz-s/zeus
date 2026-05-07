"""Ensemble-snapshot provenance quarantine contract.

Single source of truth for which ``data_version`` values are forbidden
on the ``ensemble_snapshots`` table. Any writer (live ingest, backfill,
or rebuild) MUST consult this contract before inserting rows. Any
reader that fetches snapshots for calibration MUST call
``assert_data_version_allowed`` on each row before forwarding it to
the Platt training path.

2026-04-14 quarantine rationale
-------------------------------
The TIGGE ``param_167`` (``2t`` / ``stepType=instant``) archive was
downloaded with the wrong physical quantity. Each stored
``members_json`` vector is a **per-member point temperature at
``issue_time + step`` hours (UTC)**, not the per-member local-day
maximum that the live path consumes via
``src/signal/ensemble_signal.py::member_maxes_for_target_date``.

Training Platt on point forecasts but querying it with daily-max
inputs at inference time produces a systematic train/infer geometry
mismatch that neither density normalization nor Platt's ``C*lead_days``
term can repair. The replacement archive is ``param=121.128`` / ``mx2t6``
(``stepType=max``, 6-hour sliding windows) composited into per-member
**local calendar-day max**, tagged in DB as
``tigge_mx2t6_local_calendar_day_max_v1`` (Phase 4 canonical).
The intermediate ``tigge_mx2t6_local_peak_window_max_v1`` tag (peak-window
semantics, now superseded) is also quarantined — it is a different physical
quantity than the local-calendar-day product the live path requires.

Until that replacement lands, the old ``param_167`` variants must be
kept out of every live calibration path — not just filtered at read
time, but refused at write time too, so nothing downstream can rely on
"the table is clean because I cleared it yesterday". This contract is
the refusal point.

Semantics
---------
- ``QUARANTINED_DATA_VERSIONS``: exact-match set — versions that are
  known to exist in the archive and MUST never be touched.
- ``QUARANTINED_DATA_VERSION_PREFIXES``: prefix-match tuple — catches
  near-future variants (e.g. ``tigge_step024_v2_*`` that may be produced
  by an experimental ingest path). Prefix match is deliberately
  conservative: a legitimate ``tigge_mx2t6_*`` version does NOT match
  any quarantine prefix.
- Both sets are additive; extend them if new failure modes surface.

Failure mode on write
---------------------
Writers call ``assert_data_version_allowed`` and let
``DataVersionQuarantinedError`` propagate. That bubbles up the caller
(live ingest, backfill, test fixture) and fails loudly, which is what
we want — silent drops hide bugs.

Failure mode on read
--------------------
Callers can use ``is_quarantined`` to partition rows (drop silently,
count, log). ``rebuild_calibration_pairs_canonical.py`` does this so
dry-run reports always surface the refusal count even when
``--allow-unaudited-ensemble`` is passed.
"""

from __future__ import annotations

from typing import Iterable

from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN

# 2026-05-01: Open Data ENS data_versions added so the ingest daemon can
# write same-day forecasts to ensemble_snapshots_v2 alongside the TIGGE archive
# (which has a 48h public embargo and serves only as a 2-day-lagged training
# backfill). These data_versions are written by src/data/ecmwf_open_data.py.
#
# 2026-05-07: ECMWF Open Data enfo stream deprecated mx2t6/mn2t6 (6h sliding
# aggregations). The stream now serves mx2t3/mn2t3 (3h native). Constants
# renamed to reflect the new physical quantity. Old mx2t6 versions kept in
# the allow-list so the 1568 historical rows remain readable.
ECMWF_OPENDATA_HIGH_DATA_VERSION = "ecmwf_opendata_mx2t3_local_calendar_day_max_v1"
ECMWF_OPENDATA_LOW_DATA_VERSION = "ecmwf_opendata_mn2t3_local_calendar_day_min_v1"

# Legacy versions (mx2t6 era, written before 2026-05-07). Kept in the
# allow-list so historical rows in ensemble_snapshots_v2 remain readable.
_ECMWF_OPENDATA_HIGH_DATA_VERSION_LEGACY = "ecmwf_opendata_mx2t6_local_calendar_day_max_v1"
_ECMWF_OPENDATA_LOW_DATA_VERSION_LEGACY = "ecmwf_opendata_mn2t6_local_calendar_day_min_v1"

# LOW recovery rows carry persisted contract-window evidence proving that the
# mn2t6 construction serves the same local-day settlement object.  They remain
# LOW_LOCALDAY_MIN metric-family rows, not a third metric axis.
TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION = (
    "tigge_mn2t6_local_calendar_day_min_contract_window_v2"
)
ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION = (
    "ecmwf_opendata_mn2t6_local_calendar_day_min_contract_window_v2"
)

# LOW recovery rows carry persisted contract-window evidence proving that the
# mn2t6 construction serves the same local-day settlement object.  They remain
# LOW_LOCALDAY_MIN metric-family rows, not a third metric axis.
TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION = (
    "tigge_mn2t6_local_calendar_day_min_contract_window_v2"
)
ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION = (
    "ecmwf_opendata_mn2t3_local_calendar_day_min_contract_window_v2"
)
# Legacy contract-window version (mn2t6 era, written before 2026-05-07).
# Kept in allow-list so historical recovery rows remain readable.
_ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION_LEGACY = (
    "ecmwf_opendata_mn2t6_local_calendar_day_min_contract_window_v2"
)

CANONICAL_ENSEMBLE_DATA_VERSIONS: frozenset[str] = frozenset({
    HIGH_LOCALDAY_MAX.data_version,
    LOW_LOCALDAY_MIN.data_version,
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
    TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
    ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
    # Legacy — historical rows only; no new writes use these.
    _ECMWF_OPENDATA_HIGH_DATA_VERSION_LEGACY,
    _ECMWF_OPENDATA_LOW_DATA_VERSION_LEGACY,
    _ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION_LEGACY,
})

# M3 (2026-04-24): deprecation alias. The historical name
# ``CANONICAL_DATA_VERSIONS`` applied only to ``ensemble_snapshots_v2``
# writers/readers — it was never observation-level or settlement-level.
# The rename makes the domain explicit and leaves space for parallel
# observation/settlement allowlists below. Keep the alias for one
# release cycle so external callers migrate; drop via a cleanup slice
# once all consumers reference the renamed set.
CANONICAL_DATA_VERSIONS: frozenset[str] = CANONICAL_ENSEMBLE_DATA_VERSIONS

# Module-level identity guard (con-nyx T2-S4 NICE-TO-HAVE #3): fail at
# import time if a future edit silently broadens the alias to a
# different set (e.g., union with settlement/observation). Runtime-
# only test coverage would miss an import by the daemon before tests
# run.
assert CANONICAL_DATA_VERSIONS is CANONICAL_ENSEMBLE_DATA_VERSIONS, (
    "CANONICAL_DATA_VERSIONS deprecation alias must stay object-identical "
    "to CANONICAL_ENSEMBLE_DATA_VERSIONS; semantic divergence would let "
    "non-ensemble data_versions bypass the ensemble write gate via the "
    "legacy symbol."
)

# M3 (2026-04-24): parallel allowlists for the two other canonical
# truth surfaces. Each set is scaffolding — the constants are defined
# so that future writers/readers can cite them; no current consumer
# asserts against these sets yet. Promote one consumer at a time via
# a dedicated slice that wires the assertion into the respective
# writer contract (see `CANONICAL_ENSEMBLE_DATA_VERSIONS` usage at
# ``assert_data_version_allowed`` below for the pattern).
#
# Observation data_version catalog (observation_instants_v2 writer at
# ``src/data/observation_instants_v2_writer.py``).
#
# AUTHORITY TIERS (con-nyx T2-S4 finding 1, 2026-04-24):
#   - PRODUCTION-GROUNDED: ``v1.wu-native`` (verified via grep of
#     ``src/state/schema/v2_schema.py::338,344,365`` +
#     ``src/data/observation_instants_v2_writer.py::154``). Live DB has
#     1,813,662 rows carrying this value.
#   - ASPIRATIONAL: ``v1.hko-native``, ``v1.ogimet-native``,
#     ``v1.meteostat-native``, ``v1.openmeteo-native`` — no production
#     writer or reader cites these today outside this allowlist. They
#     follow the ``v1.<source>-native`` convention + reflect known-planned
#     source paths, but they are not authority-verified.
#
# Before wiring a writer contract (``assert_observation_data_version_
# allowed``) against this set, VERIFY the target entry has an actual
# source writer. Do not treat set membership alone as proof the
# source is live. Fitz Constraint #4 applies: inherited classification
# without provenance is UNVERIFIED until re-validated.
CANONICAL_OBSERVATION_DATA_VERSIONS: frozenset[str] = frozenset({
    "v1.wu-native",         # PRODUCTION-GROUNDED (1.8M rows live)
    "v1.hko-native",         # ASPIRATIONAL — verify before consumer wiring
    "v1.ogimet-native",      # ASPIRATIONAL
    "v1.meteostat-native",   # ASPIRATIONAL
    "v1.openmeteo-native",   # ASPIRATIONAL
})

# Settlement data_version catalog (harvester live-write path at
# ``src/execution/harvester.py::_HARVESTER_LIVE_DATA_VERSION``).
# Settlement data_version is source-scoped, not metric-scoped — a HIGH
# WU-origin settlement row carries ``wu_icao_history_v1`` regardless of
# whether it derives from the HIGH or LOW metric identity (metric
# identity lives in the separate INV-14 columns). Enumerate from the
# harvester writer's dispatch dict.
CANONICAL_SETTLEMENT_DATA_VERSIONS: frozenset[str] = frozenset({
    "wu_icao_history_v1",
    "hko_daily_api_v1",
    "ogimet_metar_v1",
    "cwa_no_collector_v0",
})


class DataVersionQuarantinedError(RuntimeError):
    """Raised when a writer tries to persist a quarantined data_version."""


# Known-bad exact matches. These are the two ``param_167`` variants
# that exist in the 2026-04-14 TIGGE partial archive. Adding them by
# name makes the refusal precise — a future legitimate ingest can use
# any data_version not in this set without tripping the guard.
QUARANTINED_DATA_VERSIONS: frozenset[str] = frozenset({
    "tigge_step024_v1_near_peak",
    "tigge_step024_v1_overnight_snapshot",
    "tigge_partial_legacy",
    # peak_window ≠ local_calendar_day: different physical quantity, superseded by Phase 4
    "tigge_mx2t6_local_peak_window_max_v1",
})


# Prefix matches for conservative blanket refusal. Covers the case where
# someone re-runs the old ingest with a bumped version suffix
# (``tigge_step024_v2_``, ``tigge_param167_v3``, etc.). These are all
# point-in-time ``2t instant`` forecasts — structurally wrong physical
# quantity — so they never belong in a Platt training set. The
# replacement ``tigge_mx2t6_*`` family is intentionally NOT in the
# quarantine list.
QUARANTINED_DATA_VERSION_PREFIXES: tuple[str, ...] = (
    "tigge_step",                    # any tigge_step*_v*
    "tigge_param167",                # any tigge_param167*
    "tigge_2t_instant",              # any hand-tagged point-forecast variant
    "tigge_mx2t6_local_peak_window", # peak-window ≠ calendar-day; version bumps must not escape
)


def is_quarantined(data_version: str | None) -> bool:
    """True if ``data_version`` is forbidden for ensemble_snapshots writes."""
    if not data_version:
        return False
    if data_version in QUARANTINED_DATA_VERSIONS:
        return True
    for prefix in QUARANTINED_DATA_VERSION_PREFIXES:
        if data_version.startswith(prefix):
            return True
    return False


def assert_data_version_allowed(data_version: str | None, *, context: str = "") -> None:
    """Raise ``DataVersionQuarantinedError`` if ``data_version`` is quarantined or unknown.

    Call this from every writer of ``ensemble_snapshots`` — live ingest,
    backfill, test fixtures, rebuild. The ``context`` parameter is
    appended to the error message so operators can see which caller
    tripped the guard.

    Two-stage check:
    1. Quarantine block — rejects known-bad versions by exact name or prefix.
    2. Positive allowlist — rejects any version NOT in the canonical set.
       Prevents unknown/experimental versions from silently entering training.
    """
    if is_quarantined(data_version):
        ctx = f" (context={context})" if context else ""
        raise DataVersionQuarantinedError(
            f"ensemble_snapshots write refused: data_version={data_version!r} "
            f"is quarantined per src/contracts/ensemble_snapshot_provenance.py. "
            f"Quarantine covers: param_167 point forecasts (wrong physical quantity), "
            f"peak-window max (superseded by local-calendar-day semantics). "
            f"Use tigge_mx2t6_local_calendar_day_max_v1 (high track canonical, "
            f"Phase 4+) instead.{ctx}"
        )
    if data_version not in CANONICAL_ENSEMBLE_DATA_VERSIONS:
        ctx = f" (context={context})" if context else ""
        raise DataVersionQuarantinedError(
            f"ensemble_snapshots write refused: data_version={data_version!r} "
            f"is not in the canonical allowlist {sorted(CANONICAL_ENSEMBLE_DATA_VERSIONS)}. "
            f"Only canonical dual-track versions are permitted in ensemble_snapshots_v2.{ctx}"
        )


_VALID_MEMBERS_UNITS: frozenset[str] = frozenset({"degC", "degF"})


class MembersUnitInvalidError(ValueError):
    """Raised when members_unit is missing or not a valid temperature unit.

    Kelvin ("K") is explicitly rejected — ECMWF GRIB delivers members in
    Kelvin but the Zeus pipeline stores and compares in degC. Silent Kelvin
    storage would bias every downstream Platt evaluation by +273.
    """


def validate_members_unit(members_unit: str | None, *, context: str = "") -> None:
    """Raise MembersUnitInvalidError if members_unit is not valid.

    Valid values: "degC", "degF". Rejects None, empty string, and "K".
    Call from every writer of ensemble_snapshots_v2 before INSERT.
    """
    ctx = f" (context={context})" if context else ""
    if not members_unit:
        raise MembersUnitInvalidError(
            f"ensemble_snapshots_v2 write refused: members_unit is missing or "
            f"empty. Must be one of {sorted(_VALID_MEMBERS_UNITS)}.{ctx}"
        )
    if members_unit not in _VALID_MEMBERS_UNITS:
        raise MembersUnitInvalidError(
            f"ensemble_snapshots_v2 write refused: members_unit={members_unit!r} "
            f"is not a valid temperature unit. Must be one of "
            f"{sorted(_VALID_MEMBERS_UNITS)}. Note: 'K' (Kelvin) is rejected "
            f"— convert GRIB Kelvin to degC before storing.{ctx}"
        )


def filter_allowed(
    rows: Iterable[dict],
    *,
    data_version_key: str = "data_version",
) -> tuple[list[dict], list[dict]]:
    """Split an iterable of row-dicts into (allowed, quarantined).

    Reader-side helper. Lets ``rebuild_calibration_pairs_canonical.py``
    report quarantine counts in its dry-run plan without crashing on
    legacy rows.
    """
    allowed: list[dict] = []
    quarantined: list[dict] = []
    for row in rows:
        dv = row.get(data_version_key) if isinstance(row, dict) else None
        if is_quarantined(dv):
            quarantined.append(row)
        else:
            allowed.append(row)
    return allowed, quarantined
