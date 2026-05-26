# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI PR332 deploy-ready review; Day0 must not be advertised
# live while the online observation-context hook is absent.
from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path

import pytest


def test_edli_online_config_defaults_inert_under_legacy_cron():
    settings = json.loads(Path("config/settings.json").read_text())
    edli = settings["edli_v1"]
    assert edli["enabled"] is False
    assert edli["live_execution_mode"] == "legacy_cron"
    assert edli["reactor_mode"] == "disabled"
    assert edli["event_writer_enabled"] is False
    assert edli["forecast_snapshot_trigger_enabled"] is False
    assert edli["forecast_complete_live_enabled"] is True
    assert edli["day0_extreme_trigger_enabled"] is False
    assert edli["day0_authority_catchup_scanner_enabled"] is False
    assert edli["day0_hard_fact_live_enabled"] is False
    assert edli["market_channel_ingestor_enabled"] is False
    assert edli["edli_user_channel_reconcile_enabled"] is False
    assert edli["pre_submit_max_quote_age_ms"] <= 1000
    assert edli["pre_submit_balance_allowance_check_enabled"] is True
    assert edli["market_channel_quote_cache_enabled"] is True
    assert edli["no_trade_regret_enabled"] is True
    assert edli["reports_enabled"] is True
    assert edli["forecast_snapshot_emit_limit"] <= 20
    assert edli["day0_catchup_emit_limit"] <= 20
    assert edli["no_submit_proof_limit"] <= 10
    assert edli["market_channel_refresh_max_actions_per_window"] <= 5
    assert edli["market_channel_refresh_window_seconds"] >= 1
    assert edli["no_submit_visible_depth_fill_lcb"] < 1.0
    assert edli["stale_book_directional_trading_enabled"] is False
    assert edli["real_order_submit_enabled"] is False
    assert edli["taker_fok_fak_live_enabled"] is False
    assert edli["tiny_live_max_notional_usd"] == 5.0
    assert edli["tiny_live_max_orders_per_day"] == 1


def test_pr332_scope_marks_day0_and_market_channel_as_disabled_followups():
    settings = json.loads(Path("config/settings.json").read_text())
    edli = settings["edli_v1"]

    assert edli["day0_extreme_trigger_enabled"] is False
    assert edli["day0_hard_fact_live_enabled"] is False
    assert edli["day0_authority_catchup_scanner_enabled"] is False
    assert edli["market_channel_ingestor_enabled"] is False


def test_pr_scope_document_matches_settings_flags():
    settings = json.loads(Path("config/settings.json").read_text())
    edli = settings["edli_v1"]
    spec = Path("docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md").read_text()

    assert edli["enabled"] is False
    assert edli["forecast_snapshot_trigger_enabled"] is False
    assert edli["day0_hard_fact_live_enabled"] is False
    assert edli["market_channel_ingestor_enabled"] is False
    assert edli["real_order_submit_enabled"] is False
    assert "market_channel_ingestor_enabled=false" in spec
    assert "Day0 disabled" in spec or "Day0 online hard-fact eventing is not enabled" in spec
    assert "real submit disabled" in spec or "real submit disabled" in spec.lower()


def test_edli_online_invariants_do_not_claim_day0_online():
    source = Path("tests/money_path/test_edli_online_invariants.py").read_text()
    forbidden_claim = "DAY0_ONLINE_ENABLED" + " = true"

    assert "day0_hard_fact_live_enabled\"] is True" not in source
    assert forbidden_claim not in source


def test_edli_online_invariants_do_not_claim_market_channel_deployed_when_disabled():
    settings = json.loads(Path("config/settings.json").read_text())
    edli = settings["edli_v1"]

    assert edli["market_channel_ingestor_enabled"] is False
    assert edli["real_order_submit_enabled"] is False


def test_edli_reactor_job_wired_behind_live_execution_mode_gate():
    source = Path("src/main.py").read_text()
    assert "edli_event_reactor" in source
    assert "edli_market_channel_ingestor" in source
    assert "edli_user_channel_reconcile" in source
    assert "_edli_emit_forecast_snapshot_events" in source
    assert "_edli_emit_day0_extreme_events" in source
    assert "day0_authority_catchup_scanner_enabled" in source
    assert "event_bound_no_submit_adapter_from_trade_conn" in source
    assert "event_bound_live_adapter_from_trade_conn" in source
    assert "submit_existing_cycle_for_event" not in source
    assert 'edli_cfg.get("real_order_submit_enabled", False)' in source
    assert "real_order_submit_enabled=real_order_submit_enabled" in source
    assert 'edli_cfg.get("live_canary_enabled", False)' in source
    assert "forecast_snapshot_emit_limit" in source
    assert "no_submit_proof_limit" in source
    assert "reactor.process_pending(decision_time=now, limit=proof_limit)" in source
    assert "user_channel_or_reconcile_only" in source
    edli_start = source.index("def _edli_event_reactor_cycle")
    edli_end = source.index("@_scheduler_job", edli_start + 1)
    edli_source = source[edli_start:edli_end]
    assert "run_cycle" not in edli_source
    assert "_assert_live_execution_mode_contract" in source
    assert "live_execution_mode == \"legacy_cron\"" in source
    assert "live_execution_mode == \"edli_event_driven\"" in source
    for existing_job in ("opening_hunt", "day0_capture", "imminent_open_capture", "market_discovery", "harvester"):
        assert existing_job in source


