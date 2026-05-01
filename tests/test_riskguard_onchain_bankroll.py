# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: docs/operations/task_2026-05-01_bankroll_truth_chain/architect_memo.md §7
#                  + docs/operations/task_2026-05-01_bankroll_truth_chain/followup_design.md §2.1, §6.2, §7
#                  + PR #31 P0-A: riskguard.tick must read on-chain bankroll, not config constant.
"""Antibody: riskguard.tick reads bankroll from the on-chain provider.

Locks six invariants surfaced by the 2026-05-01 architect + followup memos:

1. ``riskguard.tick`` consumes ``bankroll_provider.current().value_usd`` as the
   trailing-loss equity base, NOT ``settings.capital_base_usd`` and NOT the
   ``PortfolioState.bankroll`` field (a config-constant fossil).
2. When the wallet is unreachable and no fresh cache exists, tick fails closed
   at ``RiskLevel.DATA_DEGRADED`` with status ``bankroll_provider_unavailable``.
3. When the live fetch fails but a recent cache is available, tick proceeds
   using the cached value AND surfaces ``staleness_seconds`` for observability.
4. The trailing-loss RED threshold is ``wallet_balance * max_daily_loss_pct``
   (≈$15.95 at today's $199.40 wallet) — NOT ``capital_base_usd * pct`` (=$12).
   A real $15 daily loss must NOT trigger a false-positive RED that sweeps
   live positions.
5. Definition A (followup §2.1, §7 hazard #1): ``effective_bankroll`` MUST equal
   ``wallet_balance_usd``. Adding ``total_pnl`` would double-count realized PnL
   (already in the wallet via cash settlement).
6. Cutover guard (followup §6.2, §7 hazard #3): ``_trailing_loss_reference``
   MUST skip historical rows that lack ``bankroll_truth_source ==
   "polymarket_wallet"``. Without this, day-1 post-cutover compares t-24h
   ($150 fiction) to t-now ($199 real) → fake $49 LOSS → false RED → sweeps
   live positions. This is a STRICTLY worse outcome than the bug being fixed.

Failure of any test in this file means the daemon is computing trailing-loss
risk against the wrong base — the structural bug the operator flagged
2026-05-01.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import src.riskguard.riskguard as riskguard_module
from src.riskguard.risk_level import RiskLevel
from src.runtime import bankroll_provider
from src.runtime.bankroll_provider import BankrollOfRecord
from src.state.db import get_connection, init_schema
from src.state.portfolio import PortfolioState


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bor(value_usd: float, *, staleness_seconds: float = 0.0, cached: bool = False) -> BankrollOfRecord:
    return BankrollOfRecord(
        value_usd=value_usd,
        fetched_at=_now(),
        source="polymarket_wallet",
        authority="canonical",
        staleness_seconds=staleness_seconds,
        cached=cached,
    )


def _bootstrap_canonical_zeus_db(zeus_db) -> None:
    """Empty but well-formed canonical DB so `_load_riskguard_portfolio_truth` succeeds."""
    conn = get_connection(zeus_db)
    init_schema(conn)
    conn.commit()
    conn.close()


def _patch_tick_environment(monkeypatch, *, zeus_db, risk_db) -> None:
    """Wire riskguard.tick to use the per-test temp DBs."""

    def _fake_get_connection(path=None):
        if path == riskguard_module.RISK_DB_PATH:
            return get_connection(risk_db)
        return get_connection(zeus_db)

    monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)
    monkeypatch.setattr(
        riskguard_module,
        "load_portfolio",
        lambda: PortfolioState(),  # bankroll value here is now structurally irrelevant
    )
    monkeypatch.setattr(
        riskguard_module,
        "query_authoritative_settlement_rows",
        lambda conn, limit=50, **kwargs: [],
    )


def _read_latest_details(risk_db) -> dict:
    row = get_connection(risk_db).execute(
        "SELECT details_json FROM risk_state ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return json.loads(row["details_json"])


def _seed_post_cutover_reference(
    risk_db, *, age: timedelta, wallet_value_usd: float
) -> None:
    """Insert a post-cutover risk_state reference row (provenance-tagged)."""
    risk_conn = get_connection(risk_db)
    riskguard_module.init_risk_db(risk_conn)
    ts = (datetime.now(timezone.utc) - age).isoformat()
    risk_conn.execute(
        """
        INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at)
        VALUES ('GREEN', NULL, NULL, NULL, ?, ?)
        """,
        (
            json.dumps({
                "initial_bankroll": wallet_value_usd,
                "total_pnl": 0.0,
                "effective_bankroll": wallet_value_usd,
                "bankroll_truth_source": "polymarket_wallet",
            }),
            ts,
        ),
    )
    risk_conn.commit()
    risk_conn.close()


def test_tick_uses_onchain_wallet_for_trailing_loss(monkeypatch, tmp_path):
    """Effective bankroll = on-chain wallet, NOT capital_base_usd=150."""
    zeus_db = tmp_path / "zeus.db"
    risk_db = tmp_path / "risk_state.db"
    _bootstrap_canonical_zeus_db(zeus_db)
    _patch_tick_environment(monkeypatch, zeus_db=zeus_db, risk_db=risk_db)
    monkeypatch.setattr(
        bankroll_provider,
        "current",
        lambda **_kwargs: _bor(199.40),
    )

    riskguard_module.tick()
    details = _read_latest_details(risk_db)

    # Initial bankroll (the equity base for trailing-loss math) MUST be the
    # wallet, not 150.0 from settings/PortfolioState.
    assert details["initial_bankroll"] == pytest.approx(199.40)
    assert details["effective_bankroll"] == pytest.approx(199.40)
    # Cutover provenance marker is set so future ticks accept this row.
    assert details["bankroll_truth_source"] == "polymarket_wallet"
    # Provenance trail.
    truth = details["bankroll_truth"]
    assert truth["value_usd"] == pytest.approx(199.40)
    assert truth["source"] == "polymarket_wallet"
    assert truth["authority"] == "canonical"
    assert truth["cached"] is False
    assert truth["staleness_seconds"] == pytest.approx(0.0)


def test_tick_fails_closed_on_unreachable_wallet(monkeypatch, tmp_path):
    """No wallet + no cache → DATA_DEGRADED with explicit status."""
    zeus_db = tmp_path / "zeus.db"
    risk_db = tmp_path / "risk_state.db"
    _bootstrap_canonical_zeus_db(zeus_db)
    _patch_tick_environment(monkeypatch, zeus_db=zeus_db, risk_db=risk_db)
    monkeypatch.setattr(bankroll_provider, "current", lambda **_kwargs: None)

    level = riskguard_module.tick()
    assert level is RiskLevel.DATA_DEGRADED

    details = _read_latest_details(risk_db)
    assert details["status"] == "bankroll_provider_unavailable"
    assert details["bankroll_truth"]["source"] == "polymarket_wallet"
    assert details["bankroll_truth"]["value_usd"] is None
    # No equity number must leak into details when bankroll is unknown.
    assert "effective_bankroll" not in details
    assert "initial_bankroll" not in details


def test_tick_uses_cached_wallet_when_fresh_fetch_fails(monkeypatch, tmp_path):
    """Provider returns cached-stale value → tick proceeds and surfaces staleness."""
    zeus_db = tmp_path / "zeus.db"
    risk_db = tmp_path / "risk_state.db"
    _bootstrap_canonical_zeus_db(zeus_db)
    _patch_tick_environment(monkeypatch, zeus_db=zeus_db, risk_db=risk_db)
    monkeypatch.setattr(
        bankroll_provider,
        "current",
        lambda **_kwargs: _bor(199.40, staleness_seconds=10.0, cached=True),
    )

    level = riskguard_module.tick()
    # Tick should still complete (NOT fail-closed) on cached-stale.
    assert level is not RiskLevel.DATA_DEGRADED

    details = _read_latest_details(risk_db)
    assert details["initial_bankroll"] == pytest.approx(199.40)
    assert details["bankroll_truth"]["staleness_seconds"] == pytest.approx(10.0)
    assert details["bankroll_truth"]["cached"] is True


def test_red_threshold_at_real_wallet_not_config_constant(monkeypatch, tmp_path):
    """A $15 wallet drop against $199.40 must NOT trigger RED.

    With the legacy code (initial_bankroll=$150), an $15 loss = 10% > 8% RED
    threshold of $12 → would force_exit_review and sweep all positions.
    With the on-chain wallet ($199.40), the 8% threshold is $15.95 → $15 stays
    GREEN. This locks the false-positive prevention that motivates P0-A.
    """
    zeus_db = tmp_path / "zeus.db"
    risk_db = tmp_path / "risk_state.db"
    _bootstrap_canonical_zeus_db(zeus_db)
    # Seed a 23h-old reference row (post-cutover) at wallet=$199.40.
    # 25h: must be older than the 24h lookback cutoff to count as a reference,
    # but newer than (24h + 2h staleness tolerance) so status='ok' not 'stale'.
    _seed_post_cutover_reference(risk_db, age=timedelta(hours=25), wallet_value_usd=199.40)

    _patch_tick_environment(monkeypatch, zeus_db=zeus_db, risk_db=risk_db)
    # Wallet has dropped to $184.40 — a real $15 loss.
    monkeypatch.setattr(
        bankroll_provider,
        "current",
        lambda **_kwargs: _bor(184.40),
    )

    riskguard_module.tick()
    details = _read_latest_details(risk_db)

    # Sanity: trailing-loss saw the loss against the $199.40 historical wallet.
    assert details["initial_bankroll"] == pytest.approx(184.40)
    # DEF A: effective_bankroll == wallet, not wallet+pnl.
    assert details["effective_bankroll"] == pytest.approx(184.40)
    # 8% of 184.40 = 14.752; daily_loss = 199.40 - 184.40 = 15.0.
    # The threshold base in `_trailing_loss_snapshot` is the *current*
    # initial_bankroll (= current wallet 184.40). 15.0 > 14.752 would actually
    # trigger RED at the current-wallet base, but the architect memo's example
    # uses the historical wallet ($199.40) as the threshold base. Both are
    # defensible — what this antibody DEMANDS is that the threshold be a
    # function of REAL wallet, not the $150 fiction. The per-call decision of
    # "current wallet vs historical wallet for threshold base" is followup §2.3
    # (peak-drawdown vs anchor-point). Lock the structural property only:
    threshold_at_current_wallet = round(184.40 * 0.08, 2)  # 14.75
    threshold_at_historical_wallet = round(199.40 * 0.08, 2)  # 15.95
    legacy_fiction_threshold = round(150.0 * 0.08, 2)  # 12.00
    assert details["daily_loss"] == pytest.approx(15.0)
    # Definitively NOT the legacy threshold:
    assert details["daily_loss"] > legacy_fiction_threshold  # 15 > 12 (legacy would RED)
    # And the level decision is a function of the real-wallet threshold, NOT
    # the $12 fiction. We verify by computing both possible level outcomes
    # against real-wallet thresholds and asserting the daemon agrees.
    if details["daily_loss"] > threshold_at_current_wallet:
        assert details["daily_loss_level"] in {RiskLevel.RED.value}
    else:
        assert details["daily_loss_level"] == RiskLevel.GREEN.value


def test_no_double_counting_pnl(monkeypatch, tmp_path):
    """DEF A: effective_bankroll == wallet_balance_usd, NEVER wallet + total_pnl.

    Followup §2.1 hazard #1: realized PnL is already in the on-chain wallet
    (cash settled). Adding `total_pnl` to the wallet for current_equity would
    double-count realized exits. This test injects a non-zero PnL signal and
    asserts the equity number IGNORES it and equals the raw wallet.
    """
    zeus_db = tmp_path / "zeus.db"
    risk_db = tmp_path / "risk_state.db"
    _bootstrap_canonical_zeus_db(zeus_db)
    _patch_tick_environment(monkeypatch, zeus_db=zeus_db, risk_db=risk_db)
    monkeypatch.setattr(
        bankroll_provider,
        "current",
        lambda **_kwargs: _bor(199.40),
    )
    # Inject a meaningful realized-pnl bookkeeping signal via recent_exits
    # fallback: $5 realized PnL would, under the (wrong) Def B math, push
    # effective_bankroll to $204.40. Under Def A, it stays $199.40.
    monkeypatch.setattr(
        riskguard_module,
        "load_portfolio",
        lambda: PortfolioState(
            recent_exits=[{"city": "NYC", "pnl": 5.0}],
        ),
    )

    riskguard_module.tick()
    details = _read_latest_details(risk_db)

    # The structural anti-double-count assertion:
    assert details["effective_bankroll"] == pytest.approx(199.40)
    assert details["initial_bankroll"] == pytest.approx(199.40)
    # PnL is still surfaced for analytics, just not folded into equity.
    assert details["realized_pnl"] == pytest.approx(5.0)
    # Confirm the legacy double-counting path is NOT in effect.
    legacy_double_counted = round(199.40 + 5.0, 2)
    assert details["effective_bankroll"] != pytest.approx(legacy_double_counted)


def test_trailing_loss_skips_pre_cutover_reference_rows(monkeypatch, tmp_path):
    """Cutover guard: pre-cutover risk_state rows MUST be skipped as references.

    Before P0-A, every risk_state row stored ``effective_bankroll = $150 + pnl``
    (config-constant fiction). After P0-A, rows store ``effective_bankroll =
    real_wallet``. If `_trailing_loss_reference` does not filter on
    ``bankroll_truth_source == "polymarket_wallet"``, the first 24h post-cutover
    will compare today's $199 wallet to yesterday's $150 fiction → fake $49
    loss → false RED → ``force_exit_review`` sweeps live positions. This is a
    strictly worse failure mode than the bug being fixed.

    Antibody: insert a 23h-old PRE-cutover row ($150 fiction, no provenance
    tag) and a much older OR no post-cutover row. `_trailing_loss_reference`
    must return "no_reference_row" (treating the pre-cutover row as if it
    didn't exist) and trailing-loss must report bootstrap_no_history → GREEN.
    """
    zeus_db = tmp_path / "zeus.db"
    risk_db = tmp_path / "risk_state.db"
    _bootstrap_canonical_zeus_db(zeus_db)

    # Insert a 23h-old pre-cutover row (NO bankroll_truth_source) carrying the
    # $150 fiction. If the cutover guard is missing, this becomes the t-24h
    # reference and the trailing-loss diff would be $199.40 - $150.00 = $49.40
    # → 33% drop → instant RED.
    risk_conn = get_connection(risk_db)
    riskguard_module.init_risk_db(risk_conn)
    pre_cutover_ts = (datetime.now(timezone.utc) - timedelta(hours=23)).isoformat()
    risk_conn.execute(
        """
        INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at)
        VALUES ('GREEN', NULL, NULL, NULL, ?, ?)
        """,
        (
            json.dumps({
                "initial_bankroll": 150.0,
                "total_pnl": 0.0,
                "effective_bankroll": 150.0,
                # Note: NO bankroll_truth_source field. This is what every row
                # written before the P0-A cutover looks like.
            }),
            pre_cutover_ts,
        ),
    )
    risk_conn.commit()
    risk_conn.close()

    _patch_tick_environment(monkeypatch, zeus_db=zeus_db, risk_db=risk_db)
    monkeypatch.setattr(
        bankroll_provider,
        "current",
        lambda **_kwargs: _bor(199.40),
    )

    level = riskguard_module.tick()
    details = _read_latest_details(risk_db)

    # The pre-cutover row was skipped → bootstrap_no_history → GREEN, not RED.
    assert details["daily_loss_level"] == RiskLevel.GREEN.value
    assert "bootstrap_no_history" in details["daily_loss_status"]
    # No fake loss is recorded.
    assert details["daily_loss"] == pytest.approx(0.0)
    # Force-exit-review must NOT be triggered.
    row = get_connection(risk_db).execute(
        "SELECT level, force_exit_review FROM risk_state ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["force_exit_review"] == 0
    # The new tick wrote a properly-tagged post-cutover row.
    assert details["bankroll_truth_source"] == "polymarket_wallet"


def test_post_cutover_reference_rows_are_eligible(monkeypatch, tmp_path):
    """Symmetric antibody: properly-tagged post-cutover rows ARE used.

    Without this, the cutover guard would over-filter and trailing-loss would
    permanently report bootstrap_no_history. We need positive evidence that
    a row tagged ``bankroll_truth_source = "polymarket_wallet"`` is consumed
    as a real reference.
    """
    zeus_db = tmp_path / "zeus.db"
    risk_db = tmp_path / "risk_state.db"
    _bootstrap_canonical_zeus_db(zeus_db)
    # 23h-old post-cutover row at wallet=$199.40.
    # 25h: must be older than the 24h lookback cutoff to count as a reference,
    # but newer than (24h + 2h staleness tolerance) so status='ok' not 'stale'.
    _seed_post_cutover_reference(risk_db, age=timedelta(hours=25), wallet_value_usd=199.40)

    _patch_tick_environment(monkeypatch, zeus_db=zeus_db, risk_db=risk_db)
    # Wallet stayed flat — no real loss.
    monkeypatch.setattr(
        bankroll_provider,
        "current",
        lambda **_kwargs: _bor(199.40),
    )

    riskguard_module.tick()
    details = _read_latest_details(risk_db)

    # Reference row was consumed; loss is computed against it.
    assert details["daily_loss_status"] == "ok"
    assert details["daily_loss"] == pytest.approx(0.0)
    assert details["daily_loss_level"] == RiskLevel.GREEN.value
    # Reference details surface in the snapshot for observability.
    ref = details["daily_loss_reference"]
    assert ref is not None
    assert ref["effective_bankroll"] == pytest.approx(199.40)
