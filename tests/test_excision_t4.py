# Created: 2026-07-11
# Last reused or audited: 2026-07-11
# Authority basis: docs/rebuild/quarantine_excision_2026-07-11.md T4 (fill_tracker
#   resolves pending-entry uncertainty through truth, retires quarantine minting)
#   + "Consult adjudication" BLOCKER-1/BLOCKER-3.
# Lifecycle: created=2026-07-11; last_reviewed=2026-07-11; last_reused=never
# Purpose: Unit + integration coverage for T4's re-routed sites: venue-truth gaps,
#   local write failures, fill-confirmation release, confirmed-void release, and
#   the BLOCKER-3 ChainObservationEnvelope gate on ambiguous timeouts.
# Reuse: Run when src/execution/fill_tracker.py, src/contracts/review_work_item.py,
#   src/contracts/chain_observation_envelope.py, or src/state/review_work_items.py change.

"""T4 quarantine excision: fill_tracker resolves pending-entry uncertainty
through chain/venue truth instead of minting a lifecycle scar.

Characterization law (T4 acceptance bar): a position that WOULD have been
quarantined yesterday now stays pending_tracked with an open ReviewWorkItem,
and — for the family-blocking reason codes — its weather family is blocked
via blocked_family_keys exactly as a ChainOnlyFact/EntryExposureObligation
would block it (T2's evaluator seam).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.contracts.chain_observation_envelope import (
    UNOBSERVED_CHAIN_ENVELOPE,
    ChainObservationEnvelope,
)
from src.contracts.review_work_item import FAMILY_BLOCKING_REASON_CODES, ReviewReasonCode
from src.execution.fill_tracker import (
    _chain_observation_for_position,
    _confirmed_absent_or_defer,
    check_pending_entries,
)
from src.state.entry_exposure_obligation import open_entry_exposure_obligation
from src.state.portfolio import FILL_AUTHORITY_NONE, PortfolioState, Position
from src.state.review_work_items import blocked_family_keys, due_work
from src.state.schema.entry_exposure_obligations_schema import (
    ensure_table as ensure_obligations_table,
)
from src.state.schema.review_work_items_schema import ensure_table as ensure_review_items_table
from unittest.mock import MagicMock


def _make_position(**overrides) -> Position:
    defaults = dict(
        trade_id="t4-pos-1",
        market_id="mkt-t4-1",
        city="Austin",
        cluster="Texas",
        target_date="2026-08-15",
        bin_label="95-100",
        temperature_metric="high",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.40,
        shares=25.0,
        cost_basis_usd=10.0,
        state="pending_tracked",
        token_id="tok-t4-1",
        no_token_id="",
        order_id="order-t4-1",
        entry_order_id="order-t4-1",
        entry_fill_verified=False,
        entered_at="",
        order_posted_at="2026-08-01T00:00:00+00:00",
        strategy_key="center_buy",
        strategy="center_buy",
        env="live",
    )
    defaults.update(overrides)
    return Position(**defaults)


def _make_portfolio(*positions) -> PortfolioState:
    return PortfolioState(positions=list(positions))


def _make_clob(payload) -> MagicMock:
    clob = MagicMock()
    clob.get_order_status.return_value = payload
    clob.cancel_order.return_value = {"status": "CANCELLED"}
    return clob


def _make_deps(db_path):
    from src.state.db import get_connection

    class Deps:
        @staticmethod
        def get_connection():
            return get_connection(db_path)

    return Deps


def _init_trade_db(db_path) -> None:
    from src.state.db import get_connection, init_schema_trade_only

    conn = get_connection(db_path)
    init_schema_trade_only(conn)
    ensure_review_items_table(conn)
    ensure_obligations_table(conn)
    conn.commit()
    conn.close()


def _seed_acked_entry_command(db_path, position, *, command_id: str = "cmd-t4") -> None:
    from src.state.db import get_connection

    conn = get_connection(db_path)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size,
            price, venue_order_id, state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'ENTRY', ?, ?, 'BUY', ?, ?, ?, 'ACKED', ?, ?)
        """,
        (
            command_id,
            f"snapshot-{command_id}",
            f"envelope-{command_id}",
            position.trade_id,
            f"decision-{command_id}",
            f"idem-{command_id}",
            position.market_id,
            position.token_id,
            float(position.shares or 25.0),
            float(position.entry_price or 0.40),
            position.entry_order_id,
            "2026-08-01T00:00:00+00:00",
            "2026-08-01T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()


def _seed_canonical_entry_baseline(db_path, position) -> None:
    """Seed the position_current pending_entry baseline _maybe_update_trade_lifecycle
    and _maybe_emit_canonical_entry_fill need to advance a real position through
    _mark_entry_filled / _mark_entry_voided (mirrors test_live_safety_invariants.py's
    identically-named helper)."""
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.db import append_many_and_project, get_connection
    from src.state.lifecycle_manager import LifecyclePhase

    conn = get_connection(db_path)
    if not getattr(position, "condition_id", ""):
        position.condition_id = "cond-t4"
    events, projection = build_entry_canonical_write(
        position,
        phase_after=LifecyclePhase.PENDING_ENTRY.value,
        decision_id="dec-t4-baseline",
        source_module="tests.test_excision_t4",
    )
    append_many_and_project(conn, events, projection)
    conn.commit()
    conn.close()


def _open_work_items(db_path):
    from src.state.db import get_connection

    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT owner_table, subject_id, reason_code, status, exposure_bound_usd, unbounded, "
        "family_city, family_target_date, family_temperature_metric FROM review_work_items"
    ).fetchall()
    conn.close()
    return rows


