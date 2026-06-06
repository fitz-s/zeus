# Created: 2026-05-24
# Last reused/audited: 2026-06-05
# Authority basis: EDLI PR332 deploy-ready review plus day0_shadow bridge;
# Day0 may run in shadow/no-submit only, never as real capital authorization.
#                  + 2026-06-04 arm direction-gate boot guard DELETED (mainstream is
#                    display-only, never a decision/arm input — operator Rule-4 antibody)
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
    # edli_v1.enabled may be True: EDLI pipeline runs shadow receipts only (zero capital).
    # Updated contract: 2026-05-31 - EDLI in edli_shadow_no_submit; no real capital at risk.
    assert edli["real_order_submit_enabled"] is False, "SAFETY: real order submission must be OFF"
    assert edli["taker_fok_fak_live_enabled"] is False, "SAFETY: taker FOK/FAK live must be OFF"
    assert edli["live_execution_mode"] == "edli_shadow_no_submit", "SAFETY: must be shadow/no-submit mode"
    assert edli["edli_live_scope"] == "day0_shadow"
    assert edli["day0_extreme_trigger_enabled"] is True
    assert edli["day0_authority_catchup_scanner_enabled"] is True
    assert edli["day0_hard_fact_live_enabled"] is True
    assert edli["market_channel_ingestor_enabled"] is False
    assert edli["edli_user_channel_reconcile_enabled"] is False
    assert edli["edli_user_channel_message_queue_path"] == ""
    assert edli["edli_venue_reconcile_facts_path"] == ""
    assert edli["edli_user_channel_reconcile_max_messages"] <= 50
    assert edli["edli_user_channel_reconcile_pending_limit"] <= 50
    assert edli["pre_submit_max_quote_age_ms"] <= 1000
    assert edli["pre_submit_balance_allowance_check_enabled"] is True
    assert edli["market_channel_quote_cache_enabled"] is True
    assert edli["no_trade_regret_enabled"] is True
    assert edli["reports_enabled"] is True
    assert edli["forecast_snapshot_emit_limit"] is False
    assert edli["coverage_fairness_emit_enabled"] is True
    assert edli["day0_catchup_emit_limit"] <= 20
    assert edli["no_submit_proof_limit"] is False
    assert edli["redecision_max_per_cycle"] is False
    assert edli["market_channel_refresh_max_actions_per_window"] <= 5
    assert edli["market_channel_refresh_window_seconds"] >= 1
    assert edli["no_submit_visible_depth_fill_lcb"] < 1.0
    assert edli["stale_book_directional_trading_enabled"] is False
    assert edli["real_order_submit_enabled"] is False
    assert edli["taker_fok_fak_live_enabled"] is False
    assert edli["edli_live_operator_authorized"] is False
    assert edli["edli_live_promotion_artifact_required"] is True
    assert edli["edli_live_min_canary_count"] == 1
    assert edli["edli_live_max_unresolved_unknowns"] == 0
    assert edli["edli_live_min_realized_edge_bps"] == 0
    # BUG #99 antibody: the notional cap and the daily order count are operator-owned
    # values (config/settings.json) and may be raised for live operation. They are NOT
    # re-pinned to the old 5.0/1 here — that would erase the real safety property. The
    # invariant we assert instead is STRUCTURAL: an order-emission RATE limiter exists
    # that is DECOUPLED from the notional cap, so raising the notional cap can never
    # silently uncap order frequency. The notional and day-count values must remain
    # positive and finite (sane), and the decoupled rate-limit must be wired with a
    # conservative default.
    assert float(edli["tiny_live_max_notional_usd"]) > 0
    assert int(edli["tiny_live_max_orders_per_day"]) >= 1

    # (1) The rate-limiter is a real, independent control on the ledger: reserve()
    #     accepts a max_orders_per_window argument SEPARATE from max_notional_usd and
    #     max_orders_per_day, and it fails closed to a conservative canary default.
    import inspect

    from src.events.live_cap import DEFAULT_MAX_ORDERS_PER_WINDOW, LiveCapLedger

    reserve_params = inspect.signature(LiveCapLedger.reserve).parameters
    assert "max_orders_per_window" in reserve_params, (
        "SAFETY: order-emission rate limit must be a SEPARATE knob from the notional cap"
    )
    assert reserve_params["max_orders_per_window"].default == DEFAULT_MAX_ORDERS_PER_WINDOW
    assert DEFAULT_MAX_ORDERS_PER_WINDOW == 1, "SAFETY: rate-limit default must be conservative (1/window)"

    # (2) The ledger reserves an independent rate-window slot pool, distinct from the
    #     notional-coupled day-slot pool.
    cap_source = Path("src/events/live_cap.py").read_text()
    assert "_reserve_window_slot" in cap_source
    assert "edli_live_cap_rate_window" in cap_source
    schema_source = Path("src/state/schema/edli_live_cap_usage_schema.py").read_text()
    assert "edli_live_cap_rate_window" in schema_source

    # (3) The daemon threads the decoupled rate-limit config key through to the ledger
    #     with a conservative .get default (operator sets the live value; we never write
    #     config/settings.json here).
    main_source = Path("src/main.py").read_text()
    assert 'edli_cfg.get("tiny_live_max_orders_per_window", 1)' in main_source
    adapter_source = Path("src/engine/event_reactor_adapter.py").read_text()
    assert "max_orders_per_window=" in adapter_source


def test_day0_shadow_scope_admits_day0_but_keeps_market_channel_disabled():
    settings = json.loads(Path("config/settings.json").read_text())
    edli = settings["edli_v1"]

    assert edli["edli_live_scope"] == "day0_shadow"
    assert edli["day0_extreme_trigger_enabled"] is True
    assert edli["day0_hard_fact_live_enabled"] is True
    assert edli["day0_authority_catchup_scanner_enabled"] is True
    assert edli["market_channel_ingestor_enabled"] is False
    assert edli["real_order_submit_enabled"] is False
    assert edli["taker_fok_fak_live_enabled"] is False


