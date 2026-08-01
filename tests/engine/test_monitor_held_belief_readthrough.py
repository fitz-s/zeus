# Created: 2026-06-21
# Last reused or audited: 2026-07-24
# Authority basis: docs/evidence/live_order_pathology/2026-06-21_forward_chain_diagnosis.md
#   "CHOSEN FIX (consult-validated, two layers)" — LAYER 2 monitor read-through.
"""ANTIBODY: stale held belief must recover without blocking portfolio monitoring.

The disease (live −$27.63): a held family's cached forecast_posteriors row goes
stale and the monitor fail-closes to HOLD (BELIEF_AUTHORITY_FAULT) FOREVER —
never recomputing — so the conservative CI_SEPARATED_REVERSAL exit is starved and
the position rides physics reversals to full settlement loss. These tests pin:

1. An unbounded diagnostic read-through may restore same-authority probability.
   The bounded live portfolio monitor never runs that Python fusion inline; it
   fails closed and dispatches the independent producer for the next re-decision.
2. When inputs are genuinely insufficient, the monitor STILL fail-closes (is_fresh
   not True) AND records a DURABLE, RETRYABLE belief_debt marker — never a silent
   permanent freeze.
3. NO FALSE EXIT: the monitor only supplies a fresh belief; it never itself decides
   an exit. A freshly-recomputed belief that has NOT reversed simply becomes fresh
   authority (HOLD is still decided downstream by the untouched CI gate).

These are antibodies: removing the bounded producer/consumer split can again
let one family retain the whole portfolio beyond its deadline; removing the
belief_debt record makes producer failure silent.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

BIN = "Will the highest temperature in Karachi be 37°C on June 12?"


def _pos():
    from src.state.portfolio import Position

    return Position(
        trade_id="t-readthrough-1",
        market_id="m1",
        city="Karachi",
        cluster="Karachi",
        target_date="2026-06-12",
        bin_label=BIN,
        direction="buy_no",
        unit="C",
        temperature_metric="high",
        entry_method="ens_member_counting",
        entry_price=0.66,
        p_posterior=0.855,
    )


def _stale_belief():
    from src.engine.position_belief import ReplacementBelief

    return ReplacementBelief(
        held_side_prob=0.758, held_side_lcb=0.69, held_side_ucb=0.82,
        q_yes_bin=0.242, q_yes_lcb=0.18, q_yes_ucb=0.31, posterior_id="p9",
        computed_at="2026-06-12T00:00:00+00:00", age_hours=99.0,
        fresh=False, bin_key=BIN, direction="buy_no",
    )


def test_readthrough_fresh_recompute_restores_probability_authority(monkeypatch):
    """Stale cached belief + a successful read-through recompute → is_fresh True.

    Antibody: without the read-through call this returns is_fresh False (the live
    freeze). The recompute yields the held-side prob and the monitor attests it.
    """
    import src.engine.monitor_refresh as mr
    import src.engine.position_belief as pb

    monkeypatch.setattr(pb, "load_replacement_belief", lambda **kw: _stale_belief())
    # The legacy chain must NEVER be the freshness source.
    monkeypatch.setattr(mr, "_refresh_ens_member_counting", lambda **kw: (0.5, []))
    # Read-through recompute succeeds and returns the held-side prob (e.g. NO has
    # collapsed to 0.30 — a reversal the frozen 0.758 belief could never see).
    monkeypatch.setattr(
        mr, "_attempt_held_belief_readthrough", lambda *a, **k: (0.30, 0.22, 0.41)
    )

    pos = _pos()
    prob, refresh_pos, is_fresh = mr.monitor_probability_refresh(
        pos, conn=None, city=object(), target_d=None,
    )

    assert is_fresh is True
    assert prob == pytest.approx(0.30)
    assert getattr(
        refresh_pos,
        "_replacement_current_evidence_held_bounds",
    ) == pytest.approx((0.22, 0.41))
    # The belief is branded as a same-authority read-through, never a legacy substitution.
    assert any(
        "readthrough" in v or "read_through" in v
        for v in refresh_pos.applied_validations
    )
    assert not any(v == "legacy_belief_substitution_suppressed" for v in refresh_pos.applied_validations)


def test_readthrough_insufficient_inputs_failclose_with_durable_belief_debt(monkeypatch):
    """Stale cached belief + read-through NOT eligible → fail-close AND a durable,
    retryable belief_debt marker (family/reason/first_failed_at/attempts).

    Antibody: removing the belief_debt record makes this assertion fail — a silent
    permanent freeze (the chronic Karachi case) would be undetectable.
    """
    import src.engine.monitor_refresh as mr
    import src.engine.position_belief as pb

    monkeypatch.setattr(pb, "load_replacement_belief", lambda **kw: _stale_belief())
    monkeypatch.setattr(mr, "_refresh_ens_member_counting", lambda **kw: (0.5, []))
    # Read-through cannot honestly recompute (no current single_runs / no on-disk anchor).
    monkeypatch.setattr(mr, "_attempt_held_belief_readthrough", lambda *a, **k: None)
    reseed_called: list[tuple] = []
    monkeypatch.setattr(
        mr, "_enqueue_single_family_belief_reseed_failsoft",
        lambda **kw: reseed_called.append((kw.get("city"), kw.get("target_date"), kw.get("metric"))) or None,
    )

    pos = _pos()
    prob, refresh_pos, is_fresh = mr.monitor_probability_refresh(
        pos, conn=None, city=object(), target_d=None,
    )

    assert is_fresh is not True
    # Still fail-closed under the belief-authority guard.
    assert any(v == "BELIEF_AUTHORITY_FAULT" for v in pos.applied_validations)
    # Durable, retryable belief-debt record exists and carries the family + reason.
    debt = [v for v in pos.applied_validations if v.startswith("belief_debt")]
    assert debt, f"no belief_debt marker recorded: {pos.applied_validations}"
    marker = debt[0]
    assert "Karachi" in marker
    assert "2026-06-12" in marker
    assert "high" in marker
    # The existing reseed repair lane still fires (NOT a silent freeze).
    assert reseed_called == [("Karachi", "2026-06-12", "high")]


def test_bounded_monitor_defers_sync_readthrough_to_independent_producer(monkeypatch):
    """One stale family cannot retain the portfolio monitor in Python fusion."""
    import src.engine.monitor_refresh as mr
    import src.engine.position_belief as pb

    belief_deadlines = []
    monkeypatch.setattr(
        pb,
        "load_replacement_belief",
        lambda **kw: belief_deadlines.append(kw.get("deadline_monotonic"))
        or _stale_belief(),
    )
    monkeypatch.setattr(mr.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        mr,
        "_attempt_held_belief_readthrough",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("bounded portfolio monitor must not run synchronous fusion")
        ),
    )
    reseed_called = []
    monkeypatch.setattr(
        mr,
        "_enqueue_single_family_belief_reseed_failsoft",
        lambda **kw: reseed_called.append(kw) or None,
    )

    pos = _pos()
    prob, refresh_pos, is_fresh = mr.monitor_probability_refresh(
        pos,
        conn=None,
        city=object(),
        target_d=None,
        deadline_monotonic=123.0,
    )

    assert prob == pytest.approx(pos.p_posterior)
    assert refresh_pos is pos
    assert is_fresh is False
    assert belief_deadlines == [pytest.approx(105.0)]
    assert reseed_called == [
        {"city": "Karachi", "target_date": "2026-06-12", "metric": "high"}
    ]
    assert "replacement_belief_readthrough_deferred_to_independent_producer" in (
        pos.applied_validations
    )
    assert any(
        "bounded_monitor_reseed_required" in validation
        for validation in pos.applied_validations
    )


def test_readthrough_does_not_itself_decide_an_exit(monkeypatch):
    """NO FALSE EXIT: a fresh recompute only supplies belief; the monitor returns
    a probability + is_fresh, never an exit verdict. The CI separation conservatism
    lives entirely downstream and is untouched here."""
    import src.engine.monitor_refresh as mr
    import src.engine.position_belief as pb

    monkeypatch.setattr(pb, "load_replacement_belief", lambda **kw: _stale_belief())
    monkeypatch.setattr(mr, "_refresh_ens_member_counting", lambda **kw: (0.5, []))
    # A fresh belief that has NOT reversed (still favors the held NO side).
    monkeypatch.setattr(
        mr,
        "_attempt_held_belief_readthrough",
        lambda *a, **k: (0.80, 0.70, 0.88),
    )

    pos = _pos()
    result = mr.monitor_probability_refresh(pos, conn=None, city=object(), target_d=None)

    # The contract is exactly (prob, Position, is_fresh) — a belief, not an exit.
    assert isinstance(result, tuple) and len(result) == 3
    prob, refresh_pos, is_fresh = result
    assert is_fresh is True
    assert prob == pytest.approx(0.80)
    from src.state.portfolio import Position
    assert isinstance(refresh_pos, Position)


def test_readthrough_restamps_expired_seed_ttl_to_decision_now(
    monkeypatch,
    tmp_path,
):
    """An expired on-disk seed must not poison the live read-through request.

    The source-cycle identity remains from the seed, but computed_at/expires_at
    are monitor-decision-time fields in this read-only path. Regression target:
    live monitor logs with ``expires_at must be after computed_at``.
    """
    import tests.test_replacement_forecast_materializer as base
    import src.data.replacement_forecast_materialization_request_builder as rb
    import src.data.replacement_forecast_materializer as mat
    import src.engine.monitor_refresh as mr
    import src.state.db as db

    monitor_now = datetime(2026, 6, 25, 14, 58, tzinfo=timezone.utc)
    expired_seed_payload = {
        "city": "Karachi",
        "target_date": "2026-06-12",
        "temperature_metric": "high",
        "computed_at": "2026-06-12T00:00:00+00:00",
        "expires_at": "2026-06-12T03:00:00+00:00",
    }
    monkeypatch.setattr(
        mr,
        "_freshest_family_seed_on_disk",
        lambda **kw: (tmp_path / "Karachi.2026-06-12.high.seed.json", expired_seed_payload),
    )
    monkeypatch.setattr(mr, "_seed_payload_covers_target_local_day", lambda **kw: True)
    monkeypatch.setattr(mr, "_held_side_probability_from_yes_bin_probability", lambda q, direction: 1.0 - q)
    monkeypatch.setattr(mr, "_match_bin", lambda q, label: (BIN, q[BIN]), raising=False)
    monkeypatch.setattr(
        "src.engine.position_belief.monitor_belief_max_age_hours",
        lambda: 3.0,
    )

    captured: dict[str, object] = {}

    def fake_build(payload, *, base_dir):
        captured["payload"] = dict(payload)
        computed_at = datetime.fromisoformat(str(payload["computed_at"]))
        expires_at = datetime.fromisoformat(str(payload["expires_at"]))
        assert computed_at == monitor_now
        assert expires_at == monitor_now + timedelta(hours=3)
        return SimpleNamespace(ok=True, request=dict(payload))

    def fake_dataclass(request_json, *, base_dir):
        return base._request(
            source_cycle_time=datetime(2026, 6, 25, 12, tzinfo=timezone.utc),
            computed_at=datetime.fromisoformat(str(request_json["computed_at"])),
            expires_at=datetime.fromisoformat(str(request_json["expires_at"])),
        )

    def fake_compute(conn, request):
        captured["request"] = request
        assert request.computed_at == monitor_now
        assert request.expires_at == monitor_now + timedelta(hours=3)
        return SimpleNamespace(
            live_eligible=True,
            q={BIN: 0.25},
            q_lcb_map={BIN: 0.18},
            q_ucb_map={BIN: 0.33},
            decorrelated_providers_served=2,
            decorrelated_providers_expected=3,
        )

    monkeypatch.setattr(rb, "build_replacement_forecast_materialization_request", fake_build)
    monkeypatch.setattr(rb, "build_materialize_request_dataclass", fake_dataclass)
    monkeypatch.setattr(mat, "compute_replacement_posterior_readonly", fake_compute)
    monkeypatch.setattr(db, "get_forecasts_connection_read_only", lambda: sqlite3.connect(":memory:"))

    held_prob, held_lcb, held_ucb = mr._attempt_held_belief_readthrough(
        _pos(),
        city=object(),
        target_d=None,
        metric="high",
        decision_now=monitor_now,
    )

    assert held_prob == pytest.approx(0.75)
    assert held_lcb == pytest.approx(0.67)
    assert held_ucb == pytest.approx(0.82)
    assert captured["payload"]["computed_at"] == monitor_now.isoformat()


def test_readthrough_sqlite_work_is_interrupted_at_monitor_deadline(
    monkeypatch,
    tmp_path,
):
    """One stale family cannot overrun the whole-book monitor cycle budget."""
    import tests.test_replacement_forecast_materializer as base
    import src.data.replacement_forecast_materialization_request_builder as rb
    import src.data.replacement_forecast_materializer as mat
    import src.engine.monitor_refresh as mr
    import src.state.db as db

    monitor_now = datetime(2026, 6, 25, 14, 58, tzinfo=timezone.utc)
    seed_payload = {
        "city": "Karachi",
        "target_date": "2026-06-12",
        "temperature_metric": "high",
        "computed_at": monitor_now.isoformat(),
        "expires_at": (monitor_now + timedelta(hours=3)).isoformat(),
    }
    monkeypatch.setattr(
        mr,
        "_freshest_family_seed_on_disk",
        lambda **kw: (
            tmp_path / "Karachi.2026-06-12.high.seed.json",
            seed_payload,
        ),
    )
    monkeypatch.setattr(mr, "_seed_payload_covers_target_local_day", lambda **kw: True)
    monkeypatch.setattr(
        rb,
        "build_replacement_forecast_materialization_request",
        lambda payload, *, base_dir: SimpleNamespace(ok=True, request=dict(payload)),
    )
    monkeypatch.setattr(
        rb,
        "build_materialize_request_dataclass",
        lambda request_json, *, base_dir: base._request(
            source_cycle_time=datetime(2026, 6, 25, 12, tzinfo=timezone.utc),
            computed_at=monitor_now,
            expires_at=monitor_now + timedelta(hours=3),
        ),
    )

    def deliberately_unbounded_sql(conn, request):
        del request
        conn.execute(
            """
            WITH RECURSIVE spin(value) AS (
                SELECT 1
                UNION ALL
                SELECT value + 1 FROM spin
            )
            SELECT SUM(value) FROM spin
            """
        ).fetchone()
        raise AssertionError("deadline failed to interrupt SQLite")

    monkeypatch.setattr(mat, "compute_replacement_posterior_readonly", deliberately_unbounded_sql)
    monkeypatch.setattr(
        db,
        "get_forecasts_connection_read_only",
        lambda: sqlite3.connect(":memory:"),
    )

    started = time.monotonic()
    result = mr._attempt_held_belief_readthrough(
        _pos(),
        city=object(),
        target_d=None,
        metric="high",
        decision_now=monitor_now,
        deadline_monotonic=started + 0.02,
    )

    assert result is None
    assert time.monotonic() - started < 1.0


def test_day0_visibility_retry_recovers_raw_hwm_after_250ms(monkeypatch):
    """A matching posterior published within the short budget restores fresh q."""
    import src.engine.monitor_refresh as mr

    clock = [10.0]
    attempts = 0
    build_deadlines: list[float] = []
    snapshot = SimpleNamespace()

    def build(*_args, deadline_monotonic, **_kwargs):
        nonlocal attempts
        attempts += 1
        build_deadlines.append(deadline_monotonic)
        if clock[0] < 10.25:
            raise ValueError("GLOBAL_CURRENT_BUNDLE_BLOCKED:REPLACEMENT_RAW_INPUT_HWM")
        return snapshot

    def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(mr, "_canonical_condition_id", lambda _position: "condition-1")
    monkeypatch.setattr(mr, "_build_current_global_day0_family_snapshot", build)
    monkeypatch.setattr(
        mr,
        "_materialize_current_global_day0_probability",
        lambda position, built: (0.30, position, built is snapshot),
    )
    monkeypatch.setattr(mr.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(mr.time, "sleep", sleep)

    result = mr._refresh_current_global_day0_probability(
        _pos(),
        trade_conn=object(),
        deadline_monotonic=11.0,
    )

    held_prob, refresh_pos, is_fresh = result
    assert held_prob == pytest.approx(0.30)
    assert refresh_pos is not None
    assert is_fresh is True
    assert attempts == 4
    assert build_deadlines == pytest.approx([10.35, 10.35, 10.35, 10.35])
    assert clock[0] == pytest.approx(10.3)


def test_day0_visibility_retry_fails_closed_when_event_never_publishes(monkeypatch):
    """Canonical observation without its Day0 event never reuses stale or market q."""
    import src.engine.monitor_refresh as mr

    clock = [20.0]
    attempts = 0

    def build(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise mr.ObservationUnavailableError(
            mr._DAY0_CANONICAL_OBSERVATION_EVENT_NOT_VISIBLE
        )

    def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(mr, "_canonical_condition_id", lambda _position: "condition-1")
    monkeypatch.setattr(mr, "_build_current_global_day0_family_snapshot", build)
    monkeypatch.setattr(mr.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(mr.time, "sleep", sleep)

    with pytest.raises(
        mr.ObservationUnavailableError,
        match=mr._DAY0_CANONICAL_OBSERVATION_EVENT_NOT_VISIBLE,
    ):
        mr._refresh_current_global_day0_probability(
            _pos(),
            trade_conn=object(),
            deadline_monotonic=95.0,
        )

    assert attempts == 4
    assert clock[0] == pytest.approx(
        20.0 + mr._DAY0_MATERIALIZATION_VISIBILITY_RETRY_BUDGET_SECONDS
    )


def test_day0_snapshot_build_sql_guard_interrupts_at_effective_deadline(monkeypatch):
    """The build's SQLite boundary interrupts at its effective monitor deadline."""
    import src.engine.monitor_refresh as mr

    conn = sqlite3.connect(":memory:")
    clock = [0.0]

    def monotonic():
        current = clock[0]
        clock[0] += 0.001
        return current
    monkeypatch.setattr(mr.time, "monotonic", monotonic)
    effective_deadline = mr._day0_materialization_visibility_retry_deadline(75.0)

    with pytest.raises(mr._Day0SnapshotReadDeadlineExceeded):
        with mr._day0_snapshot_sqlite_read_deadline(conn, effective_deadline):
            conn.execute(
                """
                WITH RECURSIVE spin(value) AS (
                    SELECT 1
                    UNION ALL
                    SELECT value + 1 FROM spin
                )
                SELECT SUM(value) FROM spin
                """
            ).fetchone()

    conn.close()
    assert effective_deadline == pytest.approx(0.35)
    assert clock[0] < 0.5


