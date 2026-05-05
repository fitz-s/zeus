# Created: 2026-05-05
# Last reused or audited: 2026-05-05
# Authority basis: docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T2F/phase.json
"""Caplog ledger: asserts every T1 counter event name fires both the typed sink AND legacy log.

T2F-CAPLOG-LEDGER-ASSERTS-EVERY-EVENT-NAME: For each of the 7 distinct counter
event names emitted by T1, each parametrized case asserts:
  (a) typed sink counter reflects the increment (read() > 0 after trigger)
  (b) legacy logger.warning("telemetry_counter event=<name>...") line still fires

Event names covered:
  1. placeholder_envelope_blocked_total    (adapter.py:submit)
  2. compat_submit_rejected_total          (adapter.py:submit_limit_order)
  3. db_write_lock_timeout_total           (db.py:_handle_db_write_lock)
  4. position_loader_field_defaulted_total (portfolio.py:_load_d6_field, labels={field})
  5. position_projection_field_dropped_total (portfolio.py:_project_d6_field, labels={field})
  6. cost_basis_chain_mutation_blocked_total (chain_reconciliation.py, labels={field})
  7. harvester_learning_write_blocked_total (harvester.py:maybe_write_learning_pair, labels={reason})
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from src.observability.counters import read, reset_all


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset():
    """Reset typed counter state before each test for isolation."""
    reset_all()
    yield
    reset_all()


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _make_placeholder_envelope(
    *,
    condition_id: str = "legacy:0xabc",
    question_id: str = "legacy-compat",
):
    """Construct a VenueSubmissionEnvelope with legacy/placeholder identity."""
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
    payload = json.dumps(
        {"condition_id": condition_id, "side": "BUY", "price": "0.5", "size": "1.0"},
        sort_keys=True,
    )
    ph = _sha256(payload)
    rh = _sha256(payload + ":raw")
    return VenueSubmissionEnvelope(
        sdk_package="py-clob-client-v2",
        sdk_version="0.0.0",
        host="https://clob.polymarket.com",
        chain_id=137,
        funder_address="0xfunder",
        condition_id=condition_id,
        question_id=question_id,
        yes_token_id="0xtoken",
        no_token_id="0xtoken",  # same as yes → collapsed identity
        selected_outcome_token_id="0xtoken",
        outcome_label="YES",
        side="BUY",
        price=Decimal("0.5"),
        size=Decimal("1.0"),
        order_type="GTC",
        post_only=False,
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        neg_risk=False,
        fee_details={"bps": 0, "builder_fee_bps": 0},
        canonical_pre_sign_payload_hash=ph,
        signed_order=None,
        signed_order_hash=None,
        raw_request_hash=rh,
        raw_response_json=None,
        order_id=None,
        trade_ids=(),
        transaction_hashes=(),
        error_code=None,
        error_message=None,
        captured_at="2026-05-05T00:00:00+00:00",
    )


def _build_adapter(tmp_path: Path):
    """Build a PolymarketV2Adapter with a real evidence file and fake client."""
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter
    evidence = tmp_path / "q1_zeus_egress_2026-05-05.txt"
    evidence.write_text("daemon host probe ok\n")
    fake_client = MagicMock()
    fake_client.get_ok.return_value = {"ok": True}
    return PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        q1_egress_evidence_path=evidence,
        client_factory=lambda **kwargs: fake_client,
    )


def _make_city(name: str = "testcity"):
    from src.config import City
    return City(
        name=name,
        lat=41.878,
        lon=-87.630,
        timezone="America/Chicago",
        settlement_unit="F",
        cluster="north",
        wu_station="KORD",
        settlement_source="KORD",
        country_code="US",
        settlement_source_type="wu_icao",
    )


def _make_db_conn():
    from src.state.db import init_schema
    from src.state.schema.v2_schema import apply_v2_schema
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)
    conn.commit()
    return conn


def _make_reconcile_conn():
    from src.state.db import apply_architecture_kernel_schema
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    return conn


def _make_eligible_position():
    from src.state.portfolio import Position
    return Position(
        trade_id="chain-pos-1",
        market_id="mkt-1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-05-01",
        bin_label="39-40F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.5,
        p_posterior=0.6,
        edge=0.1,
        shares=20.0,
        cost_basis_usd=10.0,
        entered_at="2026-05-01T00:00:00Z",
        decision_snapshot_id="snap-1",
        entry_method="ens_member_counting",
        strategy_key="center_buy",
        strategy="center_buy",
        edge_source="center_buy",
        discovery_mode="update_reaction",
        corrected_executable_economics_eligible=True,
        state="pending_tracked",
        chain_state="local_only",
        token_id="tok-1",
    )


# ---------------------------------------------------------------------------
# 1. placeholder_envelope_blocked_total
# ---------------------------------------------------------------------------

class TestPlaceholderEnvelopeBlockedTotal:
    """adapter.py submit() emits placeholder_envelope_blocked_total on legacy: envelope."""

    def test_typed_sink_incremented(self, tmp_path):
        """Typed sink reads 1 after submit() blocks a placeholder envelope."""
        adapter = _build_adapter(tmp_path)
        envelope = _make_placeholder_envelope()

        adapter.submit(envelope)

        assert read("placeholder_envelope_blocked_total") == 1

    def test_legacy_log_line_fires(self, tmp_path, caplog):
        """Legacy logger.warning with 'telemetry_counter event=placeholder_envelope_blocked_total' fires."""
        adapter = _build_adapter(tmp_path)
        envelope = _make_placeholder_envelope()

        with caplog.at_level(logging.WARNING, logger="src.venue.polymarket_v2_adapter"):
            adapter.submit(envelope)

        assert any(
            "telemetry_counter event=placeholder_envelope_blocked_total" in r.message
            for r in caplog.records
        ), f"Expected log line not found. Records: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# 2. compat_submit_rejected_total
# ---------------------------------------------------------------------------

class TestCompatSubmitRejectedTotal:
    """adapter.py submit_limit_order() emits compat_submit_rejected_total."""

    def test_typed_sink_incremented(self, tmp_path):
        """Typed sink reads 1 after submit_limit_order() rejects in live mode."""
        adapter = _build_adapter(tmp_path)

        adapter.submit_limit_order(
            token_id="legacy:tok",
            price=0.5,
            size=10.0,
            side="BUY",
        )

        assert read("compat_submit_rejected_total") == 1

    def test_legacy_log_line_fires(self, tmp_path, caplog):
        """Legacy logger.warning with 'telemetry_counter event=compat_submit_rejected_total' fires."""
        adapter = _build_adapter(tmp_path)

        with caplog.at_level(logging.WARNING, logger="src.venue.polymarket_v2_adapter"):
            adapter.submit_limit_order(
                token_id="legacy:tok",
                price=0.5,
                size=10.0,
                side="BUY",
            )

        assert any(
            "telemetry_counter event=compat_submit_rejected_total" in r.message
            for r in caplog.records
        ), f"Expected log line not found. Records: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# 3. db_write_lock_timeout_total
# ---------------------------------------------------------------------------

class TestDbWriteLockTimeoutTotal:
    """db.py _handle_db_write_lock emits db_write_lock_timeout_total."""

    def test_typed_sink_incremented(self):
        """Typed sink reads 1 after _handle_db_write_lock is called."""
        from src.state.db import _handle_db_write_lock

        exc = sqlite3.OperationalError("database is locked")
        _handle_db_write_lock(exc)

        assert read("db_write_lock_timeout_total") == 1

    def test_legacy_log_line_fires(self, caplog):
        """Legacy logger.warning with 'telemetry_counter event=db_write_lock_timeout_total' fires."""
        from src.state.db import _handle_db_write_lock

        exc = sqlite3.OperationalError("database is locked")

        with caplog.at_level(logging.WARNING, logger="src.state.db"):
            _handle_db_write_lock(exc)

        assert any(
            "telemetry_counter event=db_write_lock_timeout_total" in r.message
            for r in caplog.records
        ), f"Expected log line not found. Records: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# 4. position_loader_field_defaulted_total  (labels={field})
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field_name", ["entry_price", "cost_basis_usd", "size_usd", "shares"])
class TestPositionLoaderFieldDefaultedTotal:
    """portfolio.py _load_d6_field emits position_loader_field_defaulted_total{field}."""

    def test_typed_sink_incremented(self, field_name):
        """Typed sink reads 1 for the given field after _load_d6_field sees None."""
        from src.state.portfolio import _load_d6_field

        row = {}  # field_name absent -> None -> counter fires
        _load_d6_field(row, field_name, default=0.0)

        assert read("position_loader_field_defaulted_total", labels={"field": field_name}) == 1

    def test_legacy_log_line_fires(self, field_name, caplog):
        """Legacy logger.warning with 'telemetry_counter event=position_loader_field_defaulted_total' fires."""
        from src.state.portfolio import _load_d6_field

        row = {}

        with caplog.at_level(logging.WARNING, logger="src.state.portfolio"):
            _load_d6_field(row, field_name, default=0.0)

        assert any(
            "telemetry_counter event=position_loader_field_defaulted_total" in r.message
            for r in caplog.records
        ), f"Expected log line not found. Records: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# 5. position_projection_field_dropped_total  (labels={field})
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field_name", ["entry_price", "size_usd"])
class TestPositionProjectionFieldDroppedTotal:
    """portfolio.py _project_d6_field emits position_projection_field_dropped_total{field}."""

    def _make_pos(self):
        from src.state.portfolio import Position
        return Position(
            trade_id="proj-pos",
            market_id="mkt-1",
            city="NYC",
            cluster="US-Northeast",
            target_date="2026-05-01",
            bin_label="39-40F",
            direction="buy_yes",
            unit="F",
            size_usd=10.0,
            entry_price=0.5,
            p_posterior=0.6,
            edge=0.1,
            shares=20.0,
            cost_basis_usd=10.0,
            entered_at="2026-05-01T00:00:00Z",
            decision_snapshot_id="snap-1",
            entry_method="ens_member_counting",
            strategy_key="center_buy",
            strategy="center_buy",
            edge_source="center_buy",
            discovery_mode="update_reaction",
            corrected_executable_economics_eligible=True,
        )

    def test_typed_sink_incremented(self, field_name):
        """Typed sink reads 1 after _project_d6_field drops a chain value."""
        from src.state.portfolio import _project_d6_field

        pos = self._make_pos()
        _project_d6_field(pos, field_name, chain_value=0.7, fill_authority_value=0.5)

        assert read("position_projection_field_dropped_total", labels={"field": field_name}) == 1

    def test_legacy_log_line_fires(self, field_name, caplog):
        """Legacy logger.warning with 'telemetry_counter event=position_projection_field_dropped_total' fires."""
        from src.state.portfolio import _project_d6_field

        pos = self._make_pos()

        with caplog.at_level(logging.WARNING, logger="src.state.portfolio"):
            _project_d6_field(pos, field_name, chain_value=0.7, fill_authority_value=0.5)

        assert any(
            "telemetry_counter event=position_projection_field_dropped_total" in r.message
            for r in caplog.records
        ), f"Expected log line not found. Records: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# 6. cost_basis_chain_mutation_blocked_total  (labels={field})
# ---------------------------------------------------------------------------
# All 4 field labels are covered via parametrize over the RESCUE branch.
# The RESCUE branch fires entry_price, cost_basis_usd, size_usd, shares
# when chain has avg_price > 0, cost > 0, size > 0 and eligible=True.

@pytest.mark.parametrize("field_name", ["entry_price", "cost_basis_usd", "size_usd", "shares"])
class TestCostBasisChainMutationBlockedTotal:
    """chain_reconciliation.py RESCUE branch emits cost_basis_chain_mutation_blocked_total{field}."""

    def _trigger_rescue_branch(self):
        """Run reconcile with an eligible pending_tracked position and matching chain data."""
        from src.state.chain_reconciliation import ChainPosition, reconcile
        from src.state.portfolio import PortfolioState
        from src.engine.lifecycle_events import build_entry_canonical_write
        from src.state.db import append_many_and_project

        pos = _make_eligible_position()
        conn = _make_reconcile_conn()

        # Pre-populate canonical entry baseline so rescue can proceed
        entry_events, entry_projection = build_entry_canonical_write(
            pos, decision_id="dec-1", source_module="src.engine.cycle_runtime"
        )
        append_many_and_project(conn, entry_events, entry_projection)

        portfolio = PortfolioState(positions=[pos])
        chain_pos = ChainPosition(
            token_id="tok-1",
            size=25.0,       # differs → shares counter
            avg_price=0.6,   # differs → entry_price counter
            cost=15.0,       # differs → cost_basis_usd + size_usd counters
            condition_id="cond-1",
        )

        reconcile(portfolio, [chain_pos], conn=conn)
        conn.close()

    def test_typed_sink_incremented(self, field_name):
        """Typed sink reads >= 1 for the given field after eligible RESCUE branch fires."""
        self._trigger_rescue_branch()

        assert read("cost_basis_chain_mutation_blocked_total", labels={"field": field_name}) >= 1, (
            f"Expected cost_basis_chain_mutation_blocked_total{{field={field_name!r}}} >= 1"
        )

    def test_legacy_log_line_fires(self, field_name, caplog):
        """Legacy logger.warning with 'telemetry_counter event=cost_basis_chain_mutation_blocked_total' fires."""
        from src.state.chain_reconciliation import ChainPosition, reconcile
        from src.state.portfolio import PortfolioState
        from src.engine.lifecycle_events import build_entry_canonical_write
        from src.state.db import append_many_and_project

        pos = _make_eligible_position()
        conn = _make_reconcile_conn()
        entry_events, entry_projection = build_entry_canonical_write(
            pos, decision_id="dec-1", source_module="src.engine.cycle_runtime"
        )
        append_many_and_project(conn, entry_events, entry_projection)

        portfolio = PortfolioState(positions=[pos])
        chain_pos = ChainPosition(
            token_id="tok-1",
            size=25.0,
            avg_price=0.6,
            cost=15.0,
            condition_id="cond-1",
        )

        with caplog.at_level(logging.WARNING, logger="src.state.chain_reconciliation"):
            reconcile(portfolio, [chain_pos], conn=conn)
        conn.close()

        assert any(
            "telemetry_counter event=cost_basis_chain_mutation_blocked_total" in r.message
            for r in caplog.records
        ), f"Expected log line not found. Records: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# 7. harvester_learning_write_blocked_total  (labels={reason})
# All 3 reason values covered.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reason,smv,training_allowed,learning_ready,forecast_source", [
    # missing smv -> reason=missing_source_model_version_or_lineage
    ("missing_source_model_version_or_lineage", None, True, False, "tigge"),
    # empty smv -> reason=missing_source_model_version_or_lineage
    ("missing_source_model_version_or_lineage", "", True, False, "tigge"),
    # both training booleans False -> reason=missing_source_model_version_or_lineage
    ("missing_source_model_version_or_lineage", "tigge_ens_v3", False, False, "tigge"),
    # non-training source -> reason=live_praw_no_training_lineage
    ("live_praw_no_training_lineage", "openmeteo_live", True, True, "openmeteo"),
])
class TestHarvesterLearningWriteBlockedTotal:
    """harvester.py maybe_write_learning_pair emits harvester_learning_write_blocked_total{reason}."""

    def _make_context(self, smv, training_allowed, learning_ready, forecast_source):
        return {
            "source_model_version": smv,
            "snapshot_training_allowed": training_allowed,
            "snapshot_learning_ready": learning_ready,
            "temperature_metric": "high",
            "p_raw_vector": [0.2, 0.5, 0.3],
            "issue_time": "2026-05-01T00:00:00Z",
            "available_at": "2026-05-01T06:00:00Z",
            "lead_days": 3.0,
            "forecast_source": forecast_source,
            "decision_snapshot_id": None,
            "snapshot_causality_status": "OK",
        }

    def test_typed_sink_incremented(self, reason, smv, training_allowed, learning_ready, forecast_source):
        """Typed sink reads 1 for the given reason after maybe_write_learning_pair blocks."""
        from src.execution.harvester import maybe_write_learning_pair
        city = _make_city()
        ctx = self._make_context(smv, training_allowed, learning_ready, forecast_source)
        conn = _make_db_conn()

        try:
            maybe_write_learning_pair(
                conn, city, "2026-05-01", "30-35°F", ["<30°F", "30-35°F", "35-40°F"],
                ctx, temperature_metric="high",
            )
        finally:
            conn.close()

        assert read("harvester_learning_write_blocked_total", labels={"reason": reason}) == 1

    def test_legacy_log_line_fires(self, reason, smv, training_allowed, learning_ready, forecast_source, caplog):
        """Legacy logger.warning with 'telemetry_counter event=harvester_learning_write_blocked_total' fires."""
        from src.execution.harvester import maybe_write_learning_pair
        city = _make_city()
        ctx = self._make_context(smv, training_allowed, learning_ready, forecast_source)
        conn = _make_db_conn()

        try:
            with caplog.at_level(logging.WARNING, logger="src.execution.harvester"):
                maybe_write_learning_pair(
                    conn, city, "2026-05-01", "30-35°F", ["<30°F", "30-35°F", "35-40°F"],
                    ctx, temperature_metric="high",
                )
        finally:
            conn.close()

        assert any(
            "telemetry_counter event=harvester_learning_write_blocked_total" in r.message
            for r in caplog.records
        ), f"Expected log line not found. Records: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# 7b. missing_forecast_issue_time reason (tested via harvest_settlement directly)
# ---------------------------------------------------------------------------

class TestHarvesterMissingIssueTimeCounter:
    """harvest_settlement emits harvester_learning_write_blocked_total{reason=missing_forecast_issue_time}."""

    def test_typed_sink_incremented(self):
        """Typed sink reads 1 for missing_forecast_issue_time after harvest_settlement sees no issue_time."""
        from src.execution.harvester import harvest_settlement
        city = _make_city("issuecity")
        conn = _make_db_conn()

        try:
            result = harvest_settlement(
                conn, city, "2026-05-01", "30-35°F",
                ["<30°F", "30-35°F", "35-40°F"],
                p_raw_vector=[0.2, 0.5, 0.3],
                forecast_issue_time=None,  # missing — triggers counter
                forecast_available_at="2026-05-01T06:00:00Z",
                source_model_version="tigge_ens_v3",
                lead_days=3.0,
                temperature_metric="high",
            )
        finally:
            conn.close()

        assert result == 0
        assert read(
            "harvester_learning_write_blocked_total",
            labels={"reason": "missing_forecast_issue_time"},
        ) == 1

    def test_legacy_log_line_fires(self, caplog):
        """Legacy logger.warning fires for missing_forecast_issue_time."""
        from src.execution.harvester import harvest_settlement
        city = _make_city("issuecity2")
        conn = _make_db_conn()

        try:
            with caplog.at_level(logging.WARNING, logger="src.execution.harvester"):
                harvest_settlement(
                    conn, city, "2026-05-01", "30-35°F",
                    ["<30°F", "30-35°F", "35-40°F"],
                    p_raw_vector=[0.2, 0.5, 0.3],
                    forecast_issue_time=None,
                    forecast_available_at="2026-05-01T06:00:00Z",
                    source_model_version="tigge_ens_v3",
                    lead_days=3.0,
                    temperature_metric="high",
                )
        finally:
            conn.close()

        assert any(
            "telemetry_counter event=harvester_learning_write_blocked_total" in r.message
            for r in caplog.records
        ), f"Expected log line not found. Records: {[r.message for r in caplog.records]}"
