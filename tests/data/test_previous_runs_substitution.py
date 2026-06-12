# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: Task #32 follow-up (operator 2026-06-11) — 没有新的就用老的 applied to fusion
#   membership. The gem_global-only previous_runs exception (edc598b440) is generalized into the
#   SINGLE serving authority (src/data/replacement_current_value_serving.py): a provider absent
#   from single_runs at the selected cycle serves its previous_runs row at the SAME natural key,
#   BRANDED served_via="previous_runs" — never dropped, never silent. Live evidence: JMA publishes
#   00/12Z only, so at every 06Z-cadence cycle jma_seamless had 0/49 single_runs rows while its
#   previous_runs leg was 49/49 — the fusion ran served=4/5 and Beijing 06-12 lost all
#   conservative edge (max q_lcb 0.068).
"""Antibodies: generalized previous-runs current-value substitution (single authority).

Relationship pins:
  (a) provider absent from single_runs at the cycle + fresh previous_runs row  => SERVED, branded
      served_via="previous_runs" (the JMA-at-06Z case);
  (b) provider absent from BOTH endpoints                                      => dropped, exactly
      as today;
  (c) gem_global behavior byte-identical to the edc598b440 exception (same value, same row id;
      single_runs priority; natural-key cycle isolation);
  (d) the substituted instrument keeps its OWN lead bucket (lead_days reported verbatim from the
      served row; the walk-forward history at that lead prices the older run — no manual
      down-weighting field exists anywhere);
  (e) the freshness horizon rejects an anomalous stale-keyed row (captured > 24h after its
      cycle) while admitting every live-capture case;
  (f) the fusion-upgrade trigger's capturable set is BY CONSTRUCTION the serving authority's
      key set — a substitutable provider counts as capturable (so PARTIAL scopes upgrade);
  (g) the queue does NOT coverage-skip an upgrade re-seed (upgrade_trigger seeds intentionally
      supersede a covered posterior; their idempotency authority is the fusion_upgrade_enqueues
      marker, not coverage).
"""
from __future__ import annotations

import json
import sqlite3
import types
from pathlib import Path

from src.data.replacement_current_value_serving import (
    PREVIOUS_RUNS_SUBSTITUTION_MAX_AGE_HOURS,
    read_current_instrument_values,
)

