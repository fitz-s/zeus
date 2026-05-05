# Created: 2026-05-05
# Last reused or audited: 2026-05-05
# Authority basis: docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1BD/phase.json
"""Relationship tests: D6 projection-write and loader-default counters.

T1BD-PROJECTION-DROP-COUNTER: When _track_exit serializes a corrected-eligible Position
whose D6 fields differ from FillAuthority values, position_projection_field_dropped_total{field}
fires and the projected row carries the FillAuthority value.

T1BD-LOADER-DEFAULT-COUNTER: When _position_from_projection_row reads a row missing a D6 field,
position_loader_field_defaulted_total{field} fires and the Position uses the default 0.0.
"""

import logging

import pytest

PROJECTION_COUNTER = "position_projection_field_dropped_total"
LOADER_COUNTER = "position_loader_field_defaulted_total"


def _make_eligible_position(
    *,
    entry_price: float = 0.5,
    cost_basis_usd: float = 10.0,
    size_usd: float = 10.0,
    shares: float = 20.0,
    entry_price_avg_fill: float = 0.0,
    filled_cost_basis_usd: float = 0.0,
    shares_filled: float = 0.0,
):
    from src.state.portfolio import Position
    return Position(
        trade_id="proj-pos-1",
        market_id="mkt-1",
        city="Chicago",
        cluster="US-Midwest",
        target_date="2026-05-01",
        bin_label="32-33F",
        direction="buy_yes",
        unit="F",
        size_usd=size_usd,
        entry_price=entry_price,
        p_posterior=0.55,
        edge=0.05,
        shares=shares,
        cost_basis_usd=cost_basis_usd,
        entered_at="2026-05-01T00:00:00Z",
        decision_snapshot_id="snap-proj-1",
        entry_method="ens_member_counting",
        strategy_key="center_buy",
        strategy="center_buy",
        edge_source="center_buy",
        discovery_mode="update_reaction",
        state="entered",
        order_id="ord-proj-1",
        order_status="filled",
        order_posted_at="2026-05-01T00:00:00Z",
        chain_state="synced",
        token_id="tok-proj-1",
        corrected_executable_economics_eligible=True,
        entry_price_avg_fill=entry_price_avg_fill,
        filled_cost_basis_usd=filled_cost_basis_usd,
        shares_filled=shares_filled,
    )


# ---------------------------------------------------------------------------
# _project_d6_field (via _track_exit projection dict)
# ---------------------------------------------------------------------------

class TestProjectionD6Counter:
    """T1BD-PROJECTION-DROP-COUNTER: projection write telemetry."""

    def test_project_d6_field_emits_counter_when_fill_differs(self, caplog):
        """When FillAuthority value differs from chain-set value, counter fires and fill value used."""
        from src.state.portfolio import _project_d6_field, Position

        pos = _make_eligible_position(
            entry_price=0.6,           # chain-contaminated value
            entry_price_avg_fill=0.5,  # FillAuthority value
        )

        with caplog.at_level(logging.WARNING, logger="src.state.portfolio"):
            result = _project_d6_field(pos, "entry_price", pos.entry_price, pos.entry_price_avg_fill)

        assert result == pytest.approx(0.5), (
            f"FillAuthority value 0.5 should be used in projection; got {result}"
        )
        counter_records = [
            r for r in caplog.records
            if PROJECTION_COUNTER in r.message and "field=entry_price" in r.message
        ]
        assert len(counter_records) >= 1, (
            f"Expected position_projection_field_dropped_total{{field=entry_price}}; "
            f"got records: {[r.message for r in caplog.records]}"
        )

    def test_project_d6_field_no_counter_when_values_match(self, caplog):
        """When FillAuthority value matches chain value, no counter and value passes through."""
        from src.state.portfolio import _project_d6_field

        pos = _make_eligible_position(
            size_usd=10.0,
            filled_cost_basis_usd=10.0,  # same as size_usd
        )

        with caplog.at_level(logging.WARNING, logger="src.state.portfolio"):
            result = _project_d6_field(pos, "size_usd", pos.size_usd, pos.filled_cost_basis_usd)

        assert result == pytest.approx(10.0)
        counter_records = [
            r for r in caplog.records
            if PROJECTION_COUNTER in r.message
        ]
        assert len(counter_records) == 0, (
            f"No counter expected when values match; got {counter_records}"
        )

    def test_project_d6_field_no_counter_for_legacy_positions(self, caplog):
        """eligible=False: _project_d6_field passes chain value through unchanged without counter."""
        from src.state.portfolio import Position, _project_d6_field

        pos = Position(
            trade_id="legacy-1",
            market_id="mkt-1",
            city="NYC",
            cluster="US-Northeast",
            target_date="2026-05-01",
            bin_label="39-40F",
            direction="buy_yes",
            unit="F",
            size_usd=10.0,
            entry_price=0.6,
            p_posterior=0.6,
            edge=0.1,
            shares=20.0,
            cost_basis_usd=10.0,
            entered_at="2026-05-01T00:00:00Z",
            decision_snapshot_id="snap-leg-1",
            entry_method="ens_member_counting",
            strategy_key="center_buy",
            strategy="center_buy",
            edge_source="center_buy",
            discovery_mode="update_reaction",
            state="entered",
            order_id="ord-leg-1",
            order_status="filled",
            order_posted_at="2026-05-01T00:00:00Z",
            chain_state="synced",
            token_id="tok-leg-1",
            corrected_executable_economics_eligible=False,
            entry_price_avg_fill=0.5,  # different fill value — but NOT eligible
        )

        with caplog.at_level(logging.WARNING, logger="src.state.portfolio"):
            result = _project_d6_field(pos, "entry_price", pos.entry_price, pos.entry_price_avg_fill)

        # chain value passes through unchanged for legacy
        assert result == pytest.approx(0.6)
        counter_records = [
            r for r in caplog.records
            if PROJECTION_COUNTER in r.message
        ]
        assert len(counter_records) == 0


