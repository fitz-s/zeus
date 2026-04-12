from types import SimpleNamespace
import sys

from src.engine.replay import run_replay
from src.state.db import get_connection, init_schema
import scripts.run_replay as cli_module
from scripts.run_replay import _format_total_pnl


def test_run_replay_allows_snapshot_only_reference_opt_in(tmp_path, monkeypatch):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO settlements (city, target_date, winning_bin, settlement_value)
        VALUES ('Paris', '2026-04-03', '12°C', 12.0)
        """
    )
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version)
        VALUES (31, 'Paris', '2026-04-03', '2026-04-02T00:00:00Z', '2026-04-03T00:00:00Z',
                '2026-04-02T08:00:00Z', '2026-04-02T08:05:00Z', 24.0, '[12.0]', '[1.0]', 2.0, 0, 'ecmwf', 'v1')
        """
    )
    conn.execute(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days, season, cluster, forecast_available_at, settlement_value)
        VALUES ('Paris', '2026-04-03', '12°C', 1.0, 1, 1.0, 'MAM', 'Europe-Continental', '2026-04-02T08:00:00Z', 12.0)
        """
    )
    conn.commit()
    conn.close()

    import src.engine.replay as replay_module
    import src.state.db as db_module

    original_get_connection = replay_module.get_trade_connection_with_world
    try:
        replay_module.get_trade_connection_with_world = lambda: db_module.get_connection(db_path)
        strict = run_replay("2026-04-03", "2026-04-03", mode="audit")
        relaxed = run_replay(
            "2026-04-03",
            "2026-04-03",
            mode="audit",
            allow_snapshot_only_reference=True,
        )
    finally:
        replay_module.get_trade_connection_with_world = original_get_connection

    assert strict.n_replayed == 0
    assert relaxed.n_replayed >= 1


def test_run_replay_snapshot_only_can_fallback_to_forecast_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "forecast-fallback.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO settlements (city, target_date, settlement_value)
        VALUES ('Ankara', '2026-04-03', 20.0)
        """
    )
    conn.execute(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days, season, cluster, forecast_available_at, settlement_value)
        VALUES
        ('Ankara', '2026-04-03', '20°C', 0.5, 1, 1.0, 'MAM', 'Europe-Continental', '2026-04-02T08:00:00Z', 20.0),
        ('Ankara', '2026-04-03', '21°C', 0.5, 0, 1.0, 'MAM', 'Europe-Continental', '2026-04-02T08:00:00Z', 20.0)
        """
    )
    conn.execute(
        """
        INSERT INTO forecasts
        (city, target_date, source, forecast_basis_date, lead_days, forecast_high, temp_unit)
        VALUES
        ('Ankara', '2026-04-03', 'ecmwf_previous_runs', '2026-04-02', 1, 20.0, 'C'),
        ('Ankara', '2026-04-03', 'gfs_previous_runs', '2026-04-02', 1, 21.0, 'C')
        """
    )
    conn.commit()
    conn.close()

    import src.engine.replay as replay_module
    import src.state.db as db_module

    original_get_connection = replay_module.get_trade_connection_with_world
    try:
        replay_module.get_trade_connection_with_world = lambda: db_module.get_connection(db_path)
        strict = run_replay("2026-04-03", "2026-04-03", mode="audit")
        relaxed = run_replay(
            "2026-04-03",
            "2026-04-03",
            mode="audit",
            allow_snapshot_only_reference=True,
        )
    finally:
        replay_module.get_trade_connection_with_world = original_get_connection

    assert strict.n_replayed == 0
    assert relaxed.n_replayed == 1
    assert relaxed.outcomes[0].snapshot_id.startswith("forecast_rows:Ankara")


def test_replay_without_market_price_linkage_cannot_generate_pnl(tmp_path, monkeypatch):
    db_path = tmp_path / "unpriced-replay.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO settlements (city, target_date, winning_bin, settlement_value)
        VALUES ('Paris', '2026-04-04', '12°C', 12.0)
        """
    )
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version)
        VALUES (41, 'Paris', '2026-04-04', '2026-04-03T00:00:00Z', '2026-04-04T00:00:00Z',
                '2026-04-03T08:00:00Z', '2026-04-03T08:05:00Z', 24.0, '[12.0, 13.0]',
                '[0.9, 0.1]', 1.0, 0, 'ecmwf', 'v1')
        """
    )
    conn.execute(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days, season, cluster,
         forecast_available_at, settlement_value)
        VALUES
        ('Paris', '2026-04-04', '12°C', 0.9, 1, 1.0, 'MAM', 'Europe-Continental',
         '2026-04-03T08:00:00Z', 12.0),
        ('Paris', '2026-04-04', '13°C', 0.1, 0, 1.0, 'MAM', 'Europe-Continental',
         '2026-04-03T08:00:00Z', 12.0)
        """
    )
    conn.commit()
    conn.close()

    import src.engine.replay as replay_module
    import src.state.db as db_module
    import src.strategy.fdr_filter as fdr_module
    import src.strategy.kelly as kelly_module
    import src.strategy.market_analysis as market_analysis_module

    class FakeMarketAnalysis:
        def __init__(self, *args, **kwargs):
            self.bins = kwargs["bins"]

        def find_edges(self, n_bootstrap):
            return [
                SimpleNamespace(
                    bin=self.bins[0],
                    direction="buy_yes",
                    edge=0.40,
                    p_posterior=0.90,
                    entry_price=0.50,
                    ci_lower=0.20,
                    ci_upper=0.60,
                )
            ]

    monkeypatch.setattr(replay_module, "get_trade_connection_with_world", lambda: db_module.get_connection(db_path))
    monkeypatch.setattr(market_analysis_module, "MarketAnalysis", FakeMarketAnalysis)
    monkeypatch.setattr(fdr_module, "fdr_filter", lambda edges: edges)
    monkeypatch.setattr(kelly_module, "dynamic_kelly_mult", lambda **kwargs: 1.0)
    monkeypatch.setattr(kelly_module, "kelly_size", lambda *args, **kwargs: 25.0)

    summary = run_replay(
        "2026-04-04",
        "2026-04-04",
        mode="audit",
        allow_snapshot_only_reference=True,
    )

    assert summary.n_replayed == 1
    assert summary.n_would_trade == 0
    assert summary.replay_total_pnl == 0.0
    assert summary.replay_win_rate == 0.0
    assert summary.limitations["market_price_unavailable_subjects"] == 1
    assert summary.limitations["pnl_requires_market_price_linkage"] is True

    decision = summary.outcomes[0].replay_decisions[0]
    assert decision.should_trade is False
    assert decision.rejection_stage == "MARKET_PRICE_UNAVAILABLE"
    assert decision.size_usd == 25.0
    assert "market_price_unavailable" in decision.applied_validations

    conn = get_connection(db_path)
    stored = conn.execute(
        """
        SELECT replay_should_trade, replay_rejection_stage, replay_pnl
        FROM replay_results
        WHERE replay_run_id = ?
        """,
        (summary.run_id,),
    ).fetchone()
    conn.close()

    assert stored["replay_should_trade"] == 0
    assert stored["replay_rejection_stage"] == "MARKET_PRICE_UNAVAILABLE"
    assert stored["replay_pnl"] == 0.0