CYCLE = "2026-06-11T06:00:00+00:00"
OTHER_CYCLE = "2026-06-11T00:00:00+00:00"
FRESH_CAPTURE = "2026-06-11T14:06:48+00:00"   # 8.1h after the cycle (the live Beijing case)
STALE_CAPTURE = "2026-06-12T07:00:00+00:00"   # 25h after the cycle (beyond the horizon)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE raw_model_forecasts (
            raw_model_forecast_id INTEGER, model TEXT, forecast_value_c REAL,
            city TEXT, metric TEXT, target_date TEXT, lead_days INTEGER,
            source_cycle_time TEXT, endpoint TEXT, captured_at TEXT
        )
        """
    )
    return conn


def _insert(conn, rid, model, value, endpoint, *, cycle=CYCLE, captured=FRESH_CAPTURE, lead=1):
    conn.execute(
        "INSERT INTO raw_model_forecasts VALUES (?,?,?,?,?,?,?,?,?,?)",
        (rid, model, value, "Beijing", "high", "2026-06-12", lead, cycle, endpoint, captured),
    )


def _read(conn):
    return read_current_instrument_values(
        conn, city="Beijing", metric="high", target_date="2026-06-12",
        source_cycle_time_iso=CYCLE,
    )


# -------------------------------------------------------------------------------------
# (a) the JMA-at-06Z case: absent from single_runs, fresh previous_runs => served, branded
# -------------------------------------------------------------------------------------
def test_provider_absent_from_single_runs_served_from_previous_runs_branded() -> None:
    conn = _conn()
    _insert(conn, 1, "gfs_global", 33.0, "single_runs")
    _insert(conn, 2, "jma_seamless", 33.5, "previous_runs")  # JMA: no 06Z single_runs, ever
    out = _read(conn)
    assert "jma_seamless" in out, (
        "a provider structurally unpublished on this cycle's single_runs must serve its "
        "previous_runs row at the same natural key (没有新的就用老的), not be dropped"
    )
    jma = out["jma_seamless"]
    assert jma.value_c == 33.5 and jma.raw_model_forecast_id == 2
    assert jma.served_via == "previous_runs"            # BRANDED — never silent
    assert jma.served_cycle == CYCLE
    assert abs(jma.age_hours - 8.113) < 0.01            # honest capture-age provenance
    prov = jma.as_provenance()
    assert prov["previous_run_substitution"] is True
    assert prov["served_via"] == "previous_runs"
    assert out["gfs_global"].served_via == "single_runs"


# -------------------------------------------------------------------------------------
# (b) absent everywhere => dropped exactly as today
# -------------------------------------------------------------------------------------
def test_provider_absent_from_both_endpoints_stays_dropped() -> None:
    conn = _conn()
    _insert(conn, 1, "gfs_global", 33.0, "single_runs")
    out = _read(conn)
    assert "jma_seamless" not in out
    assert set(out) == {"gfs_global"}


# -------------------------------------------------------------------------------------
# (c) gem byte-identical to the edc598b440 exception
# -------------------------------------------------------------------------------------
def test_gem_behavior_byte_identical_to_declared_exception() -> None:
    conn = _conn()
    _insert(conn, 4, "gem_global", 19.5, "previous_runs")
    out = _read(conn)
    assert out["gem_global"].value_c == 19.5
    assert out["gem_global"].raw_model_forecast_id == 4
    assert out["gem_global"].served_via == "previous_runs"


def test_single_runs_row_wins_over_previous_runs_for_every_model() -> None:
    conn = _conn()
    _insert(conn, 1, "jma_seamless", 33.9, "single_runs")
    _insert(conn, 2, "jma_seamless", 33.5, "previous_runs")
    out = _read(conn)
    assert out["jma_seamless"].value_c == 33.9
    assert out["jma_seamless"].raw_model_forecast_id == 1
    assert out["jma_seamless"].served_via == "single_runs"


def test_substitution_respects_natural_key_cycle() -> None:
    # A previous_runs row from a DIFFERENT cycle must never leak into this capture
    # (preserved from the original gem antibody — the natural key is the freshness anchor).
    conn = _conn()
    _insert(conn, 1, "gfs_global", 33.0, "single_runs")
    _insert(conn, 2, "jma_seamless", 33.5, "previous_runs", cycle=OTHER_CYCLE)
    out = _read(conn)
    assert "jma_seamless" not in out


# -------------------------------------------------------------------------------------
# (d) the substituted instrument keeps its OWN lead bucket — no manual down-weighting
# -------------------------------------------------------------------------------------
def test_substituted_instrument_reports_its_own_lead_bucket() -> None:
    conn = _conn()
    _insert(conn, 1, "jma_seamless", 33.5, "previous_runs", lead=2)
    out = _read(conn)
    assert out["jma_seamless"].lead_days == 2, (
        "the served row's lead_days names the walk-forward history bucket that de-biases and "
        "variance-prices this instrument; the substitution must report it verbatim — the "
        "lead-bucket residual variance is the ONLY mechanism pricing the older run (no manual "
        "down-weighting exists)"
    )
    assert out["jma_seamless"].as_provenance()["lead_days"] == 2


# -------------------------------------------------------------------------------------
# (e) freshness horizon: stale-keyed anomaly rejected; live captures admitted
# -------------------------------------------------------------------------------------
def test_freshness_horizon_rejects_stale_keyed_previous_runs_row() -> None:
    conn = _conn()
    _insert(conn, 1, "jma_seamless", 33.5, "previous_runs", captured=STALE_CAPTURE)
    out = _read(conn)
    assert "jma_seamless" not in out, (
        f"a previous_runs row captured more than {PREVIOUS_RUNS_SUBSTITUTION_MAX_AGE_HOURS}h "
        "after its cycle is an anomalous stale-keyed row, not a live capture — rejected"
    )
    # single_runs rows are NEVER horizon-gated (forward capture is the authority for its cycle).
    _insert(conn, 2, "gfs_global", 33.0, "single_runs", captured=STALE_CAPTURE)
    assert "gfs_global" in _read(conn)


def test_unparseable_captured_at_fails_open_on_same_cycle_key() -> None:
    # The same-natural-key cycle match is the PRIMARY freshness anchor; the parsed age is
    # belt-and-suspenders. A stripped/unparseable capture stamp must not reject a same-cycle row
    # (the fusion wiring harness seeds captured_at='cap').
    conn = _conn()
    _insert(conn, 1, "jma_seamless", 33.5, "previous_runs", captured="cap")
    out = _read(conn)
    assert out["jma_seamless"].served_via == "previous_runs"
    assert out["jma_seamless"].age_hours == 0.0


# -------------------------------------------------------------------------------------
# (f) trigger capturable == serving authority keys (single-builder relationship)
# -------------------------------------------------------------------------------------
def test_trigger_capturable_set_is_the_serving_authority_key_set() -> None:
    from src.data.replacement_fusion_upgrade_trigger import _capturable_models_for_scope

    conn = _conn()
    _insert(conn, 1, "gfs_global", 33.0, "single_runs")
    _insert(conn, 2, "jma_seamless", 33.5, "previous_runs")
    _insert(conn, 3, "icon_global", 32.5, "previous_runs", cycle=OTHER_CYCLE)  # wrong cycle
    capturable = _capturable_models_for_scope(
        conn, city="Beijing", target_date="2026-06-12", metric="high", source_cycle_iso=CYCLE
    )
    assert capturable == set(_read(conn).keys()) == {"gfs_global", "jma_seamless"}, (
        "the trigger's capturable set must be EXACTLY the serving authority's key set — a "
        "substitutable provider (JMA via previous_runs) counts as capturable, so the PARTIAL "
        "posterior that dropped it is detected as upgradeable"
    )


# -------------------------------------------------------------------------------------
# (g) queue does not coverage-skip an upgrade re-seed
# -------------------------------------------------------------------------------------
def _minimal_seed(upgrade: bool) -> dict[str, object]:
    seed: dict[str, object] = {
        "city": "Beijing",
        "target_date": "2026-06-12",
        "temperature_metric": "high",
        "computed_at": "2026-06-11T15:00:00+00:00",
        "baseline_source_run_id": "b0-run",
        "aifs_source_run_id": "aifs-run",
        "openmeteo_source_run_id": "om9-run",
        "openmeteo_payload_json": "payload.json",
        "precision_metadata_json": "precision.json",
        "aifs_samples_json": "samples.json",
        "bins": [{"bin_id": "warm"}],
    }
    if upgrade:
        seed["upgrade_trigger"] = "instrument_set_expansion"
    return seed


def test_queue_does_not_coverage_skip_an_upgrade_reseed(tmp_path, monkeypatch) -> None:
    import src.data.replacement_forecast_shadow_materialization_queue as queue_mod

    # Coverage says EVERYTHING is covered (the exact live state an upgrade seed supersedes:
    # the served=4 posterior has q_lcb NOT NULL).
    monkeypatch.setattr(queue_mod, "_seed_already_covered", lambda **_kw: True)
    built: list[str] = []

    def _fake_builder(seed, *, base_dir):
        built.append(str(seed.get("city")))
        return types.SimpleNamespace(
            ok=True, status="READY", reason_codes=("OK",), request={"stub": True}
        )

    monkeypatch.setattr(
        queue_mod, "build_replacement_forecast_materialization_request", _fake_builder
    )

    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir()
    request_dir = tmp_path / "requests"
    (seed_dir / "normal.json").write_text(json.dumps(_minimal_seed(upgrade=False)))
    (seed_dir / "upgrade.json").write_text(json.dumps(_minimal_seed(upgrade=True)))

    processed, failed, _reasons = queue_mod._prepare_seed_requests(
        seed_dir=seed_dir,
        seed_processed_dir=tmp_path / "seed_processed",
        seed_failed_dir=tmp_path / "seed_failed",
        request_dir=request_dir,
        forecast_db=Path("/nonexistent.db"),
        limit=10,
    )
    assert not failed
    assert len(built) == 1, (
        "exactly the upgrade seed must reach the request builder: the normal seed is "
        "coverage-skipped, the upgrade_trigger seed bypasses coverage (its idempotency "
        "authority is the fusion_upgrade_enqueues marker, not coverage)"
    )
    # The normal seed's sidecar records the coverage skip; the upgrade seed's records a request.
    sidecars = {p.name: json.loads(p.read_text()) for p in (tmp_path / "seed_processed").glob("*.receipt.json")}
    skip_statuses = {s["status"] for s in sidecars.values()}
    assert "SKIPPED_ALREADY_COVERED" in skip_statuses
    request_written = [s for s in sidecars.values() if s.get("request_written")]
    assert len(request_written) == 1