def test_pr_scope_document_matches_settings_flags():
    settings = json.loads(Path("config/settings.json").read_text())
    edli = settings["edli_v1"]
    spec = Path("docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md").read_text()

    # edli_v1.enabled may be True (shadow/no-submit mode). Safety guards must be OFF.
    # Updated contract: 2026-05-31 - EDLI in edli_shadow_no_submit; no real capital at risk.
    assert edli["real_order_submit_enabled"] is False, "SAFETY: real order submission must be OFF"
    assert edli["taker_fok_fak_live_enabled"] is False, "SAFETY: taker FOK/FAK live must be OFF"
    assert edli["live_execution_mode"] == "edli_shadow_no_submit", "SAFETY: must be shadow/no-submit mode"
    assert edli["edli_live_scope"] == "day0_shadow"
    assert edli["day0_hard_fact_live_enabled"] is True
    assert edli["market_channel_ingestor_enabled"] is False
    assert edli["real_order_submit_enabled"] is False
    assert "market_channel_ingestor_enabled=false" in spec
    assert "Day0" in spec
    assert "real submit disabled" in spec or "real submit disabled" in spec.lower()


def test_edli_online_invariants_do_not_claim_day0_real_submit():
    source = Path("tests/money_path/test_edli_online_invariants.py").read_text()
    forbidden_claim = "DAY0_REAL_SUBMIT_ENABLED" + " = true"

    assert forbidden_claim not in source
    assert "real_order_submit_enabled\"] is True" not in source


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
    assert 'submit_disabled_effective_mode = reactor_mode == "live_no_submit"' in source
    assert 'real_submit_effective = real_order_submit_enabled if reactor_mode == "live" else False' in source
    assert "taker_fok_fak_effective" in source
    assert "live_submit_effective = live_bridge_mode or submit_disabled_effective_mode" in source
    assert "real submit disabled this cycle because portfolio_state_unavailable" in source
    assert "if real_submit_effective and _portfolio_state_provider is None" in source
    assert "submit_existing_cycle_for_event" not in source
    assert 'edli_cfg.get("real_order_submit_enabled", False)' in source
    assert "real_order_submit_enabled=real_order_submit_enabled" in source
    assert 'edli_cfg.get("live_canary_enabled", False)' in source
    assert "forecast_snapshot_emit_limit" in source
    assert "no_submit_proof_limit" in source
    assert "process_pending_decision_time = datetime.now(timezone.utc)" in source
    assert "reactor.process_pending(decision_time=process_pending_decision_time, limit=proof_limit)" in source
    assert "reactor.process_pending(decision_time=now, limit=proof_limit)" not in source
    assert "decision_time=process_pending_decision_time" in source
    assert "_edli_positive_int_or_unbounded" in source
    assert "user_channel_or_reconcile_only" in source
    edli_start = source.index("def _edli_event_reactor_cycle")
    edli_end = source.index("@_scheduler_job", edli_start + 1)
    edli_source = source[edli_start:edli_end]
    assert "run_cycle" not in edli_source
    assert "_assert_live_execution_mode_contract" in source
    assert "live_execution_mode == \"legacy_cron\"" in source
    assert "edli_submit_disabled_bridge" in source
    assert "edli_live_canary" in source
    assert "EDLI_EVENT_DRIVEN_MODES" in source
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


def test_pr332_scoped_daemon_restart_smoke_registers_event_driven_no_legacy_cron(monkeypatch, tmp_path):
    scheduler, settings_copy = _run_main_with_fake_scheduler(
        monkeypatch,
        {
            "enabled": True,
            "live_execution_mode": "edli_submit_disabled_bridge",
            "reactor_mode": "submit_disabled_live_bridge",
            "event_writer_enabled": True,
            "forecast_snapshot_trigger_enabled": True,
            "day0_extreme_trigger_enabled": False,
            "day0_hard_fact_live_enabled": False,
            "market_channel_ingestor_enabled": True,
            "edli_user_channel_reconcile_enabled": True,
            "real_order_submit_enabled": False,
            "taker_fok_fak_live_enabled": False,
            **_stage_evidence_updates(tmp_path),
        },
    )

    job_ids = {job.id for job in scheduler.jobs}
    assert scheduler.started is True
    assert scheduler.shutdown_called is True
    assert "edli_event_reactor" in job_ids
    assert "edli_market_channel_ingestor" in job_ids
    assert "edli_user_channel_reconcile" in job_ids
    assert "opening_hunt" not in job_ids
    assert not any(job_id.startswith("update_reaction_") for job_id in job_ids)
    assert "day0_capture" not in job_ids
    assert "imminent_open_capture" not in job_ids
    # market_discovery is registered in EDLI event-driven modes as a DATA-ONLY substrate
    # refresh for executable_market_snapshots (structural fix: EMS substrate must stay
    # fresh in EDLI modes; market_discovery is the only EMS writer). Gated by
    # market_substrate_refresh_enabled (default True). Not a legacy-cron arming job.
    assert "market_discovery" in job_ids
    # harvester is registered in EDLI event-driven modes as the settlement P&L +
    # redeem-intent resolver (守護 fix 2026-06-03). Pre-守護 it was gated to legacy_cron
    # only, so a FILLED EDLI position that rode to settlement sat phase=active forever and
    # capital stayed stuck on-chain (memory #56). It is now a REQUIRED poller per
    # architecture/cascade_liveness_contract.yaml (the boot guard FATALs if it is missing
    # in a live mode). Shadow-safe: reads VERIFIED settlement_outcomes; the on-chain redeem
    # POST is the separately-gated _redeem_submitter_cycle, not this resolver.
    assert "harvester" in job_ids
    assert "heartbeat" in job_ids
    assert settings_copy["edli_v1"]["live_execution_mode"] == "edli_submit_disabled_bridge"
    assert settings_copy["edli_v1"]["forecast_snapshot_trigger_enabled"] is True
    assert settings_copy["edli_v1"]["day0_extreme_trigger_enabled"] is False
    assert settings_copy["edli_v1"]["market_channel_ingestor_enabled"] is True
    assert settings_copy["edli_v1"]["edli_user_channel_reconcile_enabled"] is True
    assert settings_copy["edli_v1"]["real_order_submit_enabled"] is False