# ---------------------------------------------------------------------------
# _load_d6_field (via _position_from_projection_row)
# ---------------------------------------------------------------------------

class TestLoaderD6Counter:
    """T1BD-LOADER-DEFAULT-COUNTER: projection row read telemetry."""

    def test_loader_emits_counter_for_missing_entry_price(self, caplog):
        """Row missing entry_price → counter fires and default 0.0 returned."""
        from src.state.portfolio import _load_d6_field

        row = {}  # entry_price missing (None)

        with caplog.at_level(logging.WARNING, logger="src.state.portfolio"):
            result = _load_d6_field(row, "entry_price")

        assert result == pytest.approx(0.0), f"Expected default 0.0; got {result}"
        counter_records = [
            r for r in caplog.records
            if LOADER_COUNTER in r.message and "field=entry_price" in r.message
        ]
        assert len(counter_records) >= 1, (
            f"Expected {LOADER_COUNTER}{{field=entry_price}}; "
            f"got: {[r.message for r in caplog.records]}"
        )

    @pytest.mark.parametrize("field_name", ["entry_price", "cost_basis_usd", "size_usd", "shares"])
    def test_loader_emits_counter_for_each_d6_field(self, field_name, caplog):
        """Each of the 4 D6 fields emits counter when missing from row."""
        from src.state.portfolio import _load_d6_field

        row = {}  # all fields missing

        with caplog.at_level(logging.WARNING, logger="src.state.portfolio"):
            result = _load_d6_field(row, field_name)

        assert result == pytest.approx(0.0)
        counter_records = [
            r for r in caplog.records
            if LOADER_COUNTER in r.message and f"field={field_name}" in r.message
        ]
        assert len(counter_records) >= 1, (
            f"Expected counter for field={field_name}; records: {[r.message for r in caplog.records]}"
        )

    def test_loader_no_counter_when_field_present(self, caplog):
        """Field present in row → no counter, value used directly."""
        from src.state.portfolio import _load_d6_field

        row = {"entry_price": 0.55}

        with caplog.at_level(logging.WARNING, logger="src.state.portfolio"):
            result = _load_d6_field(row, "entry_price")

        assert result == pytest.approx(0.55)
        counter_records = [
            r for r in caplog.records
            if LOADER_COUNTER in r.message
        ]
        assert len(counter_records) == 0, (
            f"No counter when field present; got {counter_records}"
        )

    def test_loader_full_row_missing_all_d6_fields(self, caplog):
        """Row missing all 4 D6 fields produces 4 counter emissions."""
        from src.state.portfolio import _load_d6_field

        row = {}
        d6_fields = ["entry_price", "cost_basis_usd", "size_usd", "shares"]

        with caplog.at_level(logging.WARNING, logger="src.state.portfolio"):
            for field_name in d6_fields:
                _load_d6_field(row, field_name)

        emitted = {
            r.message.split("field=")[1].strip()
            for r in caplog.records
            if LOADER_COUNTER in r.message and "field=" in r.message
        }
        assert emitted == set(d6_fields), (
            f"Expected counters for all 4 D6 fields; got {emitted}"
        )

    def test_position_from_projection_row_with_missing_d6_fields(self, caplog):
        """Full integration: _position_from_projection_row with missing D6 fields emits counters."""
        from src.state.portfolio import _position_from_projection_row

        # Minimal row with D6 fields absent
        row = {
            "trade_id": "test-loader-1",
            "market_id": "mkt-1",
            "city": "Chicago",
            "cluster": "US-Midwest",
            "target_date": "2026-05-01",
            "bin_label": "32-33F",
            "direction": "buy_yes",
            "unit": "F",
            "state": "entered",
            "phase": "active",
            "entered_at": "2026-05-01T00:00:00Z",
            "strategy_key": "center_buy",
            "chain_state": "synced",
            # D6 fields deliberately absent: entry_price, cost_basis_usd, size_usd, shares
        }

        with caplog.at_level(logging.WARNING, logger="src.state.portfolio"):
            pos = _position_from_projection_row(row, current_mode="paper")

        # Position should have defaults
        assert pos.entry_price == pytest.approx(0.0)
        assert pos.cost_basis_usd == pytest.approx(0.0)
        assert pos.size_usd == pytest.approx(0.0)
        assert pos.shares == pytest.approx(0.0)

        # All 4 counters should have fired
        emitted = {
            r.message.split("field=")[1].strip()
            for r in caplog.records
            if LOADER_COUNTER in r.message and "field=" in r.message
        }
        assert emitted == {"entry_price", "cost_basis_usd", "size_usd", "shares"}, (
            f"Expected all 4 D6 loader counters; got {emitted}"
        )
