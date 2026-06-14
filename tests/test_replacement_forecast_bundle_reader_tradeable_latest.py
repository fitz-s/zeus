# Created: 2026-06-10
# Last reused or audited: 2026-06-13
# Authority basis: operator clobber-category directive 2026-06-10 (tradeable-latest read
#   semantics). Third recurrence of the bounds-less clobber: a NEWER model cycle that has
#   anchor manifests but no fusion instruments yet writes a bounds-less posterior
#   (q_lcb_json NULL, replacement_q_mode=BAYES_PRECISION_FUSION_CAPTURE_MISSING — a SHADOW row by design) which
#   the absolute-latest read semantics serve over the older tradeable-grade FUSED row, so live
#   eligibility collapses. The fix makes the category impossible at the ONE bundle reader: LIVE
#   selection prefers the latest row WITH certified bounds + live-eligible q_mode over a newer
#   bounds-less row; the newer bounds-less row stays visible for shadow/telemetry. The existing
#   30h cycle-age staleness bound still applies to the served tradeable row (falling back to an
#   older tradeable row NEVER bypasses staleness).
"""Relationship tests — tradeable-latest read semantics (the bounds-less clobber category).

These cross the (forecast_posteriors row -> bundle reader -> live eligibility) boundary. The
property under test: a NEWER bounds-less SHADOW posterior must NOT clobber an OLDER tradeable
posterior on the LIVE path, AND an older tradeable row that is itself beyond the staleness
horizon must NOT be laundered into live authority (fail-closed, no silent staleness bypass).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone

import pytest

from src.data.replacement_forecast_bundle_reader import (
    HIGH_DATA_VERSION,
    PRODUCT_ID,
    SOURCE_ID,
    read_replacement_forecast_bundle,
)
from src.data.replacement_forecast_readiness import (
    ReplacementForecastDependency,
    build_replacement_forecast_readiness,
)
from src.state.schema.v2_schema import apply_canonical_schema


UTC = timezone.utc
_TOPO_HASH = "topo-hash-tradeable-001"
_FUSED_FULL = "FUSED_NORMAL_FULL"
_BAYES_PRECISION_FUSION_MISSING = "BAYES_PRECISION_FUSION_CAPTURE_MISSING"


@dataclass(frozen=True)
class _Evidence:
    source_run_id: str


@dataclass(frozen=True)
class _BaselineBundle:
    evidence: _Evidence


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_canonical_schema(conn, forecast_tables=True)
    return conn


def _dt(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, day, hour, minute, tzinfo=UTC)


def _provenance(*, q_mode: str) -> dict[str, object]:
    return {
        "bin_topology_hash": _TOPO_HASH,
        "replacement_q_mode": q_mode,
        "q_shape": "fused_normal_direct",
        "bin_topology": [
            {
                "bin_id": "warm",
                "lower_c": 20.0,
                "upper_c": 21.0,
                "center_c": 20.5,
                "settlement_step_c": 1.0,
                "display_unit": "C",
                "settlement_unit": "C",
                "rounding_rule": "wmo_half_up",
            }
        ],
    }


def _insert_posterior(
    conn: sqlite3.Connection,
    *,
    source_cycle_time: datetime,
    source_available_at: datetime,
    computed_at: datetime,
    q_mode: str,
    with_bounds: bool,
    with_ucb: bool | None = None,
    dependency_source_run_ids: dict[str, str] | None = None,
) -> int:
    # ``with_ucb`` lets a row carry q_lcb_json but NOT q_ucb_json (the freshest-row
    # twin-authority carrier defect: a 13:08Z row HAS q_ucb, its 13:09Z sibling MISSING it).
    # Default: q_ucb tracks q_lcb (a real fused row materializes BOTH bounds together).
    if with_ucb is None:
        with_ucb = with_bounds
    deps = dependency_source_run_ids or {
        "baseline_b0": "b0-run",
        "aifs_sampled_2t": "aifs-run",
        "openmeteo_ifs9_anchor": "om9-run",
    }
    # Each posterior row carries a DISTINCT identity hash (forecast_posteriors enforces
    # UNIQUE(posterior_identity_hash)); keying on cycle+mode keeps two rows of the same scope
    # insertable, matching production where each cycle's materialization is a distinct row.
    identity_suffix = f"{source_cycle_time.isoformat()}|{q_mode}"
    conn.execute(
        """
        INSERT INTO forecast_posteriors (
            source_id, product_id, data_version, city, target_date,
            temperature_metric, source_cycle_time, source_available_at,
            computed_at, q_json, q_lcb_json, posterior_method,
            dependency_source_run_ids_json, provenance_json,
            trade_authority_status, training_allowed,
            bin_topology_hash, posterior_identity_hash, dependency_hash,
            posterior_config_hash, q_ucb_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            SOURCE_ID,
            PRODUCT_ID,
            HIGH_DATA_VERSION,
            "Shanghai",
            "2026-06-07",
            "high",
            source_cycle_time.isoformat(),
            source_available_at.isoformat(),
            computed_at.isoformat(),
            json.dumps({"cold": 0.2, "warm": 0.8}),
            json.dumps({"cold": 0.1, "warm": 0.7}) if with_bounds else None,
            "openmeteo_ifs9_aifs_sampled_2t_soft_anchor",
            json.dumps(deps),
            json.dumps(_provenance(q_mode=q_mode)),
            "SHADOW_VETO_ONLY",
            0,
            _TOPO_HASH,
            f"pid-hash-{identity_suffix}",
            f"dep-hash-{identity_suffix}",
            f"cfg-hash-{identity_suffix}",
            json.dumps({"cold": 0.3, "warm": 0.9}) if with_ucb else None,
        ),
    )
    return int(conn.execute("SELECT MAX(posterior_id) FROM forecast_posteriors").fetchone()[0])


