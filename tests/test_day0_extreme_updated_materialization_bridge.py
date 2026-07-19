# Created: 2026-07-19
# Last reused/audited: 2026-07-19
# Authority basis: operator directive 2026-07-19 (Day0 is a zero-sum race against the market
#   book) + docs/evidence/upstream_physical_2026_07_17/day0_latency_chain_measurement.md (the
#   measured bottleneck is the ~40-min SCHEDULED posterior recompute cadence, HOP 2b p50 39.9 min
#   / p90 90 min — fetch and event delivery are already fast). Sibling of
#   src.data.replacement_cycle_advance_trigger's single-family cycle-advance reseed (Task #32
#   family) — this is the SAME seed transport, bridged from event EMISSION instead of from a
#   reactive stale-posterior processing failure.
"""Event-driven Day0 recompute bridge tests.

``enqueue_day0_extreme_updated_materialization_seed`` (src/data/replacement_cycle_advance_trigger.py)
is called right after a DAY0_EXTREME_UPDATED event commits (ingest_main.py's fast METAR source
clock, and reactor.py's catch-up scan lane). It must:

  (a) force exactly ONE live materialization seed for the family per fresh observation, reusing
      the EXISTING single-family cycle-advance seed transport verbatim (same seed builder, same
      seed_dir, same ``cycle_advance_enqueues`` idempotency marker);
  (b) dedup a repeat call carrying the SAME observation_time via the existing monotone guard
      already proven in test_cycle_monotone_materialization.py (no new seed, no row churn), but
      advance on a STRICTLY NEWER observation_time even with no model-cycle change (the same-day
      exit-blindness fix, REQ-20260623-184115);
  (c) be fail-soft end to end — a missing config, no canonical observed extreme, or any internal
      fault returns a status dict and never raises into the event-emission path.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import src.data.replacement_cycle_advance_trigger as cycle_advance
import src.data.replacement_forecast_production as forecast_production
import src.data.replacement_forecast_seed_discovery as seed_discovery
from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema

UTC = timezone.utc


def _queue_config(tmp_path: Path) -> dict[str, object]:
    return {
        "forecast_db": tmp_path / "forecasts.db",
        "seed_dir": tmp_path / "seeds",
        "raw_manifest_dir": tmp_path / "raw",
    }


def _day0_payload(observation_time: str) -> dict[str, object]:
    return {
        "day0_observed_extreme_c": 21.0,
        "day0_observed_extreme_source": "wu_icao_history",
        "day0_observed_extreme_observation_time": observation_time,
        "day0_observed_extreme_sample_count": 4,
        "day0_observed_extreme_unit": "C",
    }


def _fake_build_seed_factory():
    """Stand in for the real seed builder (network/manifest-independent for this bridge unit
    test — the seed-content shape itself is covered by test_cycle_monotone_materialization.py)."""
    calls = {"count": 0}

    def _fake_build_seed(_conn_arg, **kwargs):
        calls["count"] += 1
        path = Path(kwargs["seed_path"]) / f"Shanghai.seed.{calls['count']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "day0_observed_extreme_observation_time": kwargs.get(
                        "day0_observed_extreme_observation_time"
                    ),
                }
            ),
            encoding="utf-8",
        )
        return path

    return _fake_build_seed, calls


def _prepare_forecast_db(tmp_path: Path) -> Path:
    """A schema-only forecast DB plus one anchor-leg raw artifact so
    freshest_materializable_cycle has a high-water mark to report."""
    db_path = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    cycle_iso = datetime(2026, 7, 19, 0, tzinfo=UTC).isoformat()
    meaningful = {
        "source_id": cycle_advance._ANCHOR_LEG_SOURCE_ID,
        "source_cycle_time": cycle_iso,
    }
    values: dict[str, object] = {}
    for row in conn.execute("PRAGMA table_info(raw_forecast_artifacts)"):
        name, notnull, pk = row[1], row[3], row[5]
        if pk:
            continue
        if name in meaningful:
            values[name] = meaningful[name]
        elif notnull:
            if name.endswith("_json"):
                values[name] = "{}"
            elif name in ("byte_size", "training_allowed"):
                values[name] = 0
            elif name == "runtime_layer":
                values[name] = "live"
            elif name.endswith("_at") or name.endswith("_time"):
                values[name] = cycle_iso
            else:
                values[name] = "x"
    names = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    conn.execute(
        f"INSERT INTO raw_forecast_artifacts ({names}) VALUES ({placeholders})",
        tuple(values.values()),
    )
    conn.commit()
    conn.close()
    return db_path


def _fetch_enqueue_row(db_path: Path) -> sqlite3.Row:
    check = sqlite3.connect(db_path)
    check.row_factory = sqlite3.Row
    row = check.execute(
        "SELECT day0_observed_extreme_observation_time, seed_file "
        "FROM cycle_advance_enqueues WHERE city='Shanghai' AND target_date='2026-07-19' "
        "AND metric='high'"
    ).fetchone()
    check.close()
    return row


def test_day0_extreme_bridge_enqueues_exactly_one_seed_and_dedups_same_observation_time(
    tmp_path, monkeypatch
) -> None:
    _prepare_forecast_db(tmp_path)
    cfg = _queue_config(tmp_path)
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    observation_time = "2026-07-19T05:00:00+00:00"
    monkeypatch.setattr(
        seed_discovery,
        "_day0_observed_extreme_seed_payload",
        lambda **_kwargs: _day0_payload(observation_time),
    )
    cycle = datetime(2026, 7, 19, 0, tzinfo=UTC)
    monkeypatch.setattr(cycle_advance, "family_materializable_cycle", lambda *a, **k: (cycle, ()))
    fake_build_seed, calls = _fake_build_seed_factory()
    monkeypatch.setattr(cycle_advance, "_build_and_write_advance_seed", fake_build_seed)

    report_1 = cycle_advance.enqueue_day0_extreme_updated_materialization_seed(
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 1, tzinfo=UTC),
        held_position=False,
    )
    assert report_1["status"] == "CYCLE_ADVANCE_FIRST_MATERIALIZATION_ENQUEUED"
    assert report_1["enqueued"] is True
    assert calls["count"] == 1, "exactly one seed built for the fresh observation"

    row = _fetch_enqueue_row(cfg["forecast_db"])
    assert row["day0_observed_extreme_observation_time"] == observation_time
    first_seed_file = row["seed_file"]

    # REPEAT call carrying the SAME observation_time must dedup: no new seed built, the
    # existing cycle_advance_enqueues row (and its seed file) is left untouched.
    report_2 = cycle_advance.enqueue_day0_extreme_updated_materialization_seed(
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 2, tzinfo=UTC),
        held_position=False,
    )
    assert report_2["status"] == "CYCLE_ADVANCE_ALREADY_ENQUEUED"
    assert calls["count"] == 1, "repeat with the same observation_time must not build a second seed"

    row_after = _fetch_enqueue_row(cfg["forecast_db"])
    assert row_after["seed_file"] == first_seed_file


def test_day0_extreme_bridge_advances_on_strictly_newer_observation_time(
    tmp_path, monkeypatch
) -> None:
    """A genuinely newer observed extreme (later observation_time) re-seeds even though the
    model cycle has not advanced — the same-day exit-blindness fix this reuses verbatim."""
    _prepare_forecast_db(tmp_path)
    cfg = _queue_config(tmp_path)
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    cycle = datetime(2026, 7, 19, 0, tzinfo=UTC)
    monkeypatch.setattr(cycle_advance, "family_materializable_cycle", lambda *a, **k: (cycle, ()))
    fake_build_seed, calls = _fake_build_seed_factory()
    monkeypatch.setattr(cycle_advance, "_build_and_write_advance_seed", fake_build_seed)

    monkeypatch.setattr(
        seed_discovery,
        "_day0_observed_extreme_seed_payload",
        lambda **_kwargs: _day0_payload("2026-07-19T05:00:00+00:00"),
    )
    report_1 = cycle_advance.enqueue_day0_extreme_updated_materialization_seed(
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 1, tzinfo=UTC),
        held_position=False,
    )
    assert report_1["enqueued"] is True

    monkeypatch.setattr(
        seed_discovery,
        "_day0_observed_extreme_seed_payload",
        lambda **_kwargs: _day0_payload("2026-07-19T06:00:00+00:00"),
    )
    report_2 = cycle_advance.enqueue_day0_extreme_updated_materialization_seed(
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 6, 1, tzinfo=UTC),
        held_position=False,
    )
    assert calls["count"] == 2, "a strictly newer observation_time must force a fresh seed"
    assert report_2["enqueued"] is True

    row = _fetch_enqueue_row(cfg["forecast_db"])
    assert row["day0_observed_extreme_observation_time"] == "2026-07-19T06:00:00+00:00"


def test_day0_extreme_bridge_not_configured_is_failsoft(monkeypatch) -> None:
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"forecast_db": None, "seed_dir": None, "raw_manifest_dir": None},
    )
    report = cycle_advance.enqueue_day0_extreme_updated_materialization_seed(
        city="Shanghai", target_date="2026-07-19", metric="high",
    )
    assert report["status"] == "DAY0_EXTREME_BRIDGE_NOT_CONFIGURED"


def test_day0_extreme_bridge_no_observed_extreme_is_failsoft(tmp_path, monkeypatch) -> None:
    cfg = _queue_config(tmp_path)
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        seed_discovery, "_day0_observed_extreme_seed_payload", lambda **_kwargs: None,
    )
    report = cycle_advance.enqueue_day0_extreme_updated_materialization_seed(
        city="Shanghai", target_date="2026-07-19", metric="high",
    )
    assert report["status"] == "DAY0_EXTREME_BRIDGE_NO_OBSERVED_EXTREME"


def test_day0_extreme_bridge_config_lookup_failure_is_failsoft(monkeypatch) -> None:
    def _raise():
        raise RuntimeError("boom")

    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        _raise,
    )
    report = cycle_advance.enqueue_day0_extreme_updated_materialization_seed(
        city="Shanghai", target_date="2026-07-19", metric="high",
    )
    assert report["status"] == "DAY0_EXTREME_BRIDGE_FAILSOFT_SKIPPED"
    assert "error" in report


def test_day0_extreme_bridge_auto_detects_held_position(tmp_path, monkeypatch) -> None:
    """held_position=None auto-detects via the coworker's held-family helper (2b5ae40a3): a
    family with money at risk is tagged held for priority draining even when the caller
    (event emission) does not itself know about held positions."""
    _prepare_forecast_db(tmp_path)
    cfg = _queue_config(tmp_path)
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        seed_discovery,
        "_day0_observed_extreme_seed_payload",
        lambda **_kwargs: _day0_payload("2026-07-19T05:00:00+00:00"),
    )
    cycle = datetime(2026, 7, 19, 0, tzinfo=UTC)
    monkeypatch.setattr(cycle_advance, "family_materializable_cycle", lambda *a, **k: (cycle, ()))
    fake_build_seed, _calls = _fake_build_seed_factory()
    monkeypatch.setattr(cycle_advance, "_build_and_write_advance_seed", fake_build_seed)

    import src.events.reactor as reactor_mod

    monkeypatch.setattr(
        reactor_mod,
        "_edli_current_held_position_family_keys",
        lambda: {("Shanghai", "2026-07-19", "high")},
    )

    report = cycle_advance.enqueue_day0_extreme_updated_materialization_seed(
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 1, tzinfo=UTC),
    )
    assert report["held_position"] is True


def test_day0_extreme_bridge_held_autodetect_failure_defaults_false(tmp_path, monkeypatch) -> None:
    """A held-family read failure must not crash the bridge — fall back to non-held so the seed
    still gets written (priority tagging is best-effort, never a gate on whether to seed)."""
    _prepare_forecast_db(tmp_path)
    cfg = _queue_config(tmp_path)
    monkeypatch.setattr(
        forecast_production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        seed_discovery,
        "_day0_observed_extreme_seed_payload",
        lambda **_kwargs: _day0_payload("2026-07-19T05:00:00+00:00"),
    )
    cycle = datetime(2026, 7, 19, 0, tzinfo=UTC)
    monkeypatch.setattr(cycle_advance, "family_materializable_cycle", lambda *a, **k: (cycle, ()))
    fake_build_seed, calls = _fake_build_seed_factory()
    monkeypatch.setattr(cycle_advance, "_build_and_write_advance_seed", fake_build_seed)

    import src.events.reactor as reactor_mod

    def _raise():
        raise RuntimeError("trades db unreachable")

    monkeypatch.setattr(reactor_mod, "_edli_current_held_position_family_keys", _raise)

    report = cycle_advance.enqueue_day0_extreme_updated_materialization_seed(
        city="Shanghai",
        target_date="2026-07-19",
        metric="high",
        computed_at=datetime(2026, 7, 19, 5, 1, tzinfo=UTC),
    )
    assert report["held_position"] is False
    assert report["enqueued"] is True
    assert calls["count"] == 1