class TestVenueTruthGapStaysPending:
    """Sites 881/905: missing fill economics / missing fill authority never
    quarantine — position stays pending_tracked, in the check_pending_entries
    scan set, with an open MISSING_FILL_* review work item."""

    def test_missing_fill_economics_opens_work_item_and_stays_pending(self, tmp_path) -> None:
        db_path = tmp_path / "zeus.db"
        _init_trade_db(db_path)
        pos = _make_position()
        _seed_acked_entry_command(db_path, pos)
        portfolio = _make_portfolio(pos)
        clob = _make_clob({"status": "CONFIRMED", "filledSize": 25.0})  # no fill price

        stats = check_pending_entries(portfolio, clob, deps=_make_deps(db_path))

        assert stats["still_pending"] == 1
        assert stats["voided"] == 0
        assert pos.state == "pending_tracked"
        assert pos.fill_authority == FILL_AUTHORITY_NONE
        # Still in the scan set next cycle (check_pending_entries filters on
        # state == "pending_tracked" — never quarantined out of it).
        assert pos in portfolio.positions

        rows = _open_work_items(db_path)
        matching = [r for r in rows if r["subject_id"] == pos.trade_id]
        assert len(matching) == 1
        assert matching[0]["reason_code"] == ReviewReasonCode.MISSING_FILL_ECONOMICS.value
        assert matching[0]["status"] == "OPEN"
        # Worst-case bound: shares x $1/share (BLOCKER-1), never unbounded
        # when shares are known.
        assert matching[0]["unbounded"] == 0
        assert matching[0]["exposure_bound_usd"] == pytest.approx(25.0)
        assert matching[0]["family_city"] == pos.city

    def test_missing_trade_identity_opens_missing_fill_authority_item(self, tmp_path) -> None:
        db_path = tmp_path / "zeus.db"
        _init_trade_db(db_path)
        pos = _make_position(trade_id="t4-pos-2")
        _seed_acked_entry_command(db_path, pos, command_id="cmd-t4-2")
        portfolio = _make_portfolio(pos)
        clob = _make_clob(
            {"status": "CONFIRMED", "avgPrice": 0.44, "filledSize": 25.0}
        )  # no trade_id

        stats = check_pending_entries(portfolio, clob, deps=_make_deps(db_path))

        assert stats["still_pending"] == 1
        assert pos.state == "pending_tracked"
        rows = _open_work_items(db_path)
        matching = [r for r in rows if r["subject_id"] == pos.trade_id]
        assert len(matching) == 1
        assert matching[0]["reason_code"] == ReviewReasonCode.MISSING_FILL_AUTHORITY.value