def test_market_substrate_warm_cadence_stays_inside_executable_price_ttl(monkeypatch, tmp_path):
    from src.contracts.executable_market_snapshot import FRESHNESS_WINDOW_DEFAULT

    scheduler, _settings_copy = _run_main_with_fake_scheduler(
        monkeypatch,
        {
            "enabled": True,
            "live_execution_mode": "edli_shadow_no_submit",
            "reactor_mode": "live_no_submit",
            "event_writer_enabled": True,
            "forecast_snapshot_trigger_enabled": True,
            "day0_extreme_trigger_enabled": False,
            "day0_hard_fact_live_enabled": False,
            "market_channel_ingestor_enabled": False,
            "edli_user_channel_reconcile_enabled": False,
            "real_order_submit_enabled": False,
            "taker_fok_fak_live_enabled": False,
            **_stage_evidence_updates(tmp_path),
        },
    )

    jobs = {job.id: job for job in scheduler.jobs}
    reactor = jobs["edli_event_reactor"]
    warmer = jobs["edli_market_substrate_warm"]

    assert warmer.kwargs["seconds"] < FRESHNESS_WINDOW_DEFAULT.total_seconds(), (
        "executable snapshots expire after 30s; the substrate warmer cadence must "
        "stay inside that TTL or the reactor reads stale price rows."
    )
    assert warmer.kwargs["next_run_time"] < reactor.kwargs["next_run_time"], (
        "the first substrate warm must run before the first reactor process_pending "
        "cycle so cold starts do not begin with EXECUTABLE_SNAPSHOT_STALE."
    )


