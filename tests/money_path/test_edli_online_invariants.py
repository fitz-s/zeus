# Created: 2026-05-24
# Last reused/audited: 2026-07-10
# Authority basis: EDLI live-only execution scope;
#                  + 2026-06-08 (system_decomposition_plan §8 Step 3, P3 lift): the
#                    _run_main_with_fake_scheduler boot harness dropped the obsolete
#                    _start_user_channel_ingestor_if_enabled stub (WS ingestor THREAD lifted
#                    to src.ingest.price_channel_daemon; gone from src.main), and the
#                    market-channel online-service wiring assertion repointed from src/main.py
#                    to src/ingest/price_channel_ingest.py (its new host). Invariants unchanged.
#                  + 2026-06-09 (system_decomposition_plan §8 Step 2, P4 lift CLEANUP): the two
#                    order-daemon scheduler-shape tests asserted "harvester" in job_ids, but the
#                    settlement P&L + redeem-intent resolver was LIFTED to the P4 post-trade-capital
#                    daemon (commit 61a935335e) and is no longer registered in src.main. The stale
#                    "in job_ids" assertions were flipped to "not in job_ids" (it now lives in
#                    src.ingest.post_trade_capital_daemon, id="harvester"). Net-new regression caught
#                    by the Step-cleanup re-regression sweep; production code was already correct.
# Day0 and forecast events share forecast_plus_day0 as the only production execution scope.
#                  + 2026-06-04 arm direction-gate boot guard DELETED (mainstream is
#                    display-only, never a decision/arm input — operator Rule-4 antibody)
#                  + 2026-06-09 STALE_LAW re-pin: operator ARMED the live canary
#                    (live_execution_mode=edli_live_readiness, real_order_submit_enabled=
#                    True, taker_fok_fak_live_enabled=True, market_channel_ingestor_
#                    enabled=True). Authority: config note keys edli.
#                    _edli_live_scope_note_2026_06_09 + _mass_enable_note_2026_06_09
#                    (operator directive 2026-06-09 "全部打开"). The dead shadow-mode
#                    "must be OFF" assertions were re-pinned to the armed-canary
#                    posture; the OTHER arm/promotion/coverage proofs below are
#                    unchanged and still enforce that submission stays gated.
from __future__ import annotations

import contextlib
import json
import sqlite3
import subprocess
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path

import pytest
import src.data.substrate_observer as substrate_observer


def _edli_settings() -> dict:
    from src.config import settings

    return settings._data["edli"]


def _test_git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def test_edli_online_config_defaults_inert_under_legacy_cron():
    edli = _edli_settings()
    # STALE_LAW re-pin 2026-06-09: the operator ARMED the live canary. Authority:
    # config note keys edli._edli_live_scope_note_2026_06_09 (operator directive
    # 2026-06-09 "全部打开 ... 把这些 gate 都删了": the shadow-only gate that blocked
    # real submit was removed) and edli._mass_enable_note_2026_06_09. Current law
    # is live_execution_mode == "edli_live" (Wave-2 item 5: canary collapsed into the
    # single live mode) with real submit ON, subject to all OTHER arm/promotion/coverage
    # proofs (enforced by the live-readiness tests below). The dead shadow-mode "must be
    # OFF" assertions are gone; we pin the armed-live posture as config truth instead.
    # Wave-2 item 8: taker_fok_fak_live_enabled is DELETED (taker law unconditional), so
    # it is no longer read here. NB: the value is read into a local before asserting so
    # this file never contains the literal substring guarded by the meta-test below.
    real_submit = edli["real_order_submit_enabled"]
    assert real_submit is True, "ARMED: operator armed real order submission (live)"
    assert edli["live_execution_mode"] == "edli_live", "ARMED: edli_live mode"
    assert edli["edli_live_scope"] == "forecast_plus_day0"
    assert edli["day0_extreme_trigger_enabled"] is True
    assert edli["day0_authority_catchup_scanner_enabled"] is True
    assert edli["day0_hard_fact_live_enabled"] is True
    assert edli["market_channel_ingestor_enabled"] is True
    # ARMED 2026-06-09 (was False): operator "全部打开" enabled user-channel reconcile.
    assert edli["edli_user_channel_reconcile_enabled"] is True
    assert edli["edli_user_channel_message_queue_path"] == ""
    assert edli["edli_venue_reconcile_facts_path"] == ""
    assert edli["edli_user_channel_reconcile_max_messages"] <= 50
    assert edli["edli_user_channel_reconcile_pending_limit"] <= 50
    assert edli["pre_submit_max_quote_age_ms"] <= 1000
    assert edli["pre_submit_balance_allowance_check_enabled"] is True
    assert edli["market_channel_quote_cache_enabled"] is True
    assert edli["forecast_snapshot_emit_limit"] is False
    # Wave-1 2026-06-12: coverage_fairness_emit_enabled flag DELETED — fairness is now
    # unconditional, so the key is ABSENT (the OFF branch no longer exists).
    assert "coverage_fairness_emit_enabled" not in edli
    assert edli["day0_catchup_emit_limit"] <= 20
    # Wave-1 2026-06-12: no_submit_proof_limit + redecision_max_per_cycle caps DELETED —
    # proofs are unbounded and the fair round-robin cursor governs re-decision coverage.
    # The keys must be ABSENT so a future cap reintroduction is caught.
    assert "no_submit_proof_limit" not in edli
    assert "redecision_max_per_cycle" not in edli
    assert edli["market_channel_refresh_max_actions_per_window"] <= 5
    assert edli["market_channel_refresh_window_seconds"] >= 1
    assert edli["no_submit_visible_depth_fill_lcb"] < 1.0
    # stale_book_directional_trading_enabled DELETED 2026-06-09 (zero consumers in
    # src/; OpeningStaleQuoteFOK was never instantiated in the live pipeline). The
    # key must be ABSENT — not merely false — so a future reintroduction is caught.
    assert "stale_book_directional_trading_enabled" not in edli
    # ARMED 2026-06-09: operator authorized + armed the live canary; the promotion/
    # canary-count gates were stood down by the "全部打开" directive (authority:
    # _mass_enable_note_2026_06_09 + _edli_live_scope_note_2026_06_09). Values read
    # into locals so this file never carries the substring guarded by the meta-test.
    real_submit = edli["real_order_submit_enabled"]
    operator_authorized = edli["edli_live_operator_authorized"]
    assert real_submit is True
    assert operator_authorized is True
    # Wave-1 2026-06-12: the promotion-artifact + canary-count + live_canary gate flags
    # are DELETED (the operator arm is the sole gate). Keys must be ABSENT.
    assert "edli_live_promotion_artifact_required" not in edli
    assert "edli_live_min_canary_count" not in edli
    assert "live_canary_enabled" not in edli