def test_replay_alpha_uses_trade_decision_market_hours_open(tmp_path, monkeypatch):
    db_path = tmp_path / "market-hours-open.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO settlements (city, target_date, winning_bin, settlement_value)
        VALUES ('Paris', '2026-04-05', '12°C', 12.0)
        """
    )
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version)
        VALUES (51, 'Paris', '2026-04-05', '2026-04-04T00:00:00Z', '2026-04-05T00:00:00Z',
                '2026-04-04T08:00:00Z', '2026-04-04T08:05:00Z', 24.0, '[12.0, 13.0]',
                '[0.9, 0.1]', 1.0, 0, 'ecmwf', 'v1')
        """
    )
    conn.execute(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days, season, cluster,
         forecast_available_at, settlement_value)
        VALUES
        ('Paris', '2026-04-05', '12°C', 0.9, 1, 1.0, 'MAM', 'Europe-Continental',
         '2026-04-04T08:00:00Z', 12.0),
        ('Paris', '2026-04-05', '13°C', 0.1, 0, 1.0, 'MAM', 'Europe-Continental',
         '2026-04-04T08:00:00Z', 12.0)
        """
    )
    conn.execute(
        """
        INSERT INTO trade_decisions
        (market_id, bin_label, direction, size_usd, price, timestamp, forecast_snapshot_id,
         p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction, status,
         edge_source, runtime_trade_id, market_hours_open, env)
        VALUES ('mkt', '12°C', 'buy_yes', 5.0, 0.4, '2026-04-04T08:10:00+00:00', 51,
                0.9, 0.9, 0.5, 0.2, 0.6, 0.0, 'entered',
                'center_buy', 'pos-1', 2.5, 'live')
        """
    )
    conn.commit()
    conn.close()

    import src.engine.replay as replay_module
    import src.state.db as db_module
    import src.strategy.market_fusion as market_fusion_module

    captured = {}

    def _compute_alpha(**kwargs):
        captured["hours_since_open"] = kwargs["hours_since_open"]
        return SimpleNamespace(value=0.5)

    monkeypatch.setattr(replay_module, "get_trade_connection_with_world", lambda: db_module.get_connection(db_path))
    monkeypatch.setattr(market_fusion_module, "compute_alpha", _compute_alpha)

    run_replay(
        "2026-04-05",
        "2026-04-05",
        mode="audit",
    )

    assert captured["hours_since_open"] == 2.5


def test_replay_alpha_uses_no_trade_market_hours_open(tmp_path, monkeypatch):
    db_path = tmp_path / "no-trade-market-hours-open.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO settlements (city, target_date, winning_bin, settlement_value)
        VALUES ('Paris', '2026-04-06', '12°C', 12.0)
        """
    )
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version)
        VALUES (61, 'Paris', '2026-04-06', '2026-04-05T00:00:00Z', '2026-04-06T00:00:00Z',
                '2026-04-05T08:00:00Z', '2026-04-05T08:05:00Z', 24.0, '[12.0, 13.0]',
                '[0.9, 0.1]', 1.0, 0, 'ecmwf', 'v1')
        """
    )
    conn.execute(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days, season, cluster,
         forecast_available_at, settlement_value)
        VALUES
        ('Paris', '2026-04-06', '12°C', 0.9, 1, 1.0, 'MAM', 'Europe-Continental',
         '2026-04-05T08:00:00Z', 12.0),
        ('Paris', '2026-04-06', '13°C', 0.1, 0, 1.0, 'MAM', 'Europe-Continental',
         '2026-04-05T08:00:00Z', 12.0)
        """
    )
    conn.execute(
        """
        INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "opening_hunt",
            "2026-04-05T08:10:00+00:00",
            "2026-04-05T08:11:00+00:00",
            """{
              "trade_cases": [],
              "no_trade_cases": [{
                "decision_id": "nt-1",
                "city": "Paris",
                "target_date": "2026-04-06",
                "range_label": "12°C",
                "direction": "buy_yes",
                "rejection_stage": "FDR_FILTERED",
                "decision_snapshot_id": "61",
                "bin_labels": ["12°C", "13°C"],
                "p_raw_vector": [0.9, 0.1],
                "p_cal_vector": [0.9, 0.1],
                "p_market_vector": [],
                "alpha": 0.0,
                "market_hours_open": 3.5,
                "agreement": "AGREE",
                "timestamp": "2026-04-05T08:10:00+00:00"
              }]
            }""",
            "2026-04-05T08:11:00+00:00",
            "live",
        ),
    )
    conn.commit()
    conn.close()

    import src.engine.replay as replay_module
    import src.state.db as db_module
    import src.strategy.market_fusion as market_fusion_module

    captured = {}

    def _compute_alpha(**kwargs):
        captured["hours_since_open"] = kwargs["hours_since_open"]
        return SimpleNamespace(value=0.5)

    monkeypatch.setattr(replay_module, "get_trade_connection_with_world", lambda: db_module.get_connection(db_path))
    monkeypatch.setattr(market_fusion_module, "compute_alpha", _compute_alpha)

    run_replay(
        "2026-04-06",
        "2026-04-06",
        mode="audit",
    )

    assert captured["hours_since_open"] == 3.5


def test_replay_alpha_legacy_no_trade_without_market_hours_uses_fallback(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy-no-trade-market-hours.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO settlements (city, target_date, winning_bin, settlement_value)
        VALUES ('Paris', '2026-04-07', '12°C', 12.0)
        """
    )
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version)
        VALUES (71, 'Paris', '2026-04-07', '2026-04-06T00:00:00Z', '2026-04-07T00:00:00Z',
                '2026-04-06T08:00:00Z', '2026-04-06T08:05:00Z', 24.0, '[12.0, 13.0]',
                '[0.9, 0.1]', 1.0, 0, 'ecmwf', 'v1')
        """
    )
    conn.execute(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days, season, cluster,
         forecast_available_at, settlement_value)
        VALUES
        ('Paris', '2026-04-07', '12°C', 0.9, 1, 1.0, 'MAM', 'Europe-Continental',
         '2026-04-06T08:00:00Z', 12.0),
        ('Paris', '2026-04-07', '13°C', 0.1, 0, 1.0, 'MAM', 'Europe-Continental',
         '2026-04-06T08:00:00Z', 12.0)
        """
    )
    conn.execute(
        """
        INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "opening_hunt",
            "2026-04-06T08:10:00+00:00",
            "2026-04-06T08:11:00+00:00",
            """{
              "trade_cases": [],
              "no_trade_cases": [{
                "decision_id": "nt-legacy",
                "city": "Paris",
                "target_date": "2026-04-07",
                "range_label": "12°C",
                "direction": "buy_yes",
                "rejection_stage": "FDR_FILTERED",
                "decision_snapshot_id": "71",
                "bin_labels": ["12°C", "13°C"],
                "p_raw_vector": [0.9, 0.1],
                "p_cal_vector": [0.9, 0.1],
                "p_market_vector": [],
                "alpha": 0.0,
                "agreement": "AGREE",
                "timestamp": "2026-04-06T08:10:00+00:00"
              }]
            }""",
            "2026-04-06T08:11:00+00:00",
            "live",
        ),
    )
    conn.commit()
    conn.close()

    import src.engine.replay as replay_module
    import src.state.db as db_module
    import src.strategy.market_fusion as market_fusion_module

    captured = {}

    def _compute_alpha(**kwargs):
        captured["hours_since_open"] = kwargs["hours_since_open"]
        return SimpleNamespace(value=0.5)

    monkeypatch.setattr(replay_module, "get_trade_connection_with_world", lambda: db_module.get_connection(db_path))
    monkeypatch.setattr(market_fusion_module, "compute_alpha", _compute_alpha)

    run_replay(
        "2026-04-07",
        "2026-04-07",
        mode="audit",
    )

    assert captured["hours_since_open"] == 48.0


def test_cli_formats_unpriced_replay_pnl_as_unavailable():
    summary = SimpleNamespace(
        replay_total_pnl=0.0,
        n_replayed=298,
        limitations={
            "pnl_available": False,
            "pnl_unavailable_reason": "market_price_unavailable",
            "market_price_unavailable_subjects": 298,
        },
    )

    assert _format_total_pnl(summary) == "N/A (market price unavailable for 298/298 replayed subjects)"


def test_cli_trade_history_audit_routes_to_backtest_lane(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "cli.db"
    called = {}

    def _run_replay(*, start_date, end_date, mode, overrides=None, allow_snapshot_only_reference=False):
        called.update(
            start_date=start_date,
            end_date=end_date,
            mode=mode,
            overrides=overrides,
            allow_snapshot_only_reference=allow_snapshot_only_reference,
        )
        return SimpleNamespace(
            run_id="run-1",
            n_settlements=1,
            n_replayed=0,
            coverage_pct=0.0,
            n_would_trade=0,
            replay_win_rate=0.0,
            replay_total_pnl=0.0,
            limitations={
                "pnl_available": False,
                "pnl_unavailable_reason": "trade_history_audit_reports_actual_trade_pnl_rows_not_simulated_strategy_pnl",
            },
            per_city={},
            outcomes=[],
        )

    import src.engine.replay as replay_module

    monkeypatch.setattr(cli_module, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(replay_module, "run_replay", _run_replay)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_replay.py",
            "--mode",
            "trade_history_audit",
            "--start",
            "2026-04-03",
            "--end",
            "2026-04-03",
        ],
    )

    cli_module.main()
    output = capsys.readouterr().out

    assert called == {
        "start_date": "2026-04-03",
        "end_date": "2026-04-03",
        "mode": "trade_history_audit",
        "overrides": None,
        "allow_snapshot_only_reference": False,
    }
    assert "TRADE_HISTORY_AUDIT" in output
    assert "Results stored in zeus_backtest.db" in output
