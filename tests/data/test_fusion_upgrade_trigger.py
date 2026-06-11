# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: Task #32 (operator 2026-06-11) — PARTIAL-fusion upgrade trigger. Relationship
#   pins for the SINGLE instrument-set comparison + the idempotency bound:
#     - a posterior fused from {A,B} with capture later containing {A,B,C} for the SAME cycle ⇒
#       exactly ONE upgrade signal (and exactly ONE enqueue marker);
#     - a posterior from {A,B,C} with no new instruments ⇒ ZERO upgrade signals (ZERO enqueues).
#   These are CROSS-MODULE invariants (capture table ⇄ posterior provenance), so they are written
#   as relationship assertions, not function tests of either side alone.
"""Antibody tests for the PARTIAL-fusion upgrade trigger comparison + idempotency marker."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from src.data.replacement_fusion_upgrade_trigger import (
    SOURCE_ID,
    decorrelated_provider_families_of,
    scope_capture_offers_larger_provider_set,
)
from src.state.schema.v2_schema import ensure_replacement_forecast_shadow_schema

UTC = timezone.utc

# Representative model per provider family used in the fixtures. icon_seamless / ecmwf_ifs are
# deliberately NOT decorrelated providers (alias-dedup / anchor) — a fixture using them proves the
# comparison ignores them.
_NCEP = "gfs_global"
_DWD = "icon_global"
_CMC = "gem_global"
_JMA = "jma_seamless"
_UKMO = "ukmo_global_deterministic_10km"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_shadow_schema(conn)
    return conn


def _insert_posterior(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    cycle_iso: str,
    used_models: list[str],
    computed_at: str,
) -> None:
    prov = {"u0r_fusion": {"used_models": used_models}}
    conn.execute(
        """
        INSERT INTO forecast_posteriors
            (source_id, product_id, data_version, city, target_date, temperature_metric,
             source_cycle_time, source_available_at, computed_at, q_json, q_lcb_json,
             posterior_method, dependency_source_run_ids_json, provenance_json,
             trade_authority_status, training_allowed)
        VALUES (?, 'pid', 'dv', ?, ?, ?, ?, ?, ?, '{}', '{}', ?, '{}', ?, 'SHADOW_ONLY', 0)
        """,
        (
            SOURCE_ID, city, target_date, metric, cycle_iso, cycle_iso, computed_at,
            SOURCE_ID, json.dumps(prov),
        ),
    )
    conn.commit()


def _insert_single_runs(
    conn: sqlite3.Connection, *, city: str, target_date: str, metric: str, cycle_iso: str, models: list[str]
) -> None:
    for m in models:
        conn.execute(
            """
            INSERT OR IGNORE INTO raw_model_forecasts
                (model, city, target_date, metric, source_cycle_time, source_available_at,
                 captured_at, lead_days, forecast_value_c, endpoint)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 20.0, 'single_runs')
            """,
            (m, city, target_date, metric, cycle_iso, cycle_iso, cycle_iso),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# RELATIONSHIP PIN 1: posterior {A,B} + capture later contains {A,B,C} (SAME cycle) ⇒ upgrade.
# ---------------------------------------------------------------------------
def test_smaller_set_with_new_instrument_signals_exactly_one_upgrade() -> None:
    conn = _conn()
    cyc = "2026-06-12T06:00:00+00:00"
    # Posterior fused from {NCEP, DWD} (a 2-family served set).
    _insert_posterior(
        conn, city="Testville", target_date="2026-06-13", metric="high", cycle_iso=cyc,
        used_models=["ecmwf_ifs", _NCEP, _DWD, "icon_seamless"], computed_at="2026-06-12T10:00:00+00:00",
    )
    # Capture for the SAME cycle now offers {NCEP, DWD, CMC} — CMC (a NEW family) just landed.
    _insert_single_runs(
        conn, city="Testville", target_date="2026-06-13", metric="high", cycle_iso=cyc,
        models=[_NCEP, _DWD, _CMC],
    )
    verdict = scope_capture_offers_larger_provider_set(
        conn, city="Testville", target_date="2026-06-13", metric="high"
    )
    assert verdict["is_upgrade"] is True
    assert verdict["served_families"] == ["DWD", "NCEP"]
    assert verdict["capturable_families"] == ["CMC", "DWD", "NCEP"]
    assert verdict["new_families"] == ["CMC"], "exactly the one newly-capturable provider family"


# ---------------------------------------------------------------------------
# RELATIONSHIP PIN 2: posterior {A,B,C} + capture {A,B,C} (no new instruments) ⇒ NO upgrade.
# ---------------------------------------------------------------------------
def test_equal_set_signals_no_upgrade() -> None:
    conn = _conn()
    cyc = "2026-06-12T06:00:00+00:00"
    _insert_posterior(
        conn, city="Testville", target_date="2026-06-13", metric="high", cycle_iso=cyc,
        used_models=["ecmwf_ifs", _NCEP, _DWD, _CMC], computed_at="2026-06-12T10:00:00+00:00",
    )
    _insert_single_runs(
        conn, city="Testville", target_date="2026-06-13", metric="high", cycle_iso=cyc,
        models=[_NCEP, _DWD, _CMC],
    )
    verdict = scope_capture_offers_larger_provider_set(
        conn, city="Testville", target_date="2026-06-13", metric="high"
    )
    assert verdict["is_upgrade"] is False
    assert verdict["new_families"] == []


def test_capture_smaller_than_served_is_not_an_upgrade() -> None:
    """A capture that LOST a family (transient) must NEVER trigger a downgrade re-seed: is_upgrade
    requires the served set to be a SUBSET of capturable (strict-superset condition)."""
    conn = _conn()
    cyc = "2026-06-12T06:00:00+00:00"
    _insert_posterior(
        conn, city="Testville", target_date="2026-06-13", metric="high", cycle_iso=cyc,
        used_models=["ecmwf_ifs", _NCEP, _DWD, _CMC], computed_at="2026-06-12T10:00:00+00:00",
    )
    # Capture now only offers {NCEP, DWD} (CMC vanished) — strictly SMALLER.
    _insert_single_runs(
        conn, city="Testville", target_date="2026-06-13", metric="high", cycle_iso=cyc,
        models=[_NCEP, _DWD],
    )
    verdict = scope_capture_offers_larger_provider_set(
        conn, city="Testville", target_date="2026-06-13", metric="high"
    )
    assert verdict["is_upgrade"] is False


def test_no_posterior_is_not_an_upgrade() -> None:
    conn = _conn()
    cyc = "2026-06-12T06:00:00+00:00"
    _insert_single_runs(
        conn, city="Ghost", target_date="2026-06-13", metric="high", cycle_iso=cyc,
        models=[_NCEP, _DWD, _CMC, _JMA, _UKMO],
    )
    verdict = scope_capture_offers_larger_provider_set(
        conn, city="Ghost", target_date="2026-06-13", metric="high"
    )
    assert verdict["is_upgrade"] is False


def test_gem_previous_runs_counts_as_capturable() -> None:
    """gem_global's CURRENT value is served from previous_runs (single_runs structurally
    unservable) — the comparison must mirror that exception so a gem-only-via-previous_runs
    capture counts as the CMC family being capturable."""
    conn = _conn()
    cyc = "2026-06-12T06:00:00+00:00"
    _insert_posterior(
        conn, city="Testville", target_date="2026-06-13", metric="high", cycle_iso=cyc,
        used_models=["ecmwf_ifs", _NCEP, _DWD], computed_at="2026-06-12T10:00:00+00:00",
    )
    _insert_single_runs(
        conn, city="Testville", target_date="2026-06-13", metric="high", cycle_iso=cyc,
        models=[_NCEP, _DWD],
    )
    # gem only via previous_runs (mirrors the materializer's gem exception).
    conn.execute(
        """
        INSERT INTO raw_model_forecasts
            (model, city, target_date, metric, source_cycle_time, source_available_at,
             captured_at, lead_days, forecast_value_c, endpoint)
        VALUES ('gem_global', 'Testville', '2026-06-13', 'high', ?, ?, ?, 1, 20.0, 'previous_runs')
        """,
        (cyc, cyc, cyc),
    )
    conn.commit()
    verdict = scope_capture_offers_larger_provider_set(
        conn, city="Testville", target_date="2026-06-13", metric="high"
    )
    assert verdict["is_upgrade"] is True
    assert "CMC" in verdict["new_families"]


def test_previous_runs_only_non_gem_is_not_capturable() -> None:
    """A non-gem model present ONLY via previous_runs (e.g. jma at an off-cadence cycle) is NOT a
    current value the materializer can fuse — it must NOT count as capturable, or the trigger would
    re-seed a scope that cannot actually upgrade (Beijing 06Z jma case)."""
    conn = _conn()
    cyc = "2026-06-12T06:00:00+00:00"
    _insert_posterior(
        conn, city="Testville", target_date="2026-06-13", metric="high", cycle_iso=cyc,
        used_models=["ecmwf_ifs", _NCEP, _DWD], computed_at="2026-06-12T10:00:00+00:00",
    )
    _insert_single_runs(
        conn, city="Testville", target_date="2026-06-13", metric="high", cycle_iso=cyc,
        models=[_NCEP, _DWD],
    )
    # jma only via previous_runs (NOT the gem exception) — must not become capturable.
    conn.execute(
        """
        INSERT INTO raw_model_forecasts
            (model, city, target_date, metric, source_cycle_time, source_available_at,
             captured_at, lead_days, forecast_value_c, endpoint)
        VALUES ('jma_seamless', 'Testville', '2026-06-13', 'high', ?, ?, ?, 1, 20.0, 'previous_runs')
        """,
        (cyc, cyc, cyc),
    )
    conn.commit()
    verdict = scope_capture_offers_larger_provider_set(
        conn, city="Testville", target_date="2026-06-13", metric="high"
    )
    assert verdict["is_upgrade"] is False
    assert "JMA" not in verdict["capturable_families"]


# ---------------------------------------------------------------------------
# IDEMPOTENCY: the marker UNIQUE index makes a second enqueue for the same
# (scope, cycle, capturable-family-superset) a no-op — at most one re-materialization per
# instrument-set transition.
# ---------------------------------------------------------------------------
def test_marker_unique_bounds_enqueue_to_once_per_superset_transition() -> None:
    conn = _conn()
    args = ("2026-06-12T10:00:00+00:00", "Testville", "2026-06-13", "high",
            "2026-06-12T06:00:00+00:00", "DWD,NCEP", "CMC,DWD,NCEP", "seed1.json")

    def _insert(seed_file: str) -> int:
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO fusion_upgrade_enqueues
                (enqueued_at, city, target_date, metric, source_cycle_time,
                 served_family_set, capturable_family_set, seed_file)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*args[:7], seed_file),
        )
        conn.commit()
        return conn.total_changes - before

    assert _insert("seed1.json") == 1, "first enqueue inserts"
    assert _insert("seed2.json") == 0, "same (scope, cycle, capturable-superset) is a no-op"
    # A LARGER capturable superset (a further provider lands) is a NEW transition -> enqueues again.
    before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO fusion_upgrade_enqueues
            (enqueued_at, city, target_date, metric, source_cycle_time,
             served_family_set, capturable_family_set, seed_file)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (*args[:5], "CMC,DWD,NCEP", "CMC,DWD,JMA,NCEP", "seed3.json"),
    )
    conn.commit()
    assert conn.total_changes - before == 1, "a strictly larger superset is a new transition"


def test_provider_family_mapping_excludes_anchor_and_alias() -> None:
    """The ECMWF anchor (prior) and icon_seamless (alias-dedup probe) are NOT decorrelated
    providers — they must contribute no family, or served counts would be inflated."""
    assert decorrelated_provider_families_of({"ecmwf_ifs", "icon_seamless"}) == frozenset()
    assert decorrelated_provider_families_of({_JMA}) == frozenset({"JMA"})
    assert decorrelated_provider_families_of(
        {_NCEP, _DWD, _CMC, _JMA, _UKMO}
    ) == frozenset({"NCEP", "DWD", "CMC", "JMA", "UKMO"})
