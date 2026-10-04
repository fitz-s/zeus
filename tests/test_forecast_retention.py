# Created: 2026-09-29
# Last reused/audited: 2026-10-03
# Lifecycle: created=2026-09-29; last_reviewed=2026-10-03; last_reused=2026-10-03
# Purpose: Protect reachability retention and isolated optional OpenData surface audit scheduling.
# Reuse: Use private databases/files and fake HTTP; never execute a live retention pass.
# Authority basis: docs/operations/current/plans/edge_program_2026-09-25.md goal 4
#   (retention by reachability, one rule for every forecast store).
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.data import forecast_retention as fr

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
OLD = "2026-09-20"  # unreachable by date
CUR = "2026-09-28"  # reachable by date (>= 2026-09-27)
HELD = "2026-09-10"  # old but a position on it is still open


def _trade_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE position_current (position_id TEXT PRIMARY KEY, phase TEXT, city TEXT,"
        " target_date TEXT, temperature_metric TEXT)"
    )
    conn.executemany(
        "INSERT INTO position_current VALUES (?,?,?,?,?)",
        [
            ("p1", "active", "Hong Kong", HELD, "high"),
            ("p2", "settled", "Paris", OLD, "high"),
        ],
    )
    conn.execute(
        "CREATE TABLE venue_commands (command_id TEXT, position_id TEXT, venue_order_id TEXT,"
        " token_id TEXT, snapshot_id TEXT, state TEXT, intent_kind TEXT)"
    )
    conn.execute(
        "CREATE TABLE venue_order_facts (venue_order_id TEXT, state TEXT, remaining_size REAL,"
        " local_sequence INTEGER)"
    )
    conn.execute(
        "CREATE TABLE executable_market_snapshots (snapshot_id TEXT, event_slug TEXT,"
        " selected_outcome_token_id TEXT, captured_at TEXT)"
    )
    conn.commit()
    conn.close()
    return path


