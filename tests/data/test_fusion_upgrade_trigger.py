# Created: 2026-06-11
# Lifecycle: created=2026-06-11; last_reviewed=2026-07-28; last_reused=2026-07-28
# Purpose: Lock provider-set and exact-input revision reseeding for replacement posteriors.
# Reuse: Run for fusion upgrade, current-value serving, source callback, or station source changes.
# Last reused or audited: 2026-07-28
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
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data import replacement_fusion_upgrade_trigger as trigger
from src.data import replacement_forecast_live_materialization_queue as queue
from src.data.replacement_fusion_upgrade_trigger import (
    SOURCE_ID,
    decorrelated_provider_families_of,
    scope_capture_offers_larger_provider_set,
)
from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema

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
    ensure_replacement_forecast_live_schema(conn)
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
    current_value_ids: dict[str, int] | None = None,
    configured_sources: list[str] | None = None,
) -> None:
    fusion: dict[str, object] = {"used_models": used_models}
    if current_value_ids is not None:
        fusion["current_value_serving"] = {
            model: {"raw_model_forecast_id": raw_id}
            for model, raw_id in current_value_ids.items()
        }
    if configured_sources is not None:
        fusion["source_clock_one_scheme"] = {
            "configured_sources": configured_sources,
        }
    prov = {"bayes_precision_fusion": fusion}
    conn.execute(
        """
        INSERT INTO forecast_posteriors
            (source_id, product_id, data_version, city, target_date, temperature_metric,
             source_cycle_time, source_available_at, computed_at, q_json, q_lcb_json,
             posterior_method, dependency_source_run_ids_json, provenance_json,
             runtime_layer, training_allowed)
        VALUES (?, 'pid', 'dv', ?, ?, ?, ?, ?, ?, '{}', '{}', ?, '{}', ?, 'live', 0)
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


def test_same_provider_family_new_raw_revision_signals_upgrade() -> None:
    """A source-clock value revision changes q even when the provider-family set is unchanged."""
    conn = _conn()
    cyc = "2026-06-12T06:00:00+00:00"
    _insert_single_runs(
        conn,
        city="Testville",
        target_date="2026-06-13",
        metric="high",
        cycle_iso=cyc,
        models=[_DWD],
    )
    old_id = int(
        conn.execute(
            "SELECT MAX(raw_model_forecast_id) FROM raw_model_forecasts WHERE model = ?",
            (_DWD,),
        ).fetchone()[0]
    )
    _insert_posterior(
        conn,
        city="Testville",
        target_date="2026-06-13",
        metric="high",
        cycle_iso=cyc,
        used_models=[_DWD],
        computed_at="2026-06-12T10:00:00+00:00",
        current_value_ids={_DWD: old_id},
        configured_sources=[_DWD],
    )
    conn.execute(
        """
        INSERT INTO raw_model_forecasts
            (model, city, target_date, metric, source_cycle_time, source_available_at,
             captured_at, lead_days, forecast_value_c, endpoint)
        VALUES (?, 'Testville', '2026-06-13', 'high', ?, ?, ?, 1, 23.0, 'single_runs')
        """,
        (_DWD, cyc, cyc, "2026-06-12T10:01:00+00:00"),
    )
    conn.commit()

    verdict = scope_capture_offers_larger_provider_set(
        conn,
        city="Testville",
        target_date="2026-06-13",
        metric="high",
        changed_sources=[_DWD],
    )

    assert verdict["is_upgrade"] is True
    assert verdict["family_upgrade"] is False
    assert verdict["input_revision_changed"] is True
    assert verdict["new_families"] == []
    assert verdict["changed_input_sources"] == [_DWD]


def test_post_carrier_provider_run_is_current_at_decision_time() -> None:
    """Source-clock center uses each provider's newest possessed run, not the ENS carrier ceiling."""
    conn = _conn()
    carrier = "2026-06-12T06:00:00+00:00"
    newer = "2026-06-12T12:00:00+00:00"
    _insert_single_runs(
        conn,
        city="Testville",
        target_date="2026-06-13",
        metric="high",
        cycle_iso=carrier,
        models=[_DWD],
    )
    old_id = int(
        conn.execute(
            "SELECT MAX(raw_model_forecast_id) FROM raw_model_forecasts"
        ).fetchone()[0]
    )
    _insert_posterior(
        conn,
        city="Testville",
        target_date="2026-06-13",
        metric="high",
        cycle_iso=carrier,
        used_models=[_DWD],
        computed_at="2026-06-12T10:00:00+00:00",
        current_value_ids={_DWD: old_id},
        configured_sources=[_DWD],
    )
    _insert_single_runs(
        conn,
        city="Testville",
        target_date="2026-06-13",
        metric="high",
        cycle_iso=newer,
        models=[_DWD],
    )

    carrier_bound = scope_capture_offers_larger_provider_set(
        conn,
        city="Testville",
        target_date="2026-06-13",
        metric="high",
        changed_sources=[_DWD],
    )
    source_clock = scope_capture_offers_larger_provider_set(
        conn,
        city="Testville",
        target_date="2026-06-13",
        metric="high",
        changed_sources=[_DWD],
        decision_time=datetime(2026, 6, 12, 13, 0, tzinfo=UTC),
    )

    assert carrier_bound["is_upgrade"] is False
    assert source_clock["is_upgrade"] is True
    assert source_clock["family_upgrade"] is False
    assert source_clock["input_revision_changed"] is True
    assert source_clock["changed_input_sources"] == [_DWD]
    assert source_clock["changed_input_revisions"][_DWD] > old_id


def test_post_carrier_provider_run_enqueues_same_carrier_refresh(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The periodic repair lane must enqueue the stale family before the carrier advances."""
    db = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    carrier = "2026-06-12T06:00:00+00:00"
    newer = "2026-06-12T12:00:00+00:00"
    _insert_single_runs(
        conn,
        city="Testville",
        target_date="2026-06-13",
        metric="high",
        cycle_iso=carrier,
        models=[_DWD],
    )
    old_id = int(
        conn.execute(
            "SELECT MAX(raw_model_forecast_id) FROM raw_model_forecasts"
        ).fetchone()[0]
    )
    _insert_posterior(
        conn,
        city="Testville",
        target_date="2026-06-13",
        metric="high",
        cycle_iso=carrier,
        used_models=[_DWD],
        computed_at="2026-06-12T10:00:00+00:00",
        current_value_ids={_DWD: old_id},
        configured_sources=[_DWD],
    )
    _insert_single_runs(
        conn,
        city="Testville",
        target_date="2026-06-13",
        metric="high",
        cycle_iso=newer,
        models=[_DWD],
    )
    conn.close()

    def _build(_conn, **kwargs):
        seed = Path(kwargs["seed_file"])
        seed.parent.mkdir(parents=True, exist_ok=True)
        seed.write_text("{}\n", encoding="utf-8")
        return seed

    monkeypatch.setattr(
        trigger,
        "_build_and_write_upgrade_seed",
        _build,
    )
    report = trigger.enqueue_fusion_upgrade_reseeds(
        forecast_db=db,
        seed_dir=tmp_path / "seeds",
        raw_manifest_dir=tmp_path / "raw",
        computed_at=datetime(2026, 6, 12, 13, 0, tzinfo=UTC),
        scopes=[("Testville", "2026-06-13", "high")],
        changed_sources=[_DWD],
        manifests=(),
    )

    assert report["input_revisions_detected"] == 1
    assert report["seeds_enqueued"] == 1
    assert report["enqueued"][0]["source_cycle_time"] == carrier


def test_day0_input_revision_enqueues_observation_conditioned_seed(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same-cycle late inputs must have a RESET without dropping Day0 truth."""
    db, kwargs = _revision_upgrade_kwargs(tmp_path)
    kwargs.update(
        computed_at=datetime(2026, 7, 25, 12, 0, tzinfo=UTC),
        scopes=[("Seoul", "2026-07-25", "high")],
    )
    day0_payload = {
        "day0_observed_extreme_c": 31.0,
        "day0_observed_extreme_source": "wu_icao_history",
        "day0_observed_extreme_observation_time": "2026-07-25T11:00:00+00:00",
        "day0_observed_extreme_sample_count": 12,
        "day0_observed_extreme_unit": "C",
    }
    from src.data import replacement_forecast_current_target_plan as target_plan
    from src.data import replacement_forecast_seed_discovery as discovery

    monkeypatch.setattr(
        target_plan,
        "_day0_observed_extreme_required",
        lambda **_kwargs: True,
    )
    monkeypatch.setattr(
        discovery,
        "_day0_observed_extreme_seed_payload",
        lambda **_kwargs: dict(day0_payload),
    )
    monkeypatch.setattr(
        trigger,
        "scope_capture_offers_larger_provider_set",
        lambda *_args, **_kwargs: _revision_upgrade_verdict(),
    )
    observed: dict[str, object] = {}

    def _build(_conn, **build_kwargs):
        observed.update(build_kwargs.get("day0_payload") or {})
        path = Path(build_kwargs["seed_file"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        return path

    monkeypatch.setattr(trigger, "_build_and_write_upgrade_seed", _build)

    report = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)

    assert report["input_revisions_detected"] == 1
    assert report["day0_conditioned_upgrades"] == 1
    assert report["day0_skipped"] == 0
    assert report["seeds_enqueued"] == 1
    assert observed == day0_payload


def test_scoped_reseed_uses_db_family_manifests_without_global_tree_scan(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A held-family repair must reach enqueue independent of manifest-tree size."""
    db, kwargs = _revision_upgrade_kwargs(tmp_path)
    kwargs.pop("manifests")
    from src.data import replacement_cycle_advance_trigger as cycle_advance
    from src.data import replacement_forecast_seed_discovery as discovery

    family_manifests = (object(),)
    monkeypatch.setattr(
        discovery,
        "_load_manifests",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("scoped repair must not scan the global manifest tree")
        ),
    )
    monkeypatch.setattr(
        cycle_advance,
        "_family_manifests_from_db",
        lambda *_args, **_kwargs: family_manifests,
    )
    monkeypatch.setattr(
        trigger,
        "scope_capture_offers_larger_provider_set",
        lambda *_args, **_kwargs: _revision_upgrade_verdict(),
    )
    observed: dict[str, object] = {}

    def _build(_conn, **build_kwargs):
        observed["manifests"] = build_kwargs["manifests"]
        path = Path(build_kwargs["seed_file"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        return path

    monkeypatch.setattr(trigger, "_build_and_write_upgrade_seed", _build)

    report = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)

    assert report["seeds_enqueued"] == 1
    assert observed["manifests"] is family_manifests


def test_upgrade_seed_baseline_lookup_obeys_manifest_and_decision_clocks(
    tmp_path: Path,
) -> None:
    """Fusion upgrades must satisfy the current causal baseline lookup API."""
    cycle = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
    computed_at = datetime(2026, 7, 24, 13, 0, tzinfo=UTC)
    manifest = SimpleNamespace(
        source_cycle_time=cycle,
        artifact_path=tmp_path / "anchor.json",
    )
    observed: dict[str, object] = {}

    def _coverage(_conn, **kwargs):
        observed.update(kwargs)
        return {"coverage": True}

    output = tmp_path / "staging" / "seed.json"
    built = trigger._build_and_write_upgrade_seed(
        _conn(),
        city="Seoul",
        target_date="2026-07-25",
        metric="high",
        manifests=(manifest,),
        raw_dir=tmp_path,
        seed_path=tmp_path / "seeds",
        seed_file=output,
        computed_at=computed_at,
        build_seed=lambda **_kwargs: SimpleNamespace(ok=True, seed={}),
        latest_baseline_coverage=_coverage,
        market_bins=lambda *_args, **_kwargs: (object(),),
        write_seed=lambda path, _payload: (
            path.parent.mkdir(parents=True, exist_ok=True),
            path.write_text("{}\n", encoding="utf-8"),
        ),
        latest_manifest=lambda *_args, **_kwargs: manifest,
        manifest_path_value=lambda *_args, **_kwargs: tmp_path / "input.json",
        manifest_base_dir=lambda *_args, **_kwargs: tmp_path,
        resolve_path=lambda path, **_kwargs: path,
        expected_identity=lambda _metric: {
            "openmeteo_ifs9_anchor": SimpleNamespace(
                source_id="anchor",
                data_version="v1",
            )
        },
    )

    assert built == output
    assert observed["not_after_source_cycle_time"] == cycle
    assert observed["as_of_time"] == computed_at


def test_consumed_failed_publication_reclaims_same_transition_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A vanished seed with an unchanged posterior must have a retry RESET."""
    _db, kwargs = _revision_upgrade_kwargs(tmp_path)
    monkeypatch.setattr(
        trigger,
        "scope_capture_offers_larger_provider_set",
        lambda *_args, **_kwargs: _revision_upgrade_verdict(),
    )

    def _build(_conn, **build_kwargs):
        path = Path(build_kwargs["seed_file"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        return path

    monkeypatch.setattr(trigger, "_build_and_write_upgrade_seed", _build)
    first = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)
    first_seed = Path(first["enqueued"][0]["seed_file"])
    first_seed.unlink()

    retried = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)

    assert retried["seeds_enqueued"] == 1
    assert retried["already_enqueued"] == 0
    assert Path(retried["enqueued"][0]["seed_file"]).is_file()


def test_active_exact_request_keeps_transition_fenced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A claimed publication is not republished while exact queue work exists."""
    _db, kwargs = _revision_upgrade_kwargs(tmp_path)
    monkeypatch.setattr(
        trigger,
        "scope_capture_offers_larger_provider_set",
        lambda *_args, **_kwargs: _revision_upgrade_verdict(),
    )

    def _build(_conn, **build_kwargs):
        path = Path(build_kwargs["seed_file"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        return path

    monkeypatch.setattr(trigger, "_build_and_write_upgrade_seed", _build)
    first = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)
    first_seed = Path(first["enqueued"][0]["seed_file"])
    request = first_seed.parent.parent / "requests" / first_seed.name
    request.parent.mkdir(parents=True, exist_ok=True)
    first_seed.replace(request)

    duplicate = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)

    assert duplicate["seeds_enqueued"] == 0
    assert duplicate["already_enqueued"] == 1
    assert request.is_file()


def test_unchanged_or_unrelated_raw_revision_is_noop() -> None:
    conn = _conn()
    cyc = "2026-06-12T06:00:00+00:00"
    _insert_single_runs(
        conn,
        city="Testville",
        target_date="2026-06-13",
        metric="high",
        cycle_iso=cyc,
        models=[_DWD],
    )
    raw_id = int(conn.execute("SELECT MAX(raw_model_forecast_id) FROM raw_model_forecasts").fetchone()[0])
    _insert_posterior(
        conn,
        city="Testville",
        target_date="2026-06-13",
        metric="high",
        cycle_iso=cyc,
        used_models=[_DWD],
        computed_at="2026-06-12T10:00:00+00:00",
        current_value_ids={_DWD: raw_id},
        configured_sources=[_DWD],
    )

    unchanged = scope_capture_offers_larger_provider_set(
        conn,
        city="Testville",
        target_date="2026-06-13",
        metric="high",
        changed_sources=[_DWD],
    )
    unrelated = scope_capture_offers_larger_provider_set(
        conn,
        city="Testville",
        target_date="2026-06-13",
        metric="high",
        changed_sources=[_UKMO],
    )

    assert unchanged["is_upgrade"] is False
    assert unrelated["is_upgrade"] is False


def test_new_station_source_is_an_input_revision_before_old_posterior_configures_it() -> None:
    conn = _conn()
    grid_cycle = "2026-07-23T18:00:00+00:00"
    station_cycle = "2026-07-24T03:30:00+00:00"
    _insert_single_runs(
        conn,
        city="Hong Kong",
        target_date="2026-07-25",
        metric="low",
        cycle_iso=grid_cycle,
        models=[_DWD],
    )
    grid_id = int(
        conn.execute(
            "SELECT MAX(raw_model_forecast_id) FROM raw_model_forecasts"
        ).fetchone()[0]
    )
    _insert_posterior(
        conn,
        city="Hong Kong",
        target_date="2026-07-25",
        metric="low",
        cycle_iso=grid_cycle,
        used_models=[_DWD],
        computed_at="2026-07-24T03:00:00+00:00",
        current_value_ids={_DWD: grid_id},
        configured_sources=[_DWD],
    )
    _insert_single_runs(
        conn,
        city="Hong Kong",
        target_date="2026-07-25",
        metric="low",
        cycle_iso=station_cycle,
        models=["hko_fnd"],
    )

    callback = scope_capture_offers_larger_provider_set(
        conn,
        city="Hong Kong",
        target_date="2026-07-25",
        metric="low",
        changed_sources=["hko_fnd"],
    )
    periodic = scope_capture_offers_larger_provider_set(
        conn,
        city="Hong Kong",
        target_date="2026-07-25",
        metric="low",
    )

    for verdict in (callback, periodic):
        assert verdict["is_upgrade"] is True
        assert verdict["family_upgrade"] is False
        assert verdict["input_revision_changed"] is True
        assert verdict["changed_input_sources"] == ["hko_fnd"]


def test_consumed_station_source_revision_returns_to_noop() -> None:
    conn = _conn()
    grid_cycle = "2026-07-23T18:00:00+00:00"
    station_cycle = "2026-07-24T03:30:00+00:00"
    _insert_single_runs(
        conn,
        city="Hong Kong",
        target_date="2026-07-25",
        metric="low",
        cycle_iso=grid_cycle,
        models=[_DWD],
    )
    _insert_single_runs(
        conn,
        city="Hong Kong",
        target_date="2026-07-25",
        metric="low",
        cycle_iso=station_cycle,
        models=["hko_fnd"],
    )
    ids = {
        str(row[0]): int(row[1])
        for row in conn.execute(
            """
            SELECT model, MAX(raw_model_forecast_id)
            FROM raw_model_forecasts
            GROUP BY model
            """
        )
    }
    _insert_posterior(
        conn,
        city="Hong Kong",
        target_date="2026-07-25",
        metric="low",
        cycle_iso=grid_cycle,
        used_models=[_DWD, "hko_fnd"],
        computed_at="2026-07-24T04:00:00+00:00",
        current_value_ids=ids,
        configured_sources=[_DWD],
    )

    verdict = scope_capture_offers_larger_provider_set(
        conn,
        city="Hong Kong",
        target_date="2026-07-25",
        metric="low",
        changed_sources=["hko_fnd"],
    )

    assert verdict["is_upgrade"] is False
    assert verdict["input_revision_changed"] is False
    assert verdict["changed_input_sources"] == []


def test_station_input_revision_enqueue_is_idempotent_until_raw_id_changes(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    grid_cycle = "2026-07-23T18:00:00+00:00"
    station_cycle = "2026-07-24T03:30:00+00:00"
    _insert_single_runs(
        conn,
        city="Hong Kong",
        target_date="2026-07-25",
        metric="low",
        cycle_iso=grid_cycle,
        models=[_DWD, _UKMO],
    )
    grid_id = int(
        conn.execute(
            "SELECT MAX(raw_model_forecast_id) FROM raw_model_forecasts"
        ).fetchone()[0]
    )
    _insert_posterior(
        conn,
        city="Hong Kong",
        target_date="2026-07-25",
        metric="low",
        cycle_iso=grid_cycle,
        used_models=[_DWD],
        computed_at="2026-07-24T03:00:00+00:00",
        current_value_ids={_DWD: grid_id},
        configured_sources=[_DWD],
    )
    _insert_single_runs(
        conn,
        city="Hong Kong",
        target_date="2026-07-25",
        metric="low",
        cycle_iso=station_cycle,
        models=["hko_fnd"],
    )
    conn.close()

    built: list[str] = []

    def _build(_conn, **kwargs):
        path = Path(kwargs["seed_file"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        built.append(str(path))
        return path

    monkeypatch.setattr(trigger, "_build_and_write_upgrade_seed", _build)
    kwargs = {
        "forecast_db": db,
        "seed_dir": tmp_path / "seeds",
        "raw_manifest_dir": tmp_path / "raw",
        "computed_at": datetime(2026, 7, 24, 4, 0, tzinfo=UTC),
        "scopes": [("Hong Kong", "2026-07-25", "low")],
        "changed_sources": ["hko_fnd"],
        "manifests": (),
    }

    first = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)
    duplicate = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)

    conn = sqlite3.connect(db)
    conn.execute(
        """
        INSERT INTO raw_model_forecasts
            (model, city, target_date, metric, source_cycle_time, source_available_at,
             captured_at, lead_days, forecast_value_c, endpoint)
        VALUES ('hko_fnd', 'Hong Kong', '2026-07-25', 'low', ?, ?, ?, 1, 26.0,
                'single_runs')
        """,
        (
            "2026-07-24T06:30:00+00:00",
            "2026-07-24T06:30:00+00:00",
            "2026-07-24T06:31:00+00:00",
        ),
    )
    conn.commit()
    conn.close()
    revised = trigger.enqueue_fusion_upgrade_reseeds(
        **{
            **kwargs,
            "computed_at": datetime(2026, 7, 24, 7, 0, tzinfo=UTC),
        }
    )

    assert first["seeds_enqueued"] == 1
    assert duplicate["seeds_enqueued"] == 0
    assert duplicate["already_enqueued"] == 1
    assert revised["seeds_enqueued"] == 1
    assert len(built) == 2
    conn = sqlite3.connect(db)
    markers = conn.execute(
        """
        SELECT capturable_family_set
        FROM fusion_upgrade_enqueues
        WHERE city = 'Hong Kong' AND target_date = '2026-07-25' AND metric = 'low'
        ORDER BY enqueue_id
        """
    ).fetchall()
    conn.close()
    assert len(markers) == 3
    assert markers[0][0] == "DWD,UKMO"
    assert all(
        "|input_revision=hko_fnd:" in marker[0] for marker in markers[1:]
    )
    assert len({marker[0] for marker in markers}) == 3


def _revision_upgrade_verdict(
    *,
    family_upgrade: bool = False,
) -> dict[str, object]:
    capturable = ["DWD", "UKMO"] if family_upgrade else ["DWD"]
    return {
        "is_upgrade": True,
        "family_upgrade": family_upgrade,
        "input_revision_changed": True,
        "source_cycle_time": "2026-07-24T12:00:00+00:00",
        "served_families": ["DWD"],
        "capturable_families": capturable,
        "new_families": ["UKMO"] if family_upgrade else [],
        "changed_input_sources": [_DWD],
        "changed_input_revisions": {_DWD: 91},
    }


def _revision_upgrade_kwargs(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    db = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    conn.close()
    return db, {
        "forecast_db": db,
        "seed_dir": tmp_path / "seeds",
        "raw_manifest_dir": tmp_path / "raw",
        "computed_at": datetime(2026, 7, 24, 13, 0, tzinfo=UTC),
        "scopes": [("Seoul", "2026-07-25", "high")],
        "changed_sources": [_DWD],
        "manifests": (),
    }


@pytest.mark.parametrize(
    "cycle_time",
    (
        "2026-07-22T06:59:59+00:00",
        "2026-07-24T13:00:01+00:00",
    ),
)
def test_outside_causal_cycle_never_reaches_fusion_upgrade_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cycle_time: str,
) -> None:
    db, kwargs = _revision_upgrade_kwargs(tmp_path)
    verdict = _revision_upgrade_verdict()
    verdict["source_cycle_time"] = cycle_time
    monkeypatch.setattr(
        trigger,
        "scope_capture_offers_larger_provider_set",
        lambda *_args, **_kwargs: verdict,
    )
    monkeypatch.setattr(
        trigger,
        "_build_and_write_upgrade_seed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("outside-bound cycle must not build or publish a seed")
        ),
    )

    report = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)

    assert report["upgrades_detected"] == 1
    assert report["seeds_enqueued"] == 0
    assert report["cycle_too_old_skipped"] == 1


def test_directory_fsync_uses_portable_readonly_open_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        trigger.os,
        "open",
        lambda path, flags: calls.append(("open", (path, flags))) or 41,
    )
    monkeypatch.setattr(
        trigger.os,
        "fsync",
        lambda fd: calls.append(("fsync", fd)),
    )
    monkeypatch.setattr(
        trigger.os,
        "close",
        lambda fd: calls.append(("close", fd)),
    )

    directory = Path("/tmp/fusion-upgrade-queue")
    trigger._fsync_directory(directory)

    expected_flags = trigger.os.O_RDONLY | int(
        getattr(trigger.os, "O_DIRECTORY", 0)
    )
    assert calls == [
        ("open", (directory, expected_flags)),
        ("fsync", 41),
        ("close", 41),
    ]


def test_durable_publish_happens_before_sqlite_marker_transitions(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Staging durability precedes PENDING; queue durability precedes final marker."""
    db, kwargs = _revision_upgrade_kwargs(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    queue_root = state_dir / "replacement_forecast_live"
    seed_dir = queue_root / "seeds"
    kwargs["seed_dir"] = seed_dir
    assert not queue_root.exists()
    monkeypatch.setattr(
        trigger,
        "scope_capture_offers_larger_provider_set",
        lambda *_args, **_kwargs: _revision_upgrade_verdict(),
    )
    events: list[str] = []

    def _build(_conn, **build_kwargs):
        stage = Path(build_kwargs["seed_file"])
        stage.parent.mkdir(parents=True, exist_ok=True)
        stage.write_text("{}\n", encoding="utf-8")
        events.append("write_seed")
        return stage

    monkeypatch.setattr(trigger, "_build_and_write_upgrade_seed", _build)
    monkeypatch.setattr(
        trigger,
        "_fsync_file",
        lambda path: events.append(f"fsync_file:{Path(path).name}"),
    )

    def _fsync_directory(path):
        directory = Path(path)
        if directory == state_dir:
            event = "fsync_queue_root_parent_entry"
        elif directory == queue_root:
            event = "fsync_seed_parent_entry"
        elif directory == seed_dir:
            event = (
                "fsync_queue_dir"
                if "marker_finalize" in events
                else "fsync_staging_parent_entry"
            )
        elif directory.name == ".fusion_upgrade_staging":
            event = "fsync_staging_dir"
        else:
            event = "fsync_queue_dir"
        events.append(event)

    monkeypatch.setattr(trigger, "_fsync_directory", _fsync_directory)
    real_finalize = trigger._finalize_enqueue_reservations
    real_complete = trigger._complete_published_enqueues

    def _finalize(*args, **finalize_kwargs):
        assert events[-2:] == [
            f"fsync_file:{Path(finalize_kwargs['publication'].staging_file).name}",
            "fsync_staging_dir",
        ]
        events.append("marker_finalize")
        return real_finalize(*args, **finalize_kwargs)

    def _complete(*args, **complete_kwargs):
        assert events[-1] == "fsync_queue_dir"
        events.append("marker_complete")
        return real_complete(*args, **complete_kwargs)

    monkeypatch.setattr(trigger, "_finalize_enqueue_reservations", _finalize)
    monkeypatch.setattr(trigger, "_complete_published_enqueues", _complete)

    report = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)

    assert report["seeds_enqueued"] == 1
    assert events.index("write_seed") < events.index("marker_finalize")
    assert events.index("fsync_queue_root_parent_entry") < events.index(
        "marker_finalize"
    )
    assert events.index("fsync_seed_parent_entry") < events.index(
        "marker_finalize"
    )
    assert events.index("fsync_staging_parent_entry") < events.index(
        "marker_finalize"
    )
    assert events.index("fsync_staging_dir") < events.index("marker_finalize")
    assert events.index("fsync_queue_dir") < events.index("marker_complete")


def test_queue_root_parent_fsync_failure_never_finalizes_and_retries(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newly created queue root is not SQLite-visible before STATE_DIR fsync."""
    db, kwargs = _revision_upgrade_kwargs(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    queue_root = state_dir / "replacement_forecast_live"
    seed_dir = queue_root / "seeds"
    kwargs["seed_dir"] = seed_dir
    assert not queue_root.exists()
    monkeypatch.setattr(
        trigger,
        "scope_capture_offers_larger_provider_set",
        lambda *_args, **_kwargs: _revision_upgrade_verdict(),
    )
    monkeypatch.setattr(trigger, "_RESERVATION_TTL", timedelta(0))
    builds = 0

    def _build(_conn, **build_kwargs):
        nonlocal builds
        builds += 1
        stage = Path(build_kwargs["seed_file"])
        stage.parent.mkdir(parents=True, exist_ok=True)
        stage.write_text("{}\n", encoding="utf-8")
        return stage

    real_fsync_directory = trigger._fsync_directory
    state_dir_fsync_attempts = 0

    def _fsync_directory(path):
        nonlocal state_dir_fsync_attempts
        directory = Path(path)
        if directory == state_dir:
            state_dir_fsync_attempts += 1
            if state_dir_fsync_attempts == 1:
                raise OSError("injected queue-root-parent fsync failure")
        real_fsync_directory(directory)

    finalize_calls = 0
    real_finalize = trigger._finalize_enqueue_reservations

    def _finalize(*args, **finalize_kwargs):
        nonlocal finalize_calls
        finalize_calls += 1
        return real_finalize(*args, **finalize_kwargs)

    monkeypatch.setattr(trigger, "_build_and_write_upgrade_seed", _build)
    monkeypatch.setattr(trigger, "_fsync_directory", _fsync_directory)
    monkeypatch.setattr(trigger, "_finalize_enqueue_reservations", _finalize)

    failed = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)
    conn = sqlite3.connect(db)
    reservation = conn.execute(
        "SELECT seed_file FROM fusion_upgrade_enqueues"
    ).fetchone()[0]
    conn.close()

    assert failed["seed_staging_fsync_failed"] == 1
    assert finalize_calls == 0
    assert str(reservation).startswith(trigger._RESERVATION_PREFIX)
    assert queue_root.is_dir()
    assert list(seed_dir.glob("*.json")) == []

    recovered = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)

    assert recovered["seeds_enqueued"] == 1
    assert builds == 2
    assert state_dir_fsync_attempts >= 2
    assert finalize_calls == 1
    assert len(list(seed_dir.glob("*.json"))) == 1


def test_hidden_staging_parent_fsync_failure_never_finalizes_and_retries(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A created staging dir is not SQLite-visible before its parent entry is durable."""
    db, kwargs = _revision_upgrade_kwargs(tmp_path)
    seed_dir = Path(kwargs["seed_dir"])
    monkeypatch.setattr(
        trigger,
        "scope_capture_offers_larger_provider_set",
        lambda *_args, **_kwargs: _revision_upgrade_verdict(),
    )
    monkeypatch.setattr(trigger, "_RESERVATION_TTL", timedelta(0))
    builds = 0

    def _build(_conn, **build_kwargs):
        nonlocal builds
        builds += 1
        stage = Path(build_kwargs["seed_file"])
        stage.parent.mkdir(parents=True, exist_ok=True)
        stage.write_text("{}\n", encoding="utf-8")
        return stage

    real_fsync_directory = trigger._fsync_directory
    staging_parent_attempts = 0

    def _fsync_directory(path):
        nonlocal staging_parent_attempts
        directory = Path(path)
        if directory == seed_dir:
            staging_parent_attempts += 1
            if staging_parent_attempts == 1:
                raise OSError("injected staging-parent fsync failure")
        real_fsync_directory(directory)

    finalize_calls = 0
    real_finalize = trigger._finalize_enqueue_reservations

    def _finalize(*args, **finalize_kwargs):
        nonlocal finalize_calls
        finalize_calls += 1
        return real_finalize(*args, **finalize_kwargs)

    monkeypatch.setattr(trigger, "_build_and_write_upgrade_seed", _build)
    monkeypatch.setattr(trigger, "_fsync_directory", _fsync_directory)
    monkeypatch.setattr(trigger, "_finalize_enqueue_reservations", _finalize)

    failed = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)
    conn = sqlite3.connect(db)
    reservation = conn.execute(
        "SELECT seed_file FROM fusion_upgrade_enqueues"
    ).fetchone()[0]
    conn.close()

    assert failed["seed_staging_fsync_failed"] == 1
    assert finalize_calls == 0
    assert str(reservation).startswith(trigger._RESERVATION_PREFIX)
    assert (seed_dir / ".fusion_upgrade_staging").is_dir()
    assert list(seed_dir.glob("*.json")) == []

    recovered = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)

    assert recovered["seeds_enqueued"] == 1
    assert builds == 2
    assert staging_parent_attempts >= 2
    assert finalize_calls == 1
    assert len(list(seed_dir.glob("*.json"))) == 1


def test_staging_fsync_failure_leaves_reservation_for_retry(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-durable staging inode never advances its SQLite marker to PENDING."""
    db, kwargs = _revision_upgrade_kwargs(tmp_path)
    monkeypatch.setattr(
        trigger,
        "scope_capture_offers_larger_provider_set",
        lambda *_args, **_kwargs: _revision_upgrade_verdict(),
    )
    monkeypatch.setattr(trigger, "_RESERVATION_TTL", timedelta(0))
    builds = 0

    def _build(_conn, **build_kwargs):
        nonlocal builds
        builds += 1
        stage = Path(build_kwargs["seed_file"])
        stage.parent.mkdir(parents=True, exist_ok=True)
        stage.write_text("{}\n", encoding="utf-8")
        return stage

    real_fsync_file = trigger._fsync_file
    fsync_attempts = 0

    def _fsync_file(path):
        nonlocal fsync_attempts
        fsync_attempts += 1
        if fsync_attempts == 1:
            raise OSError("injected staging fsync failure")
        real_fsync_file(path)

    finalize_calls = 0
    real_finalize = trigger._finalize_enqueue_reservations

    def _finalize(*args, **finalize_kwargs):
        nonlocal finalize_calls
        finalize_calls += 1
        return real_finalize(*args, **finalize_kwargs)

    monkeypatch.setattr(trigger, "_build_and_write_upgrade_seed", _build)
    monkeypatch.setattr(trigger, "_fsync_file", _fsync_file)
    monkeypatch.setattr(trigger, "_finalize_enqueue_reservations", _finalize)

    failed = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)
    conn = sqlite3.connect(db)
    reservation = conn.execute(
        "SELECT seed_file FROM fusion_upgrade_enqueues"
    ).fetchone()[0]
    conn.close()
    reserved_publication = trigger._parse_publication(
        reservation,
        prefix=trigger._RESERVATION_PREFIX,
    )

    assert failed["seed_staging_fsync_failed"] == 1
    assert finalize_calls == 0
    assert str(reservation).startswith(trigger._RESERVATION_PREFIX)
    assert reserved_publication is not None
    assert reserved_publication.staging_file.is_file()
    assert list((tmp_path / "seeds").glob("*.json")) == []

    recovered = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)

    assert recovered["seeds_enqueued"] == 1
    assert builds == 2
    assert finalize_calls == 1
    visible = list((tmp_path / "seeds").glob("*.json"))
    assert len(visible) == 1
    conn = sqlite3.connect(db)
    marker = conn.execute(
        "SELECT seed_file FROM fusion_upgrade_enqueues"
    ).fetchone()[0]
    conn.close()
    assert marker == str(visible[0])


def test_queue_directory_fsync_failure_keeps_pending_until_recovery(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A visible-but-not-directory-durable seed cannot complete its SQLite marker."""
    db, kwargs = _revision_upgrade_kwargs(tmp_path)
    seed_dir = Path(kwargs["seed_dir"])
    monkeypatch.setattr(
        trigger,
        "scope_capture_offers_larger_provider_set",
        lambda *_args, **_kwargs: _revision_upgrade_verdict(),
    )
    builds = 0

    def _build(_conn, **build_kwargs):
        nonlocal builds
        builds += 1
        stage = Path(build_kwargs["seed_file"])
        stage.parent.mkdir(parents=True, exist_ok=True)
        stage.write_text("{}\n", encoding="utf-8")
        return stage

    real_fsync_directory = trigger._fsync_directory
    queue_fsync_attempts = 0
    queue_fsync_successes = 0

    def _fsync_directory(path):
        nonlocal queue_fsync_attempts, queue_fsync_successes
        directory = Path(path)
        if directory == seed_dir:
            queue_fsync_attempts += 1
            if queue_fsync_attempts == 2:
                raise OSError("injected queue-directory fsync failure")
            if queue_fsync_attempts > 2:
                queue_fsync_successes += 1
        real_fsync_directory(directory)

    complete_calls = 0
    real_complete = trigger._complete_published_enqueues

    def _complete(*args, **complete_kwargs):
        nonlocal complete_calls
        assert queue_fsync_successes > 0
        complete_calls += 1
        return real_complete(*args, **complete_kwargs)

    monkeypatch.setattr(trigger, "_build_and_write_upgrade_seed", _build)
    monkeypatch.setattr(trigger, "_fsync_directory", _fsync_directory)
    monkeypatch.setattr(trigger, "_complete_published_enqueues", _complete)

    failed = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)
    conn = sqlite3.connect(db)
    pending = conn.execute(
        "SELECT seed_file FROM fusion_upgrade_enqueues"
    ).fetchone()[0]
    conn.close()

    assert failed["seed_publish_failed"] == 1
    assert complete_calls == 0
    assert str(pending).startswith(trigger._PUBLISH_PENDING_PREFIX)
    assert len(list(seed_dir.glob("*.json"))) == 1
    assert len(list((seed_dir / ".fusion_upgrade_staging").glob("*.json"))) == 1

    recovered = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)

    assert recovered["already_enqueued"] == 1
    assert builds == 1
    assert complete_calls == 1
    assert queue_fsync_attempts == 3
    visible = list(seed_dir.glob("*.json"))
    assert len(visible) == 1
    conn = sqlite3.connect(db)
    marker = conn.execute(
        "SELECT seed_file FROM fusion_upgrade_enqueues"
    ).fetchone()[0]
    conn.close()
    assert marker == str(visible[0])
    assert list((seed_dir / ".fusion_upgrade_staging").glob("*.json")) == []


def test_uncertain_finalize_commit_retains_durable_staging_for_recovery(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception after PENDING commit cannot delete the only durable recovery seed."""
    db, kwargs = _revision_upgrade_kwargs(tmp_path)
    seed_dir = Path(kwargs["seed_dir"])
    monkeypatch.setattr(
        trigger,
        "scope_capture_offers_larger_provider_set",
        lambda *_args, **_kwargs: _revision_upgrade_verdict(),
    )
    builds = 0

    def _build(_conn, **build_kwargs):
        nonlocal builds
        builds += 1
        stage = Path(build_kwargs["seed_file"])
        stage.parent.mkdir(parents=True, exist_ok=True)
        stage.write_text("{}\n", encoding="utf-8")
        return stage

    real_finalize = trigger._finalize_enqueue_reservations
    finalize_attempts = 0

    def _finalize(*args, **finalize_kwargs):
        nonlocal finalize_attempts
        finalize_attempts += 1
        publish_pending = real_finalize(*args, **finalize_kwargs)
        if finalize_attempts == 1:
            raise RuntimeError("injected lost acknowledgement after finalize commit")
        return publish_pending

    monkeypatch.setattr(trigger, "_build_and_write_upgrade_seed", _build)
    monkeypatch.setattr(trigger, "_finalize_enqueue_reservations", _finalize)

    failed = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)
    conn = sqlite3.connect(db)
    pending = conn.execute(
        "SELECT seed_file FROM fusion_upgrade_enqueues"
    ).fetchone()[0]
    conn.close()
    publication = trigger._parse_publication(
        pending,
        prefix=trigger._PUBLISH_PENDING_PREFIX,
    )

    assert failed["reservation_finalize_failed"] == 1
    assert publication is not None
    assert publication.staging_file.is_file()
    assert list(seed_dir.glob("*.json")) == []

    recovered = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)

    assert recovered["already_enqueued"] == 1
    assert builds == 1
    visible = list(seed_dir.glob("*.json"))
    assert len(visible) == 1
    assert publication.staging_file.exists() is False
    conn = sqlite3.connect(db)
    marker = conn.execute(
        "SELECT seed_file FROM fusion_upgrade_enqueues"
    ).fetchone()[0]
    conn.close()
    assert marker == str(visible[0])


def test_overlapping_scoped_and_broad_reseed_owns_marker_before_seed_visibility(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent scoped/broad scans expose one seed only after one durable reservation."""
    import src.data.replacement_forecast_current_target_plan as target_plan

    db, kwargs = _revision_upgrade_kwargs(tmp_path)
    monkeypatch.setattr(
        trigger,
        "scope_capture_offers_larger_provider_set",
        lambda *_args, **_kwargs: _revision_upgrade_verdict(),
    )
    monkeypatch.setattr(
        target_plan,
        "build_replacement_forecast_current_target_plan",
        lambda *_args, **_kwargs: SimpleNamespace(
            status="READY",
            reason_codes=(),
            rows=(
                SimpleNamespace(
                    city="Seoul",
                    target_date="2026-07-25",
                    temperature_metric="high",
                    day0_observed_extreme_required=False,
                ),
            ),
        ),
    )
    build_started = threading.Event()
    release_build = threading.Event()
    build_calls = 0
    build_lock = threading.Lock()
    seed_dir = tmp_path / "seeds"

    def _build(_conn, **kwargs):
        nonlocal build_calls
        with build_lock:
            build_calls += 1
        marker_conn = sqlite3.connect(db)
        marker = marker_conn.execute(
            "SELECT seed_file FROM fusion_upgrade_enqueues"
        ).fetchone()
        marker_conn.close()
        assert marker is not None
        assert str(marker[0]).startswith(trigger._RESERVATION_PREFIX)
        assert list(seed_dir.glob("*.json")) == []
        build_started.set()
        assert release_build.wait(timeout=5)
        seed = Path(kwargs["seed_file"])
        seed.parent.mkdir(parents=True, exist_ok=True)
        seed.write_text("{}\n", encoding="utf-8")
        return seed

    monkeypatch.setattr(trigger, "_build_and_write_upgrade_seed", _build)
    broad_kwargs = {**kwargs, "scopes": None, "changed_sources": None}
    with ThreadPoolExecutor(max_workers=2) as executor:
        scoped = executor.submit(trigger.enqueue_fusion_upgrade_reseeds, **kwargs)
        assert build_started.wait(timeout=5)
        broad = executor.submit(
            trigger.enqueue_fusion_upgrade_reseeds,
            **broad_kwargs,
        )
        broad_report = broad.result(timeout=5)
        release_build.set()
        scoped_report = scoped.result(timeout=5)

    assert scoped_report["seeds_enqueued"] == 1
    assert broad_report["seeds_enqueued"] == 0
    assert broad_report["already_enqueued"] == 1
    assert build_calls == 1
    visible = list(seed_dir.glob("*.json"))
    assert len(visible) == 1
    conn = sqlite3.connect(db)
    markers = conn.execute(
        "SELECT capturable_family_set, seed_file FROM fusion_upgrade_enqueues"
    ).fetchall()
    conn.close()
    assert len(markers) == 1
    assert "|input_revision=icon_global:91" in markers[0][0]
    assert markers[0][1] == str(visible[0])


def test_failed_reserved_build_releases_marker_for_retry(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed owner removes only its reservation so the same revision can retry."""
    db, kwargs = _revision_upgrade_kwargs(tmp_path)
    monkeypatch.setattr(
        trigger,
        "scope_capture_offers_larger_provider_set",
        lambda *_args, **_kwargs: _revision_upgrade_verdict(),
    )
    attempts = 0
    seed_dir = tmp_path / "seeds"

    def _build(_conn, **kwargs):
        nonlocal attempts
        attempts += 1
        marker_conn = sqlite3.connect(db)
        marker = marker_conn.execute(
            "SELECT seed_file FROM fusion_upgrade_enqueues"
        ).fetchone()
        marker_conn.close()
        assert marker is not None
        assert str(marker[0]).startswith(trigger._RESERVATION_PREFIX)
        if attempts == 1:
            raise RuntimeError("injected seed build failure")
        seed = Path(kwargs["seed_file"])
        seed.parent.mkdir(parents=True, exist_ok=True)
        seed.write_text("{}\n", encoding="utf-8")
        return seed

    monkeypatch.setattr(trigger, "_build_and_write_upgrade_seed", _build)
    failed = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)
    conn = sqlite3.connect(db)
    marker_count_after_failure = conn.execute(
        "SELECT COUNT(*) FROM fusion_upgrade_enqueues"
    ).fetchone()[0]
    conn.close()
    retried = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)

    assert failed["seeds_enqueued"] == 0
    assert failed["seed_build_failed"] == 1
    assert marker_count_after_failure == 0
    assert retried["seeds_enqueued"] == 1
    assert attempts == 2
    visible = list(seed_dir.glob("*.json"))
    assert len(visible) == 1
    conn = sqlite3.connect(db)
    markers = conn.execute(
        "SELECT capturable_family_set, seed_file FROM fusion_upgrade_enqueues"
    ).fetchall()
    conn.close()
    assert len(markers) == 1
    assert "|input_revision=icon_global:91" in markers[0][0]
    assert markers[0][1] == str(visible[0])


def test_expired_owner_is_fenced_before_queue_publish(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale owner may finish private staging but cannot publish after takeover."""
    db, kwargs = _revision_upgrade_kwargs(tmp_path)
    monkeypatch.setattr(
        trigger,
        "scope_capture_offers_larger_provider_set",
        lambda *_args, **_kwargs: _revision_upgrade_verdict(),
    )
    monkeypatch.setattr(trigger, "_RESERVATION_TTL", timedelta(0))
    first_staged = threading.Event()
    release_first = threading.Event()
    build_count = 0
    build_lock = threading.Lock()

    def _build(_conn, **build_kwargs):
        nonlocal build_count
        with build_lock:
            build_count += 1
            ordinal = build_count
        stage = Path(build_kwargs["seed_file"])
        stage.parent.mkdir(parents=True, exist_ok=True)
        stage.write_text(
            json.dumps({"owner": "A" if ordinal == 1 else "B"}) + "\n",
            encoding="utf-8",
        )
        if ordinal == 1:
            first_staged.set()
            assert release_first.wait(timeout=5)
        return stage

    monkeypatch.setattr(trigger, "_build_and_write_upgrade_seed", _build)
    with ThreadPoolExecutor(max_workers=2) as executor:
        owner_a = executor.submit(trigger.enqueue_fusion_upgrade_reseeds, **kwargs)
        assert first_staged.wait(timeout=5)
        owner_b_report = executor.submit(
            trigger.enqueue_fusion_upgrade_reseeds,
            **kwargs,
        ).result(timeout=5)
        release_first.set()
        owner_a_report = owner_a.result(timeout=5)

    visible = list((tmp_path / "seeds").glob("*.json"))
    assert owner_b_report["seeds_enqueued"] == 1
    assert owner_a_report["seeds_enqueued"] == 0
    assert owner_a_report["reservation_finalize_failed"] == 1
    assert build_count == 2
    assert len(visible) == 1
    assert json.loads(visible[0].read_text(encoding="utf-8")) == {"owner": "B"}
    assert list((tmp_path / "seeds" / ".fusion_upgrade_staging").glob("*.json")) == []
    conn = sqlite3.connect(db)
    markers = conn.execute(
        "SELECT seed_file FROM fusion_upgrade_enqueues"
    ).fetchall()
    conn.close()
    assert markers == [(str(visible[0]),)]


def test_finalize_before_publish_crash_recovers_recorded_staging(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash after durable finalize recovers staging without rebuilding."""
    db, kwargs = _revision_upgrade_kwargs(tmp_path)
    monkeypatch.setattr(
        trigger,
        "scope_capture_offers_larger_provider_set",
        lambda *_args, **_kwargs: _revision_upgrade_verdict(),
    )
    builds = 0

    def _build(_conn, **build_kwargs):
        nonlocal builds
        builds += 1
        stage = Path(build_kwargs["seed_file"])
        stage.parent.mkdir(parents=True, exist_ok=True)
        stage.write_text('{"attempt":1}\n', encoding="utf-8")
        return stage

    real_publish = trigger._publish_finalized_seed
    publish_attempts = 0

    def _publish(publication):
        nonlocal publish_attempts
        publish_attempts += 1
        if publish_attempts == 1:
            raise RuntimeError("injected crash after marker finalize")
        return real_publish(publication)

    monkeypatch.setattr(trigger, "_build_and_write_upgrade_seed", _build)
    monkeypatch.setattr(trigger, "_publish_finalized_seed", _publish)
    failed = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)
    conn = sqlite3.connect(db)
    pending = conn.execute(
        "SELECT seed_file FROM fusion_upgrade_enqueues"
    ).fetchone()[0]
    conn.close()
    assert str(pending).startswith(trigger._PUBLISH_PENDING_PREFIX)
    assert list((tmp_path / "seeds").glob("*.json")) == []

    recovered = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)

    visible = list((tmp_path / "seeds").glob("*.json"))
    assert failed["seed_publish_failed"] == 1
    assert recovered["seeds_enqueued"] == 0
    assert recovered["already_enqueued"] == 1
    assert builds == 1
    assert publish_attempts == 2
    assert len(visible) == 1
    conn = sqlite3.connect(db)
    marker = conn.execute(
        "SELECT seed_file FROM fusion_upgrade_enqueues"
    ).fetchone()[0]
    conn.close()
    assert marker == str(visible[0])


def test_partial_transition_ownership_never_publishes_partial_group(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One live key blocks ownership of the missing sibling transition key."""
    db, kwargs = _revision_upgrade_kwargs(tmp_path)
    verdict = _revision_upgrade_verdict(family_upgrade=True)
    monkeypatch.setattr(
        trigger,
        "scope_capture_offers_larger_provider_set",
        lambda *_args, **_kwargs: verdict,
    )
    family_key = "DWD,UKMO"
    existing_publication = trigger._new_seed_publication(
        tmp_path / "seeds" / "existing.json"
    )
    conn = sqlite3.connect(db)
    conn.execute(
        """
        INSERT INTO fusion_upgrade_enqueues
            (enqueued_at, city, target_date, metric, source_cycle_time,
             served_family_set, capturable_family_set, seed_file)
        VALUES (?, 'Seoul', '2026-07-25', 'high', ?, 'DWD', ?, ?)
        """,
        (
            datetime.now(tz=UTC).isoformat(),
            str(verdict["source_cycle_time"]),
            family_key,
            trigger._publication_value(
                trigger._RESERVATION_PREFIX,
                existing_publication,
            ),
        ),
    )
    conn.commit()
    conn.close()
    builds = 0

    def _build(_conn, **build_kwargs):
        nonlocal builds
        builds += 1
        stage = Path(build_kwargs["seed_file"])
        stage.parent.mkdir(parents=True, exist_ok=True)
        stage.write_text("{}\n", encoding="utf-8")
        return stage

    monkeypatch.setattr(trigger, "_build_and_write_upgrade_seed", _build)
    blocked = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)
    conn = sqlite3.connect(db)
    blocked_rows = conn.execute(
        "SELECT capturable_family_set FROM fusion_upgrade_enqueues"
    ).fetchall()
    conn.execute(
        "UPDATE fusion_upgrade_enqueues SET enqueued_at = ?",
        ((datetime.now(tz=UTC) - timedelta(hours=1)).isoformat(),),
    )
    conn.commit()
    conn.close()

    recovered = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)

    assert blocked["seeds_enqueued"] == 0
    assert blocked["already_enqueued"] == 1
    assert blocked_rows == [(family_key,)]
    assert recovered["seeds_enqueued"] == 1
    assert builds == 1
    conn = sqlite3.connect(db)
    markers = conn.execute(
        "SELECT capturable_family_set, seed_file "
        "FROM fusion_upgrade_enqueues ORDER BY capturable_family_set"
    ).fetchall()
    conn.close()
    assert len(markers) == 2
    assert markers[0][1] == markers[1][1]
    assert not str(markers[0][1]).startswith("__fusion_upgrade_")


def test_unchanged_blocked_consumer_receipt_fences_pending_marker_recovery(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Marker-complete crash + unchanged-blocked consume retains a no-republish witness."""
    db, kwargs = _revision_upgrade_kwargs(tmp_path)
    queue_root = tmp_path / "replacement_forecast_live"
    seed_dir = queue_root / "seeds"
    kwargs["seed_dir"] = seed_dir
    monkeypatch.setattr(
        trigger,
        "scope_capture_offers_larger_provider_set",
        lambda *_args, **_kwargs: _revision_upgrade_verdict(),
    )
    builds = 0

    def _build(_conn, **build_kwargs):
        nonlocal builds
        builds += 1
        stage = Path(build_kwargs["seed_file"])
        stage.parent.mkdir(parents=True, exist_ok=True)
        stage.write_text(
            json.dumps(
                {
                    "city": "Seoul",
                    "target_date": "2026-07-25",
                    "temperature_metric": "high",
                    "computed_at": "2026-07-24T13:00:00+00:00",
                    "source_cycle_time": "2026-07-24T12:00:00+00:00",
                    "baseline_source_run_id": "baseline:test",
                    "openmeteo_source_run_id": "openmeteo:test",
                    "openmeteo_payload_json": "openmeteo.json",
                    "precision_metadata_json": "precision.json",
                    "bins": [{"bin_id": "20C"}],
                    "upgrade_trigger": "instrument_set_expansion",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return stage

    real_complete = trigger._complete_published_enqueues
    complete_attempts = 0
    durability_events: list[str] = []

    def _complete(*args, **complete_kwargs):
        nonlocal complete_attempts
        complete_attempts += 1
        if complete_attempts == 1:
            raise RuntimeError("injected crash after queue publish")
        assert "processed_dir_fsync" in durability_events
        durability_events.append("final_marker_complete")
        return real_complete(*args, **complete_kwargs)

    monkeypatch.setattr(trigger, "_build_and_write_upgrade_seed", _build)
    monkeypatch.setattr(trigger, "_complete_published_enqueues", _complete)
    failed = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)
    visible = list(seed_dir.glob("*.json"))
    assert len(visible) == 1

    # latest/ is legally newer, so it cannot become a hardlink witness for this
    # seed. The unchanged-blocked terminal path itself must move the public link
    # into processed/ and write a receipt.
    latest = queue_root / "seeds_latest" / "Seoul.2026-07-25.high.json"
    latest.parent.mkdir(parents=True)
    latest.write_text(
        json.dumps({"source_cycle_time": "2026-07-24T18:00:00+00:00"}) + "\n",
        encoding="utf-8",
    )
    blocked_marker = queue_root / "blocked_attempts" / "scope.json"
    blocked_marker.parent.mkdir(parents=True)
    blocked_marker.write_text('{"status":"BLOCKED"}\n', encoding="utf-8")
    seed_processed_dir = queue_root / "seed_processed"
    real_move_fsync = queue._fsync_directory

    def _move_fsync(path):
        directory = Path(path)
        real_move_fsync(directory)
        if directory == queue_root:
            durability_events.append("processed_parent_entry_fsync")
        elif directory == seed_processed_dir:
            durability_events.append("processed_dir_fsync")
        elif directory == seed_dir:
            durability_events.append("queue_dir_fsync")

    monkeypatch.setattr(
        queue,
        "build_replacement_forecast_materialization_request",
        lambda *_args, **_kwargs: SimpleNamespace(
            ok=True,
            request={
                "city": "Seoul",
                "target_date": "2026-07-25",
                "temperature_metric": "high",
                "source_cycle_time": "2026-07-24T12:00:00+00:00",
            },
            status="READY",
            reason_codes=(),
        ),
    )
    monkeypatch.setattr(
        queue,
        "_blocked_attempt_state",
        lambda **_kwargs: (blocked_marker, "same-input", True),
    )
    monkeypatch.setattr(queue, "_fsync_directory", _move_fsync)
    processed, queue_failed, reasons = queue._prepare_seed_requests(
        seed_dir=seed_dir,
        seed_processed_dir=seed_processed_dir,
        seed_failed_dir=queue_root / "seed_failed",
        request_dir=queue_root / "requests",
        forecast_db=db,
        limit=1,
    )
    moved = Path(processed[0])
    assert queue_failed == []
    assert queue._UNCHANGED_BLOCKED_SEED_SKIP_REASON in reasons
    assert list(seed_dir.glob("*.json")) == []
    assert moved.is_file()
    assert moved.stat().st_nlink == 2
    assert not moved.samefile(latest)
    receipt = json.loads(
        moved.with_suffix(moved.suffix + ".receipt.json").read_text(encoding="utf-8")
    )
    assert receipt == {
        "attempt_fingerprint": "same-input",
        "blocked_attempt_marker": str(blocked_marker),
        "reason_codes": [queue._UNCHANGED_BLOCKED_SEED_SKIP_REASON],
        "request_written": False,
        "status": "SKIPPED_UNCHANGED_BLOCKED_INPUT",
    }

    recovered = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)

    assert failed["publish_marker_complete_failed"] == 1
    assert recovered["already_enqueued"] == 1
    assert builds == 1
    assert complete_attempts == 2
    assert durability_events.index(
        "processed_parent_entry_fsync"
    ) < durability_events.index("processed_dir_fsync")
    assert durability_events.index("processed_dir_fsync") < durability_events.index(
        "final_marker_complete"
    )
    assert list(seed_dir.glob("*.json")) == []
    assert moved.exists()
    conn = sqlite3.connect(db)
    marker = conn.execute(
        "SELECT seed_file FROM fusion_upgrade_enqueues"
    ).fetchone()[0]
    conn.close()
    assert marker.endswith(".json")
    assert not str(marker).startswith(trigger._PUBLISH_PENDING_PREFIX)


def test_two_revisions_in_same_second_publish_distinct_transition_seeds(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second-granular base names cannot collapse two exact raw revisions."""
    db, kwargs = _revision_upgrade_kwargs(tmp_path)
    revisions = iter((91, 92))

    def _verdict(*_args, **_kwargs):
        raw_revision = next(revisions)
        return {
            **_revision_upgrade_verdict(),
            "changed_input_revisions": {_DWD: raw_revision},
        }

    built_revisions: list[int] = []

    def _build(_conn, **build_kwargs):
        raw_revision = 91 + len(built_revisions)
        built_revisions.append(raw_revision)
        stage = Path(build_kwargs["seed_file"])
        stage.parent.mkdir(parents=True, exist_ok=True)
        stage.write_text(
            json.dumps({"raw_revision": raw_revision}) + "\n",
            encoding="utf-8",
        )
        return stage

    monkeypatch.setattr(
        trigger,
        "scope_capture_offers_larger_provider_set",
        _verdict,
    )
    monkeypatch.setattr(trigger, "_build_and_write_upgrade_seed", _build)

    first = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)
    second = trigger.enqueue_fusion_upgrade_reseeds(**kwargs)

    visible = sorted((tmp_path / "seeds").glob("*.json"))
    assert first["seeds_enqueued"] == 1
    assert second["seeds_enqueued"] == 1
    assert built_revisions == [91, 92]
    assert len(visible) == 2
    assert {
        json.loads(path.read_text(encoding="utf-8"))["raw_revision"]
        for path in visible
    } == {91, 92}
    conn = sqlite3.connect(db)
    markers = conn.execute(
        "SELECT capturable_family_set, seed_file "
        "FROM fusion_upgrade_enqueues ORDER BY capturable_family_set"
    ).fetchall()
    conn.close()
    assert len(markers) == 2
    assert len({marker[1] for marker in markers}) == 2
    assert all(Path(marker[1]).is_file() for marker in markers)


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