def test_day0_snapshot_tokens_use_closed_independent_trade_reader(monkeypatch):
    """The snapshot bind read must never install a handler on shared trade_conn."""
    import src.engine.monitor_refresh as mr
    import src.state.db as db

    class SharedTradeConnection(sqlite3.Connection):
        def set_progress_handler(self, *_args, **_kwargs):
            raise AssertionError("shared trade connection must not receive a handler")

    class SnapshotTradeConnection(sqlite3.Connection):
        closed = False

        def close(self):
            self.closed = True
            super().close()

    shared = sqlite3.connect(":memory:", factory=SharedTradeConnection)
    snapshot = sqlite3.connect(":memory:", factory=SnapshotTradeConnection)
    snapshot.row_factory = sqlite3.Row
    snapshot.execute(
        """
        CREATE TABLE executable_market_snapshot_latest (
            condition_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            captured_at TEXT,
            snapshot_id TEXT
        )
        """
    )
    snapshot.execute(
        """
        INSERT INTO executable_market_snapshot_latest VALUES
        ('condition-1', 'yes-1', 'no-1', '2026-07-30T00:00:00+00:00', 'snapshot-1')
        """
    )
    monkeypatch.setattr(db, "get_trade_connection_read_only", lambda: snapshot)

    rows = mr._read_current_global_day0_snapshot_tokens(
        trade_conn=shared,
        condition_ids=("condition-1",),
        deadline_monotonic=time.monotonic() + 1.0,
    )

    shared.close()
    assert rows[0]["yes_token_id"] == "yes-1"
    assert snapshot.closed is True