class TestLocalWriteFailureStaysPending:
    """Sites 988/1050/1114/1165/1240/1287: a LOCAL ledger/canonical/void write
    failure must never relabel venue truth — loud ERROR + stays pending +
    retried, and repeated failures converge on ONE work item (idempotent)."""

    def test_ledger_write_failure_logs_error_and_holds_pending(self, tmp_path, caplog) -> None:
        db_path = tmp_path / "zeus.db"
        _init_trade_db(db_path)
        pos = _make_position(trade_id="t4-pos-3")
        # Deliberately do NOT seed a venue_commands row: _maybe_append_venue_fill_observation
        # looks up venue_commands by venue_order_id and no-ops (returns True) when
        # absent, so instead force a failure via a payload that trips the
        # "unsupported explicit trade status" semantic-conflict branch, which is a
        # real, durable ledger write outcome classified LOCAL_WRITE_FAILURE.
        _seed_acked_entry_command(db_path, pos, command_id="cmd-t4-3")
        portfolio = _make_portfolio(pos)
        clob = _make_clob(
            {
                "status": "MATCHED",
                "trade_status": "SOME_UNKNOWN_VENDOR_STATE",
                "filledSize": 25.0,
                "avgPrice": 0.40,
            }
        )

        import logging

        with caplog.at_level(logging.ERROR):
            stats = check_pending_entries(portfolio, clob, deps=_make_deps(db_path))

        assert stats["still_pending"] == 1
        assert pos.state == "pending_tracked"
        assert any(
            "local bug must not relabel venue truth" in r.message for r in caplog.records
        )

        rows = _open_work_items(db_path)
        matching = [r for r in rows if r["subject_id"] == pos.trade_id]
        assert len(matching) == 1
        assert matching[0]["reason_code"] == ReviewReasonCode.LOCAL_WRITE_FAILURE.value

    def test_repeated_failure_converges_on_one_open_work_item(self, tmp_path) -> None:
        """Double-failure across two cycles opens ONE work item, not two —
        open_work_item's idempotent-open guarantee (INSERT OR IGNORE + partial
        unique index) applies identically from fill_tracker's own seam."""
        db_path = tmp_path / "zeus.db"
        _init_trade_db(db_path)
        pos = _make_position(trade_id="t4-pos-4")
        _seed_acked_entry_command(db_path, pos, command_id="cmd-t4-4")
        portfolio = _make_portfolio(pos)
        clob = _make_clob(
            {
                "status": "MATCHED",
                "trade_status": "SOME_UNKNOWN_VENDOR_STATE",
                "filledSize": 25.0,
                "avgPrice": 0.40,
            }
        )

        check_pending_entries(portfolio, clob, deps=_make_deps(db_path))
        check_pending_entries(portfolio, clob, deps=_make_deps(db_path))

        rows = _open_work_items(db_path)
        matching = [r for r in rows if r["subject_id"] == pos.trade_id and r["status"] == "OPEN"]
        assert len(matching) == 1