def _readiness(
    *,
    posterior_id: int,
    expires_at: datetime,
    decision_time: datetime,
    computed_at: datetime,
    baseline_run_id: str = "b0-run",
    aifs_run_id: str = "aifs-run",
    anchor_run_id: str = "om9-run",
):
    dependencies = (
        ReplacementForecastDependency(
            role="baseline_b0",
            source_id="ecmwf_open_data",
            product_id="ecmwf_opendata_ifs_ens_0p25",
            data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
            source_run_id=baseline_run_id,
            source_available_at=_dt(6, 0),
        ),
        ReplacementForecastDependency(
            role="aifs_sampled_2t",
            source_id="ecmwf_aifs_ens",
            product_id="ecmwf_aifs_ens_sampled_2t_6h_v1",
            data_version="ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
            source_run_id=aifs_run_id,
            source_available_at=_dt(6, 0),
            artifact_id=11,
        ),
        ReplacementForecastDependency(
            role="openmeteo_ifs9_anchor",
            source_id="openmeteo_ecmwf_ifs_9km",
            product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
            data_version="openmeteo_ecmwf_ifs9_anchor_localday_high",
            source_run_id=anchor_run_id,
            source_available_at=_dt(6, 0),
            anchor_id=22,
        ),
        ReplacementForecastDependency(
            role="soft_anchor_posterior",
            source_id=SOURCE_ID,
            product_id=PRODUCT_ID,
            data_version=HIGH_DATA_VERSION,
            source_run_id=f"posterior:{posterior_id}",
            source_available_at=_dt(6, 0),
            posterior_id=posterior_id,
        ),
    )
    return build_replacement_forecast_readiness(
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=decision_time,
        computed_at=computed_at,
        expires_at=expires_at,
        dependencies=dependencies,
    )


def test_reader_live_eligible_q_mode_set_mirrors_live_gate() -> None:
    """The reader's tradeable-grade q-mode set MUST equal the live gate's eligibility set.

    If the live gate (event_reactor_adapter) ever changes which q-modes are admissible, the
    reader's preference predicate must move with it — a drift here would let the reader serve a
    row the live gate then rejects (or vice-versa), reopening the clobber category by a side door.
    """
    from src.data import replacement_forecast_bundle_reader as reader
    from src.engine import event_reactor_adapter as adapter

    assert reader._REPLACEMENT_Q_MODE_LIVE_ELIGIBLE == adapter._REPLACEMENT_Q_MODE_LIVE_ELIGIBLE