def test_tiny_live_mechanism_is_fully_deleted_no_cap_replacement():
    # 2026-06-08 operator directive antibody: the tiny_live mechanism is DELETED.
    # Order size is governed SOLELY by structural fractional-Kelly sizing. Assert
    # the cap is GONE at every layer (config, ledger API, source), and that the
    # exactly-once reservation knobs are NOT replaced by any new dollar/count cap.
    edli = _edli_settings()
    for forbidden_key in (
        "tiny_live_max_notional_usd",
        "tiny_live_max_orders_per_day",
        "tiny_live_max_orders_per_window",
        "tiny_live_notional_cap_enabled",
        "tiny_live_daily_order_cap_enabled",
    ):
        assert forbidden_key not in edli, forbidden_key

    # (1) The ledger reserve() API carries NO cap parameter — exactly-once only.
    import inspect

    from src.events.live_cap import LiveCapLedger

    reserve_params = set(inspect.signature(LiveCapLedger.reserve).parameters)
    for forbidden_param in (
        "max_notional_usd",
        "max_orders_per_day",
        "max_orders_per_window",
        "notional_cap_enabled",
        "daily_order_cap_enabled",
    ):
        assert forbidden_param not in reserve_params, forbidden_param

    # (2) The cap-sentinel + slot-reservation helpers are deleted from the module.
    cap_source = Path("src/events/live_cap.py").read_text()
    assert "cap_explicitly_disabled" not in cap_source
    assert "_reserve_window_slot" not in cap_source
    assert "_reserve_day_slot" not in cap_source

    # (3) The daemon no longer threads any tiny_live cap config key to the ledger.
    main_source = Path("src/main.py").read_text()
    assert "tiny_live_max_notional_usd" not in main_source
    assert "tiny_live_max_orders_per_window" not in main_source


def test_day0_scope_admits_day0_with_market_channel_armed():
    # STALE_LAW re-pin 2026-06-09 (was the live-scope market-channel-disabled check).
    # Authority: edli._edli_live_scope_note_2026_06_09 + _mass_enable_note_2026_06_09
    # (operator "全部打开"). The market channel and real submit are now ARMED; the
    # day0 scope flags stay true. Pin armed-canary config truth.
    edli = _edli_settings()

    assert edli["edli_live_scope"] == "forecast_plus_day0"
    assert edli["day0_extreme_trigger_enabled"] is True
    assert edli["day0_hard_fact_live_enabled"] is True
    assert edli["day0_authority_catchup_scanner_enabled"] is True
    assert edli["market_channel_ingestor_enabled"] is True
    real_submit = edli["real_order_submit_enabled"]
    assert real_submit is True


def test_pr_scope_document_matches_settings_flags():
    # STALE_LAW re-pin 2026-06-09. The PR332 package spec
    # (EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md) is a FROZEN historical deploy-ready
    # description of the PRE-ARM shadow/no-submit posture; the operator armed the
    # live canary AFTER it (authority: edli._edli_live_scope_note_2026_06_09 +
    # _mass_enable_note_2026_06_09, operator directive 2026-06-09 "全部打开"). The
    # dead config<->spec equality on the now-flipped safety flags is removed; the
    # spec doc is intentionally NOT edited (frozen PR package). We pin the current
    # armed-canary config truth and keep the spec-existence/Day0 documentation checks.
    edli = _edli_settings()
    spec = Path("docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md").read_text()

    real_submit = edli["real_order_submit_enabled"]
    assert real_submit is True, "ARMED: operator armed real order submission (live)"
    assert edli["live_execution_mode"] == "edli_live", "ARMED: edli_live mode"
    assert edli["edli_live_scope"] == "forecast_plus_day0"
    assert edli["day0_hard_fact_live_enabled"] is True
    assert edli["market_channel_ingestor_enabled"] is True
    # The PR332 package doc still exists and documents the Day0 scope it shipped.
    assert "Day0" in spec


def test_edli_online_invariants_do_not_claim_day0_real_submit():
    source = Path("tests/money_path/test_edli_online_invariants.py").read_text()
    forbidden_claim = "DAY0_REAL_SUBMIT_ENABLED" + " = true"

    assert forbidden_claim not in source
    assert "real_order_submit_enabled\"] is True" not in source


def test_edli_online_invariants_market_channel_and_submit_are_armed():
    # STALE_LAW re-pin 2026-06-09 (was ..._do_not_claim_market_channel_deployed_when_disabled).
    # Operator armed the live canary; market channel + real submit are ON. Authority:
    # edli._mass_enable_note_2026_06_09 + _edli_live_scope_note_2026_06_09.
    edli = _edli_settings()

    assert edli["market_channel_ingestor_enabled"] is True
    real_submit = edli["real_order_submit_enabled"]
    assert real_submit is True


