# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md §A5 + Bug review Finding F (phase observability boundary).
"""MarketPhaseEvidence + dispatch.PhaseAuthorityViolation regression antibodies.

A5 closes Bug review Finding F: pre-A5 the cycle runtime kept
``Optional[MarketPhase]`` as the phase tag, collapsing four distinct
states into ``None``:

  - MISSING (city/target absent from market dict)
  - PARSE_FAILED (Gamma payload corrupted)
  - PRE_FLAG_FLIP (computed before phase tagging was wired)
  - GENUINE_OFF_CYCLE (out-of-band candidate construction)

Each requires a different operator response. ``MarketPhaseEvidence``
makes the source explicit (``verified_gamma`` /
``fallback_f1`` / ``onchain_resolved`` / ``unknown``) and surfaces the
failure reason when source == "unknown".

These tests pin:

1. The four phase_source values resolve correctly from input shape.
2. ``onchain_resolved`` overrides every other source when a UMA tx
   hash is provided — RESOLVED is strictly stronger than POST_TRADING.
3. The tri-state authority API (``is_live_authoritative`` /
   ``is_strict_authoritative``) reflects the live policy: fallback_f1
   passes for live entries (with A6 Kelly haircut) but NOT for
   strict-authority callers.
4. ``PhaseAuthorityViolation`` raises only under flag ON + strict mode
   + phase=None; flag OFF preserves the legacy fail-soft contract.
5. The UMA resolution listener parses real-shape eth_getLogs entries
   and persists them through the SQLite path without raising; the
   default no-RPC-client mode returns [].
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.engine.dispatch import (
    PhaseAuthorityViolation,
    is_settlement_day_dispatch,
)
from src.state.uma_resolution_listener import (
    ResolvedMarket,
    UmaRpcClient,
    init_uma_resolution_schema,
    lookup_resolution,
    parse_settle_event,
    poll_uma_resolutions,
    record_resolution,
)
from src.strategy.market_phase import MarketPhase
from src.strategy.market_phase_evidence import (
    MarketPhaseEvidence,
    from_market_dict,
    from_target_date_only,
)


# ── phase_source resolution ────────────────────────────────────────── #


def test_verified_gamma_when_market_dict_has_explicit_end():
    decision = datetime(2026, 5, 8, 6, 0, 0, tzinfo=timezone.utc)
    market = {
        "market_end_at": "2026-05-08T12:00:00Z",
        "market_start_at": "2026-05-06T00:00:00Z",
    }
    evidence = from_market_dict(
        market=market,
        city_timezone="America/New_York",
        target_date_str="2026-05-08",
        decision_time_utc=decision,
    )
    assert evidence.phase == MarketPhase.SETTLEMENT_DAY
    assert evidence.phase_source == "verified_gamma"
    assert evidence.market_end_at == datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
    assert evidence.market_start_at == datetime(2026, 5, 6, 0, 0, 0, tzinfo=timezone.utc)
    assert evidence.is_live_authoritative()
    assert evidence.is_strict_authoritative()


def test_fallback_f1_when_market_dict_lacks_end_timestamp():
    """No explicit market_end_at → uses F1 anchor (12:00 UTC of target).
    phase_source = fallback_f1, NOT unknown."""
    decision = datetime(2026, 5, 8, 6, 0, 0, tzinfo=timezone.utc)
    evidence = from_market_dict(
        market={},  # no end_at, no start_at
        city_timezone="America/New_York",
        target_date_str="2026-05-08",
        decision_time_utc=decision,
    )
    assert evidence.phase == MarketPhase.SETTLEMENT_DAY
    assert evidence.phase_source == "fallback_f1"
    # F1 anchor: 12:00 UTC of target_date.
    assert evidence.market_end_at == datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
    assert evidence.is_live_authoritative()
    assert not evidence.is_strict_authoritative()


def test_unknown_when_target_date_unparseable():
    """target_date that doesn't parse → phase=None, source=unknown,
    failure_reason populated. Strict callers use this to log + reject."""
    decision = datetime(2026, 5, 8, 6, 0, 0, tzinfo=timezone.utc)
    evidence = from_market_dict(
        market={"market_end_at": "2026-05-08T12:00:00Z"},
        city_timezone="America/New_York",
        target_date_str="not-a-date",
        decision_time_utc=decision,
    )
    assert evidence.phase is None
    assert evidence.phase_source == "unknown"
    assert evidence.failure_reason is not None
    assert "target_date" in evidence.failure_reason
    assert not evidence.is_live_authoritative()
    assert not evidence.is_strict_authoritative()


def test_unknown_when_city_timezone_invalid():
    decision = datetime(2026, 5, 8, 6, 0, 0, tzinfo=timezone.utc)
    evidence = from_market_dict(
        market={"market_end_at": "2026-05-08T12:00:00Z"},
        city_timezone="Not/A/Zone",
        target_date_str="2026-05-08",
        decision_time_utc=decision,
    )
    assert evidence.phase is None
    assert evidence.phase_source == "unknown"
    assert evidence.failure_reason is not None


def test_onchain_resolved_overrides_post_trading():
    """When a UMA tx hash is supplied, phase_source=onchain_resolved
    AND phase=RESOLVED — even if the wall-clock would say POST_TRADING."""
    # decision_time is well after the F1 endDate (which would be 12:00 UTC)
    decision = datetime(2026, 5, 8, 18, 0, 0, tzinfo=timezone.utc)
    evidence = from_market_dict(
        market={"market_end_at": "2026-05-08T12:00:00Z"},
        city_timezone="America/New_York",
        target_date_str="2026-05-08",
        decision_time_utc=decision,
        uma_resolved_source="0xabc123",
    )
    assert evidence.phase == MarketPhase.RESOLVED
    assert evidence.phase_source == "onchain_resolved"
    assert evidence.uma_resolved_source == "0xabc123"
    assert evidence.is_live_authoritative()
    assert evidence.is_strict_authoritative()


def test_from_target_date_only_uses_f1_fallback():
    """Monitor loop helper: only target_date + city_timezone, no Gamma
    payload. Always fallback_f1 unless a UMA tx is present."""
    decision = datetime(2026, 5, 8, 6, 0, 0, tzinfo=timezone.utc)
    evidence = from_target_date_only(
        target_date_str="2026-05-08",
        city_timezone="America/New_York",
        decision_time_utc=decision,
    )
    assert evidence.phase == MarketPhase.SETTLEMENT_DAY
    assert evidence.phase_source == "fallback_f1"
    assert evidence.market_start_at is None
    assert not evidence.is_strict_authoritative()


def test_evidence_is_frozen():
    """Frozen dataclass — mutation must raise."""
    decision = datetime(2026, 5, 8, 6, 0, 0, tzinfo=timezone.utc)
    evidence = from_market_dict(
        market={"market_end_at": "2026-05-08T12:00:00Z"},
        city_timezone="America/New_York",
        target_date_str="2026-05-08",
        decision_time_utc=decision,
    )
    with pytest.raises((AttributeError, TypeError)):
        evidence.phase = MarketPhase.POST_TRADING  # type: ignore[misc]


# ── dispatch.PhaseAuthorityViolation under flag ON ─────────────────── #


class _FakeCandidate:
    """Minimal duck-typed candidate for dispatch tests."""

    def __init__(self, *, market_phase=None, discovery_mode="opening_hunt", condition_id="0xfake"):
        self.market_phase = market_phase
        self.discovery_mode = discovery_mode
        self.condition_id = condition_id


def test_strict_dispatch_raises_on_phase_none_under_flag_on(monkeypatch):
    """PLAN.md §A5 + Finding F floor: strict callers refuse silent
    fallback when phase tagging fails under flag ON."""
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    candidate = _FakeCandidate(market_phase=None)
    with pytest.raises(PhaseAuthorityViolation, match="market_phase=None"):
        is_settlement_day_dispatch(candidate, strict=True)


def test_strict_dispatch_silent_when_phase_tagged(monkeypatch):
    """Strict mode + phase tagged + flag ON: behaves like non-strict —
    returns the phase==SETTLEMENT_DAY answer without raising."""
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    candidate = _FakeCandidate(market_phase=MarketPhase.SETTLEMENT_DAY)
    assert is_settlement_day_dispatch(candidate, strict=True) is True

    candidate2 = _FakeCandidate(market_phase=MarketPhase.POST_TRADING)
    assert is_settlement_day_dispatch(candidate2, strict=True) is False


def test_strict_dispatch_falls_back_to_legacy_under_flag_off(monkeypatch):
    """Flag OFF: strict is ignored; legacy cycle-axis rule resolves
    every candidate without needing a phase tag. Migration safety
    property — strict mode never breaks the OFF default."""
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
    candidate = _FakeCandidate(market_phase=None, discovery_mode="day0_capture")
    assert is_settlement_day_dispatch(candidate, strict=True) is True

    candidate2 = _FakeCandidate(market_phase=None, discovery_mode="opening_hunt")
    assert is_settlement_day_dispatch(candidate2, strict=True) is False


def test_non_strict_dispatch_silent_legacy_fallback(monkeypatch):
    """Default (strict=False) preserves the pre-A5 fail-soft contract:
    phase=None under flag ON falls back to legacy cycle-axis rule."""
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "1")
    candidate = _FakeCandidate(market_phase=None, discovery_mode="day0_capture")
    # Non-strict: phase=None defers to legacy → discovery_mode == day0_capture → True
    assert is_settlement_day_dispatch(candidate, strict=False) is True


def test_phase_authority_violation_is_a_runtime_error():
    """Type discipline: PhaseAuthorityViolation subclasses RuntimeError so
    callers can catch the broader class for compatibility, but it's its
    own type for grep-ability + targeted handling."""
    assert issubclass(PhaseAuthorityViolation, RuntimeError)


# ── UMA resolution listener: parsing + persistence ─────────────────── #


def _synthetic_log_entry(condition_id: str, *, value: int = 1) -> dict:
    """Build a synthetic eth_getLogs entry shaped like Polygon would
    return for a UMA OO Settle event."""
    # condition_id is indexed → 32-byte left-padded hex in topics[1].
    cond_hex = condition_id.replace("0x", "").zfill(64)
    return {
        "address": "0xCFFff5F5d1bC74F8c7eEeBB4a5aA31Eaab9eD13e",
        "topics": [
            # topic[0] would be the keccak256 sig in production; we
            # only assert parse_settle_event ignores it (filter is at
            # eth_getLogs level).
            "0x" + "0" * 64,
            "0x" + cond_hex,
        ],
        # 32 bytes (= 64 hex chars) trailing payload encoding the resolved value.
        "data": "0x" + format(value, "064x"),
        "blockNumber": "0x10ABCDE",
        "transactionHash": "0x" + "ab" * 32,
        "blockTimestamp": 1735689600,  # 2025-01-01T00:00:00Z
    }


def test_parse_settle_event_extracts_typed_fields():
    log = _synthetic_log_entry("0xdead", value=42)
    resolution = parse_settle_event(log)
    assert resolution.condition_id.startswith("0x")
    assert resolution.condition_id.endswith("dead")
    assert resolution.resolved_value == 42
    assert resolution.tx_hash == "0x" + "ab" * 32
    assert resolution.block_number == 0x10ABCDE
    assert resolution.resolved_at_utc.year == 2025


def test_parse_settle_event_rejects_missing_topics():
    with pytest.raises(ValueError, match="topics"):
        parse_settle_event({"topics": [], "data": "0x"})


def test_parse_settle_event_rejects_missing_tx_hash():
    log = _synthetic_log_entry("0xdead")
    log["transactionHash"] = ""
    with pytest.raises(ValueError, match="transactionHash"):
        parse_settle_event(log)


def test_parse_settle_event_handles_decimal_block_timestamp():
    log = _synthetic_log_entry("0xdead")
    log["blockTimestamp"] = "1735689600"  # decimal string instead of int
    resolution = parse_settle_event(log)
    assert resolution.resolved_at_utc.year == 2025


def test_record_and_lookup_resolution_round_trip(tmp_path):
    db_path = tmp_path / "uma.db"
    conn = sqlite3.connect(str(db_path))
    init_uma_resolution_schema(conn)

    log = _synthetic_log_entry("0xdead", value=42)
    resolution = parse_settle_event(log)
    record_resolution(conn, resolution)
    conn.commit()

    found = lookup_resolution(conn, resolution.condition_id)
    assert found is not None
    assert found.tx_hash == resolution.tx_hash
    assert found.resolved_value == 42


def test_record_resolution_is_idempotent_on_duplicate(tmp_path):
    """OR IGNORE on (condition_id, tx_hash) PK — the same Settle event
    seen twice (overlapping poll windows) does not produce two rows."""
    db_path = tmp_path / "uma.db"
    conn = sqlite3.connect(str(db_path))
    init_uma_resolution_schema(conn)

    resolution = parse_settle_event(_synthetic_log_entry("0xdead", value=42))
    record_resolution(conn, resolution)
    record_resolution(conn, resolution)
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM uma_resolution WHERE condition_id = ?",
        (resolution.condition_id,),
    ).fetchone()[0]
    assert count == 1


def test_lookup_resolution_returns_none_for_unknown_condition(tmp_path):
    db_path = tmp_path / "uma.db"
    conn = sqlite3.connect(str(db_path))
    init_uma_resolution_schema(conn)
    assert lookup_resolution(conn, "0xnonexistent") is None


# ── poller behavior ────────────────────────────────────────────────── #


class _FakeRpcClient(UmaRpcClient):
    """Test RPC client that returns a fixed list of synthetic logs."""

    def __init__(self, logs):
        self._logs = logs
        self.calls: list[dict] = []

    def get_logs(self, *, contract_address, topic0, condition_ids, from_block, to_block=None):
        self.calls.append(
            {
                "contract_address": contract_address,
                "condition_ids": list(condition_ids),
                "from_block": from_block,
                "to_block": to_block,
            }
        )
        return self._logs


def test_poll_returns_empty_when_no_rpc_client_wired():
    """Default no-client mode (production today): listener returns [],
    cycle_runtime sees no resolutions, falls back to heuristic
    POST_TRADING. No live behavior change. Operator wires real client
    via settings; until then the listener is structurally present
    without being load-bearing."""
    result = poll_uma_resolutions(
        condition_ids=["0xdead"],
        contract_address="0xfoo",
        rpc_client=None,
    )
    assert result == []


def test_poll_persists_resolutions_when_client_returns_logs(tmp_path):
    db_path = tmp_path / "uma.db"
    conn = sqlite3.connect(str(db_path))
    init_uma_resolution_schema(conn)

    log = _synthetic_log_entry("0xdead", value=42)
    rpc = _FakeRpcClient([log])

    result = poll_uma_resolutions(
        condition_ids=["0xdead"],
        contract_address="0xoo",
        rpc_client=rpc,
        conn=conn,
        from_block=0,
    )
    assert len(result) == 1
    assert result[0].resolved_value == 42

    # Persistence side effect.
    found = lookup_resolution(conn, result[0].condition_id)
    assert found is not None


def test_poll_skips_malformed_logs_without_raising(tmp_path):
    db_path = tmp_path / "uma.db"
    conn = sqlite3.connect(str(db_path))
    init_uma_resolution_schema(conn)

    bad_log = {"topics": [], "data": "0x"}  # missing fields
    good_log = _synthetic_log_entry("0xbeef", value=7)
    rpc = _FakeRpcClient([bad_log, good_log])

    result = poll_uma_resolutions(
        condition_ids=["0xbeef"],
        contract_address="0xoo",
        rpc_client=rpc,
        conn=conn,
    )
    assert len(result) == 1
    assert result[0].resolved_value == 7


def test_poll_no_op_on_empty_condition_id_list():
    """Defensive: empty condition_ids skips the RPC call entirely.
    Avoids the case where a misconfigured caller hits the RPC with a
    no-op filter that some providers reject."""
    rpc = _FakeRpcClient([])
    result = poll_uma_resolutions(
        condition_ids=[],
        contract_address="0xoo",
        rpc_client=rpc,
    )
    assert result == []
    assert rpc.calls == []