class TestFillConfirmationReleasesReservationAndResolvesWorkItems:
    """T4 item 5: the success path of _mark_entry_filled releases the T2
    EntryRiskReservation and resolves any open review debt for the subject."""

    def test_confirmed_fill_resolves_open_work_item_and_releases_obligation(self, tmp_path) -> None:
        db_path = tmp_path / "zeus.db"
        _init_trade_db(db_path)
        pos = _make_position(trade_id="t4-pos-5", shares=0.0, cost_basis_usd=0.0)
        _seed_acked_entry_command(db_path, pos, command_id="cmd-t4-5")
        _seed_canonical_entry_baseline(db_path, pos)

        from src.state.db import get_connection

        conn = get_connection(db_path)
        open_entry_exposure_obligation(
            conn,
            command_id="cmd-t4-5",
            owner_domain="trade",
            token_id=pos.token_id,
            condition_id="",
            shares=25.0,
            cost_basis_usd=10.0,
            unbounded=False,
        )
        from src.state.review_work_items import open_work_item

        open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id=pos.trade_id,
            reason_code=ReviewReasonCode.MISSING_FILL_ECONOMICS,
            exposure_bound_usd=25.0,
        )
        conn.commit()
        conn.close()

        portfolio = _make_portfolio(pos)
        clob = _make_clob(
            {
                "status": "CONFIRMED",
                "trade_id": "trade-t4-5",
                "avgPrice": 0.42,
                "filledSize": 25.0,
                "timestamp": "2026-08-01T00:01:00+00:00",
            }
        )

        stats = check_pending_entries(portfolio, clob, deps=_make_deps(db_path))

        assert stats["entered"] == 1
        assert pos.fill_authority != FILL_AUTHORITY_NONE

        conn = get_connection(db_path)
        obligation_status = conn.execute(
            "SELECT status FROM entry_exposure_obligations WHERE command_id = 'cmd-t4-5'"
        ).fetchone()["status"]
        work_item_status = conn.execute(
            "SELECT status FROM review_work_items WHERE subject_id = ?",
            (pos.trade_id,),
        ).fetchone()["status"]
        conn.close()

        assert obligation_status == "RESOLVED"
        assert work_item_status == "RESOLVED"


class TestConfirmedVoidReleasesReservationAndResolvesWorkItems:
    """BLOCKER-1: confirmed absence supersedes the conservative estimate the
    same way a confirmed fill does — _mark_entry_voided's success path also
    releases the reservation and resolves open review debt."""

    def test_venue_confirmed_cancel_void_releases_obligation(self, tmp_path) -> None:
        db_path = tmp_path / "zeus.db"
        _init_trade_db(db_path)
        pos = _make_position(
            trade_id="t4-pos-6",
            shares=0.0,
            cost_basis_usd=0.0,
            order_status="",
        )
        _seed_acked_entry_command(db_path, pos, command_id="cmd-t4-6")
        _seed_canonical_entry_baseline(db_path, pos)

        from src.state.db import get_connection

        conn = get_connection(db_path)
        open_entry_exposure_obligation(
            conn,
            command_id="cmd-t4-6",
            owner_domain="trade",
            token_id=pos.token_id,
            condition_id="",
            shares=25.0,
            cost_basis_usd=10.0,
            unbounded=False,
        )
        conn.commit()
        conn.close()

        portfolio = _make_portfolio(pos)
        clob = _make_clob({"status": "CANCELLED"})

        stats = check_pending_entries(portfolio, clob, deps=_make_deps(db_path))

        assert stats["voided"] == 1

        conn = get_connection(db_path)
        obligation_status = conn.execute(
            "SELECT status FROM entry_exposure_obligations WHERE command_id = 'cmd-t4-6'"
        ).fetchone()["status"]
        conn.close()
        assert obligation_status == "RESOLVED"