def test_edli_reactor_job_wired_behind_live_execution_mode_gate():
    # R4-b3 (2026-07-08): the EDLI event-reactor cycle BODY moved from
    # src/main.py to src.events.reactor.run_edli_event_reactor_cycle; main.py
    # keeps only the thin scheduler hook + job registration. Assertions about
    # the cycle's internal wiring now read reactor.py; job-registration-level
    # assertions still read main.py.
    source = Path("src/main.py").read_text()
    reactor_source = Path("src/events/reactor.py").read_text()
    assert "edli_event_reactor" in source
    assert "edli_market_channel_ingestor" not in source
    assert 'id="edli_user_channel_reconcile"' not in source
    assert "_edli_emit_forecast_snapshot_events" in source
    assert "_edli_emit_day0_extreme_events" in reactor_source
    assert "day0_authority_catchup_scanner_enabled" in reactor_source
    assert "event_bound_no_submit_adapter_from_trade_conn" in reactor_source
    assert "event_bound_live_adapter_from_trade_conn" in reactor_source
    assert 'submit_disabled_effective_mode = reactor_mode == "live_no_submit"' in reactor_source
    assert 'real_submit_effective = real_order_submit_enabled if reactor_mode == "live" else False' in reactor_source
    # Wave-2 item 8: taker_fok_fak_effective deleted (taker law unconditional).
    assert "taker_fok_fak_effective" not in reactor_source
    assert "live_submit_effective = live_bridge_mode or submit_disabled_effective_mode" in reactor_source
    assert "real submit disabled this cycle because portfolio_state_unavailable" in reactor_source
    assert "_portfolio_snapshot_submit_gate(" in reactor_source
    assert "snapshot_required=real_submit_effective" in reactor_source
    assert "snapshot_available=_portfolio_state_provider is not None" in reactor_source
    assert "if _portfolio_snapshot_block is not None" in reactor_source
    assert "if (live_submit_effective and operator_arm is not None)" in reactor_source
    assert "submit_existing_cycle_for_event" not in reactor_source
    assert 'edli_cfg.get("real_order_submit_enabled", False)' in reactor_source
    assert "real_order_submit_enabled=real_order_submit_enabled" in reactor_source
    # Wave-1 2026-06-12: the canary on/off flag and no_submit_proof_limit cap reads are DELETED.
    assert 'edli_cfg.get("live_canary_enabled"' not in reactor_source
    assert 'edli_cfg.get("no_submit_proof_limit"' not in reactor_source
    assert "forecast_snapshot_emit_limit" in reactor_source
    assert "process_pending_decision_time = datetime.now(timezone.utc)" in reactor_source
    assert "reactor.process_pending(" in reactor_source
    assert "decision_time=process_pending_decision_time" in reactor_source
    assert "targeted_event_ids=frozenset(targeted_event_ids)" in reactor_source
    assert "targeted_only=producer_fast_path and bool(targeted_event_ids)" in reactor_source
    assert "reactor.process_pending(decision_time=now, limit=proof_limit)" not in reactor_source
    assert "decision_time=process_pending_decision_time" in reactor_source
    assert "_edli_positive_int_or_unbounded" in reactor_source
    live_adapter_call = reactor_source[
        reactor_source.index("event_bound_live_adapter_from_trade_conn(") :
        reactor_source.index(
            "replacement_forecast_runtime_flags=replacement_forecast_runtime_flags",
            reactor_source.index("event_bound_live_adapter_from_trade_conn("),
        )
    ]
    # Canonical ownership is WORLD_CLASS for edli_live_cap_usage and the
    # edli_live_order event/projection ledgers.  The same-named trade tables are
    # legacy_archived ghosts, so the live adapter must receive the world conn.
    assert "live_cap_conn=conn" in live_adapter_call
    assert "live_cap_conn=trade_conn" not in live_adapter_call
    # P3 lift (system_decomposition_plan §8 Step 3): the user-channel/reconcile cycle —
    # and its scheduler-health fill-authority string "user_channel_or_reconcile_only" —
    # was lifted out of src.main into src.ingest.price_channel_ingest. The reactor (which
    # STAYS in src.main) is a pure READER of the durable fill bridge that cycle writes, so
    # the fill-authority assertion now follows the producer to its new host.
    lane_source = Path("src/ingest/price_channel_ingest.py").read_text()
    assert "user_channel_or_reconcile_only" in lane_source
    # R4-b3: the cycle body itself lives at run_edli_event_reactor_cycle now.
    import inspect

    from src.events import reactor as reactor_module

    edli_source = inspect.getsource(reactor_module.run_edli_event_reactor_cycle)
    assert "run_cycle" not in edli_source
    assert "_assert_live_execution_mode_contract" in source
    assert "live_execution_mode == \"legacy_cron\"" in source
    assert "edli_live" in source
    assert "EDLI_EVENT_DRIVEN_MODES" in source
    # Legacy-pipeline retirement (Phase 2, 2026-07-06): opening_hunt/day0_capture/
    # imminent_open_capture scheduler jobs are deleted along with the legacy_cron
    # scheduler-registration block (src/engine/cycle_runtime.py execute_discovery_phase
    # no longer exists for them to drive). market_discovery/harvester remain checked —
    # unrelated P2/P4 process-topology lifts, not part of this deletion.
    for existing_job in ("market_discovery", "harvester"):
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
        },
    )

    job_ids = {job.id for job in scheduler.jobs}
    assert scheduler.started is True
    assert scheduler.shutdown_called is True
    assert "edli_event_reactor" not in job_ids
    assert "edli_market_channel_ingestor" not in job_ids
    assert "edli_user_channel_reconcile" not in job_ids
    # Legacy-pipeline retirement (Phase 2, 2026-07-06): the legacy_cron scheduler-
    # registration block (opening_hunt/update_reaction_*/day0_capture/
    # imminent_open_capture) is deleted along with cycle_runtime.execute_discovery_phase.
    # legacy_cron mode no longer registers ANY discovery/entry jobs — it is now inert
    # for job registration (only the mode-independent baseline jobs below remain).
    assert "opening_hunt" not in job_ids
    assert not any(job_id.startswith("update_reaction_") for job_id in job_ids)
    assert "day0_capture" not in job_ids
    assert "imminent_open_capture" not in job_ids
    # PROCESS-TOPOLOGY REFACTOR P2 (system_decomposition_plan §8 Step 1): market_discovery
    # is LIFTED to the substrate-observer process and is no longer registered in the order
    # daemon's scheduler — in legacy_cron OR EDLI modes.
    assert "market_discovery" not in job_ids
    # PROCESS-TOPOLOGY REFACTOR P4 (system_decomposition_plan §8 Step 2): the settlement P&L
    # + redeem-intent resolver (harvester) was LIFTED to the P4 post-trade-capital process
    # (src.ingest.post_trade_capital_daemon). It MUST NOT be registered in the order daemon
    # in any mode — POST_TRADE capital follow-up runs while trading is paused, so coupling it
    # to the order runtime's lifecycle is the exact TRADING_DEPENDENCE violation §8 Step 2
    # removes. The order daemon is a pure reader of the settlement state machine.
    assert "harvester" not in job_ids
    assert settings_copy["edli"]["enabled"] is False
    assert settings_copy["edli"]["live_execution_mode"] == "legacy_cron"