def test_freshest_seed_skips_payload_without_target_local_day(tmp_path, monkeypatch):
    """Newest seed can be a poison file; read-through must pick the newest usable one."""
    import src.data.replacement_forecast_production as prod
    import src.engine.monitor_refresh as mr

    root = tmp_path / "replacement_forecast_live"
    seed_dir = root / "seeds"
    processed_dir = root / "seeds_processed"
    queue_processed_dir = root / "processed"
    raw_dir = root / "raw_manifests"
    for path in (seed_dir, processed_dir, queue_processed_dir, raw_dir):
        path.mkdir(parents=True)

    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {
            "seed_dir": str(seed_dir),
            "seed_processed_dir": str(processed_dir),
            "processed_dir": str(queue_processed_dir),
        },
    )

    bad_payload = raw_dir / "openmeteo_Hong_Kong_2026-06-25_low.json"
    bad_payload.write_text(
        json.dumps({"hourly": {"time": ["2026-06-25T01:00"], "temperature_2m": [28.0]}}),
        encoding="utf-8",
    )
    good_payload = raw_dir / "openmeteo_Hong_Kong_2026-06-26_low.json"
    good_payload.write_text(
        json.dumps({"hourly": {"time": ["2026-06-26T01:00"], "temperature_2m": [27.0]}}),
        encoding="utf-8",
    )

    def write_seed(stamp: str, payload_path) -> None:
        seed = {
            "city": "Hong Kong",
            "target_date": "2026-06-26",
            "temperature_metric": "low",
            "city_timezone": "Asia/Hong_Kong",
            "openmeteo_payload_json": f"../raw_manifests/{payload_path.name}",
        }
        (seed_dir / f"Hong_Kong.2026-06-26.low.{stamp}.json").write_text(
            json.dumps(seed),
            encoding="utf-8",
        )

    write_seed("20260624T222604Z", bad_payload)
    write_seed("20260624T222503Z", good_payload)

    selected = mr._freshest_family_seed_on_disk(
        city="Hong Kong",
        target_date="2026-06-26",
        metric="low",
    )

    assert selected is not None
    selected_path, selected_payload = selected
    assert selected_path.name.endswith("20260624T222503Z.json")
    assert selected_payload["openmeteo_payload_json"].endswith("2026-06-26_low.json")