class TestAmbiguousTimeoutChainObservationGate:
    """BLOCKER-3: the ambiguous (no definitive CLOB status) timeout branch
    voids ONLY on a qualifying ChainObservationEnvelope; stale/incomplete/
    unobserved defers to a TIMEOUT_ABSENCE_UNCONFIRMED work item instead."""

    def test_unobserved_chain_never_qualifies(self) -> None:
        pos = _make_position(trade_id="t4-pos-7")
        assert _chain_observation_for_position(pos) == UNOBSERVED_CHAIN_ENVELOPE
        assert not _chain_observation_for_position(pos).qualifies_for_absence_vote()

    @pytest.mark.parametrize(
        ("complete", "post_command_watermark", "fetched_at", "expected"),
        [
            (True, True, "2026-08-01T00:00:00+00:00", True),
            (False, True, "2026-08-01T00:00:00+00:00", False),
            (True, False, "2026-08-01T00:00:00+00:00", False),
            (True, True, "", False),
        ],
    )
    def test_envelope_qualifies_only_when_every_dimension_holds(
        self, complete, post_command_watermark, fetched_at, expected
    ) -> None:
        """Direct dataclass coverage: qualifies_for_absence_vote is a
        conservative AND over completeness, post-command watermark, and a
        non-empty fetched_at — missing any one dimension refuses the vote."""
        envelope = ChainObservationEnvelope(
            account_scope="wallet:zeus_operator",
            fetched_at=fetched_at,
            complete=complete,
            post_command_watermark=post_command_watermark,
            source="chain_reconciliation",
        )
        assert envelope.qualifies_for_absence_vote() is expected

    def test_positive_presence_overrides_absence(self) -> None:
        """A chain_verified_at at/after the absence timestamp means the chain
        currently shows presence — never a qualifying absence envelope."""
        now = datetime.now(timezone.utc)
        pos = _make_position(
            trade_id="t4-pos-8",
            order_posted_at=(now - timedelta(minutes=30)).isoformat(),
            last_chain_absence_observed_at=(now - timedelta(minutes=5)).isoformat(),
            chain_verified_at=(now - timedelta(minutes=1)).isoformat(),
        )
        envelope = _chain_observation_for_position(pos)
        assert envelope == UNOBSERVED_CHAIN_ENVELOPE

    def test_fresh_post_command_absence_qualifies_and_confirms_void(self) -> None:
        now = datetime.now(timezone.utc)
        pos = _make_position(
            trade_id="t4-pos-9",
            order_posted_at=(now - timedelta(minutes=30)).isoformat(),
            last_chain_absence_observed_at=(now - timedelta(minutes=5)).isoformat(),
        )
        envelope = _chain_observation_for_position(pos)
        assert envelope.qualifies_for_absence_vote()
        assert _confirmed_absent_or_defer(pos, now) is True

    def test_stale_absence_does_not_qualify(self) -> None:
        """Freshness bound (two chain_mirror_reconciler cycles, ~20 min): an
        absence observed long ago must never itself justify a void — even
        though the envelope itself qualifies structurally (complete,
        post-command), the CALLER (_confirmed_absent_or_defer) also enforces
        the freshness bound before treating it as a live absence vote."""
        now = datetime.now(timezone.utc)
        pos = _make_position(
            trade_id="t4-pos-10",
            order_posted_at=(now - timedelta(hours=3)).isoformat(),
            last_chain_absence_observed_at=(now - timedelta(hours=2)).isoformat(),
        )
        envelope = _chain_observation_for_position(pos)
        assert envelope.qualifies_for_absence_vote()
        assert _confirmed_absent_or_defer(pos, now) is False

    def test_pre_command_absence_does_not_qualify(self) -> None:
        """An absence observed BEFORE this order was even posted says nothing
        about whether THIS command filled — no post-command watermark."""
        now = datetime.now(timezone.utc)
        pos = _make_position(
            trade_id="t4-pos-11",
            order_posted_at=now.isoformat(),
            last_chain_absence_observed_at=(now - timedelta(minutes=30)).isoformat(),
        )
        envelope = _chain_observation_for_position(pos)
        assert envelope.post_command_watermark is False
        assert not envelope.qualifies_for_absence_vote()

    def test_ambiguous_ungradeable_timeout_defers_with_work_item(self, tmp_path) -> None:
        """No definitive CLOB status, cancel attempt itself unconfirmed, and no
        qualifying chain observation -> stays pending with an open
        TIMEOUT_ABSENCE_UNCONFIRMED item; never force-voided on ambiguity."""
        db_path = tmp_path / "zeus.db"
        _init_trade_db(db_path)
        now = datetime.now(timezone.utc)
        pos = _make_position(
            trade_id="t4-pos-12",
            order_timeout_at=(now - timedelta(minutes=5)).isoformat(),
        )
        _seed_acked_entry_command(db_path, pos, command_id="cmd-t4-12")
        portfolio = _make_portfolio(pos)
        clob = _make_clob(None)  # no status at all from CLOB
        clob.cancel_order.return_value = None  # cancel attempt itself fails

        stats = check_pending_entries(portfolio, clob, deps=_make_deps(db_path), now=now)

        assert stats["still_pending"] == 1
        assert stats["voided"] == 0
        assert pos.state == "pending_tracked"

        rows = _open_work_items(db_path)
        matching = [r for r in rows if r["subject_id"] == pos.trade_id]
        assert len(matching) == 1
        assert matching[0]["reason_code"] == ReviewReasonCode.TIMEOUT_ABSENCE_UNCONFIRMED.value