def test_live_execution_mode_rejects_legacy_cron_with_edli_runtime_enabled(monkeypatch):
    with pytest.raises(RuntimeError, match="LEGACY_CRON_REQUIRES_REACTOR_MODE_DISABLED"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            {
                "enabled": False,
                "live_execution_mode": "legacy_cron",
                "reactor_mode": "live_no_submit",
                "event_writer_enabled": False,
                "forecast_snapshot_trigger_enabled": False,
                "real_order_submit_enabled": False,
            },
        )

    with pytest.raises(RuntimeError, match="EDLI_RUNTIME_CONFLICTS_WITH_LEGACY_CRON"):
        _run_main_with_fake_scheduler(
            monkeypatch,
                {
                    "enabled": True,
                    "live_execution_mode": "legacy_cron",
                    "reactor_mode": "disabled",
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


def test_forecast_only_live_scope_rejects_day0_runtime(monkeypatch):
    with pytest.raises(RuntimeError, match="DAY0_OUT_OF_SCOPE_FOR_PR332"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            {
                "enabled": False,
                "live_execution_mode": "legacy_cron",
                "edli_live_scope": "forecast_only",
                "reactor_mode": "disabled",
                "event_writer_enabled": False,
                "forecast_snapshot_trigger_enabled": False,
                "day0_extreme_trigger_enabled": True,
                "real_order_submit_enabled": False,
            },
        )


def test_live_execution_mode_stage_requires_matching_reactor_mode(monkeypatch):
    with pytest.raises(RuntimeError, match="EDLI_LIVE_CANARY_REQUIRES_REACTOR_MODE_LIVE"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            {
                "enabled": True,
                "live_execution_mode": "edli_live_canary",
                "reactor_mode": "live_no_submit",
                "event_writer_enabled": True,
                "forecast_snapshot_trigger_enabled": True,
                "market_channel_ingestor_enabled": True,
                "edli_user_channel_reconcile_enabled": True,
                "real_order_submit_enabled": True,
                "live_canary_enabled": True,
            },
        )

    with pytest.raises(RuntimeError, match="EDLI_SUBMIT_DISABLED_BRIDGE_REQUIRES_REACTOR_MODE_SUBMIT_DISABLED_LIVE_BRIDGE"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            {
                "enabled": True,
                "live_execution_mode": "edli_submit_disabled_bridge",
                "reactor_mode": "live",
                "event_writer_enabled": True,
                "forecast_snapshot_trigger_enabled": True,
                "market_channel_ingestor_enabled": True,
                "edli_user_channel_reconcile_enabled": True,
                "real_order_submit_enabled": False,
            },
        )

    with pytest.raises(RuntimeError, match="EDLI_SHADOW_NO_SUBMIT_REQUIRES_REACTOR_MODE_LIVE_NO_SUBMIT"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            {
                "enabled": True,
                "live_execution_mode": "edli_shadow_no_submit",
                "reactor_mode": "submit_disabled_live_bridge",
                "event_writer_enabled": True,
                "forecast_snapshot_trigger_enabled": True,
                "real_order_submit_enabled": False,
            },
        )


def test_live_execution_mode_rejects_ambiguous_edli_event_driven_mode(monkeypatch):
    with pytest.raises(ValueError, match="UNSUPPORTED_LIVE_EXECUTION_MODE:edli_event_driven"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            {
                "enabled": True,
                "live_execution_mode": "edli_event_driven",
                "reactor_mode": "submit_disabled_live_bridge",
                "event_writer_enabled": True,
                "forecast_snapshot_trigger_enabled": True,
                "real_order_submit_enabled": False,
            },
        )


def test_submit_disabled_bridge_requires_lifecycle_authorities(monkeypatch):
    with pytest.raises(RuntimeError, match="EDLI_SUBMIT_DISABLED_BRIDGE_REQUIRES_MARKET_CHANNEL_INGESTOR_ENABLED"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            {
                "enabled": True,
                "live_execution_mode": "edli_submit_disabled_bridge",
                "reactor_mode": "submit_disabled_live_bridge",
                "event_writer_enabled": True,
                "forecast_snapshot_trigger_enabled": True,
                "market_channel_ingestor_enabled": False,
                "edli_user_channel_reconcile_enabled": True,
                "real_order_submit_enabled": False,
            },
        )


def test_live_canary_requires_submit_and_canary_flags(monkeypatch):
    with pytest.raises(RuntimeError, match="EDLI_LIVE_CANARY_REQUIRES_REAL_ORDER_SUBMIT_ENABLED_AND_LIVE_CANARY_ENABLED"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            {
                "enabled": True,
                "live_execution_mode": "edli_live_canary",
                "reactor_mode": "live",
                "event_writer_enabled": True,
                "forecast_snapshot_trigger_enabled": True,
                "market_channel_ingestor_enabled": True,
                "edli_user_channel_reconcile_enabled": True,
                "real_order_submit_enabled": False,
                "live_canary_enabled": False,
            },
        )


# ---------------------------------------------------------------------------
# RETIRED 2026-06-04 (operator directive, Rule-4 antibody): the two-key arm
# direction-gate boot guard (_assert_edli_arm_requires_direction_gate) is DELETED.
# It coupled arming to the mainstream-enforcement flag, but mainstream is now
# OBSERVATIONAL / DISPLAY-ONLY and is NEVER a decision/arm input. The submit-time
# enforce branch it guarded was also deleted, so there is no "direction gate" left to
# require at arm time. The inverse law (mainstream cannot block boot/arm/submit) is
# proven in tests/money_path/test_mainstream_display_only_unconstructable.py.
# ---------------------------------------------------------------------------


def test_arm_direction_gate_boot_guard_is_deleted():
    """The two-key arm direction-gate boot guard must NOT exist — mainstream is
    display-only and can never block arming."""
    import src.main as main

    assert not hasattr(main, "_assert_edli_arm_requires_direction_gate"), (
        "the deleted arm direction-gate boot guard reappeared; mainstream must NOT "
        "be coupled to arming (operator law: observational/display-only)."
    )


def test_live_canary_requires_stage_evidence_file_paths(monkeypatch, tmp_path):
    # Post-PR #367: stage paths are configured in settings.json, so
    # _require_stage_file_paths (config-key check) no longer raises.
    # evaluate_edli_stage_readiness (disk-existence check) raises instead:
    # absent files → STALE/MISSING reasons → FAIL status →
    # _assert_edli_stage_readiness raises EDLI_LIVE_CANARY_READINESS_FAIL.
    # Boot is fail-CLOSED: guard is intact, source of raise shifted.
    # RATIFIED 2026-06-03 (FIX-2 investigation). See also: src/main.py
    # lines 370-426 (_require_stage_file_paths, evaluate_edli_stage_readiness).
    with pytest.raises(RuntimeError, match="EDLI_LIVE_CANARY_READINESS_FAIL"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            _edli_live_canary_updates(
                edli_stage_loaded_sha_file=str(tmp_path / "missing-loaded-sha.json"),
                edli_stage_source_health_json=str(tmp_path / "missing-source-health.json"),
                edli_stage_status_json=str(tmp_path / "missing-status-summary.json"),
            ),
        )


def test_edli_live_canary_stage_readiness_waits_on_clean_db(monkeypatch, tmp_path):
    import src.main as main

    db_path = tmp_path / "world.db"
    _init_stage_world_db(db_path)
    monkeypatch.setattr(main, "get_world_connection_read_only", lambda *args, **kwargs: _stage_conn(db_path))

    report = main.evaluate_edli_stage_readiness(
        stage="edli_live_canary",
        canary_artifact_path=str(tmp_path / "missing-canary.json"),
    )

    assert report.status == "WAITING_FOR_QUALIFYING_EVENT"
    assert report.live_entries_allowed is True
    assert report.submit_allowed is True
    assert report.scaleout_allowed is False


def test_edli_live_canary_with_stage_evidence_waits_for_qualifying_event(monkeypatch, tmp_path):
    scheduler, settings_copy = _run_main_with_fake_scheduler(
        monkeypatch,
        _edli_live_canary_updates(**_stage_evidence_updates(tmp_path)),
    )

    job_ids = {job.id for job in scheduler.jobs}
    assert scheduler.started is True
    assert "edli_event_reactor" in job_ids
    assert "edli_market_channel_ingestor" in job_ids
    assert "edli_user_channel_reconcile" in job_ids
    assert settings_copy["edli_v1"]["live_execution_mode"] == "edli_live_canary"


def test_edli_live_canary_boot_runs_stage_readiness_before_registering_edli_jobs(monkeypatch):
    import src.main as main

    calls: list[str] = []

    def _fake_readiness(_cfg):
        calls.append("readiness")
        return main.EdliStageReadiness(
            stage="edli_live_canary",
            status=main.EDLI_STAGE_WAITING,
            live_entries_allowed=True,
            submit_allowed=True,
            scaleout_allowed=False,
            reasons=("CANARY_ARTIFACT_MISSING",),
        )

    monkeypatch.setattr(main, "_assert_edli_stage_readiness", _fake_readiness)
    _run_main_with_fake_scheduler(
        monkeypatch,
        _edli_live_canary_updates(),
        scheduler_calls=calls,
    )

    edli_job_indices = [
        index for index, call in enumerate(calls) if call.startswith("add_job:edli_")
    ]
    assert edli_job_indices
    assert calls.index("readiness") < min(edli_job_indices)


def test_edli_live_canary_boot_readiness_failure_blocks_edli_job_registration(monkeypatch):
    import src.main as main

    calls: list[str] = []

    def _fake_readiness(_cfg):
        calls.append("readiness")
        raise RuntimeError("EDLI_LIVE_CANARY_READINESS_FAIL:EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN")

    monkeypatch.setattr(main, "_assert_edli_stage_readiness", _fake_readiness)
    with pytest.raises(RuntimeError, match="EDLI_LIVE_CANARY_READINESS_FAIL"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            _edli_live_canary_updates(),
            scheduler_calls=calls,
        )

    assert calls == ["readiness"]


def test_edli_live_canary_stage_readiness_blocks_unresolved_unknown(monkeypatch, tmp_path):
    import src.main as main

    db_path = tmp_path / "world.db"
    conn = _init_stage_world_db(db_path)
    conn.execute(
        """
        INSERT INTO edli_live_order_projection (
            aggregate_id, event_id, final_intent_id, current_state, last_sequence,
            last_event_type, last_event_hash, pending_reconcile, venue_order_id,
            updated_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("event-1:intent-1", "event-1", "intent-1", "SUBMIT_UNKNOWN", 1, "SubmitUnknown", "hash-1", 1, "venue-1", "2026-05-26T12:00:00+00:00", 1),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(main, "get_world_connection_read_only", lambda *args, **kwargs: _stage_conn(db_path))

    report = main.evaluate_edli_stage_readiness(stage="edli_live_canary")

    assert report.status == "FAIL"
    assert any(reason.startswith("EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN") for reason in report.reasons)


def test_edli_live_canary_stage_readiness_blocks_open_cap_reservation(monkeypatch, tmp_path):
    import src.main as main
    from src.state.schema.edli_live_cap_usage_schema import ensure_table as ensure_live_cap_table

    db_path = tmp_path / "world.db"
    conn = _init_stage_world_db(db_path)
    ensure_live_cap_table(conn)
    conn.execute(
        """
        INSERT INTO edli_live_cap_usage (
            usage_id, event_id, decision_time, cap_scope, max_notional_usd,
            max_orders_per_day, reserved_notional_usd, order_count,
            reservation_status, final_intent_id, execution_command_id,
            created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("usage-open", "event-1", "2026-05-26T12:00:00+00:00", "tiny_live_canary", 5.0, 1, 5.0, 1, "RESERVED", "intent-1", "command-1", "2026-05-26T12:00:00+00:00", 1),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(main, "get_world_connection_read_only", lambda *args, **kwargs: _stage_conn(db_path))

    report = main.evaluate_edli_stage_readiness(stage="edli_live_canary")

    assert report.status == "FAIL"
    assert any(reason.startswith("EDLI_STAGE_LIVE_CAP_RESERVED") for reason in report.reasons)


def test_edli_live_canary_stage_readiness_fails_closed_on_missing_projection(monkeypatch, tmp_path):
    import src.main as main

    db_path = tmp_path / "world.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE placeholder (id TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(main, "get_world_connection_read_only", lambda *args, **kwargs: _stage_conn(db_path))

    report = main.evaluate_edli_stage_readiness(stage="edli_live_canary")

    assert report.status == "FAIL"
    assert any(reason.startswith("EDLI_STAGE_PENDING_RECONCILE_QUERY_FAILED") for reason in report.reasons)


def test_edli_live_canary_stage_readiness_fails_closed_on_missing_cap_usage(monkeypatch, tmp_path):
    import src.main as main
    from src.state.schema.edli_live_order_events_schema import ensure_tables as ensure_live_order_tables

    db_path = tmp_path / "world.db"
    conn = sqlite3.connect(db_path)
    ensure_live_order_tables(conn)
    conn.commit()
    conn.close()
    monkeypatch.setattr(main, "get_world_connection_read_only", lambda *args, **kwargs: _stage_conn(db_path))

    report = main.evaluate_edli_stage_readiness(stage="edli_live_canary")

    assert report.status == "FAIL"
    assert any(reason.startswith("EDLI_STAGE_OPEN_CAP_QUERY_FAILED") for reason in report.reasons)


def test_edli_live_canary_stage_readiness_blocks_stale_source(monkeypatch, tmp_path):
    import src.main as main

    db_path = tmp_path / "world.db"
    _init_stage_world_db(db_path).close()
    source = tmp_path / "source_health.json"
    source.write_text(json.dumps({"generated_at": "2026-01-01T00:00:00+00:00"}))
    monkeypatch.setattr(main, "get_world_connection_read_only", lambda *args, **kwargs: _stage_conn(db_path))

    report = main.evaluate_edli_stage_readiness(
        stage="edli_live_canary",
        source_health_json=str(source),
        max_age_seconds=1,
    )

    assert report.status == "FAIL"
    assert any(reason.startswith("EDLI_STAGE_SOURCE_HEALTH_STALE") for reason in report.reasons)


def _edli_live_canary_updates(**overrides):
    values = {
        "enabled": True,
        "live_execution_mode": "edli_live_canary",
        "reactor_mode": "live",
        "event_writer_enabled": True,
        "forecast_snapshot_trigger_enabled": True,
        "market_channel_ingestor_enabled": True,
        "edli_user_channel_reconcile_enabled": True,
        "real_order_submit_enabled": True,
        "live_canary_enabled": True,
        # 2026-06-04: the arm direction-gate boot guard is DELETED (mainstream is
        # display-only). These keys are now INERT (no boot guard reads them); retained
        # here only to keep this canary config explicit. They do not affect boot.
        "mainstream_agreement_enforce_on_submit": True,
        "mainstream_agreement_reference_enabled": True,
    }
    values.update(overrides)
    return values


def _edli_live_updates(**overrides):
    values = {
        "enabled": True,
        "live_execution_mode": "edli_live",
        "reactor_mode": "live",
        "event_writer_enabled": True,
        "forecast_snapshot_trigger_enabled": True,
        "market_channel_ingestor_enabled": True,
        "edli_user_channel_reconcile_enabled": True,
        "real_order_submit_enabled": True,
        "live_canary_enabled": True,
        "edli_live_operator_authorized": True,
        "edli_live_promotion_artifact_required": True,
        "edli_live_min_canary_count": 1,
        "edli_live_max_unresolved_unknowns": 0,
        "edli_live_min_realized_edge_bps": 0,
        # 2026-06-04: inert keys (arm direction-gate guard DELETED). See _edli_live_canary_updates.
        "mainstream_agreement_enforce_on_submit": True,
        "mainstream_agreement_reference_enabled": True,
    }
    values.update(overrides)
    return values


def test_edli_live_requires_operator_authorized_flag(monkeypatch):
    # F1 rename (PR-2 B): edli_live_scaleout_enabled -> edli_live_operator_authorized
    # (the flag is the operator ARM kill-switch, not a scale-out knob).
    with pytest.raises(RuntimeError, match="EDLI_LIVE_REQUIRES_EDLI_LIVE_OPERATOR_AUTHORIZED"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            _edli_live_updates(edli_live_operator_authorized=False),
        )


def test_edli_live_requires_promotion_artifact(monkeypatch):
    with pytest.raises(RuntimeError, match="EDLI_LIVE_REQUIRES_PROMOTION_ARTIFACT"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            _edli_live_updates(edli_live_promotion_artifact_path=""),
        )


def _arm_gate_artifact_updates(tmp_path, **overrides):
    """Stage a VALID ARM-gate evidence artifact and turn the boot requirement ON.

    The harness pins ``_capture_boot_state`` to sha="abc123", so the artifact's
    commit_sha must match that for the SHA binding to pass.
    """
    art_path = tmp_path / "arm_gate_artifact.json"
    art = {
        "schema": "edli_arm_gate_v1",
        "commit_sha": "abc123",
        "measurement_cmd_hash": "f" * 64,
        "capital_weighted_ev": 0.012,
        "production_n": 40,
        "gate_pass_n": 40,
        "per_city_n": {"shanghai": 6, "singapore": 7},
        "ev_sigma": 2.1,
        "date_coverage": ["2026-06-01", "2026-06-02"],
        "coverage_licensed": True,
    }
    art.update(overrides)
    art_path.write_text(json.dumps(art))
    return {
        "edli_arm_gate_artifact_required": True,
        "edli_arm_gate_artifact_path": str(art_path),
    }


def test_live_boot_fails_without_arm_artifact(monkeypatch, tmp_path):
    # PR-2 (A) RED: with the ARM-gate requirement ON but NO artifact on disk,
    # arming the live daemon is a BOOT FAILURE, not a runtime path. A
    # well-formed positive promotion artifact is staged so the failure is
    # ATTRIBUTABLE to the missing ARM artifact (not the promotion gate).
    artifact, db_path = _write_db_backed_promotion_artifact(tmp_path, realized_edge=0.01)

    with pytest.raises(RuntimeError, match="EDLI_LIVE_PROMOTION_ARM_GATE_ARTIFACT_MISSING"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            _edli_live_updates(
                edli_live_promotion_artifact_path=str(artifact),
                **_stage_evidence_updates(tmp_path),
                edli_arm_gate_artifact_required=True,
                edli_arm_gate_artifact_path=str(tmp_path / "does-not-exist.json"),
            ),
            world_db_path=db_path,
        )


def test_capital_weighted_arm_denial_blocks_boot(monkeypatch, tmp_path):
    # PR-2 (A) RED: an ARM artifact that exists and matches HEAD but reports a
    # NON-POSITIVE capital-weighted EV must DENY the boot. The capital-weighted
    # edge is THE arm criterion; a zero/negative one cannot arm.
    artifact, db_path = _write_db_backed_promotion_artifact(tmp_path, realized_edge=0.01)
    arm_updates = _arm_gate_artifact_updates(tmp_path, capital_weighted_ev=0.0)

    with pytest.raises(RuntimeError, match="EDLI_LIVE_PROMOTION_ARM_GATE_EV_NOT_POSITIVE"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            _edli_live_updates(
                edli_live_promotion_artifact_path=str(artifact),
                **_stage_evidence_updates(tmp_path),
                **arm_updates,
            ),
            world_db_path=db_path,
        )


def test_arm_artifact_sha_mismatch_blocks_boot(monkeypatch, tmp_path):
    # PR-2 (A): a stale ARM artifact (measured on a DIFFERENT commit) cannot arm
    # the current code — SHA binding makes "armed on unproven code" unconstructable.
    artifact, db_path = _write_db_backed_promotion_artifact(tmp_path, realized_edge=0.01)
    arm_updates = _arm_gate_artifact_updates(tmp_path, commit_sha="deadbeefdeadbeef")

    with pytest.raises(RuntimeError, match="EDLI_LIVE_PROMOTION_ARM_GATE_COMMIT_SHA_MISMATCH"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            _edli_live_updates(
                edli_live_promotion_artifact_path=str(artifact),
                **_stage_evidence_updates(tmp_path),
                **arm_updates,
            ),
            world_db_path=db_path,
        )


def test_live_boot_accepts_valid_arm_artifact(monkeypatch, tmp_path):
    # PR-2 (A) GREEN: a valid, HEAD-bound, positive-EV, coverage-licensed ARM
    # artifact lets the armed boot proceed (alongside the promotion artifact).
    artifact, db_path = _write_db_backed_promotion_artifact(tmp_path, realized_edge=0.000001)
    arm_updates = _arm_gate_artifact_updates(tmp_path)

    scheduler, settings_copy = _run_main_with_fake_scheduler(
        monkeypatch,
        _edli_live_updates(
            edli_live_promotion_artifact_path=str(artifact),
            **_stage_evidence_updates(tmp_path),
            **arm_updates,
        ),
        world_db_path=db_path,
    )

    assert scheduler.started is True
    assert "edli_event_reactor" in {job.id for job in scheduler.jobs}
    assert settings_copy["edli_v1"]["edli_arm_gate_artifact_required"] is True


def test_edli_live_blocks_unresolved_unknowns(monkeypatch, tmp_path):
    artifact, db_path = _write_db_backed_promotion_artifact(
        tmp_path,
        realized_edge=0.01,
        audit_state="POST_SUBMIT_UNKNOWN",
    )

    with pytest.raises(RuntimeError, match="EDLI_LIVE_PROMOTION_UNRESOLVED_UNKNOWN"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            _edli_live_updates(edli_live_promotion_artifact_path=str(artifact)),
            world_db_path=db_path,
        )


def test_edli_live_blocks_non_positive_realized_edge(monkeypatch, tmp_path):
    artifact, db_path = _write_db_backed_promotion_artifact(tmp_path, realized_edge=-0.01)

    with pytest.raises(RuntimeError, match="EDLI_LIVE_PROMOTION_REALIZED_EDGE_INSUFFICIENT"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            _edli_live_updates(edli_live_promotion_artifact_path=str(artifact)),
            world_db_path=db_path,
        )


def test_edli_live_blocks_scalar_promotion_artifact_without_db_proof(monkeypatch, tmp_path):
    artifact = tmp_path / "promotion.json"
    artifact.write_text(json.dumps({"canary_count": 1, "unresolved_unknowns": 0, "realized_edge_bps": 1}))

    with pytest.raises(RuntimeError, match="EDLI_LIVE_PROMOTION_ARTIFACT_SCHEMA_INVALID"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            _edli_live_updates(edli_live_promotion_artifact_path=str(artifact)),
        )


def test_edli_live_blocks_exact_zero_realized_edge(monkeypatch, tmp_path):
    artifact, db_path = _write_db_backed_promotion_artifact(tmp_path, realized_edge=0.0)

    with pytest.raises(RuntimeError, match="EDLI_LIVE_PROMOTION_REALIZED_EDGE_INSUFFICIENT"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            _edli_live_updates(edli_live_promotion_artifact_path=str(artifact)),
            world_db_path=db_path,
        )


def test_edli_live_accepts_positive_promotion_artifact(monkeypatch, tmp_path):
    artifact, db_path = _write_db_backed_promotion_artifact(tmp_path, realized_edge=0.000001)

    scheduler, settings_copy = _run_main_with_fake_scheduler(
        monkeypatch,
        _edli_live_updates(edli_live_promotion_artifact_path=str(artifact), **_stage_evidence_updates(tmp_path)),
        world_db_path=db_path,
    )

    job_ids = {job.id for job in scheduler.jobs}
    assert scheduler.started is True
    assert "edli_event_reactor" in job_ids
    assert "edli_market_channel_ingestor" in job_ids
    assert "edli_user_channel_reconcile" in job_ids
    assert settings_copy["edli_v1"]["live_execution_mode"] == "edli_live"
    assert settings_copy["edli_v1"]["edli_live_operator_authorized"] is True


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


def _run_main_with_fake_scheduler(monkeypatch, edli_updates, *, world_db_path=None, scheduler_calls=None):
    import src.main as main

    settings_source = main.settings._data if hasattr(main.settings, "_data") else main.settings
    settings_copy = deepcopy(settings_source)
    # PR-2 (A) ARM-gate boot binding: armed modes (canary/live) now ALSO require
    # state/edli_arm_gate_artifact.json. These boot tests exercise EDLI boot
    # logic, NOT the arm-gate artifact shape (which has dedicated coverage in
    # tests/events/test_arm_gate_artifact_boot_binding.py and the explicit
    # *_arm_artifact tests below). Default the requirement OFF here so the broad
    # boot fixtures stay focused; a test that asserts the arm-gate fires sets
    # edli_arm_gate_artifact_required=True explicitly (and wins via the update).
    settings_copy["edli_v1"]["edli_arm_gate_artifact_required"] = False
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
    monkeypatch.setattr(main, "_startup_wallet_check", lambda clob=None, bankroll_record=None: None)
    monkeypatch.setattr(main, "_start_user_channel_ingestor_if_enabled", lambda: None)
    monkeypatch.setattr(main, "_check_s1_without_s2_sla", lambda: None)
    # W0-T2 boot-guards: tests use live settings.json which has model_keys as a list
    # (the bad config the guard catches). Patch out here so tests exercise EDLI boot
    # logic, not calibration-pin shape. The guards have dedicated tests in
    # test_boot_guard_pin_shape.py.
    monkeypatch.setattr(main, "assert_calibration_pin_shape_is_dict", lambda _cfg: None)
    monkeypatch.setattr(main, "assert_frozen_as_of_not_stale", lambda _cfg, **_kw: None)
    monkeypatch.setattr(main, "_assert_cascade_liveness_contract", lambda _scheduler: None)
    monkeypatch.setattr(main, "init_schema_trade_only", lambda _conn: None)
    monkeypatch.setenv("ZEUS_BOOT_REGISTRY_ASSERT_ENABLED", "0")

    def _conn():
        conn = sqlite3.connect(world_db_path or ":memory:")
        conn.row_factory = sqlite3.Row
        if world_db_path is None:
            from src.state.schema.edli_live_cap_usage_schema import ensure_table as ensure_live_cap_table
            from src.state.schema.edli_live_order_events_schema import ensure_tables as ensure_live_order_tables

            ensure_live_order_tables(conn)
            ensure_live_cap_table(conn)
        return conn

    monkeypatch.setattr(main, "get_world_connection", lambda *args, **kwargs: _conn())
    monkeypatch.setattr(main, "get_world_connection_read_only", lambda *args, **kwargs: _conn())
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
            if scheduler_calls is not None:
                scheduler_calls.append(f"add_job:{id or getattr(func, '__name__', '<unknown>')}")
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


def _stage_evidence_updates(tmp_path):
    loaded = tmp_path / "loaded_sha.json"
    source = tmp_path / "source_health.json"
    status = tmp_path / "status_summary.json"
    now = "2026-05-26T12:00:00+00:00"
    loaded.write_text(json.dumps({"loaded_sha": "abc123"}))
    source.write_text(json.dumps({"generated_at": now}))
    status.write_text(json.dumps({"generated_at": now}))
    return {
        "edli_stage_loaded_sha_file": str(loaded),
        "edli_stage_source_health_json": str(source),
        "edli_stage_status_json": str(status),
        "edli_stage_readiness_max_age_seconds": 365 * 24 * 60 * 60,
    }


def _write_db_backed_promotion_artifact(tmp_path, *, realized_edge: float, audit_state: str | None = None):
    from datetime import datetime, timezone

    from src.events.live_order_aggregate import LiveOrderAggregateLedger
    from src.events.live_profit_audit import write_promotion_artifact
    from src.state.schema.decision_certificates_schema import ensure_tables as ensure_decision_certificate_tables
    from src.state.schema.edli_live_cap_usage_schema import ensure_table as ensure_live_cap_table

    db_path = tmp_path / "world.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_live_cap_table(conn)
    _seed_promotion_authority_certificates(conn, ensure_decision_certificate_tables=ensure_decision_certificate_tables)
    now = datetime(2026, 5, 26, 12, tzinfo=timezone.utc)
    ledger = LiveOrderAggregateLedger(conn)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=now,
        source_authority="decision_kernel",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitPlanBuilt",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=now,
        source_authority="engine_adapter",
    )
    pre_submit = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="PreSubmitRevalidated",
        payload=_pre_submit_payload_for_promotion(realized_edge=realized_edge),
        occurred_at=now,
        source_authority="engine_adapter",
    )
    live_cap = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="LiveCapReserved",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "usage_id": "usage-1", "reserved_notional_usd": 5.0, "reservation_status": "RESERVED"},
        occurred_at=now,
        source_authority="live_cap_ledger",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="ExecutionCommandCreated",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "command-1",
            "pre_submit_event_hash": pre_submit.event_hash,
            "live_cap_reserved_event_hash": live_cap.event_hash,
            "usage_id": "usage-1",
        },
        occurred_at=now,
        source_authority="engine_adapter",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="VenueSubmitAttempted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "execution_command_id": "command-1"},
        occurred_at=now,
        source_authority="existing_executor",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="VenueSubmitAcknowledged",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "command-1",
            "venue_order_id": "venue-1",
        },
        occurred_at=now,
        source_authority="existing_executor",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="UserTradeObserved",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "source_authority": "polymarket_user_channel",
            "trade_status": "CONFIRMED",
            "fill_authority_state": "FILL_CONFIRMED",
            "venue_order_id": "venue-1",
            "raw_user_channel_message_hash": "trade-msg-1",
            "avg_fill_price": 0.45 - realized_edge,
            "filled_size": 10.0,
            "fees": 0.0,
        },
        occurred_at=now,
        source_authority="user_channel",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="CapTransitioned",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "command-1",
            "execution_receipt_hash": "receipt-hash-1",
            "to_status": "CONSUMED",
            "projection_status": "CONSUMED",
            "transition_reason": "CONFIRMED",
        },
        occurred_at=now,
        source_authority="live_cap_ledger",
    )
    if audit_state is not None:
        from src.events.live_profit_audit import LiveProfitAuditLedger

        LiveProfitAuditLedger(conn).insert_record(
            event_id="event-1",
            aggregate_id="event-1:intent-1",
            final_intent_id="intent-1",
            execution_command_id="command-1",
            condition_id="condition-1",
            token_id="token-1",
            direction="YES",
            side="BUY",
            realized_edge=realized_edge,
            order_lifecycle_state=audit_state,
            expected_edge_source_certificate_hash="actionable-hash-1",
            cost_basis_source_certificate_hash="cost-hash-1",
            fill_source_event_hash="fill-event-hash-1" if audit_state == "CONFIRMED" else None,
            promotion_eligible=1 if audit_state == "CONFIRMED" and realized_edge > 0 else 0,
        )
    artifact = tmp_path / "promotion.json"
    write_promotion_artifact(conn, str(artifact))
    conn.commit()
    conn.close()
    return artifact, db_path