def test_live_execution_mode_legacy_cron_does_not_register_edli_reactor(monkeypatch):
    scheduler, settings_copy = _run_main_with_fake_scheduler(
        monkeypatch,
        {
            "enabled": False,
            "live_execution_mode": "legacy_cron",
            "reactor_mode": "disabled",
            "event_writer_enabled": False,
            "forecast_snapshot_trigger_enabled": False,
            "day0_extreme_trigger_enabled": False,
            "day0_hard_fact_live_enabled": False,
            "market_channel_ingestor_enabled": False,
            "edli_user_channel_reconcile_enabled": False,
            "real_order_submit_enabled": False,
            "taker_fok_fak_live_enabled": False,
        },
    )

    job_ids = {job.id for job in scheduler.jobs}
    assert scheduler.started is True
    assert scheduler.shutdown_called is True
    assert "edli_event_reactor" not in job_ids
    assert "edli_market_channel_ingestor" not in job_ids
    assert "edli_user_channel_reconcile" not in job_ids
    assert "opening_hunt" in job_ids
    assert any(job_id.startswith("update_reaction_") for job_id in job_ids)
    assert "day0_capture" in job_ids
    assert "imminent_open_capture" in job_ids
    assert "market_discovery" in job_ids
    assert "harvester" in job_ids
    assert settings_copy["edli_v1"]["enabled"] is False
    assert settings_copy["edli_v1"]["live_execution_mode"] == "legacy_cron"


def test_pr332_scoped_daemon_restart_smoke_registers_event_driven_no_legacy_cron(monkeypatch):
    scheduler, settings_copy = _run_main_with_fake_scheduler(
        monkeypatch,
        {
            "enabled": True,
            "live_execution_mode": "edli_event_driven",
            "reactor_mode": "submit_disabled_live_bridge",
            "event_writer_enabled": True,
            "forecast_snapshot_trigger_enabled": True,
            "day0_extreme_trigger_enabled": False,
            "day0_hard_fact_live_enabled": False,
            "market_channel_ingestor_enabled": False,
            "edli_user_channel_reconcile_enabled": False,
            "real_order_submit_enabled": False,
            "taker_fok_fak_live_enabled": False,
        },
    )

    job_ids = {job.id for job in scheduler.jobs}
    assert scheduler.started is True
    assert scheduler.shutdown_called is True
    assert "edli_event_reactor" in job_ids
    assert "edli_market_channel_ingestor" not in job_ids
    assert "edli_user_channel_reconcile" not in job_ids
    assert "opening_hunt" not in job_ids
    assert not any(job_id.startswith("update_reaction_") for job_id in job_ids)
    assert "day0_capture" not in job_ids
    assert "imminent_open_capture" not in job_ids
    assert "market_discovery" not in job_ids
    assert "harvester" not in job_ids
    assert "heartbeat" in job_ids
    assert settings_copy["edli_v1"]["live_execution_mode"] == "edli_event_driven"
    assert settings_copy["edli_v1"]["forecast_snapshot_trigger_enabled"] is True
    assert settings_copy["edli_v1"]["day0_extreme_trigger_enabled"] is False
    assert settings_copy["edli_v1"]["market_channel_ingestor_enabled"] is False
    assert settings_copy["edli_v1"]["edli_user_channel_reconcile_enabled"] is False
    assert settings_copy["edli_v1"]["real_order_submit_enabled"] is False


def test_live_execution_mode_rejects_legacy_cron_with_edli_runtime_enabled(monkeypatch):
    with pytest.raises(RuntimeError, match="EDLI_RUNTIME_CONFLICTS_WITH_LEGACY_CRON"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            {
                "enabled": True,
                "live_execution_mode": "legacy_cron",
                "reactor_mode": "live_no_submit",
                "event_writer_enabled": True,
                "forecast_snapshot_trigger_enabled": True,
                "real_order_submit_enabled": False,
            },
        )


def test_live_execution_mode_rejects_disabled_with_edli_runtime_enabled(monkeypatch):
    with pytest.raises(RuntimeError, match="EDLI_RUNTIME_CONFLICTS_WITH_DISABLED_MODE"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            {
                "enabled": True,
                "live_execution_mode": "disabled",
                "reactor_mode": "disabled",
                "event_writer_enabled": True,
                "forecast_snapshot_trigger_enabled": False,
                "real_order_submit_enabled": False,
            },
        )