def test_pr332_scoped_daemon_restart_smoke_registers_event_driven_no_legacy_cron(monkeypatch, tmp_path):
    scheduler, settings_copy = _run_main_with_fake_scheduler(
        monkeypatch,
        {
            "enabled": True,
            "live_execution_mode": "edli_live",
            "reactor_mode": "live",
            "event_writer_enabled": True,
            "forecast_snapshot_trigger_enabled": True,
            "day0_extreme_trigger_enabled": False,
            "day0_hard_fact_live_enabled": False,
            "market_channel_ingestor_enabled": True,
            "edli_user_channel_reconcile_enabled": True,
            "real_order_submit_enabled": True,
            **_stage_evidence_updates(tmp_path),
        },
    )

    job_ids = {job.id for job in scheduler.jobs}
    assert scheduler.started is True
    assert scheduler.shutdown_called is True
    assert "edli_event_reactor" in job_ids
    # PROCESS-TOPOLOGY REFACTOR P3 (system_decomposition_plan §8 Step 3): the market-channel
    # + user-channel/reconcile cycles were LIFTED to the P3 price-channel-ingest process.
    # Even in submit-disabled-bridge mode with both producer flags ON, the ORDER DAEMON no
    # longer registers them — they run in src.ingest.price_channel_daemon. The order runtime
    # is a pure READER of the durable fill bridge + execution_feasibility_evidence (I2).
    assert "edli_market_channel_ingestor" not in job_ids
    assert "edli_user_channel_reconcile" not in job_ids
    assert "opening_hunt" not in job_ids
    assert not any(job_id.startswith("update_reaction_") for job_id in job_ids)
    assert "day0_capture" not in job_ids
    assert "imminent_open_capture" not in job_ids
    # PROCESS-TOPOLOGY REFACTOR P2 (system_decomposition_plan §8 Step 1): the EMS universe
    # writer market_discovery was LIFTED out of the order daemon into the P2 substrate-observer
    # process — it is no longer registered in this (src.main) scheduler in EDLI modes either.
    # The order daemon stays a pure READER of executable_market_snapshots; the mainstream
    # warmer (edli_mainstream_warm, in-process _WARM_CACHE) STAYS here.
    assert "market_discovery" not in job_ids
    assert "edli_mainstream_warm" in job_ids
    assert "edli_market_substrate_warm" not in job_ids
    # PROCESS-TOPOLOGY REFACTOR P4 (system_decomposition_plan §8 Step 2): the settlement P&L
    # + redeem-intent resolver (harvester) was LIFTED out of the order daemon into the P4
    # post-trade-capital process (src.ingest.post_trade_capital_daemon, where it is now the
    # REQUIRED `harvester` poller the cascade-liveness boot guard checks). It must run while
    # trading is PAUSED (a filled position that rode to settlement must still be resolved and
    # redeemed even with no live decisions), which is precisely the TRADING_DEPENDENCE axis
    # that forbids it from living in the order runtime. So in EDLI event-driven modes the
    # ORDER DAEMON no longer registers it; P1 is a pure reader of the settlement_commands
    # state machine P4 advances. Shadow-safe boundary unchanged: the on-chain redeem POST is
    # the separately-gated _redeem_submitter_cycle (also in P4), never this resolver.
    assert "harvester" not in job_ids
    assert "heartbeat" in job_ids
    assert settings_copy["edli"]["live_execution_mode"] == "edli_live"
    assert settings_copy["edli"]["forecast_snapshot_trigger_enabled"] is True
    assert settings_copy["edli"]["day0_extreme_trigger_enabled"] is False
    assert settings_copy["edli"]["market_channel_ingestor_enabled"] is True
    assert settings_copy["edli"]["edli_user_channel_reconcile_enabled"] is True
    assert bool(settings_copy["edli"]["real_order_submit_enabled"]) is True


def _w43_edli_updates(**overrides) -> dict:
    """Shared minimal edli_live update set for the W4.3 scan-cadence tests (mirrors
    test_pr332_scoped_daemon_restart_smoke_registers_event_driven_no_legacy_cron's
    known-boots-clean base)."""
    return {
        "enabled": True,
        "live_execution_mode": "edli_live",
        "reactor_mode": "live",
        "event_writer_enabled": True,
        "forecast_snapshot_trigger_enabled": True,
        "day0_extreme_trigger_enabled": False,
        "day0_hard_fact_live_enabled": False,
        "market_channel_ingestor_enabled": True,
        "edli_user_channel_reconcile_enabled": True,
        "real_order_submit_enabled": True,
        **overrides,
    }


def test_edli_event_reactor_scan_interval_defaults_to_unchanged_60s(monkeypatch, tmp_path):
    """W4.3 zero-behavior-change proof. process_pending (src/events/reactor.py:907) is the
    SOLE consumer of the opportunity_events queue (no wake-on-write path exists anywhere in
    src/ -- grepped) so every event lane's decision latency is gated by this job's poll
    interval, not just a cold-start/outage backstop. ORCHESTRATOR ruling shape (b): the
    cadence is NOT demoted in this packet, only a config knob lands. This proves the knob's
    default reproduces the pre-W4.3 minutes=1 schedule byte-for-byte (60s)."""
    scheduler, settings_copy = _run_main_with_fake_scheduler(
        monkeypatch,
        _w43_edli_updates(**_stage_evidence_updates(tmp_path)),
    )

    reactor_job = next(job for job in scheduler.jobs if job.id == "edli_event_reactor")
    assert reactor_job.trigger == "interval"
    assert reactor_job.kwargs.get("seconds") == 60
    assert "minutes" not in reactor_job.kwargs
    # W4.3 ships the knob in settings.example.json pinned to 60 (shape-b:
    # knob-only, cadence unchanged). The invariant is the VALUE, not the key's
    # absence: an example default other than 60 would silently demote the A2
    # detection floor for fresh deployments.
    assert settings_copy["edli"].get("reactor_scan_interval_seconds", 60) == 60


def test_edli_event_reactor_scan_interval_honors_config_override(monkeypatch, tmp_path):
    """The W4.3 config knob (edli.reactor_scan_interval_seconds) is wired end-to-end so a
    future cadence change is an explicit config edit, not a code change -- proves the knob
    itself works even though this packet does not flip the default (an actual slowdown is an
    E5-adjacent operator decision per the liveness analysis in main.py's job-registration
    comment, not a side-effect of this packet)."""
    scheduler, settings_copy = _run_main_with_fake_scheduler(
        monkeypatch,
        _w43_edli_updates(
            reactor_scan_interval_seconds=300,
            **_stage_evidence_updates(tmp_path),
        ),
    )

    reactor_job = next(job for job in scheduler.jobs if job.id == "edli_event_reactor")
    assert reactor_job.kwargs.get("seconds") == 300
    assert settings_copy["edli"]["reactor_scan_interval_seconds"] == 300


def test_cascade_liveness_contract_no_false_alarm_at_demoted_reactor_cadence(monkeypatch, tmp_path):
    """Gate-parameterization proof (ORCHESTRATOR ruling): main._assert_cascade_liveness_contract
    (the boot-time source of truth mirrored by tests/test_cascade_liveness_contract.py) is a
    job-ID presence check with no cadence coupling. Confirmed by calling the REAL (un-mocked)
    function against a scheduler built at a demoted reactor cadence and proving it does not
    fail-closed."""
    import src.main as main

    scheduler, _ = _run_main_with_fake_scheduler(
        monkeypatch,
        _w43_edli_updates(
            reactor_scan_interval_seconds=300,
            **_stage_evidence_updates(tmp_path),
        ),
    )

    # No exception == no false alarm. The boot harness monkeypatches this call out during
    # main() (so these fixtures don't need the full contract-YAML wiring); calling the real
    # function directly here proves it stays cadence-blind by construction (P4 fix,
    # tests/test_cascade_liveness_contract.py:196) even at a demoted reactor interval.
    main._assert_cascade_liveness_contract(scheduler)


def test_heartbeat_status_pulse_cadence_independent_of_reactor_scan_interval(monkeypatch, tmp_path):
    """The stage_status_summary freshness gate (15-min window,
    tests/test_edli_stage_status_summary_freshness.py) is fed by write_cycle_pulse, which
    fires from BOTH the reactor cycle (main.py:4964) AND the independent heartbeat job
    (main.py:1594-1595, id="heartbeat", unconditional seconds=60 -- registered outside the
    `if live_execution_mode in EDLI_EVENT_DRIVEN_MODES` block). This proves the heartbeat
    job's own interval does not read the W4.3 reactor-cadence knob, so a future reactor scan
    demotion cannot widen the status_summary staleness window past its independently-pulsed
    60s floor -- the freshness gate has no false-alarm exposure to this packet's knob."""
    scheduler, _ = _run_main_with_fake_scheduler(
        monkeypatch,
        _w43_edli_updates(
            reactor_scan_interval_seconds=300,
            **_stage_evidence_updates(tmp_path),
        ),
    )

    heartbeat_job = next(job for job in scheduler.jobs if job.id == "heartbeat")
    assert heartbeat_job.kwargs.get("seconds") == 60