def _seed_promotion_authority_certificates(conn, *, ensure_decision_certificate_tables):
    ensure_decision_certificate_tables(conn)
    rows = (
        (
            "actionable-cert-1",
            "ActionableTradeCertificate",
            "actionable-hash-1",
            {
                "q_live": 0.45,
                "expected_edge": 0.029,
                "condition_id": "condition-1",
                "token_id": "token-1",
                "side": "BUY",
                "direction": "YES",
                "native_token_side": "YES",
                "order_policy": "maker_post_only",
            },
        ),
        (
            "cost-cert-1",
            "ExecutableCostCertificate",
            "cost-hash-1",
            {
                "expected_cost_basis": 0.421,
                "expected_fee": 0.001,
                "expected_spread_cost": 0.0005,
                "visible_depth_fill_lcb": 0.95,
                "order_policy": "maker_post_only",
                "native_token_side": "YES",
                "condition_id": "condition-1",
                "token_id": "token-1",
                "side": "BUY",
                "direction": "YES",
            },
        ),
    )
    for certificate_id, certificate_type, certificate_hash, payload in rows:
        conn.execute(
            """
            INSERT OR REPLACE INTO decision_certificates (
                certificate_id, certificate_type, schema_version,
                canonicalization_version, semantic_key, claim_type, mode,
                decision_time, source_available_at, agent_received_at,
                persisted_at, max_parent_source_available_at,
                max_parent_agent_received_at, max_parent_persisted_at,
                authority_id, authority_version, algorithm_id, algorithm_version,
                config_hash, model_version_hash, payload_json, payload_hash,
                certificate_hash, verifier_status, created_at
            ) VALUES (
                ?, ?, 1, 'canonical-json-v1', ?, 'edli_live_profit_authority', 'LIVE',
                '2026-05-26T12:00:00+00:00', NULL, NULL, NULL, NULL, NULL, NULL,
                'test_authority', 'v1', 'test_algorithm', 'v1',
                NULL, NULL, ?, ?, ?, 'VERIFIED', '2026-05-26T12:00:00+00:00'
            )
            """,
            (
                certificate_id,
                certificate_type,
                certificate_id,
                json.dumps(payload, sort_keys=True),
                f"payload-{certificate_id}",
                certificate_hash,
            ),
        )


