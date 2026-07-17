# Created: 2026-06-11
# Last reused or audited: 2026-07-16
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
      single_runs priority; future-cycle isolation);
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
import subprocess
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


def test_substitution_uses_prior_cycle_when_selected_cycle_has_no_row() -> None:
    # Live 00Z can be selected by the anchor lane before single-runs has complete local-day
    # coverage for a city. The serving boundary must not go blind: use the newest persisted row
    # no later than the selected cycle, branded by its actual served_cycle.
    conn = _conn()
    _insert(conn, 1, "gfs_global", 33.0, "single_runs")
    _insert(conn, 2, "jma_seamless", 33.5, "previous_runs", cycle=OTHER_CYCLE)
    out = _read(conn)
    assert out["jma_seamless"].value_c == 33.5
    assert out["jma_seamless"].served_cycle == OTHER_CYCLE


def test_substitution_rejects_future_cycle_rows() -> None:
    conn = _conn()
    future_cycle = "2026-06-11T12:00:00+00:00"
    _insert(conn, 2, "jma_seamless", 33.5, "previous_runs", cycle=future_cycle)
    out = _read(conn)
    assert "jma_seamless" not in out


def test_selected_cycle_row_wins_over_prior_cycle_row() -> None:
    conn = _conn()
    _insert(conn, 1, "jma_seamless", 33.2, "previous_runs", cycle=OTHER_CYCLE)
    _insert(conn, 2, "jma_seamless", 33.5, "previous_runs", cycle=CYCLE)
    out = _read(conn)
    assert out["jma_seamless"].value_c == 33.5
    assert out["jma_seamless"].served_cycle == CYCLE


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
    _insert(conn, 3, "icon_global", 32.5, "previous_runs", cycle=OTHER_CYCLE)  # prior possessed cycle
    capturable = _capturable_models_for_scope(
        conn, city="Beijing", target_date="2026-06-12", metric="high", source_cycle_iso=CYCLE
    )
    assert capturable == set(_read(conn).keys()) == {"gfs_global", "jma_seamless", "icon_global"}, (
        "the trigger's capturable set must be EXACTLY the serving authority's key set — a "
        "substitutable provider (same-cycle JMA or prior-cycle icon_global via previous_runs) "
        "counts as capturable, so the PARTIAL posterior that dropped it is detected as upgradeable"
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
    import src.data.replacement_forecast_live_materialization_queue as queue_mod

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


def test_queue_skips_seed_older_than_current_family_posterior(tmp_path, monkeypatch) -> None:
    import src.data.replacement_forecast_live_materialization_queue as queue_mod

    forecast_db = tmp_path / "forecasts.db"
    conn = sqlite3.connect(forecast_db)
    try:
        conn.execute(
            """
            CREATE TABLE forecast_posteriors (
                source_id TEXT,
                city TEXT,
                target_date TEXT,
                temperature_metric TEXT,
                source_cycle_time TEXT,
                computed_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO forecast_posteriors VALUES (?, ?, ?, ?, ?, ?)",
            (
                queue_mod.SOURCE_ID,
                "Beijing",
                "2026-06-12",
                "high",
                "2026-06-11T12:00:00+00:00",
                "2026-06-11T20:00:00+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(queue_mod, "_seed_already_covered", lambda **_kw: False)
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
    seed = {**_minimal_seed(upgrade=False), "source_cycle_time": "2026-06-11T06:00:00+00:00"}
    (seed_dir / "old-cycle.json").write_text(json.dumps(seed), encoding="utf-8")

    processed, failed, _reasons = queue_mod._prepare_seed_requests(
        seed_dir=seed_dir,
        seed_processed_dir=tmp_path / "seed_processed",
        seed_failed_dir=tmp_path / "seed_failed",
        request_dir=request_dir,
        forecast_db=forecast_db,
        limit=10,
    )

    assert not failed
    assert len(processed) == 1
    assert built == []
    assert not (request_dir / "old-cycle.json").exists()
    sidecar = next((tmp_path / "seed_processed").glob("*.receipt.json"))
    receipt = json.loads(sidecar.read_text(encoding="utf-8"))
    assert receipt["status"] == "SKIPPED_SOURCE_CYCLE_REGRESSION"
    assert receipt["reason_codes"] == ["REPLACEMENT_MATERIALIZATION_SOURCE_CYCLE_REGRESSION"]


def test_queue_coverage_skip_requires_matching_openmeteo_anchor_source_run(tmp_path) -> None:
    import src.data.replacement_forecast_live_materialization_queue as queue_mod

    forecast_db = tmp_path / "forecasts.db"
    conn = sqlite3.connect(forecast_db)
    try:
        conn.executescript(
            """
            CREATE TABLE forecast_posteriors (
                source_id TEXT,
                runtime_layer TEXT,
                city TEXT,
                target_date TEXT,
                temperature_metric TEXT,
                training_allowed INTEGER,
                dependency_source_run_ids_json TEXT
            );
            CREATE TABLE readiness_state (
                strategy_key TEXT,
                status TEXT,
                provenance_json TEXT,
                dependency_json TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO forecast_posteriors VALUES (?, 'live', 'Beijing', '2026-06-12', 'high', 0, ?)
            """,
            (
                queue_mod.SOURCE_ID,
                json.dumps({"baseline_b0": "b0-run", "openmeteo_ifs9_anchor": "old-om-run"}),
            ),
        )
        conn.execute(
            """
            INSERT INTO readiness_state VALUES (?, 'READY', ?, ?)
            """,
            (
                queue_mod.STRATEGY_KEY,
                json.dumps({"city": "Beijing", "target_date": "2026-06-12", "temperature_metric": "high"}),
                json.dumps(
                    {
                        "dependencies": [
                            {"role": "baseline_b0", "source_run_id": "b0-run"},
                            {"role": "openmeteo_ifs9_anchor", "source_run_id": "old-om-run"},
                        ]
                    }
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    fresh_anchor_seed = {**_minimal_seed(upgrade=False), "openmeteo_source_run_id": "new-om-run"}
    stale_anchor_seed = {**_minimal_seed(upgrade=False), "openmeteo_source_run_id": "old-om-run"}

    assert queue_mod._seed_already_covered(
        forecast_db=forecast_db, seed=fresh_anchor_seed
    ) is False
    assert queue_mod._seed_already_covered(
        forecast_db=forecast_db, seed=stale_anchor_seed
    ) is True


def test_queue_processes_held_cycle_advance_seed_before_nonheld_seed(
    tmp_path, monkeypatch
) -> None:
    import src.data.replacement_forecast_live_materialization_queue as queue_mod

    forecast_db = tmp_path / "forecasts.db"
    conn = sqlite3.connect(forecast_db)
    conn.execute(
        """
        CREATE TABLE cycle_advance_enqueues (
            seed_file TEXT,
            held_position INTEGER,
            enqueued_at TEXT
        )
        """
    )
    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir()
    request_dir = tmp_path / "requests"
    nonheld_seed = seed_dir / "A_nonheld.2026-06-21.high.json"
    held_seed = seed_dir / "Z_held.2026-06-21.high.json"
    nonheld_payload = {**_minimal_seed(upgrade=False), "city": "Busan"}
    held_payload = {**_minimal_seed(upgrade=False), "city": "Kuala Lumpur"}
    nonheld_seed.write_text(json.dumps(nonheld_payload), encoding="utf-8")
    held_seed.write_text(json.dumps(held_payload), encoding="utf-8")
    conn.executemany(
        "INSERT INTO cycle_advance_enqueues VALUES (?, ?, ?)",
        [
            (str(nonheld_seed), 0, "2026-06-20T05:00:00+00:00"),
            (str(held_seed), 1, "2026-06-20T07:00:00+00:00"),
        ],
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(queue_mod, "_seed_already_covered", lambda **_kw: False)
    built: list[str] = []

    def _fake_builder(seed, *, base_dir):
        built.append(str(seed.get("city")))
        return types.SimpleNamespace(
            ok=True, status="READY", reason_codes=("OK",), request={"stub": seed.get("city")}
        )

    monkeypatch.setattr(
        queue_mod, "build_replacement_forecast_materialization_request", _fake_builder
    )

    processed, failed, _reasons = queue_mod._prepare_seed_requests(
        seed_dir=seed_dir,
        seed_processed_dir=tmp_path / "seed_processed",
        seed_failed_dir=tmp_path / "seed_failed",
        request_dir=request_dir,
        forecast_db=forecast_db,
        limit=1,
    )

    assert not failed
    assert len(processed) == 1
    assert built == ["Kuala Lumpur"]
    assert (request_dir / held_seed.name).exists()
    assert not (request_dir / nonheld_seed.name).exists()


def test_materialization_queue_timeout_moves_request_to_failed(tmp_path) -> None:
    import src.data.replacement_forecast_live_materialization_queue as queue_mod

    request_dir = tmp_path / "requests"
    processed_dir = tmp_path / "processed"
    failed_dir = tmp_path / "failed"
    request_dir.mkdir()
    request = {
        "city": "London",
        "target_date": "2026-06-25",
        "temperature_metric": "high",
        "source_cycle_time": "2026-06-24T12:00:00+00:00",
        "computed_at": "2026-06-24T20:20:45+00:00",
        "baseline_source_run_id": "b0-run",
        "aifs_source_run_id": "aifs-run",
        "openmeteo_source_run_id": "om9-run",
        "openmeteo_payload_json": "payload.json",
        "precision_metadata_json": "precision.json",
        "aifs_samples_json": "samples.json",
        "bins": [{"bin_id": "30C"}],
    }
    request_path = request_dir / "London.2026-06-25.high.timeout.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    def _timeout_runner(argv):
        raise subprocess.TimeoutExpired(cmd=list(argv), timeout=1.5, output="", stderr="")

    report = queue_mod.process_replacement_forecast_live_materialization_queue(
        request_dir=request_dir,
        processed_dir=processed_dir,
        failed_dir=failed_dir,
        forecast_db=tmp_path / "forecasts.db",
        raw_manifest_dir=None,
        limit=1,
        runner=_timeout_runner,
    )

    assert report.status == "FAILED"
    assert report.failed_count == 1
    assert not request_path.exists()
    assert len(report.failed_files) == 1
    failed_request = Path(report.failed_files[0])
    assert failed_request.exists()
    sidecar = json.loads(
        failed_request.with_suffix(failed_request.suffix + ".receipt.json").read_text()
    )
    assert sidecar["returncode"] == 124
    assert sidecar["timeout_seconds"] == 1.5
    assert sidecar["reason_codes"] == [
        "REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_TIMEOUT"
    ]


def test_materialization_queue_coalesces_duplicate_requests_before_limit(tmp_path) -> None:
    import src.data.replacement_forecast_live_materialization_queue as queue_mod

    request_dir = tmp_path / "requests"
    processed_dir = tmp_path / "processed"
    failed_dir = tmp_path / "failed"
    request_dir.mkdir()
    base_request = {
        "city": "Shanghai",
        "target_date": "2026-07-02",
        "temperature_metric": "high",
        "source_cycle_time": "2026-07-02T00:00:00+00:00",
        "baseline_source_run_id": "ecmwf_open_data:mx2t6_high:2026-07-02T00Z",
        "openmeteo_source_run_id": "openmeteo-current-targets-Shanghai-high-20260702T000000Z",
        "openmeteo_payload_json": "payload.json",
        "precision_metadata_json": "precision.json",
        "bins": [{"bin_id": "30C"}],
    }
    older = {**base_request, "computed_at": "2026-07-02T08:19:11+00:00"}
    newer = {**base_request, "computed_at": "2026-07-02T08:31:11+00:00"}
    older_path = request_dir / "Shanghai.2026-07-02.high.20260702T081911Z.json"
    newer_path = request_dir / "Shanghai.2026-07-02.high.20260702T083111Z.json"
    older_path.write_text(json.dumps(older), encoding="utf-8")
    newer_path.write_text(json.dumps(newer), encoding="utf-8")
    spawned: list[str] = []

    def _successful_runner(argv):
        assert "--init-schema" not in argv
        spawned.append(Path(argv[argv.index("--input-json") + 1]).name)
        return subprocess.CompletedProcess(list(argv), 0, stdout="ok\n", stderr="")

    report = queue_mod.process_replacement_forecast_live_materialization_queue(
        request_dir=request_dir,
        processed_dir=processed_dir,
        failed_dir=failed_dir,
        forecast_db=tmp_path / "forecasts.db",
        raw_manifest_dir=None,
        limit=1,
        runner=_successful_runner,
    )

    assert report.status == "PROCESSED"
    assert report.failed_count == 0
    assert report.processed_count == 2
    assert report.skipped_count == 0
    assert spawned == [newer_path.name]
    assert "REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_SUPERSEDED_BY_NEWER_DUPLICATE" in report.reason_codes
    assert not older_path.exists()
    assert not newer_path.exists()
    receipts = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in processed_dir.glob("*.receipt.json")
    ]
    superseded = [receipt for receipt in receipts if receipt.get("status") == "SKIPPED_SUPERSEDED_REQUEST"]
    assert len(superseded) == 1
    assert superseded[0]["subprocess_spawned"] is False
    assert superseded[0]["superseded_by"] == newer_path.name


def test_materialization_queue_batches_default_runner_once_per_cycle(
    tmp_path, monkeypatch
) -> None:
    import src.data.replacement_forecast_live_materialization_queue as queue_mod

    request_dir = tmp_path / "requests"
    processed_dir = tmp_path / "processed"
    failed_dir = tmp_path / "failed"
    request_dir.mkdir()
    base_request = {
        "target_date": "2026-07-02",
        "temperature_metric": "high",
        "source_cycle_time": "2026-07-02T00:00:00+00:00",
        "computed_at": "2026-07-02T08:31:11+00:00",
        "baseline_source_run_id": "ecmwf_open_data:mx2t6_high:2026-07-02T00Z",
        "openmeteo_source_run_id": "openmeteo-current-targets-20260702T000000Z",
        "openmeteo_payload_json": "payload.json",
        "precision_metadata_json": "precision.json",
        "bins": [{"bin_id": "30C"}],
    }
    paths = []
    for city in ("Shanghai", "Paris"):
        path = request_dir / f"{city}.2026-07-02.high.json"
        path.write_text(json.dumps({**base_request, "city": city}), encoding="utf-8")
        paths.append(path)
    calls: list[list[str]] = []

    def _batch_runner(argv):
        command = list(argv)
        calls.append(command)
        start = command.index("--batch-input-json") + 1
        end = command.index("--commit")
        input_paths = command[start:end]
        stdout = "\n".join(
            json.dumps(
                {
                    "input_json": input_path,
                    "returncode": 0,
                    "stdout": (
                        '{"status":"READY","reason_codes":[],"committed":true,'
                        '"posterior_id":42,"reactor_wake_published":true}\n'
                    ),
                    "stderr": "",
                }
            )
            for input_path in input_paths
        )
        return subprocess.CompletedProcess(command, 0, stdout=stdout + "\n", stderr="")

    monkeypatch.setattr(queue_mod, "_run_command", _batch_runner)
    report = queue_mod.process_replacement_forecast_live_materialization_queue(
        request_dir=request_dir,
        processed_dir=processed_dir,
        failed_dir=failed_dir,
        forecast_db=tmp_path / "forecasts.db",
        raw_manifest_dir=None,
        limit=2,
    )

    assert report.status == "PROCESSED"
    assert report.processed_count == 2
    assert report.failed_count == 0
    assert report.committed_posterior_count == 2
    assert report.reactor_wake_published_count == 2
    assert len(calls) == 1
    assert "--batch-input-json" in calls[0]
    assert "--init-schema" not in calls[0]
    assert set(calls[0][calls[0].index("--batch-input-json") + 1 : -1]) == {
        str(path) for path in paths
    }


def test_materialization_batch_timeout_keeps_completed_request_committed(
    tmp_path, monkeypatch
) -> None:
    import src.data.replacement_forecast_live_materialization_queue as queue_mod

    request_dir = tmp_path / "requests"
    processed_dir = tmp_path / "processed"
    failed_dir = tmp_path / "failed"
    request_dir.mkdir()
    request = {
        "target_date": "2026-07-02",
        "temperature_metric": "high",
        "source_cycle_time": "2026-07-02T00:00:00+00:00",
        "computed_at": "2026-07-02T08:31:11+00:00",
        "baseline_source_run_id": "ecmwf_open_data:mx2t6_high:2026-07-02T00Z",
        "openmeteo_source_run_id": "openmeteo-current-targets-20260702T000000Z",
        "openmeteo_payload_json": "payload.json",
        "precision_metadata_json": "precision.json",
        "bins": [{"bin_id": "30C"}],
    }
    for city in ("A", "B"):
        (request_dir / f"{city}.json").write_text(
            json.dumps({**request, "city": city}),
            encoding="utf-8",
        )

    def _timeout_after_first(argv):
        command = list(argv)
        first = command[command.index("--batch-input-json") + 1]
        completed = json.dumps(
            {
                "input_json": first,
                "returncode": 0,
                "stdout": '{"status":"READY","reason_codes":[]}\n',
                "stderr": "",
            }
        )
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=1.5,
            output=completed + "\n",
            stderr="",
        )

    monkeypatch.setattr(queue_mod, "_run_command", _timeout_after_first)
    report = queue_mod.process_replacement_forecast_live_materialization_queue(
        request_dir=request_dir,
        processed_dir=processed_dir,
        failed_dir=failed_dir,
        forecast_db=tmp_path / "forecasts.db",
        raw_manifest_dir=None,
        limit=2,
    )

    assert report.status == "FAILED"
    assert report.processed_count == 1
    assert report.failed_count == 1
    failed_request = Path(report.failed_files[0])
    sidecar = json.loads(
        failed_request.with_suffix(failed_request.suffix + ".receipt.json").read_text()
    )
    assert sidecar["returncode"] == 124
    assert sidecar["timeout_seconds"] == 1.5


def test_materialization_queue_retries_blocked_request_only_after_input_change(
    tmp_path, monkeypatch
) -> None:
    import src.data.replacement_forecast_live_materialization_queue as queue_mod

    request_dir = tmp_path / "requests"
    processed_dir = tmp_path / "processed"
    failed_dir = tmp_path / "failed"
    request_dir.mkdir()
    request_path = request_dir / "Helsinki.2026-07-18.high.json"
    request = {
        "city": "Helsinki",
        "city_timezone": "Europe/Helsinki",
        "target_date": "2026-07-18",
        "temperature_metric": "high",
        "source_cycle_time": "2026-07-16T06:00:00+00:00",
        "computed_at": "2026-07-16T12:16:24+00:00",
        "baseline_source_run_id": "ecmwf_open_data:mx2t6_high:2026-07-16T06Z",
        "baseline_data_version": "ecmwf_opendata",
        "baseline_source_available_at": "2026-07-16T12:00:00+00:00",
        "openmeteo_source_run_id": "openmeteo-current-targets-Helsinki-high-20260716T060000Z",
        "openmeteo_source_available_at": "2026-07-16T12:15:35+00:00",
        "openmeteo_payload_json": "payload.json",
        "precision_metadata_json": "precision.json",
        "bins": [{"bin_id": "30C"}],
    }
    watermark = {"value": (3, 99, "2026-07-16T12:15:00+00:00", "")}
    original_fingerprint = queue_mod._blocked_attempt_fingerprint

    def _fingerprint(*, input_json, payload, forecast_db):
        base = original_fingerprint(
            input_json=input_json,
            payload=payload,
            forecast_db=forecast_db,
        )
        return f"{base}:{watermark['value']}"

    monkeypatch.setattr(queue_mod, "_blocked_attempt_fingerprint", _fingerprint)
    spawned: list[str] = []

    def _blocked_runner(argv):
        spawned.append(Path(argv[argv.index("--input-json") + 1]).name)
        return subprocess.CompletedProcess(
            list(argv),
            1,
            stdout=json.dumps(
                {
                    "status": "BLOCKED",
                    "reason_codes": [
                        "REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET"
                    ],
                }
            )
            + "\n",
            stderr="missing configured sources",
        )

    request_path.write_text(json.dumps(request), encoding="utf-8")
    first = queue_mod.process_replacement_forecast_live_materialization_queue(
        request_dir=request_dir,
        processed_dir=processed_dir,
        failed_dir=failed_dir,
        forecast_db=tmp_path / "forecasts.db",
        limit=1,
        runner=_blocked_runner,
    )
    assert first.status == "FAILED"
    assert len(spawned) == 1
    assert len(tuple((tmp_path / "blocked_attempts").glob("*.json"))) == 1

    request_path.write_text(
        json.dumps({**request, "computed_at": "2026-07-16T12:17:24+00:00"}),
        encoding="utf-8",
    )
    second = queue_mod.process_replacement_forecast_live_materialization_queue(
        request_dir=request_dir,
        processed_dir=processed_dir,
        failed_dir=failed_dir,
        forecast_db=tmp_path / "forecasts.db",
        limit=1,
        runner=_blocked_runner,
    )
    assert second.status == "PROCESSED"
    assert len(spawned) == 1
    assert (
        "REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_UNCHANGED_BLOCKED_INPUT"
        in second.reason_codes
    )
    skipped_receipt = max(
        processed_dir.glob("*.receipt.json"),
        key=lambda path: path.stat().st_mtime_ns,
    )
    skipped = json.loads(skipped_receipt.read_text(encoding="utf-8"))
    assert skipped["subprocess_spawned"] is False

    watermark["value"] = (4, 100, "2026-07-16T12:18:00+00:00", "")
    request_path.write_text(
        json.dumps({**request, "computed_at": "2026-07-16T12:18:24+00:00"}),
        encoding="utf-8",
    )
    third = queue_mod.process_replacement_forecast_live_materialization_queue(
        request_dir=request_dir,
        processed_dir=processed_dir,
        failed_dir=failed_dir,
        forecast_db=tmp_path / "forecasts.db",
        limit=1,
        runner=_blocked_runner,
    )
    assert third.status == "FAILED"
    assert len(spawned) == 2


def test_blocked_source_clock_request_ignores_unrelated_input_churn(
    tmp_path, monkeypatch
) -> None:
    import src.data.replacement_forecast_live_materialization_queue as queue_mod
    from src.strategy.live_inference import source_clock_city_weights as source_clock

    request_dir = tmp_path / "requests"
    processed_dir = tmp_path / "processed"
    failed_dir = tmp_path / "failed"
    request_dir.mkdir()
    payload_path = tmp_path / "payload.json"
    precision_path = tmp_path / "precision.json"
    payload_path.write_text("{}", encoding="utf-8")
    precision_path.write_text("{}", encoding="utf-8")
    scheme_path = tmp_path / "city_one_scheme.csv"
    scheme_path.write_text(
        "city,selection_status,grid_aware_sources,grid_aware_weighted_sources,"
        "candidate_count,eligible_live_grid_cap10_count,eligible_grid_cap10_count,reason\n"
        "Helsinki,GRID_CAP10_LIVE_READY,icon_eu+met_nordic,"
        "icon_eu:0.5+met_nordic:0.5,10,2,2,\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(source_clock.ENV_CITY_ONE_SCHEME_PATH, str(scheme_path))
    source_clock.load_city_one_schemes.cache_clear()

    db_path = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE raw_model_forecasts (
            raw_model_forecast_id INTEGER PRIMARY KEY,
            model TEXT NOT NULL,
            city TEXT NOT NULL,
            metric TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source_cycle_time TEXT NOT NULL,
            source_available_at TEXT,
            captured_at TEXT,
            endpoint TEXT NOT NULL,
            forecast_value_c REAL NOT NULL,
            lead_days INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO raw_model_forecasts VALUES
        (1, 'icon_eu', 'Helsinki', 'high', '2026-07-18',
         '2026-07-16T12:00:00+00:00', '2026-07-16T15:00:00+00:00',
         '2026-07-16T15:00:00+00:00', 'single_runs', 25.0, 2)
        """
    )
    conn.commit()

    request_path = request_dir / "Helsinki.2026-07-18.high.json"
    request = {
        "city": "Helsinki",
        "city_timezone": "Europe/Helsinki",
        "target_date": "2026-07-18",
        "temperature_metric": "high",
        "source_cycle_time": "2026-07-16T12:00:00+00:00",
        "computed_at": "2026-07-16T16:00:00+00:00",
        "baseline_source_run_id": "baseline",
        "baseline_data_version": "ecmwf_opendata",
        "baseline_source_available_at": "2026-07-16T15:00:00+00:00",
        "openmeteo_source_run_id": "openmeteo",
        "openmeteo_source_available_at": "2026-07-16T15:00:00+00:00",
        "openmeteo_payload_json": str(payload_path),
        "precision_metadata_json": str(precision_path),
        "bins": [{"bin_id": "25C"}],
    }
    spawned: list[str] = []

    def _blocked_runner(argv):
        spawned.append(Path(argv[argv.index("--input-json") + 1]).name)
        return subprocess.CompletedProcess(
            list(argv),
            1,
            stdout=json.dumps(
                {
                    "status": "BLOCKED",
                    "reason_codes": [
                        "REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET"
                    ],
                }
            )
            + "\n",
            stderr="missing configured sources",
        )

    try:
        request_path.write_text(json.dumps(request), encoding="utf-8")
        first = queue_mod.process_replacement_forecast_live_materialization_queue(
            request_dir=request_dir,
            processed_dir=processed_dir,
            failed_dir=failed_dir,
            forecast_db=db_path,
            limit=1,
            runner=_blocked_runner,
        )
        assert first.status == "FAILED"
        assert len(spawned) == 1

        payload_path.write_text('{"unrelated": true}', encoding="utf-8")
        conn.execute(
            """
            INSERT INTO raw_model_forecasts VALUES
            (2, 'icon_global', 'Helsinki', 'high', '2026-07-18',
             '2026-07-16T12:00:00+00:00', '2026-07-16T16:01:00+00:00',
             '2026-07-16T16:01:00+00:00', 'single_runs', 24.0, 2)
            """
        )
        conn.commit()
        request_path.write_text(
            json.dumps({**request, "computed_at": "2026-07-16T16:02:00+00:00"}),
            encoding="utf-8",
        )
        second = queue_mod.process_replacement_forecast_live_materialization_queue(
            request_dir=request_dir,
            processed_dir=processed_dir,
            failed_dir=failed_dir,
            forecast_db=db_path,
            limit=1,
            runner=_blocked_runner,
        )
        assert second.status == "PROCESSED"
        assert len(spawned) == 1

        conn.execute(
            """
            INSERT INTO raw_model_forecasts VALUES
            (3, 'met_nordic', 'Helsinki', 'high', '2026-07-18',
             '2026-07-16T12:00:00+00:00', '2026-07-16T16:03:00+00:00',
             '2026-07-16T16:03:00+00:00', 'single_runs', 25.5, 2)
            """
        )
        conn.commit()
        request_path.write_text(
            json.dumps({**request, "computed_at": "2026-07-16T16:04:00+00:00"}),
            encoding="utf-8",
        )
        third = queue_mod.process_replacement_forecast_live_materialization_queue(
            request_dir=request_dir,
            processed_dir=processed_dir,
            failed_dir=failed_dir,
            forecast_db=db_path,
            limit=1,
            runner=_blocked_runner,
        )
        assert third.status == "FAILED"
        assert len(spawned) == 2
    finally:
        conn.close()
        source_clock.load_city_one_schemes.cache_clear()