def _samples_provenance() -> str:
    return json.dumps(
        {
            "q_bootstrap_samples_by_bin": {"b": [0.1] * 400},
            "day0_remaining_carrier_probability_samples": [[0.2] * 11] * 40,
            "q_bootstrap_samples_hash": "h" * 64,
            "day0_remaining_carrier_future_extremes_c": [20.0, 21.0],
            "q_shape": "x",
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _forecast_db(path: Path, rows: list[tuple[str, str, str]]) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE forecast_posteriors (posterior_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " city TEXT, target_date TEXT, temperature_metric TEXT, provenance_json TEXT)"
    )
    conn.execute(
        "CREATE TABLE raw_forecast_artifacts (artifact_id INTEGER PRIMARY KEY, source_id TEXT,"
        " source_cycle_time TEXT, artifact_metadata_json TEXT)"
    )
    conn.executemany(
        "INSERT INTO forecast_posteriors (city, target_date, temperature_metric, provenance_json)"
        " VALUES (?,?,?,?)",
        [(c, d, m, _samples_provenance()) for c, d, m in rows],
    )
    conn.commit()
    conn.close()
    return path


def _touch(path: Path, body: str = "{}") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def _queue_files(state: Path) -> dict[str, Path]:
    q = state / "replacement_forecast_live"
    files = {
        "old": _touch(q / "seed_processed" / f"Paris.{OLD}.high.20260919T000000Z.x.json"),
        "old_receipt": _touch(q / "seed_processed" / f"Paris.{OLD}.high.20260919T000000Z.x.json.receipt.json"),
        "cur": _touch(q / "seed_processed" / f"Paris.{CUR}.high.20260927T000000Z.x.json"),
        "held": _touch(q / "seed_failed" / f"Hong_Kong.{HELD}.high.20260909T000000Z.x.json"),
        "latest_old": _touch(q / "seeds_latest" / f"Paris.{OLD}.low.json"),
        "live_seed": _touch(q / "seeds" / f"Paris.{OLD}.high.20260919T000000Z.x.json"),
        "request": _touch(q / "requests" / f"Paris.{OLD}.high.x.json"),
        "index_old": _touch(
            q / "seed_receipts" / "ab" / "abc.json",
            json.dumps({"seed_file": str(q / "seeds" / f"Paris.{OLD}.high.1.json")}),
        ),
        "index_cur": _touch(
            q / "seed_receipts" / "cd" / "cde.json",
            json.dumps({"seed_file": str(q / "seeds" / f"Paris.{CUR}.high.1.json")}),
        ),
    }
    return files


def _manifest(raw: Path, city: str, date: str, cycle: str, sha: str = "0" * 12) -> tuple[Path, Path, Path]:
    cycle_dir = raw / cycle
    seg = city.replace(" ", "_")
    payload = _touch(cycle_dir / f"openmeteo_{seg}_{date}_high_{cycle}.json")
    precision = _touch(cycle_dir / f"openmeteo_precision_{seg}_{date}_high.json")
    iso = datetime.strptime(cycle, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
    manifest = _touch(
        raw / f"openmeteo_ecmwf_ifs_9km.v.{cycle}.{sha}.{seg}.manifest.json",
        json.dumps(
            {
                "artifact_path": str(payload),
                "source_cycle_time": iso,
                "product_metadata": {
                    "city": city,
                    "metric": "high",
                    "target_date": date,
                    "target_dates": [date],
                    "openmeteo_payload_json": str(payload),
                    "precision_metadata_json": str(precision),
                },
            }
        ),
    )
    return manifest, payload, precision


@pytest.fixture()
def env(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    trade = _trade_db(tmp_path / "trades.db")
    forecast = _forecast_db(
        tmp_path / "forecasts.db",
        [("Paris", OLD, "high"), ("Paris", CUR, "high"), ("Hong Kong", HELD, "high")],
    )
    return state, trade, forecast


def _run(state: Path, trade: Path, forecast: Path, *, apply: bool, **kw):
    return fr.run_forecast_retention(
        apply=apply, now=NOW, state_dir=state, forecast_db=forecast, trade_db=trade, **kw
    )


def _provenance(forecast: Path) -> dict[tuple[str, str], dict]:
    conn = sqlite3.connect(forecast)
    rows = conn.execute("SELECT city, target_date, provenance_json FROM forecast_posteriors").fetchall()
    conn.close()
    return {(c, d): json.loads(p) for c, d, p in rows}


def test_reachable_items_survive_and_unreachable_are_evicted(env):
    state, trade, forecast = env
    files = _queue_files(state)
    raw = state / "replacement_forecast_live" / "raw_manifests"
    old_m = _manifest(raw, "Paris", OLD, "20260919T000000Z")
    cur_m = _manifest(raw, "Paris", CUR, "20260927T000000Z", sha="1" * 12)
    held_m = _manifest(raw, "Hong Kong", HELD, "20260909T000000Z", sha="2" * 12)

    summary = _run(state, trade, forecast, apply=True)
    assert summary["status"] == "APPLIED"

    for key in ("old", "old_receipt", "latest_old", "index_old"):
        assert not files[key].exists(), key
    for key in ("cur", "held", "live_seed", "request", "index_cur"):
        assert files[key].exists(), key
    assert not any(p.exists() for p in old_m)
    assert not old_m[1].parent.exists()  # drained cycle dir removed
    assert all(p.exists() for p in cur_m)
    assert all(p.exists() for p in held_m)

    prov = _provenance(forecast)
    old = prov[("Paris", OLD)]
    assert "q_bootstrap_samples_by_bin" not in old
    assert "day0_remaining_carrier_probability_samples" not in old
    assert old[fr.EVICTED_KEYS_FIELD] == sorted(fr.EVICTABLE_POSTERIOR_KEYS)
    # identity, hashes and refit inputs are kept
    assert old["q_bootstrap_samples_hash"] == "h" * 64
    assert old["day0_remaining_carrier_future_extremes_c"] == [20.0, 21.0]
    for key in (("Paris", CUR), ("Hong Kong", HELD)):
        assert "q_bootstrap_samples_by_bin" in prov[key], key


def test_dry_run_reports_and_mutates_nothing(env):
    state, trade, forecast = env
    files = _queue_files(state)
    before = _provenance(forecast)
    summary = _run(state, trade, forecast, apply=False)
    assert summary["status"] == "DRY_RUN"
    assert summary["stores"]["queue_files"]["evicted"] == 4
    assert summary["stores"]["posterior_samples"]["evicted"] == 1
    assert summary["stores"]["posterior_samples"]["bytes"] > 0
    assert all(p.exists() for p in files.values())
    assert _provenance(forecast) == before
    assert not (state / fr.CURSOR_FILE).exists()


def test_idempotent_second_pass_evicts_nothing(env):
    state, trade, forecast = env
    _queue_files(state)
    _run(state, trade, forecast, apply=True)
    (state / fr.CURSOR_FILE).unlink()
    second = _run(state, trade, forecast, apply=True)
    assert second["stores"]["queue_files"]["evicted"] == 0
    assert second["stores"]["posterior_samples"]["evicted"] == 0


def test_pending_cross_check_cycle_is_kept(env):
    state, trade, forecast = env
    raw = state / "replacement_forecast_live" / "raw_manifests"
    manifest, payload, _ = _manifest(raw, "Paris", OLD, "20260919T000000Z")
    conn = sqlite3.connect(forecast)
    conn.execute(
        "INSERT INTO raw_forecast_artifacts VALUES (1, 'openmeteo_ecmwf_ifs_9km',"
        " '2026-09-19T00:00:00+00:00', ?)",
        (json.dumps({"city": "Paris", "run_authority": "provider_meta_declared"}),),
    )
    conn.commit()
    conn.close()
    _run(state, trade, forecast, apply=True)
    assert manifest.exists() and payload.exists()
    # terminal receipt releases the cycle
    (state / "anchor_cross_check.json").write_text(
        json.dumps({"2026-09-19T00:00:00+00:00": {"verdict": "VERIFIED"}})
    )
    _run(state, trade, forecast, apply=True)
    assert not manifest.exists() and not payload.exists()


def test_unknown_reachability_evicts_nothing(env, tmp_path):
    state, _trade, forecast = env
    files = _queue_files(state)
    summary = _run(state, tmp_path / "missing" / "trades.db", forecast, apply=True)
    assert summary["status"] == "REACHABILITY_UNAVAILABLE"
    assert all(p.exists() for p in files.values())


def test_file_work_is_bounded_per_pass(env):
    state, trade, forecast = env
    q = state / "replacement_forecast_live" / "seed_processed"
    for i in range(25):
        _touch(q / f"Paris.{OLD}.high.{i:04d}.json")
    first = _run(state, trade, forecast, apply=True, file_budget=10)
    assert first["stores"]["queue_files"]["evicted"] == 10
    assert first["stores"]["queue_files"]["stopped"] == "file_budget"
    assert len(list(q.iterdir())) == 15
    _run(state, trade, forecast, apply=True, file_budget=10)
    _run(state, trade, forecast, apply=True, file_budget=10)
    assert list(q.iterdir()) == []


def test_posterior_work_is_row_bounded_and_resumes_from_cursor(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    trade = _trade_db(tmp_path / "trades.db")
    forecast = _forecast_db(tmp_path / "f.db", [("Paris", OLD, "high")] * 7)
    first = _run(state, trade, forecast, apply=True, row_budget=3, batch_rows=2)
    assert first["stores"]["posterior_samples"]["scanned"] == 3
    assert first["stores"]["posterior_samples"]["evicted"] == 3
    _run(state, trade, forecast, apply=True, row_budget=3, batch_rows=2)
    _run(state, trade, forecast, apply=True, row_budget=3, batch_rows=2)
    assert all(fr.EVICTED_KEYS_FIELD in p for p in _provenance(forecast).values())
    wrapped = _run(state, trade, forecast, apply=True, row_budget=3, batch_rows=2)
    assert wrapped["stores"]["posterior_samples"]["stopped"] == "wrapped"


def test_wal_limit_stops_posterior_writes(env):
    state, trade, forecast = env
    before = _provenance(forecast)
    summary = _run(state, trade, forecast, apply=True, wal_limit_bytes=-1)
    assert summary["stores"]["posterior_samples"]["stopped"] == "wal_limit"
    assert summary["stores"]["posterior_samples"]["evicted"] == 0
    assert _provenance(forecast) == before


def test_reachability_window_covers_every_local_day():
    reach = fr.Reachability("2026-09-27", frozenset({("Hong_Kong", HELD, "high")}))
    # UTC-12 local day 2026-09-28 ends 2026-09-29T12:00Z; still reachable on 09-29.
    assert reach.reachable(("Paris", "2026-09-27", "high"))
    assert not reach.reachable(("Paris", "2026-09-26", "high"))
    assert reach.reachable(("Hong_Kong", HELD, "high"))
    assert not reach.reachable(("Hong_Kong", HELD, "low"))


def test_forecast_live_daemon_registers_retention_job(monkeypatch):
    from src.ingest import forecast_live_daemon as daemon

    jobs: list[tuple[object, str, dict]] = []

    class Scheduler:
        def add_job(self, fn, trigger, **kwargs) -> None:
            jobs.append((fn, trigger, kwargs))

    monkeypatch.setattr(daemon, "_replacement_forecast_materialize_interval_minutes", lambda: 5)
    daemon._register_replacement_forecast_production_jobs(Scheduler())
    (job,) = [j for j in jobs if j[2]["id"] == daemon.FORECAST_RETENTION_JOB_ID]
    assert job[0] is daemon._forecast_retention_job
    assert job[2]["executor"] == daemon.FORECAST_RETENTION_EXECUTOR_LANE
    assert job[2]["max_instances"] == 1


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
@pytest.mark.parametrize("hour", (0, 18))
def test_retention_normal_lane_captures_real_surface_after_return(tmp_path, monkeypatch, track, hour):
    from src.ingest import forecast_live_daemon as daemon
    from tests.test_ecmwf_open_data_collect_cycle import _terrain_audit_fixture

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch, tracks=(track,),
        issue=datetime(2026, 10, 3, hour, tzinfo=timezone.utc))
    returned = []
    summary = {"status": "ok", "evicted": 0}

    def retention(**kwargs):
        assert kwargs == {"apply": True}
        returned.append(True)
        return summary

    monkeypatch.setattr(fr, "run_forecast_retention", retention)
    fixture["session"].before_get = lambda: (returned == [True] and fixture["closed"] == [True])
    result = daemon._forecast_retention_job.__wrapped__()
    assert result is summary
    assert fixture["surface_paths"][track].exists(), "normal retention must capture genuine optional z bytes"
    assert len(fixture["session"].calls) == 2
    assert tuple(fixture["db"].iterdump()) == fixture["before"]


@pytest.mark.parametrize("status", ("ok", "REACHABILITY_UNAVAILABLE"))
def test_retention_result_unchanged_by_slow_or_failed_surface_audit(tmp_path, monkeypatch, status):
    from src.ingest import forecast_live_daemon as daemon
    from src.data import ecmwf_open_data
    from tests.test_ecmwf_open_data_collect_cycle import _terrain_audit_fixture

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    fixture["session"].failure = ecmwf_open_data.requests.Timeout("slow optional z")
    summary = {"status": status, "error": "retention reachability gap"}
    monkeypatch.setattr(fr, "run_forecast_retention", lambda **kwargs: summary)
    result = daemon._forecast_retention_job.__wrapped__()
    assert result == (summary if status == "ok" else {"status": "failed", "error": summary["error"]})
    assert len(fixture["session"].calls) == 1
    assert tuple(fixture["db"].iterdump()) == fixture["before"]


def _add_position(trade: Path, pid: str, phase: str, city, date, metric) -> None:
    conn = sqlite3.connect(trade)
    conn.execute("INSERT INTO position_current VALUES (?,?,?,?,?)", (pid, phase, city, date, metric))
    conn.commit()
    conn.close()


@pytest.mark.parametrize("missing", ["city", "target_date", "temperature_metric"])
def test_unnamed_open_position_evicts_nothing(env, missing):
    state, trade, forecast = env
    files = _queue_files(state)
    values = {"city": "Paris", "target_date": OLD, "temperature_metric": "high"}
    values[missing] = None
    _add_position(trade, "p9", "pending_exit", values["city"], values["target_date"], values["temperature_metric"])
    before = _provenance(forecast)
    summary = _run(state, trade, forecast, apply=True)
    assert summary["status"] == "REACHABILITY_UNAVAILABLE"
    assert all(p.exists() for p in files.values())
    assert _provenance(forecast) == before


def test_unnamed_terminal_position_does_not_block(env):
    state, trade, forecast = env
    _add_position(trade, "p9", "voided", None, None, None)
    assert _run(state, trade, forecast, apply=False)["status"] == "DRY_RUN"


def test_economically_closed_family_is_kept(env):
    state, trade, forecast = env
    _add_position(trade, "p9", "economically_closed", "Paris", OLD, "high")
    files = _queue_files(state)
    _run(state, trade, forecast, apply=True)
    assert files["old"].exists()
    assert "q_bootstrap_samples_by_bin" in _provenance(forecast)[("Paris", OLD)]


def test_open_entry_rest_without_position_row_is_kept(env):
    state, trade, forecast = env
    conn = sqlite3.connect(trade)
    conn.execute(
        "INSERT INTO venue_commands VALUES ('c1', '', 'o1', 'tok', 'snap', 'ACKED', 'ENTRY')"
    )
    conn.execute("INSERT INTO venue_order_facts VALUES ('o1', 'LIVE', 5.0, 1)")
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES"
        " ('snap', 'highest-temperature-in-paris-on-september-20-2026', 'tok', '2026-09-19')"
    )
    conn.commit()
    conn.close()
    files = _queue_files(state)
    _run(state, trade, forecast, apply=True)
    assert files["old"].exists()
    assert "q_bootstrap_samples_by_bin" in _provenance(forecast)[("Paris", OLD)]