def test_market_discovery_constructs_public_clob_with_bounded_timeout(monkeypatch):
    import src.main as main
    import src.data.market_scanner as market_scanner
    import src.data.polymarket_client as polymarket_client
    import src.state.db as db

    captured = {}

    class FakePolymarketClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeConn:
        def __init__(self):
            self.committed = False
            self.closed = False

        def commit(self):
            self.committed = True

        def close(self):
            self.closed = True

    fake_conn = FakeConn()
    monkeypatch.setenv("ZEUS_DISCOVERY_CLOB_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)
    monkeypatch.setattr(market_scanner, "find_weather_markets", lambda **_kwargs: [])
    monkeypatch.setattr(
        market_scanner,
        "refresh_executable_market_substrate_snapshots",
        lambda conn, **_kwargs: {"attempted": 0, "inserted": 0},
    )
    monkeypatch.setattr(db, "get_trade_connection", lambda *args, **kwargs: fake_conn)

    main._market_discovery_cycle()

    assert captured["public_http_timeout"] == 7.5
    assert fake_conn.committed is True
    assert fake_conn.closed is True


def test_market_discovery_uses_full_weather_discovery_with_slug_fallback():
    source = Path("src/main.py").read_text()
    start = source.index("def _market_discovery_cycle")
    end = source.index("def _capture_boot_state", start)
    discovery_source = source[start:end]
    assert "find_weather_markets" in discovery_source
    assert "include_slug_pattern=True" in discovery_source
    assert "public_http_timeout=_discovery_clob_timeout" in discovery_source
    assert "find_slug_pattern_weather_markets" not in discovery_source


def test_edli_market_channel_online_service_wired_to_rest_seed_and_websocket():
    source = Path("src/main.py").read_text()
    ingestor_source = Path("src/events/triggers/market_channel_ingestor.py").read_text()
    assert "PolymarketClient" in source
    assert "get_orderbook_snapshot" in source
    assert "run_market_channel_service_forever" in source
    assert "no_rest_orderbook_client_configured" not in source
    assert "wss://ws-subscriptions-clob.polymarket.com/ws/market" in ingestor_source
    assert '"type": "market"' in ingestor_source


def test_no_shadow_named_edli_modules():
    edli_paths = [
        path
        for path in Path("src").rglob("*")
        if path.is_file() and ("edli" in str(path).lower() or "events" in str(path).lower())
    ]
    assert all("shadow_" not in path.name for path in edli_paths)


def _run_main_with_fake_scheduler(monkeypatch, edli_updates):
    import src.main as main

    settings_copy = deepcopy(main.settings._data)
    settings_copy["edli_v1"].update(edli_updates)
    monkeypatch.setattr(main, "settings", settings_copy)
    monkeypatch.setattr(main, "get_mode", lambda: "live")
    monkeypatch.setattr(main.sys, "argv", ["src/main.py"])
    monkeypatch.setattr(main, "_capture_boot_state", lambda: {"sha": "abc123", "ts": None})
    monkeypatch.setattr(main, "_start_venue_heartbeat_loop_if_needed", lambda: None)
    monkeypatch.setattr(main, "_startup_world_schema_ready_check", lambda: None)
    monkeypatch.setattr(main, "_run_f109_consolidator", lambda: None)
    monkeypatch.setattr(main, "_startup_data_health_check", lambda _conn: None)
    monkeypatch.setattr(main, "_startup_freshness_check", lambda: None)
    monkeypatch.setattr(main, "_assert_live_safe_strategies_or_exit", lambda: None)
    monkeypatch.setattr(main, "_boot_deployment_freshness_auto_resume", lambda: None)
    monkeypatch.setattr(main, "_startup_wallet_check", lambda: None)
    monkeypatch.setattr(main, "_start_user_channel_ingestor_if_enabled", lambda: None)
    monkeypatch.setattr(main, "_check_s1_without_s2_sla", lambda: None)
    monkeypatch.setattr(main, "_assert_cascade_liveness_contract", lambda _scheduler: None)
    monkeypatch.setattr(main, "init_schema_trade_only", lambda _conn: None)
    monkeypatch.setenv("ZEUS_BOOT_REGISTRY_ASSERT_ENABLED", "0")

    def _conn():
        return sqlite3.connect(":memory:")

    monkeypatch.setattr(main, "get_world_connection", lambda *args, **kwargs: _conn())
    monkeypatch.setattr(main, "get_trade_connection", lambda *args, **kwargs: _conn())

    class FakeScheduler:
        instances = []

        def __init__(self, *args, **kwargs):
            self.timezone = kwargs.get("timezone")
            self.jobs = []
            self.started = False
            self.shutdown_called = False
            FakeScheduler.instances.append(self)

        def add_job(self, func, trigger, *args, id=None, **kwargs):
            self.jobs.append(SimpleNamespace(id=id, func=func, trigger=trigger, kwargs=kwargs))

        def get_jobs(self):
            return self.jobs

        def start(self):
            self.started = True
            raise KeyboardInterrupt()

        def shutdown(self, wait=True):
            self.shutdown_called = wait

    monkeypatch.setattr(main, "BlockingScheduler", FakeScheduler)
    main.main()
    return FakeScheduler.instances[-1], settings_copy
