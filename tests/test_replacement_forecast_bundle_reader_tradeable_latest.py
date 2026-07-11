# Lifecycle: created=2026-06-10; last_reviewed=2026-07-11; last_reused=2026-07-11
# Purpose: Prove readiness binds one exact live-grade replacement posterior.
# Reuse: Re-audit no-fallback identity and freshness before changing posterior selection.
"""Relationship tests for readiness-bound replacement posterior selection.

The current readiness dependency is the only posterior identity licensed for a decision.
A non-live-grade bound row, expired readiness, or stale source cycle fails closed; the reader
never substitutes an older row under a different certificate.
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
            runtime_layer, training_allowed,
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
            "live",
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
    """The reader's live-grade q-mode set MUST equal the live gate's eligibility set.

    If the live gate (event_reactor_adapter) ever changes which q-modes are admissible, the
    reader's preference predicate must move with it — a drift here would let the reader serve a
    row the live gate then rejects (or vice-versa), reopening the clobber category by a side door.
    """
    from src.data import replacement_forecast_bundle_reader as reader
    from src.engine import event_reactor_adapter as adapter

    assert reader._REPLACEMENT_Q_MODE_LIVE_ELIGIBLE == adapter._REPLACEMENT_Q_MODE_LIVE_ELIGIBLE


def test_diagnostic_bounded_row_is_not_live_readable() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_BAYES_PRECISION_FUSION_MISSING,
        with_bounds=True,
    )
    readiness = _readiness(
        posterior_id=posterior_id,
        computed_at=_dt(6, 7, 30),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 7, 30),
    )

    result = _read(conn, readiness, decision_time=_dt(6, 12))

    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE"
    assert result.bundle is None


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
# Relationship 1: readiness bound to a newer non-live-grade row fails closed;
# an older FUSED row cannot borrow the newer certificate.
# ---------------------------------------------------------------------------
def test_newer_bounds_less_readiness_cannot_borrow_older_fused() -> None:
    conn = _conn()
    # Older live-authority FUSED row (00Z cycle, ~12h before decision -> within staleness bound).
    fused_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
    )
    # NEWER bounds-less diagnostic row (06Z cycle, instruments lag -> BAYES_PRECISION_FUSION_CAPTURE_MISSING).
    diagnostic_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 6),
        source_available_at=_dt(6, 11),
        computed_at=_dt(6, 11, 30),
        q_mode=_BAYES_PRECISION_FUSION_MISSING,
        with_bounds=False,
    )
    assert diagnostic_id > fused_id
    # Readiness points at the newer bounds-less posterior, so the exact certified row
    # is non-executable and the older row cannot be substituted.
    readiness = _readiness(
        posterior_id=diagnostic_id,
        computed_at=_dt(6, 11, 30),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 11, 30),
    )
    result = _read(conn, readiness, decision_time=_dt(6, 12))
    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE"


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
    # No fallback note when the newest row is itself live-grade.
    assert "tradeable_latest_selection" not in dict(result.bundle.provenance_json)


# ---------------------------------------------------------------------------
# Relationship 3: older FUSED row beyond the staleness bound -> NOT served
#   (fail-closed, no silent laundering of a stale cycle into live authority).
# ---------------------------------------------------------------------------
def test_older_fused_beyond_staleness_is_blocked() -> None:
    conn = _conn()
    # FUSED but the cycle is 06-04 00Z vs decision 06-06 12:00 == 60h > 30h bound.
    stale_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(4, 0),
        source_available_at=_dt(4, 7),
        computed_at=_dt(4, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
    )
    # Newer bounds-less diagnostic row on top (also stale-cycle, irrelevant — it's bounds-less).
    diagnostic_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 6),
        source_available_at=_dt(6, 11),
        computed_at=_dt(6, 11, 30),
        q_mode=_BAYES_PRECISION_FUSION_MISSING,
        with_bounds=False,
    )
    readiness = _readiness(
        posterior_id=stale_id,
        computed_at=_dt(6, 11, 30),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 11, 30),
    )
    result = _read(conn, readiness, decision_time=_dt(6, 12))
    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_LIVE_CYCLE_AGE_EXCEEDS_BOUND"


# ---------------------------------------------------------------------------
# Relationship 4: once readiness advances to a NULL-bounds row, eligibility closes
# until a new live-grade certificate is materialized.
# ---------------------------------------------------------------------------
def test_readiness_advance_to_bounds_less_closes_eligibility() -> None:
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

    # The 06Z bounds-less wave lands on top and becomes the exact readiness dependency.
    diagnostic_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 6),
        source_available_at=_dt(6, 11),
        computed_at=_dt(6, 11, 30),
        q_mode=_BAYES_PRECISION_FUSION_MISSING,
        with_bounds=False,
    )
    readiness_after = _readiness(
        posterior_id=diagnostic_id,
        computed_at=_dt(6, 11, 30),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 11, 30),
    )
    after = _read(conn, readiness_after, decision_time=_dt(6, 12))
    assert after.ok is False
    assert after.reason_code == "REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE"


# ---------------------------------------------------------------------------
# Relationship 5: a readiness-bound row missing q_ucb cannot license either side;
# an older both-bounds row remains a different, uncertified decision-time identity.
def test_readiness_bound_q_ucb_missing_cannot_borrow_older_bounds() -> None:
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
    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE"