def test_freshest_seed_reads_latest_cache_without_enumerating_archives(
    tmp_path, monkeypatch
):
    import os
    from pathlib import Path

    import src.data.replacement_forecast_production as prod
    import src.engine.monitor_refresh as mr

    seed_dir = tmp_path / "seeds"
    seed_processed_dir = tmp_path / "seeds_processed"
    processed_dir = tmp_path / "processed"
    for path in (seed_dir, seed_processed_dir, processed_dir):
        path.mkdir()
    latest_dir = tmp_path / "seeds_latest"
    latest_dir.mkdir()

    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {
            "seed_dir": str(seed_dir),
            "seed_processed_dir": str(seed_processed_dir),
            "processed_dir": str(processed_dir),
        },
    )
    payload_path = tmp_path / "current.json"
    payload_path.write_text(
        json.dumps(
            {
                "hourly": {
                    "time": ["2026-07-22T01:00"],
                    "temperature_2m": [29.0],
                }
            }
        ),
        encoding="utf-8",
    )
    latest_seed = latest_dir / "Seoul.2026-07-22.high.json"
    latest_seed.write_text(
        json.dumps(
            {
                "city": "Seoul",
                "target_date": "2026-07-22",
                "temperature_metric": "high",
                "city_timezone": "Asia/Seoul",
                "openmeteo_payload_json": str(payload_path),
            }
        ),
        encoding="utf-8",
    )
    real_scandir = os.scandir

    def guarded_scandir(path):
        raise AssertionError(f"unexpected directory enumeration: {Path(path)}")

    monkeypatch.setattr(os, "scandir", guarded_scandir)

    selected = mr._freshest_family_seed_on_disk(
        city="Seoul",
        target_date="2026-07-22",
        metric="high",
    )

    assert selected is not None
    assert selected[0] == latest_seed


