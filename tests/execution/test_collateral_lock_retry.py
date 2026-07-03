# Created: 2026-06-16
# Last reused/audited: 2026-06-19
# Authority basis: #122 / GOAL #83 — ARCH_PLAN_EVIDENCE
#   docs/evidence/qkernel_rebuild/fix_122_collateral_lock_retry_2026-06-16.md
"""A TRANSIENT `database is locked` on the pre-submit collateral refresh must RETRY,
not reject the decided order as CollateralInsufficient (the #122 conflation that
discarded armed harvest crosses on transient zeus_trades.db write-contention)."""
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

_CTF_SCALE = 1_000_000


def _patch(monkeypatch, refresh_side_effect):
    import src.execution.executor as ex  # noqa: F401  (imported for the function under test)
    from src.state.collateral_ledger import CollateralInsufficient

    class _StubClient:
        def _ensure_v2_adapter(self):
            return object()

    class _StubLedger:
        def __init__(self, conn):  # noqa: D401
            pass

        def snapshot(self):
            return SimpleNamespace(
                authority_tier="DEGRADED",
                captured_at=datetime(2026, 6, 16, tzinfo=timezone.utc),
            )

        def refresh(self, adapter):
            return refresh_side_effect()

    monkeypatch.setattr(
        "src.data.polymarket_client.PolymarketClient", lambda *a, **k: _StubClient()
    )
    monkeypatch.setattr("src.state.collateral_ledger.CollateralLedger", _StubLedger)
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)  # no real backoff in tests
    return ex, CollateralInsufficient


def _ok_snapshot():
    return SimpleNamespace(
        authority_tier="CHAIN", captured_at=datetime(2026, 6, 16, tzinfo=timezone.utc)
    )