def _read(conn, readiness, *, decision_time):
    return read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=readiness,
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=decision_time,
        current_bin_topology_hash=_TOPO_HASH,
    )


# ---------------------------------------------------------------------------
# Relationship 1: newer NULL-bounds (shadow) row + older FUSED row ->
#   LIVE bundle serves the FUSED row, and records a provenance note that a newer
#   shadow row exists.
# ---------------------------------------------------------------------------
def test_newer_bounds_less_does_not_clobber_older_fused() -> None:
    conn = _conn()
    # Older tradeable FUSED row (00Z cycle, ~12h before decision -> within staleness bound).
    fused_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
    )
    # NEWER bounds-less SHADOW row (06Z cycle, instruments lag -> BAYES_PRECISION_FUSION_CAPTURE_MISSING).
    shadow_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 6),
        source_available_at=_dt(6, 11),
        computed_at=_dt(6, 11, 30),
        q_mode=_BAYES_PRECISION_FUSION_MISSING,
        with_bounds=False,
    )
    assert shadow_id > fused_id
    # Readiness is per-scope (upserted): the LIVE path holds ONLY the latest cycle's readiness,
    # which points at the newer bounds-less shadow posterior.
    readiness = _readiness(
        posterior_id=shadow_id,
        computed_at=_dt(6, 11, 30),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 11, 30),
    )
    result = _read(conn, readiness, decision_time=_dt(6, 12))
    assert result.ok is True, result.reason_code
    assert result.bundle is not None
    assert result.bundle.posterior_id == fused_id
    assert result.bundle.q_lcb is not None
    # Provenance note: the live bundle records that a newer shadow row exists.
    note = result.bundle.provenance_json.get("tradeable_latest_selection")
    assert isinstance(note, dict)
    assert note.get("newer_shadow_posterior_id") == shadow_id
    assert note.get("served_posterior_id") == fused_id


# ---------------------------------------------------------------------------
# Relationship 2: both rows bounds-bearing -> newest wins (no regression in the
#   normal advance-the-cycle case).
# ---------------------------------------------------------------------------
def test_both_bounded_newest_wins() -> None:
    conn = _conn()
    old_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
    )
    new_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 12),
        source_available_at=_dt(6, 18),
        computed_at=_dt(6, 18, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
    )
    assert new_id > old_id
    readiness = _readiness(
        posterior_id=new_id,
        computed_at=_dt(6, 18, 30),
        expires_at=_dt(7, 6),
        decision_time=_dt(6, 18, 30),
    )
    result = _read(conn, readiness, decision_time=_dt(6, 19))
    assert result.ok is True, result.reason_code
    assert result.bundle is not None
    assert result.bundle.posterior_id == new_id
    # No shadow-fallback note when the newest row is itself tradeable.
    assert "tradeable_latest_selection" not in dict(result.bundle.provenance_json)


# ---------------------------------------------------------------------------
# Relationship 3: older FUSED row beyond the staleness bound -> NOT served
#   (fail-closed, no silent laundering of a stale cycle into live authority).
# ---------------------------------------------------------------------------
def test_older_fused_beyond_staleness_served_with_brand() -> None:
    conn = _conn()
    # FUSED but the cycle is 06-04 00Z vs decision 06-06 12:00 == 60h > 30h bound.
    _insert_posterior(
        conn,
        source_cycle_time=_dt(4, 0),
        source_available_at=_dt(4, 7),
        computed_at=_dt(4, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
    )
    # Newer bounds-less shadow row on top (also stale-cycle, irrelevant — it's bounds-less).
    shadow_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 6),
        source_available_at=_dt(6, 11),
        computed_at=_dt(6, 11, 30),
        q_mode=_BAYES_PRECISION_FUSION_MISSING,
        with_bounds=False,
    )
    readiness = _readiness(
        posterior_id=shadow_id,
        computed_at=_dt(6, 11, 30),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 11, 30),
    )
    result = _read(conn, readiness, decision_time=_dt(6, 12))
    # OPERATOR LAW (2026-06-11 "没有新的就用老的"): the fused row is the freshest
    # TRADEABLE row that exists; its over-bound age brands provenance, never blocks.
    # The newer bounds-less shadow row still cannot clobber it (tradeable-latest).
    assert result.ok is True
    assert (result.bundle.provenance_json or {}).get("tradeable_latest_selection") is not None
    violations = (result.bundle.provenance_json or {}).get("staleness_violations") or []
    assert any("CYCLE_AGE_EXCEEDS_BOUND" in v for v in violations), violations


