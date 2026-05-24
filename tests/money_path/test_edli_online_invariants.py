# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI v1 implementation prompt §16 final online config and §21 A16-A40.
from __future__ import annotations

import json
from pathlib import Path


def test_edli_online_config_enabled_with_stale_book_and_fok_off():
    settings = json.loads(Path("config/settings.json").read_text())
    edli = settings["edli_v1"]
    assert edli["enabled"] is True
    assert edli["reactor_mode"] == "live_no_submit"
    assert edli["event_writer_enabled"] is True
    assert edli["forecast_snapshot_trigger_enabled"] is True
    assert edli["forecast_complete_live_enabled"] is True
    assert edli["day0_extreme_trigger_enabled"] is True
    assert edli["day0_authority_catchup_scanner_enabled"] is False
    assert edli["day0_hard_fact_live_enabled"] is True
    assert edli["market_channel_ingestor_enabled"] is True
    assert edli["market_channel_quote_cache_enabled"] is True
    assert edli["no_trade_regret_enabled"] is True
    assert edli["reports_enabled"] is True
    assert edli["stale_book_directional_trading_enabled"] is False
    assert edli["real_order_submit_enabled"] is False
    assert edli["taker_fok_fak_live_enabled"] is False
    assert edli["tiny_live_max_notional_usd"] == 5.0
    assert edli["tiny_live_max_orders_per_day"] == 1


def test_edli_reactor_job_wired_without_removing_scheduler_jobs():
    source = Path("src/main.py").read_text()
    assert "edli_event_reactor" in source
    assert "edli_market_channel_ingestor" in source
    assert "_edli_emit_forecast_snapshot_events" in source
    assert "_edli_emit_day0_extreme_events" in source
    assert "day0_authority_catchup_scanner_enabled" in source
    assert "event_bound_no_submit_adapter_from_trade_conn" in source
    assert "submit_existing_cycle_for_event" not in source
    assert 'edli_cfg.get("real_order_submit_enabled"' not in source
    assert "real_order_submit_enabled=False" in source
    assert "user_channel_or_reconcile_only" in source
    edli_start = source.index("def _edli_event_reactor_cycle")
    edli_end = source.index("@_scheduler_job", edli_start + 1)
    edli_source = source[edli_start:edli_end]
    assert "run_cycle" not in edli_source
    for existing_job in ("opening_hunt", "day0_capture", "imminent_open_capture", "market_discovery", "harvester"):
        assert existing_job in source


def test_market_discovery_uses_full_weather_discovery_with_slug_fallback():
    source = Path("src/main.py").read_text()
    start = source.index("def _market_discovery_cycle")
    end = source.index("def _capture_boot_state", start)
    discovery_source = source[start:end]
    assert "find_weather_markets" in discovery_source
    assert "include_slug_pattern=True" in discovery_source
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