def test_freshest_seed_does_not_enumerate_processed_archives(tmp_path, monkeypatch):
    import os
    from pathlib import Path

    import src.data.replacement_forecast_production as prod
    import src.engine.monitor_refresh as mr

    seed_dir = tmp_path / "seeds"
    seed_processed_dir = tmp_path / "seeds_processed"
    processed_dir = tmp_path / "processed"
    for path in (seed_dir, seed_processed_dir, processed_dir):
        path.mkdir()

    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {
            "seed_dir": str(seed_dir),
            "seed_processed_dir": str(seed_processed_dir),
            "processed_dir": str(processed_dir),
        },
    )
    real_scandir = os.scandir

    def guarded_scandir(path):
        assert Path(path) == seed_dir
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", guarded_scandir)

    assert mr._freshest_family_seed_on_disk(
        city="Seoul",
        target_date="2026-07-22",
        metric="high",
    ) is None


def test_freshest_seed_caps_pending_queue_enumeration(tmp_path, monkeypatch):
    import os

    import src.data.replacement_forecast_production as prod
    import src.engine.monitor_refresh as mr

    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir()
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"seed_dir": str(seed_dir)},
    )
    monkeypatch.setattr(mr, "_HELD_BELIEF_PENDING_SEED_SCAN_LIMIT", 3)
    seen = 0

    class Entries:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return self

        def __next__(self):
            nonlocal seen
            seen += 1
            return SimpleNamespace(name=f"unrelated-{seen}.json")

    monkeypatch.setattr(os, "scandir", lambda _path: Entries())

    assert mr._freshest_family_seed_on_disk(
        city="Seoul",
        target_date="2026-07-22",
        metric="high",
    ) is None
    assert seen == 3