class TestFamilyBlockingIntegration:
    """T4/T2 seam: TIMEOUT_ABSENCE_UNCONFIRMED is the one T4 reason code that
    is real-or-unknown FAMILY exposure (BLOCKER-1) — it must reach
    blocked_family_keys exactly as a ChainOnlyFact/EntryExposureObligation
    does, blocking sibling temperature bins in the same weather family.
    """

    def test_timeout_absence_unconfirmed_blocks_family(self, tmp_path) -> None:
        assert ReviewReasonCode.TIMEOUT_ABSENCE_UNCONFIRMED in FAMILY_BLOCKING_REASON_CODES

        db_path = tmp_path / "zeus.db"
        _init_trade_db(db_path)
        now = datetime.now(timezone.utc)
        pos = _make_position(
            trade_id="t4-pos-13",
            city="Miami",
            target_date="2026-09-01",
            temperature_metric="high",
            order_timeout_at=(now - timedelta(minutes=5)).isoformat(),
        )
        _seed_acked_entry_command(db_path, pos, command_id="cmd-t4-13")
        portfolio = _make_portfolio(pos)
        clob = _make_clob(None)
        clob.cancel_order.return_value = None

        check_pending_entries(portfolio, clob, deps=_make_deps(db_path), now=now)

        from src.state.db import get_connection
        from src.strategy.family_exclusive_dedup import WeatherFamilyKey

        conn = get_connection(db_path)
        blocked = blocked_family_keys(conn, portfolio=None)
        conn.close()

        assert WeatherFamilyKey("Miami", "2026-09-01", "high", "") in blocked

    def test_missing_fill_economics_reason_code_never_blocks_family(self) -> None:
        """MISSING_FILL_ECONOMICS/MISSING_FILL_AUTHORITY/LOCAL_WRITE_FAILURE
        are real review debt but do not by themselves imply unknown family
        exposure — only the family-blocking set does (see
        src.contracts.review_work_item.FAMILY_BLOCKING_REASON_CODES)."""
        assert ReviewReasonCode.MISSING_FILL_ECONOMICS not in FAMILY_BLOCKING_REASON_CODES
        assert ReviewReasonCode.MISSING_FILL_AUTHORITY not in FAMILY_BLOCKING_REASON_CODES
        assert ReviewReasonCode.LOCAL_WRITE_FAILURE not in FAMILY_BLOCKING_REASON_CODES


class TestDueWorkScheduler:
    def test_venue_truth_gap_item_is_due_immediately(self, tmp_path) -> None:
        db_path = tmp_path / "zeus.db"
        _init_trade_db(db_path)
        pos = _make_position(trade_id="t4-pos-14")
        _seed_acked_entry_command(db_path, pos, command_id="cmd-t4-14")
        portfolio = _make_portfolio(pos)
        clob = _make_clob({"status": "CONFIRMED", "filledSize": 25.0})

        check_pending_entries(portfolio, clob, deps=_make_deps(db_path))

        from src.state.db import get_connection

        conn = get_connection(db_path)
        due = due_work(conn, limit=10)
        conn.close()
        assert any(item.subject_id == pos.trade_id for item in due)