def _pre_submit_payload_for_promotion(**overrides):
    payload = {
        "event_id": "event-1",
        "final_intent_id": "intent-1",
        "condition_id": "condition-1",
        "token_id": "token-1",
        "side": "BUY",
        "direction": "YES",
        "order_type": "LIMIT",
        "time_in_force": "GTC",
        "post_only": True,
        "checked_at": "2026-05-26T12:00:00+00:00",
        "quote_seen_at": "2026-05-26T11:59:59.900000+00:00",
        "quote_age_ms": 100,
        "max_quote_age_ms": 1000,
        "book_hash": "book-hash-1",
        "current_best_bid": 0.42,
        "current_best_ask": 0.43,
        "limit_price": 0.42,
        "q_live": 0.45,
        "expected_cost_basis": 0.421,
        "expected_fee": 0.001,
        "expected_spread_cost": 0.0005,
        "visible_depth_fill_lcb": 0.95,
        "order_policy": "maker_post_only",
        "native_token_side": "YES",
        "would_cross_book": False,
        "tick_size": 0.01,
        "tick_aligned": True,
        "min_order_size": 5.0,
        "size_ok": True,
        "neg_risk": False,
        "heartbeat_status": "OK",
        "user_ws_status": "OK",
        "venue_connectivity_status": "OK",
        "balance_allowance_status": "OK",
        "book_authority_id": "execution_feasibility_evidence",
        "book_captured_at": "2026-05-26T11:59:59.900000+00:00",
        "heartbeat_authority_id": "heartbeat_supervisor",
        "heartbeat_checked_at": "2026-05-26T12:00:00+00:00",
        "user_ws_authority_id": "authenticated_user_channel",
        "user_ws_checked_at": "2026-05-26T12:00:00+00:00",
        "venue_connectivity_authority_id": "polymarket_preflight",
        "venue_connectivity_checked_at": "2026-05-26T12:00:00+00:00",
        "balance_allowance_authority_id": "polymarket_wallet_readonly",
        "balance_allowance_checked_at": "2026-05-26T12:00:00+00:00",
        "expected_edge_source_certificate_hash": "actionable-hash-1",
        "cost_basis_source_certificate_hash": "cost-hash-1",
    }
    payload.update(overrides)
    return payload


def _stage_conn(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _init_stage_world_db(path) -> sqlite3.Connection:
    from src.state.schema.edli_live_order_events_schema import ensure_tables as ensure_live_order_tables
    from src.state.schema.edli_live_cap_usage_schema import ensure_table as ensure_live_cap_table

    conn = _stage_conn(path)
    ensure_live_order_tables(conn)
    ensure_live_cap_table(conn)
    conn.commit()
    return conn