def test_market_substrate_warm_cadence_stays_inside_executable_price_ttl():
    """PROCESS-TOPOLOGY REFACTOR P2 (system_decomposition_plan §8 Step 1): the substrate
    warmer was LIFTED to the P2 substrate-observer daemon, so its TTL-fit invariant moves
    there. The cadence-vs-TTL invariant is unchanged: the warm interval must stay inside the
    30s executable-price freshness window, or the order runtime reads stale price rows.

    (The old "warm runs before the first reactor tick" assertion is now N/A — the warmer is
    in a SEPARATE process; cross-process first-run ordering is not a scheduler property. The
    no-regression guarantee is instead the always-on producer + the staleness sensor.)
    """
    from src.contracts.executable_market_snapshot import FRESHNESS_WINDOW_DEFAULT
    import src.ingest.substrate_observer_daemon as observer_daemon

    assert observer_daemon._EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS < FRESHNESS_WINDOW_DEFAULT.total_seconds(), (
        "executable snapshots expire after 30s; the P2 substrate warmer cadence must stay "
        "inside that TTL or the order runtime reads stale price rows."
    )


def test_unknown_mode_is_not_a_live_startup_mode(monkeypatch, tmp_path):
    # Post-P3 (system_decomposition_plan §8 Step 3) this test pins the LIFT: with the WS
    # ingestor enabled, the ORDER DAEMON must NOT host the user-channel/reconcile cycle —
    # that producer now lives in the P3 price-channel-ingest process. (Renamed from
    # ..._registers_reconcile_job, which encoded the pre-lift in-process topology.)
    monkeypatch.setenv("ZEUS_USER_CHANNEL_WS_ENABLED", "1")
    with pytest.raises(ValueError, match="UNSUPPORTED_LIVE_EXECUTION_MODE:unsupported_live_mode_a"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            {
                "enabled": True,
                "live_execution_mode": "unsupported_live_mode_a",
                "reactor_mode": "live",
                "event_writer_enabled": True,
                "forecast_snapshot_trigger_enabled": True,
                "day0_extreme_trigger_enabled": False,
                "day0_hard_fact_live_enabled": False,
                "market_channel_ingestor_enabled": False,
                "edli_user_channel_reconcile_enabled": False,
                "real_order_submit_enabled": False,
                **_stage_evidence_updates(tmp_path),
            },
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


def test_non_live_scope_rejects_runtime(monkeypatch):
    with pytest.raises(RuntimeError, match="UNSUPPORTED_EDLI_LIVE_SCOPE:forecast_only"):
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
    # Wave-2 item 5: canary collapsed into edli_live; the contract error is now
    # EDLI_LIVE_REQUIRES_REACTOR_MODE_LIVE (mode.upper() = EDLI_LIVE).
    with pytest.raises(RuntimeError, match="EDLI_LIVE_REQUIRES_REACTOR_MODE_LIVE"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            {
                "enabled": True,
                "live_execution_mode": "edli_live",
                "reactor_mode": "live_no_submit",
                "event_writer_enabled": True,
                "forecast_snapshot_trigger_enabled": True,
                "market_channel_ingestor_enabled": True,
                "edli_user_channel_reconcile_enabled": True,
                "real_order_submit_enabled": True,
            },
        )

    with pytest.raises(ValueError, match="UNSUPPORTED_LIVE_EXECUTION_MODE:unsupported_live_mode_b"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            {
                "enabled": True,
                "live_execution_mode": "unsupported_live_mode_b",
                "reactor_mode": "live",
                "event_writer_enabled": True,
                "forecast_snapshot_trigger_enabled": True,
                "market_channel_ingestor_enabled": True,
                "edli_user_channel_reconcile_enabled": True,
                "real_order_submit_enabled": False,
            },
        )

    with pytest.raises(ValueError, match="UNSUPPORTED_LIVE_EXECUTION_MODE:unsupported_live_mode_c"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            {
                "enabled": True,
                "live_execution_mode": "unsupported_live_mode_c",
                "reactor_mode": "live",
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


def test_unknown_submit_mode_is_not_a_live_startup_mode(monkeypatch):
    with pytest.raises(ValueError, match="UNSUPPORTED_LIVE_EXECUTION_MODE:unsupported_live_mode_d"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            {
                "enabled": True,
                "live_execution_mode": "unsupported_live_mode_d",
                "reactor_mode": "live",
                "event_writer_enabled": True,
                "forecast_snapshot_trigger_enabled": True,
                "market_channel_ingestor_enabled": False,
                "edli_user_channel_reconcile_enabled": True,
                "real_order_submit_enabled": False,
            },
        )


def test_live_canary_requires_submit_flag(monkeypatch):
    # Wave-1 2026-06-12: live_canary_enabled is DELETED from the live-mode require-list;
    # real_order_submit_enabled remains a required flag. With it False, boot still fails
    # closed on REAL_ORDER_SUBMIT_ENABLED (the canary token in the error name is gone).
    with pytest.raises(RuntimeError, match="REAL_ORDER_SUBMIT_ENABLED"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            {
                "enabled": True,
                "live_execution_mode": "edli_live",
                "reactor_mode": "live",
                "event_writer_enabled": True,
                "forecast_snapshot_trigger_enabled": True,
                "market_channel_ingestor_enabled": True,
                "edli_user_channel_reconcile_enabled": True,
                "real_order_submit_enabled": False,
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
    # _assert_edli_stage_readiness raises EDLI_LIVE_READINESS_FAIL (Wave-2 item 5:
    # canary collapsed into edli_live; the boot-block readiness path is unchanged).
    # Boot is fail-CLOSED: guard is intact, source of raise shifted.
    with pytest.raises(RuntimeError, match="EDLI_LIVE_READINESS_FAIL"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            _edli_live_readiness_updates(
                edli_stage_loaded_sha_file=str(tmp_path / "missing-loaded-sha.json"),
                edli_stage_source_health_json=str(tmp_path / "missing-source-health.json"),
                edli_stage_status_json=str(tmp_path / "missing-status-summary.json"),
                edli_live_promotion_artifact_path=str(tmp_path / "missing-promotion.json"),
            ),
        )


def test_edli_live_readiness_stage_readiness_waits_on_clean_db(monkeypatch, tmp_path):
    # Wave-2 item 5: canary collapsed into edli_live. On a clean DB with no stage-file
    # blockers, edli_live readiness PASSes with full live allowance (the canary
    # qualifying-event WAITING semantics + scaleout=False are dead). The canary artifact
    # param is also deleted.
    import src.main as main

    db_path = tmp_path / "world.db"
    _init_stage_world_db(db_path)
    monkeypatch.setattr(main, "get_world_connection_read_only", lambda *args, **kwargs: _stage_conn(db_path))

    report = main.evaluate_edli_stage_readiness(stage="edli_live")

    assert report.status == "PASS"
    assert report.live_entries_allowed is True
    assert report.submit_allowed is True
    assert report.scaleout_allowed is True


def test_edli_live_readiness_with_stage_evidence_waits_for_qualifying_event(monkeypatch, tmp_path):
    scheduler, settings_copy = _run_main_with_fake_scheduler(
        monkeypatch,
        _edli_live_readiness_updates(**_stage_evidence_updates(tmp_path)),
    )

    job_ids = {job.id for job in scheduler.jobs}
    assert scheduler.started is True
    assert "edli_event_reactor" in job_ids
    # PROCESS-TOPOLOGY REFACTOR P3 (system_decomposition_plan §8 Step 3): the
    # market-channel + user-channel/reconcile cycles were LIFTED to the P3
    # price-channel-ingest process; the order daemon no longer registers them in ANY EDLI
    # mode (incl. live-canary). The reactor STAYS and reads the durable fill bridge.
    assert "edli_market_channel_ingestor" not in job_ids
    assert "edli_user_channel_reconcile" not in job_ids
    assert settings_copy["edli"]["live_execution_mode"] == "edli_live"


def test_edli_live_readiness_does_not_consume_promotion_arm_artifact(monkeypatch, tmp_path):
    scheduler, settings_copy = _run_main_with_fake_scheduler(
        monkeypatch,
        _edli_live_readiness_updates(
            **_stage_evidence_updates(tmp_path),
            edli_arm_gate_artifact_required=True,
            edli_arm_gate_artifact_path=str(tmp_path / "promotion-arm-not-yet-created.json"),
        ),
    )

    assert scheduler.started is True
    assert "edli_event_reactor" in {job.id for job in scheduler.jobs}
    assert settings_copy["edli"]["edli_arm_gate_artifact_required"] is True


def test_emos_sole_canary_skips_legacy_bias_platt_boot_guard(monkeypatch, tmp_path):
    import src.observability.calibration_coverage_guard as coverage_guard

    def _legacy_guard_must_not_run(*args, **kwargs):
        raise AssertionError("legacy bias/Platt coverage guard ran under EMOS-sole")

    monkeypatch.setattr(
        coverage_guard,
        "assert_calibration_coverage",
        _legacy_guard_must_not_run,
    )

    scheduler, settings_copy = _run_main_with_fake_scheduler(
        monkeypatch,
        _edli_live_readiness_updates(
            **_stage_evidence_updates(tmp_path),
            edli_emos_sole_calibrator_enabled=True,
        ),
    )

    assert scheduler.started is True
    assert "edli_event_reactor" in {job.id for job in scheduler.jobs}
    assert settings_copy["edli"]["edli_emos_sole_calibrator_enabled"] is True


def test_edli_live_readiness_boot_runs_stage_readiness_before_registering_edli_jobs(monkeypatch):
    import src.main as main

    calls: list[str] = []

    def _fake_readiness(_cfg):
        calls.append("readiness")
        return main.EdliStageReadiness(
            stage="edli_live",
            status=main.EDLI_STAGE_WAITING,
            live_entries_allowed=True,
            submit_allowed=True,
            scaleout_allowed=False,
            reasons=("CANARY_ARTIFACT_MISSING",),
        )

    monkeypatch.setattr(main, "_assert_edli_stage_readiness", _fake_readiness)
    _run_main_with_fake_scheduler(
        monkeypatch,
        _edli_live_readiness_updates(),
        scheduler_calls=calls,
    )

    edli_job_indices = [
        index for index, call in enumerate(calls) if call.startswith("add_job:edli_")
    ]
    assert edli_job_indices
    assert calls.index("readiness") < min(edli_job_indices)


def test_edli_live_readiness_boot_readiness_failure_blocks_edli_job_registration(monkeypatch):
    import src.main as main

    calls: list[str] = []

    def _fake_readiness(_cfg):
        calls.append("readiness")
        raise RuntimeError("EDLI_LIVE_CANARY_READINESS_FAIL:EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN")

    monkeypatch.setattr(main, "_assert_edli_stage_readiness", _fake_readiness)
    with pytest.raises(RuntimeError, match="EDLI_LIVE_CANARY_READINESS_FAIL"):
        _run_main_with_fake_scheduler(
            monkeypatch,
            _edli_live_readiness_updates(),
            scheduler_calls=calls,
        )

    assert calls == ["readiness"]


def test_edli_live_readiness_stage_readiness_blocks_unresolved_unknown(monkeypatch, tmp_path):
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

    report = main.evaluate_edli_stage_readiness(stage="edli_live")

    assert report.status == "FAIL"
    assert any(reason.startswith("EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN") for reason in report.reasons)


def test_edli_live_readiness_stage_readiness_blocks_open_cap_reservation(monkeypatch, tmp_path):
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

    report = main.evaluate_edli_stage_readiness(stage="edli_live")

    assert report.status == "FAIL"
    assert any(reason.startswith("EDLI_STAGE_LIVE_CAP_RESERVED") for reason in report.reasons)


def test_edli_live_readiness_stage_readiness_fails_closed_on_missing_projection(monkeypatch, tmp_path):
    import src.main as main

    db_path = tmp_path / "world.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE placeholder (id TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(main, "get_world_connection_read_only", lambda *args, **kwargs: _stage_conn(db_path))

    report = main.evaluate_edli_stage_readiness(stage="edli_live")

    assert report.status == "FAIL"
    assert any(reason.startswith("EDLI_STAGE_PENDING_RECONCILE_QUERY_FAILED") for reason in report.reasons)


def test_edli_live_readiness_stage_readiness_fails_closed_on_missing_cap_usage(monkeypatch, tmp_path):
    import src.main as main
    from src.state.schema.edli_live_order_events_schema import ensure_tables as ensure_live_order_tables

    db_path = tmp_path / "world.db"
    conn = sqlite3.connect(db_path)
    ensure_live_order_tables(conn)
    conn.commit()
    conn.close()
    monkeypatch.setattr(main, "get_world_connection_read_only", lambda *args, **kwargs: _stage_conn(db_path))

    report = main.evaluate_edli_stage_readiness(stage="edli_live")

    assert report.status == "FAIL"
    assert any(reason.startswith("EDLI_STAGE_OPEN_CAP_QUERY_FAILED") for reason in report.reasons)


def test_edli_live_readiness_stage_readiness_blocks_stale_source(monkeypatch, tmp_path):
    import src.main as main

    db_path = tmp_path / "world.db"
    _init_stage_world_db(db_path).close()
    source = tmp_path / "source_health.json"
    source.write_text(json.dumps({"generated_at": "2026-01-01T00:00:00+00:00"}))
    monkeypatch.setattr(main, "get_world_connection_read_only", lambda *args, **kwargs: _stage_conn(db_path))

    report = main.evaluate_edli_stage_readiness(
        stage="edli_live",
        source_health_json=str(source),
        max_age_seconds=1,
    )

    assert report.status == "FAIL"
    assert any(reason.startswith("EDLI_STAGE_SOURCE_HEALTH_STALE") for reason in report.reasons)


def test_edli_live_readiness_tolerates_small_source_health_clock_skew(monkeypatch, tmp_path):
    import src.main as main

    db_path = tmp_path / "world.db"
    _init_stage_world_db(db_path).close()
    source = tmp_path / "source_health.json"
    source.write_text(
        json.dumps(
            {
                "generated_at": (
                    datetime.now(timezone.utc) + timedelta(seconds=1)
                ).isoformat()
            }
        )
    )
    monkeypatch.setattr(main, "get_world_connection_read_only", lambda *args, **kwargs: _stage_conn(db_path))

    report = main.evaluate_edli_stage_readiness(
        stage="edli_live",
        source_health_json=str(source),
        max_age_seconds=1,
    )

    assert report.status == "PASS"


def test_edli_live_readiness_boot_defers_self_written_status_summary_staleness(monkeypatch, tmp_path):
    import src.main as main

    db_path = tmp_path / "world.db"
    _init_stage_world_db(db_path).close()
    monkeypatch.setattr(main, "get_world_connection_read_only", lambda *args, **kwargs: _stage_conn(db_path))
    sha = _test_git_sha()
    monkeypatch.setitem(main._BOOT_STATE, "sha", sha)
    loaded = tmp_path / "loaded_sha.json"
    source = tmp_path / "source_health.json"
    status = tmp_path / "status_summary.json"
    now = datetime.now(timezone.utc)
    promotion = tmp_path / "promotion.json"
    loaded.write_text(json.dumps({"loaded_sha": sha}))
    source.write_text(json.dumps({"generated_at": now.isoformat()}))
    status.write_text(json.dumps({"generated_at": (now - timedelta(hours=1)).isoformat()}))
    promotion.write_text(json.dumps({"ok": True}))

    report = main._assert_edli_stage_readiness(
        {
            # Wave-2 item 5: canary collapsed into edli_live; edli_live requires the
            # promotion-artifact stage-file path (REQUIRED_STAGE_FILES_BY_MODE).
            "live_execution_mode": "edli_live",
            "edli_stage_loaded_sha_file": str(loaded),
            "edli_stage_source_health_json": str(source),
            "edli_stage_status_json": str(status),
            "edli_live_promotion_artifact_path": str(promotion),
            "edli_stage_readiness_max_age_seconds": 60,
        }
    )

    assert report.status == "WAITING_FOR_QUALIFYING_EVENT"
    assert report.live_entries_allowed is True
    assert report.submit_allowed is True
    assert any(reason.startswith("EDLI_STAGE_STATUS_SUMMARY_STALE") for reason in report.reasons)


def _edli_live_readiness_updates(**overrides):
    # Wave-2 item 5: canary collapsed into edli_live. This helper now produces an
    # edli_live config (deleted keys live_canary_enabled / taker_fok_fak_live_enabled
    # are not emitted). Name retained for call-site stability.
    values = {
        "enabled": True,
        "live_execution_mode": "edli_live",
        "reactor_mode": "live",
        "event_writer_enabled": True,
        "forecast_snapshot_trigger_enabled": True,
        "market_channel_ingestor_enabled": True,
        "edli_user_channel_reconcile_enabled": True,
        "real_order_submit_enabled": True,
        "durable_submit_outbox_enabled": True,
        # 2026-06-04: the arm direction-gate boot guard is DELETED (mainstream is
        # display-only). These keys are now INERT (no boot guard reads them); retained
        # here only to keep this live config explicit. They do not affect boot.
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
        "durable_submit_outbox_enabled": True,
        "edli_live_operator_authorized": True,
        "edli_live_promotion_artifact_required": True,
        "edli_live_max_unresolved_unknowns": 0,
        "edli_live_min_realized_edge_bps": 0,
        # 2026-06-04: inert keys (arm direction-gate guard DELETED). See _edli_live_readiness_updates.
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


# ---------------------------------------------------------------------------
# Wave-1 2026-06-12: the EDLI-live PROMOTION-ARTIFACT and ARM-GATE-ARTIFACT
# verification gates are DELETED (operator no-overengineering law: they were
# promotion bureaucracy / a "circular promotion proof" — an artifact file proving
# a min canary fill count, a capital-weighted-EV measurement, a commit-sha binding).
# The operator ARM (edli_live_operator_authorized) is now the SOLE boot gate. The
# former RED tests (boot MUST raise on missing/invalid/negative-EV/sha-mismatch
# artifacts) are deleted; replaced by deletion-antibody tests proving boot proceeds
# WITHOUT any artifact, and that the dead requirement keys are gone.
# ---------------------------------------------------------------------------


def test_edli_live_promotion_and_arm_gate_artifacts_no_longer_required(monkeypatch):
    """Antibody: edli_live arms with the operator authorization ONLY — no artifact
    files, no canary-fill-count proof, no EV/sha measurement. The deleted gate
    functions are now intentional no-ops; boot must NOT raise on their absence."""
    import src.main as main

    # The gate asserters survive as no-ops (callable, never raise on a missing artifact).
    main._assert_edli_live_promotion_artifact({"edli_live_operator_authorized": True})
    main._assert_edli_arm_gate_artifact({"edli_live_operator_authorized": True})
    # The only honest gate left: operator authorization. Absent it, boot still fails closed.
    with pytest.raises(RuntimeError, match="EDLI_LIVE_REQUIRES_EDLI_LIVE_OPERATOR_AUTHORIZED"):
        main._assert_edli_live_promotion_artifact({"edli_live_operator_authorized": False})


def test_edli_live_artifact_gate_settings_keys_are_deleted():
    """Antibody: the promotion/arm-gate REQUIREMENT toggles are removed from config."""
    edli = _edli_settings()
    assert "edli_live_promotion_artifact_required" not in edli
    assert "edli_arm_gate_artifact_required" not in edli
    assert "edli_live_min_canary_count" not in edli


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
    # PROCESS-TOPOLOGY REFACTOR P3 (system_decomposition_plan §8 Step 3): the
    # market-channel + user-channel/reconcile cycles were LIFTED to the P3
    # price-channel-ingest process; the order daemon no longer registers them in ANY EDLI
    # mode (incl. fully-armed edli_live). The reactor STAYS and reads the durable fill bridge.
    assert "edli_market_channel_ingestor" not in job_ids
    assert "edli_user_channel_reconcile" not in job_ids
    assert settings_copy["edli"]["live_execution_mode"] == "edli_live"
    assert settings_copy["edli"]["edli_live_operator_authorized"] is True


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
    monkeypatch.setenv("ZEUS_SUBSTRATE_CLOB_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setattr("src.data.dual_run_lock.acquire_lock", lambda _name: contextlib.nullcontext(True))
    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)
    monkeypatch.setattr(market_scanner, "find_weather_markets", lambda **_kwargs: [])
    monkeypatch.setattr(
        market_scanner,
        "refresh_executable_market_substrate_snapshots",
        lambda conn, **_kwargs: {"attempted": 0, "inserted": 0},
    )
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_active", lambda: False)
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_families", lambda: [])
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_condition_ids", lambda: [])
    monkeypatch.setattr(db, "get_trade_connection", lambda *args, **kwargs: fake_conn)
    # P2: force STALE substrate so the producer-local staleness gate falls through to the
    # CLOB-construction path this test asserts on.
    monkeypatch.setattr(substrate_observer, "_market_discovery_last_completed_monotonic", None)

    substrate_observer._market_discovery_cycle()

    assert captured["public_http_timeout"] == 7.5
    assert fake_conn.committed is True
    assert fake_conn.closed is True


def test_market_discovery_uses_full_weather_discovery_with_slug_fallback():
    # PROCESS-TOPOLOGY REFACTOR P2 (system_decomposition_plan §8 Step 1): _market_discovery_cycle
    # was lifted from src/main.py to src/data/substrate_observer.py. Its body (full weather
    # discovery + bounded CLOB timeout) is unchanged by the lift.
    import inspect

    import src.data.substrate_observer as substrate_observer

    discovery_source = inspect.getsource(substrate_observer._market_discovery_cycle)
    assert "find_weather_markets" in discovery_source
    assert "include_slug_pattern=True" in discovery_source
    assert "public_http_timeout=_discovery_clob_timeout" in discovery_source
    assert "find_slug_pattern_weather_markets" not in discovery_source


def test_edli_market_channel_online_service_wired_to_rest_seed_and_websocket():
    # P3 lift (system_decomposition_plan §8 Step 3): the market-channel ONLINE-SERVICE
    # wiring (the REST-orderbook seed `PolymarketClient.get_orderbook_snapshot` + the
    # `run_market_channel_service_forever` driver) was lifted out of the order daemon into
    # the P3 lane module src/ingest/price_channel_ingest.py. This relationship test follows
    # the wiring to its new host — the invariant ("the online service IS wired to a REST
    # seed AND the market WebSocket, never falling back to no-orderbook-client") is
    # unchanged; only the host file moved.
    source = Path("src/ingest/price_channel_ingest.py").read_text()
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
    settings_copy["edli"]["edli_arm_gate_artifact_required"] = False
    settings_copy["edli"].update(edli_updates)
    monkeypatch.setattr(main, "settings", settings_copy)
    monkeypatch.setattr(main, "get_mode", lambda: "live")
    monkeypatch.setattr(main.sys, "argv", ["src/main.py"])
    monkeypatch.setattr(main, "_capture_boot_state", lambda: {"sha": _test_git_sha(), "ts": None})
    monkeypatch.setattr(main, "_write_loaded_sha_state", lambda _sha: None)
    monkeypatch.setattr(main, "_start_venue_heartbeat_loop_if_needed", lambda: None)
    monkeypatch.setattr(main, "_startup_world_schema_ready_check", lambda: None)
    monkeypatch.setattr(main, "_run_f109_consolidator", lambda: None)
    monkeypatch.setattr(main, "_startup_data_health_check", lambda _conn: None)
    monkeypatch.setattr(main, "_startup_freshness_check", lambda: None)
    monkeypatch.setattr(main, "_startup_required_sidecar_head_check", lambda **_kwargs: None)
    monkeypatch.setattr(main, "_assert_live_safe_strategies_or_exit", lambda: None)
    monkeypatch.setattr(main, "_boot_deployment_freshness_auto_resume", lambda: None)
    monkeypatch.setattr(main, "_startup_wallet_check", lambda clob=None, bankroll_record=None: None)
    # P3 lift (system_decomposition_plan §8 Step 3): the user-channel WS ingestor THREAD
    # (_start_user_channel_ingestor_if_enabled) was lifted to src.ingest.price_channel_daemon.
    # The order-daemon boot no longer starts it and the symbol is gone from src.main, so this
    # stub now targets a non-existent attribute (AttributeError). Removed — the boot harness
    # no longer references the lifted WS starter.
    monkeypatch.setattr(main, "_check_s1_without_s2_sla", lambda: None)
    # W0-T2 boot-guards: tests use live settings.json which has model_keys as a list
    # (the bad config the guard catches). Patch out here so tests exercise EDLI boot
    # logic, not calibration-pin shape. The guards have dedicated tests in
    # test_boot_guard_pin_shape.py.
    monkeypatch.setattr(main, "assert_calibration_pin_shape_is_dict", lambda _cfg: None)
    monkeypatch.setattr(main, "assert_frozen_as_of_not_stale", lambda _cfg, **_kw: None)
    monkeypatch.setattr(main, "_ensure_day0_identity_platt_fit_at_boot", lambda: None)
    monkeypatch.setattr(main, "_edli_boot_fill_bridge_recovery", lambda: None)
    monkeypatch.setattr(main, "_edli_boot_settlement_redeem_recovery", lambda: None)
    monkeypatch.setattr(main, "_edli_boot_command_recovery_once", lambda: None)
    monkeypatch.setattr(main, "_edli_boot_invalid_pending_entry_authority_cancel_once", lambda: None)
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
    loaded.write_text(json.dumps({"loaded_sha": _test_git_sha()}))
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
        "size": 10.0,
        "q_live": 0.45,
        "q_lcb_5pct": 0.44,
        "expected_edge": 0.02,
        "selection_authority_applied": "qkernel_spine",
        "min_entry_price": 0.10,
        "min_expected_profit_usd": 0.05,
        "min_submit_edge_density": 0.01,
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
        "qkernel_execution_economics": {
            "source": "qkernel_spine",
            "route_id": "DIRECT_YES:b20@proof",
            "route_type": "direct",
            "side": "YES",
            "payoff_q_point": 0.45,
            "payoff_q_lcb": 0.44,
            "cost": 0.42,
            "edge_lcb": 0.02,
            "delta_u_at_min": 0.01,
            "optimal_stake_usd": 10.0,
            "optimal_delta_u": 0.01,
            "false_edge_rate": 0.02,
            "direction_law_ok": True,
            "coherence_allows": True,
            "selection_guard_basis": "SELECTION_BETA_95",
            "selection_guard_abstained": False,
            "selection_guard_q_safe": 0.44,
        },
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