def test_transient_lock_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def side():
        calls["n"] += 1
        if calls["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return _ok_snapshot()

    ex, _ci = _patch(monkeypatch, side)
    out = ex._refresh_entry_collateral_snapshot_for_submit(sqlite3.connect(":memory:"))
    assert calls["n"] == 3  # two transient locks retried, third succeeds
    assert out["allowed"] is True


def test_entry_fresh_snapshot_reused_without_adapter_fetch(monkeypatch):
    from src.execution.collateral import refresh_collateral_snapshot_for_submit
    from src.state.collateral_ledger import CollateralLedger, CollateralSnapshot

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(
        CollateralSnapshot(
            pusd_balance_micro=1_000_000,
            pusd_allowance_micro=1_000_000,
            usdc_e_legacy_balance_micro=0,
            ctf_token_balances={},
            ctf_token_allowances={},
            reserved_pusd_for_buys_micro=0,
            reserved_tokens_for_sells={},
            captured_at=datetime.now(timezone.utc),
            authority_tier="CHAIN",
        )
    )

    class ClientShouldNotBeConstructed:
        def __init__(self, *args, **kwargs):  # pragma: no cover - tripwire
            raise AssertionError("fresh collateral snapshot should not refresh")

    monkeypatch.setattr(
        "src.data.polymarket_client.PolymarketClient",
        ClientShouldNotBeConstructed,
    )

    out = refresh_collateral_snapshot_for_submit(
        conn,
        action="entry_submit",
        reuse_fresh_snapshot=True,
    )
    assert out["allowed"] is True
    assert out["details"]["reused_fresh_snapshot"] is True


def test_entry_fresh_zero_allowance_snapshot_forces_submit_refresh(monkeypatch):
    from src.execution.collateral import refresh_collateral_snapshot_for_submit
    from src.state.collateral_ledger import CollateralLedger, CollateralSnapshot

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(
        CollateralSnapshot(
            pusd_balance_micro=1_000_000,
            pusd_allowance_micro=0,
            usdc_e_legacy_balance_micro=0,
            ctf_token_balances={},
            ctf_token_allowances={},
            reserved_pusd_for_buys_micro=0,
            reserved_tokens_for_sells={},
            captured_at=datetime.now(timezone.utc),
            authority_tier="CHAIN",
        )
    )

    calls = {"client": 0, "payload": 0}

    class _Adapter:
        def get_collateral_payload(self):
            calls["payload"] += 1
            return {
                "pusd_balance_micro": 1_000_000,
                "pusd_allowance_micro": 1_000_000,
                "usdc_e_legacy_balance_micro": 0,
                "ctf_token_balances": {},
                "ctf_token_allowances": {},
                "authority_tier": "CHAIN",
            }

    class _Client:
        def __init__(self):
            calls["client"] += 1

        def _ensure_v2_adapter(self):
            return _Adapter()

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", _Client)

    out = refresh_collateral_snapshot_for_submit(
        conn,
        action="entry_submit",
        reuse_fresh_snapshot=True,
    )

    assert out["allowed"] is True
    assert "reused_fresh_snapshot" not in out["details"]
    assert calls == {"client": 1, "payload": 1}
    row = conn.execute(
        "SELECT pusd_allowance_micro FROM collateral_ledger_snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row[0] == 1_000_000


def test_exit_refresh_wrapper_does_not_reuse_entry_pusd_snapshot(monkeypatch):
    from src.execution.executor import _refresh_exit_collateral_snapshot_for_submit
    from src.state.collateral_ledger import CollateralLedger, CollateralSnapshot

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(
        CollateralSnapshot(
            pusd_balance_micro=1_000_000,
            pusd_allowance_micro=1_000_000,
            usdc_e_legacy_balance_micro=0,
            ctf_token_balances={},
            ctf_token_allowances={},
            reserved_pusd_for_buys_micro=0,
            reserved_tokens_for_sells={},
            captured_at=datetime.now(timezone.utc),
            authority_tier="CHAIN",
        )
    )

    calls = {"payload": 0}

    class _ExitAdapter:
        def get_collateral_payload(self):
            calls["payload"] += 1
            return {
                "pusd_balance_micro": 1_000_000,
                "pusd_allowance_micro": 1_000_000,
                "usdc_e_legacy_balance_micro": 0,
                "ctf_token_balances_units": {"exit-token": _CTF_SCALE},
                "ctf_token_allowances_units": {"exit-token": _CTF_SCALE},
                "authority_tier": "CHAIN",
            }

    class _StubClient:
        def _ensure_v2_adapter(self):
            return _ExitAdapter()

    monkeypatch.setattr(
        "src.data.polymarket_client.PolymarketClient",
        lambda *a, **k: _StubClient(),
    )

    out = _refresh_exit_collateral_snapshot_for_submit(conn)

    assert calls["payload"] == 1
    assert out["allowed"] is True
    assert out["details"]["action"] == "exit_submit"
    assert "reused_fresh_snapshot" not in out["details"]


def test_exit_refresh_wrapper_uses_target_ctf_payload(monkeypatch):
    from src.execution.collateral import refresh_collateral_snapshot_for_submit
    from src.state.collateral_ledger import CollateralLedger, CollateralSnapshot

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    CollateralLedger(conn).set_snapshot(
        CollateralSnapshot(
            pusd_balance_micro=1_000_000,
            pusd_allowance_micro=1_000_000,
            usdc_e_legacy_balance_micro=0,
            ctf_token_balances={},
            ctf_token_allowances={},
            reserved_pusd_for_buys_micro=0,
            reserved_tokens_for_sells={},
            captured_at=datetime.now(timezone.utc),
            authority_tier="CHAIN",
        )
    )
    calls = {"target": 0, "full": 0}

    class _ExitAdapter:
        def get_ctf_collateral_payload(self, *, token_ids):
            calls["target"] += 1
            assert token_ids == ["exit-token"]
            return {
                "pusd_balance_micro": 1_000_000,
                "pusd_allowance_micro": 1_000_000,
                "usdc_e_legacy_balance_micro": 0,
                "ctf_token_balances_units": {"exit-token": 7 * _CTF_SCALE},
                "ctf_token_allowances_units": {"exit-token": 7 * _CTF_SCALE},
                "authority_tier": "CHAIN",
                "ctf_token_scope": "targeted",
            }

        def get_collateral_payload(self):  # pragma: no cover - tripwire
            calls["full"] += 1
            raise AssertionError("target exit refresh must not fan out full CTF payload")

    class _StubClient:
        def _ensure_v2_adapter(self):
            return _ExitAdapter()

    monkeypatch.setattr(
        "src.data.polymarket_client.PolymarketClient",
        lambda *a, **k: _StubClient(),
    )

    out = refresh_collateral_snapshot_for_submit(
        conn,
        action="exit_submit",
        token_id="exit-token",
    )

    assert out["allowed"] is True
    assert out["details"]["action"] == "exit_submit"
    assert calls == {"target": 1, "full": 0}
    row = conn.execute(
        "SELECT ctf_token_balances_json FROM collateral_ledger_snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert '"exit-token": 7000000' in row[0]


def test_entry_refresh_wrapper_reuses_fresh_sidecar_snapshot(monkeypatch):
    from src.execution.executor import _refresh_entry_collateral_snapshot_for_submit
    from src.state.collateral_ledger import CollateralLedger, CollateralSnapshot

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ledger = CollateralLedger(conn)
    ledger.set_snapshot(
        CollateralSnapshot(
            pusd_balance_micro=1_000_000,
            pusd_allowance_micro=1_000_000,
            usdc_e_legacy_balance_micro=0,
            ctf_token_balances={},
            ctf_token_allowances={},
            reserved_pusd_for_buys_micro=0,
            reserved_tokens_for_sells={},
            captured_at=datetime.now(timezone.utc),
            authority_tier="CHAIN",
        )
    )

    class ClientShouldNotBeConstructed:
        def __init__(self, *args, **kwargs):  # pragma: no cover - tripwire
            raise AssertionError("fresh sidecar collateral snapshot should not refresh")

    monkeypatch.setattr(
        "src.data.polymarket_client.PolymarketClient",
        ClientShouldNotBeConstructed,
    )

    out = _refresh_entry_collateral_snapshot_for_submit(conn)
    assert out["allowed"] is True
    assert out["details"]["action"] == "entry_submit"
    assert out["details"]["reused_fresh_snapshot"] is True


def test_exit_refresh_wrapper_retries_transient_lock(monkeypatch):
    calls = {"n": 0}

    def side():
        calls["n"] += 1
        if calls["n"] < 2:
            raise sqlite3.OperationalError("database is locked")
        return _ok_snapshot()

    ex, _ci = _patch(monkeypatch, side)
    out = ex._refresh_exit_collateral_snapshot_for_submit(sqlite3.connect(":memory:"))
    assert calls["n"] == 2
    assert out["allowed"] is True
    assert out["details"]["action"] == "exit_submit"


def test_exit_sell_preflight_uses_refreshed_submit_connection_snapshot():
    from src.execution.collateral import check_sell_collateral
    from src.execution.executor import _assert_collateral_allows_sell
    from src.state.collateral_ledger import (
        CollateralLedger,
        CollateralSnapshot,
        configure_global_ledger,
    )

    token_id = "exit-token-001"
    stale_conn = sqlite3.connect(":memory:")
    stale_conn.row_factory = sqlite3.Row
    submit_conn = sqlite3.connect(":memory:")
    submit_conn.row_factory = sqlite3.Row

    stale_ledger = CollateralLedger(stale_conn)
    stale_ledger.set_snapshot(
        CollateralSnapshot(
            pusd_balance_micro=0,
            pusd_allowance_micro=0,
            usdc_e_legacy_balance_micro=0,
            ctf_token_balances={},
            ctf_token_allowances={},
            reserved_pusd_for_buys_micro=0,
            reserved_tokens_for_sells={},
            captured_at=datetime(2026, 6, 16, tzinfo=timezone.utc),
            authority_tier="CHAIN",
        )
    )
    fresh_ledger = CollateralLedger(submit_conn)
    fresh_ledger.set_snapshot(
        CollateralSnapshot(
            pusd_balance_micro=0,
            pusd_allowance_micro=0,
            usdc_e_legacy_balance_micro=0,
            ctf_token_balances={token_id: 10 * _CTF_SCALE},
            ctf_token_allowances={token_id: 10 * _CTF_SCALE},
            reserved_pusd_for_buys_micro=0,
            reserved_tokens_for_sells={},
            captured_at=datetime.now(timezone.utc),
            authority_tier="CHAIN",
        )
    )

    configure_global_ledger(stale_ledger)
    try:
        can_sell, reason = check_sell_collateral(
            entry_price=0.50,
            shares=5.0,
            clob=object(),
            token_id=token_id,
            conn=submit_conn,
        )
        assert can_sell is True
        assert reason is None

        out = _assert_collateral_allows_sell(token_id, 5.0, conn=submit_conn)
        assert out["allowed"] is True
        assert out["details"]["token_id"] == token_id
    finally:
        configure_global_ledger(None)
        stale_conn.close()
        submit_conn.close()


def test_genuine_insufficiency_does_not_retry(monkeypatch):
    calls = {"n": 0}

    def side():
        calls["n"] += 1
        from src.state.collateral_ledger import CollateralInsufficient

        raise CollateralInsufficient("real_shortfall")

    ex, ci = _patch(monkeypatch, side)
    with pytest.raises(ci):
        ex._refresh_entry_collateral_snapshot_for_submit(sqlite3.connect(":memory:"))
    assert calls["n"] == 1  # genuine insufficiency surfaces immediately, no retry


def test_non_lock_operational_error_surfaces_immediately(monkeypatch):
    calls = {"n": 0}

    def side():
        calls["n"] += 1
        raise sqlite3.OperationalError("no such table: foo")

    ex, ci = _patch(monkeypatch, side)
    with pytest.raises(ci):
        ex._refresh_entry_collateral_snapshot_for_submit(sqlite3.connect(":memory:"))
    assert calls["n"] == 1  # not a lock → no retry, surfaces as collateral_refresh_failed


def test_persistent_lock_surfaces_after_retries(monkeypatch):
    calls = {"n": 0}

    def side():
        calls["n"] += 1
        raise sqlite3.OperationalError("database is locked")

    ex, ci = _patch(monkeypatch, side)
    with pytest.raises(ci):
        ex._refresh_entry_collateral_snapshot_for_submit(sqlite3.connect(":memory:"))
    assert calls["n"] == 5  # bounded retries exhausted, then surfaces


def test_submit_collateral_refresh_timeout_fails_closed_without_deadlocking(monkeypatch):
    from src.execution import collateral
    from src.execution.collateral import refresh_collateral_snapshot_for_submit
    from src.state.collateral_ledger import CollateralInsufficient

    release = threading.Event()

    class _SlowAdapter:
        def get_collateral_payload(self):
            release.wait(timeout=5.0)
            return {
                "pusd_balance_micro": 1_000_000,
                "pusd_allowance_micro": 1_000_000,
                "usdc_e_legacy_balance_micro": 0,
                "ctf_token_balances": {},
                "ctf_token_allowances": {},
                "authority_tier": "CHAIN",
            }

    class _StubClient:
        def _ensure_v2_adapter(self):
            return _SlowAdapter()

    monkeypatch.setattr(
        "src.data.polymarket_client.PolymarketClient",
        lambda *a, **k: _StubClient(),
    )
    monkeypatch.setattr(collateral, "SUBMIT_COLLATERAL_REFRESH_TIMEOUT_SECONDS", 0.05)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    started = time.monotonic()
    try:
        with pytest.raises(
            CollateralInsufficient,
            match="collateral_snapshot_degraded: refreshed_before_entry_submit: timeout_guard: submit_collateral_refresh",
        ):
            refresh_collateral_snapshot_for_submit(conn, action="entry_submit")
    finally:
        release.set()
        conn.close()

    assert time.monotonic() - started < 1.0