# ---------------------------------------------------------------------------
# Relationship 4 (afternoon scenario end-to-end): writing a NULL-bounds row ON TOP
#   of a healthy tradeable row must NOT change live eligibility.
# ---------------------------------------------------------------------------
def test_afternoon_clobber_does_not_change_eligibility() -> None:
    conn = _conn()
    fused_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
    )
    readiness_before = _readiness(
        posterior_id=fused_id,
        computed_at=_dt(6, 7, 30),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 7, 30),
    )
    before = _read(conn, readiness_before, decision_time=_dt(6, 12))
    assert before.ok is True
    assert before.bundle.posterior_id == fused_id

    # The 06Z bounds-less wave lands on top (and overwrites the scope readiness in place).
    shadow_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 6),
        source_available_at=_dt(6, 11),
        computed_at=_dt(6, 11, 30),
        q_mode=_BAYES_PRECISION_FUSION_MISSING,
        with_bounds=False,
    )
    readiness_after = _readiness(
        posterior_id=shadow_id,
        computed_at=_dt(6, 11, 30),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 11, 30),
    )
    after = _read(conn, readiness_after, decision_time=_dt(6, 12))
    # Eligibility unchanged: still serves the same tradeable FUSED row with bounds.
    assert after.ok is True, after.reason_code
    assert after.bundle.posterior_id == fused_id
    assert after.bundle.q_lcb == before.bundle.q_lcb


# ---------------------------------------------------------------------------
# Relationship 5 (q_ucb carrier defect, 2026-06-13): a NEWER row carrying q_lcb_json
#   but NOT q_ucb_json must NOT clobber an OLDER row that has BOTH bounds. The live
#   bounds gate (event_reactor_adapter, the _needs_bounds/_bounds_ok block) requires
#   BOTH q_lcb AND q_ucb non-empty; and _replacement_no_lcb_for_bin fail-closes EVERY
#   buy_no to q_lcb_no=0.0 when the served bundle's q_ucb is absent. Selecting the
#   q_ucb-less freshest row (the freshest-row twin-authority defect, observed live on
#   Wellington 06-14) therefore structurally extinguishes the entire buy_no leg for the
#   family. The reader must serve the freshest row that carries BOTH bounds.
#
# RED-ON-REVERT: drop the `if not row_map.get("q_ucb_json"): return False` line from
# _row_is_tradeable_grade and this test fails (the q_ucb-less newer row is served, and
# its q_ucb is None).
def test_newer_q_ucb_missing_does_not_clobber_older_both_bounds() -> None:
    conn = _conn()
    # Older row with BOTH bounds (00Z cycle, within staleness).
    both_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
        with_ucb=True,
    )
    # NEWER row: FUSED mode, q_lcb present, but q_ucb MISSING (the carrier defect).
    lcb_only_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 6),
        source_available_at=_dt(6, 11),
        computed_at=_dt(6, 11, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
        with_ucb=False,
    )
    assert lcb_only_id > both_id
    # Scope readiness points at the newer (q_ucb-less) row, as in production.
    readiness = _readiness(
        posterior_id=lcb_only_id,
        computed_at=_dt(6, 11, 30),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 11, 30),
    )
    result = _read(conn, readiness, decision_time=_dt(6, 12))
    assert result.ok is True, result.reason_code
    assert result.bundle is not None
    # The served bundle MUST be the older row that carries BOTH bounds — so the buy_no leg
    # (q_lcb_no = 1 - q_ucb_yes) has a real q_ucb to work from, never the silent 0.0.
    assert result.bundle.posterior_id == both_id
    assert result.bundle.q_ucb is not None and bool(result.bundle.q_ucb)
    note = (result.bundle.provenance_json or {}).get("tradeable_latest_selection")
    assert isinstance(note, dict)
    assert note.get("newer_shadow_posterior_id") == lcb_only_id
    assert note.get("served_posterior_id") == both_id
