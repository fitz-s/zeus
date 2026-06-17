# Created: 2026-06-11
# Last reused or audited: 2026-06-17
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

# Representative model per provider family used in the fixtures. ecmwf_ifs is deliberately NOT
# a decorrelated provider (anchor/prior) — a fixture using it proves the comparison ignores it.
# icon_seamless was the alias-dedup probe and was removed from the candidate set entirely on
# 2026-06-17 (it also contributed no family). 2026-06-17: the NCEP/CMC reps are the high-res
# nests (gfs_hrrr 3km / gem_hrdps 2.5km) — the coarse globals gfs_global/gem_global AND
# jma_seamless were dropped and are no longer family members. The contract is now 4 families
# {NCEP, DWD, CMC, UKMO}.
_NCEP = "gfs_hrrr"
_DWD = "icon_global"
_CMC = "gem_hrdps_continental"
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
    prov = {"bayes_precision_fusion": {"used_models": used_models}}
    conn.execute(
        """
        INSERT INTO forecast_posteriors
            (source_id, product_id, data_version, city, target_date, temperature_metric,
             source_cycle_time, source_available_at, computed_at, q_json, q_lcb_json,
             posterior_method, dependency_source_run_ids_json, provenance_json,
             trade_authority_status, training_allowed)
        VALUES (?, 'pid', 'dv', ?, ?, ?, ?, ?, ?, '{}', '{}', ?, '{}', ?, 'DIAGNOSTIC_ONLY', 0)
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
        used_models=["ecmwf_ifs", _NCEP, _DWD], computed_at="2026-06-12T10:00:00+00:00",
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
        models=[_NCEP, _DWD, _CMC, _UKMO],
    )
    verdict = scope_capture_offers_larger_provider_set(
        conn, city="Ghost", target_date="2026-06-13", metric="high"
    )
    assert verdict["is_upgrade"] is False


def test_legacy_gem_global_is_not_cmc_but_gem_hrdps_is() -> None:
    """2026-06-17 coarse-global removal antibody: gem_global is no longer a CMC family member, so a
    stray legacy gem_global capture must NOT register CMC. The new CMC rep gem_hrdps_continental
    (served via single_runs) is what counts. Re-adding gem_global to CMC would flip both halves RED."""
    assert "CMC" not in decorrelated_provider_families_of({"gem_global"})
    assert decorrelated_provider_families_of({_CMC}) == frozenset({"CMC"})
    # At the comparison level: a posterior {NCEP,DWD} whose capture adds gem_hrdps (CMC) upgrades.
    conn = _conn()
    cyc = "2026-06-12T06:00:00+00:00"
    _insert_posterior(
        conn, city="Testville", target_date="2026-06-13", metric="high", cycle_iso=cyc,
        used_models=["ecmwf_ifs", _NCEP, _DWD], computed_at="2026-06-12T10:00:00+00:00",
    )
    _insert_single_runs(
        conn, city="Testville", target_date="2026-06-13", metric="high", cycle_iso=cyc,
        models=[_NCEP, _DWD, _CMC],  # gem_hrdps lands -> CMC newly capturable
    )
    verdict = scope_capture_offers_larger_provider_set(
        conn, city="Testville", target_date="2026-06-13", metric="high"
    )
    assert verdict["is_upgrade"] is True
    assert "CMC" in verdict["new_families"]


def test_non_conus_city_excludes_absent_ncep_cmc_no_phantom_upgrade() -> None:
    """DOMAIN-AWARE RED-ON-REVERT (2026-06-17): for a non-CONUS/non-NA city (Tokyo) NCEP and CMC
    are STRUCTURALLY ABSENT — expected_provider_families_for_city(Tokyo) is {DWD,UKMO}. A
    stray out-of-domain NCEP capture (a legacy gfs_hrrr row) must NOT become a capturable-AND-
    expected growth target, so a posterior already serving {DWD,JMA,UKMO} sees NO upgrade.
    Removing the per-city expected intersection would let the stray row trigger a phantom
    re-enqueue forever -> this goes RED."""
    from src.config import runtime_cities_by_name  # noqa: PLC0415
    from src.data.replacement_fusion_upgrade_trigger import (  # noqa: PLC0415
        expected_provider_families_for_city,
    )

    tok = runtime_cities_by_name().get("Tokyo")
    assert tok is not None, "Tokyo must be a configured city for this domain test"
    assert expected_provider_families_for_city(float(tok.lat), float(tok.lon), 1) == frozenset(
        {"DWD", "UKMO"}
    )
    conn = _conn()
    cyc = "2026-06-12T06:00:00+00:00"
    _insert_posterior(
        conn, city="Tokyo", target_date="2026-06-13", metric="high", cycle_iso=cyc,
        used_models=["ecmwf_ifs", _DWD, _UKMO], computed_at="2026-06-12T10:00:00+00:00",
    )
    # the real served set {DWD,UKMO} (2026-06-17: JMA dropped) PLUS a stray out-of-domain NCEP
    # capture (gfs_hrrr row) that the domain gate must exclude.
    _insert_single_runs(
        conn, city="Tokyo", target_date="2026-06-13", metric="high", cycle_iso=cyc,
        models=[_DWD, _UKMO, "gfs_hrrr"],
    )
    verdict = scope_capture_offers_larger_provider_set(
        conn, city="Tokyo", target_date="2026-06-13", metric="high"
    )
    assert verdict["is_upgrade"] is False, verdict
    assert "NCEP" not in verdict["capturable_families"], verdict
    assert verdict["new_families"] == []


def test_previous_runs_only_provider_is_capturable_and_upgrades() -> None:
    """The generalized 没有新的就用老的 serving rule (replacement_current_value_serving) serves ANY
    provider absent from single_runs at the cycle from its previous_runs row at the same natural
    key, branded. A posterior that dropped that provider is exactly the PARTIAL fusion the upgrade
    trigger must detect. (2026-06-17: the original vehicle here was jma_seamless at 06Z; jma was
    dropped from the fusion, so the substitution is pinned on a surviving provider — ukmo_global.)"""
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
    # ukmo_global only via previous_runs at the same natural key (single_runs absent this cycle):
    # served by substitution => the UKMO family is capturable.
    conn.execute(
        """
        INSERT INTO raw_model_forecasts
            (model, city, target_date, metric, source_cycle_time, source_available_at,
             captured_at, lead_days, forecast_value_c, endpoint)
        VALUES (?, 'Testville', '2026-06-13', 'high', ?, ?, ?, 1, 20.0, 'previous_runs')
        """,
        (_UKMO, cyc, cyc, cyc),
    )
    conn.commit()
    verdict = scope_capture_offers_larger_provider_set(
        conn, city="Testville", target_date="2026-06-13", metric="high"
    )
    assert "UKMO" in verdict["capturable_families"], (
        "a previous_runs-substitutable provider must count as capturable — the serving authority "
        "(read_current_instrument_values) is the shared single rule and WILL fuse it"
    )
    assert verdict["is_upgrade"] is True
    assert verdict["new_families"] == ["UKMO"]


def test_conus_far_lead_does_not_over_expect_lead_capped_nests() -> None:
    """LEAD-AWARE RED-ON-REVERT (2026-06-17 critic fix): the NCEP/CMC nests are lead-capped
    (ncep_nbm=3, gfs_hrrr=2, gem_hrdps=2). For a CONUS city at a lead PAST those caps NCEP/CMC
    cannot serve -> must NOT be expected, else a far-lead scope false-flags PARTIAL and re-fires
    the upgrade loop this contract exists to kill. (Reverting the expected-set to lead 0 makes it
    expect NCEP/CMC at lead 5 -> RED.)"""
    from src.config import runtime_cities_by_name  # noqa: PLC0415
    from src.data.replacement_fusion_upgrade_trigger import (  # noqa: PLC0415
        expected_provider_families_for_city,
    )

    chi = runtime_cities_by_name().get("Chicago")
    assert chi is not None, "Chicago must be a configured CONUS city for this lead test"
    lat, lon = float(chi.lat), float(chi.lon)
    # lead 1 (within every cap): CONUS expects NCEP + CMC + the pure globals.
    assert {"NCEP", "CMC", "DWD", "UKMO"} <= expected_provider_families_for_city(lat, lon, 1)
    # lead 3 (== ncep_nbm cap, past gem_hrdps cap 2): NCEP still expected, CMC NOT.
    mid = expected_provider_families_for_city(lat, lon, 3)
    assert "NCEP" in mid and "CMC" not in mid, mid
    # lead 5 (past every nest cap): only the pure globals remain.
    assert expected_provider_families_for_city(lat, lon, 5) == frozenset({"DWD", "UKMO"})


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
        (*args[:5], "CMC,DWD,NCEP", "CMC,DWD,NCEP,UKMO", "seed3.json"),
    )
    conn.commit()
    assert conn.total_changes - before == 1, "a strictly larger superset is a new transition"


def test_provider_family_mapping_excludes_anchor_and_dropped_models() -> None:
    """The ECMWF anchor (prior) is NOT a decorrelated provider — it must contribute no family.
    icon_seamless was removed from the candidate set entirely (2026-06-17 alias-dedup removal),
    but any stray row in provenance must still map to no family (it was never in DECORRELATED_PROVIDER_FAMILIES).
    jma_seamless was DROPPED (2026-06-17), so stray jma rows must also map to no family."""
    assert decorrelated_provider_families_of({"ecmwf_ifs"}) == frozenset()
    assert decorrelated_provider_families_of({"icon_seamless"}) == frozenset(), (
        "icon_seamless was removed from the candidate set (2026-06-17) — stray rows must contribute no family"
    )
    assert decorrelated_provider_families_of({"ecmwf_ifs", "icon_seamless"}) == frozenset()
    assert decorrelated_provider_families_of({"jma_seamless"}) == frozenset(), (
        "jma_seamless was dropped from the fusion — it is no longer a decorrelated provider family"
    )
    assert decorrelated_provider_families_of(
        {_NCEP, _DWD, _CMC, _UKMO}
    ) == frozenset({"NCEP", "DWD", "CMC", "UKMO"})
