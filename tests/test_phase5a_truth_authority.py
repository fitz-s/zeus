# Lifecycle: created=2026-04-17; last_reviewed=2026-04-17; last_reused=never
# Purpose: Phase 5A truth-authority foundation tests (R-AB, R-AC, R-AD, R-AE)
# Reuse: Inspect PortfolioState, read_runtime_truth_json, portfolio_loader_view,
#        save_portfolio, and write_status signatures before running — tests are
#        spec-anchored to B069/B073/B077/MAJOR-4; R-AE RED until exec-emma wires
#        authority= at production call sites.
"""Phase 5A truth authority tests: R-AB, R-AC, R-AD

Tests anchored to SPEC semantics from the coordination handoff (B069/B073/B077)
and the remediation plan, NOT to code-on-disk signatures. Code-on-disk is
intentionally incomplete; tests must fail RED until exec-emma implements.

R-AB (PortfolioState.authority): PortfolioState carries authority: Literal field.
    load_portfolio() with DB outage returns authority="unverified". Risk-sizing
    callers must assert authority before consuming state.

R-AC (RuntimeStateMismatchError): read_runtime_truth_json(filename) rejects retired
    selector tags and returns live runtime-state metadata on valid files.

R-AD (MetricIdentity view-layer): portfolio_loader_view emits temperature_metric
    on every position row. Empty low-book and empty high-book are DISTINGUISHABLE
    as separate views, not a single undifferentiated empty default.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# R-AB: PortfolioState.authority field + load_portfolio authority propagation
# ---------------------------------------------------------------------------


class TestPortfolioStateAuthority:
    """R-AB: PortfolioState must carry an authority Literal field.

    B069 root cause: load_portfolio synthesizes defaults on DB outage; callers
    cannot distinguish a legitimate-empty portfolio from a degraded one without
    a first-class authority field.
    """

    def test_portfolio_state_has_authority_field(self):
        """R-AB (rejection): PortfolioState without authority= kwarg must raise TypeError.

        The field must be declared as a dataclass field; constructing without it
        (when the field is required) exposes the gap. We test the attribute exists
        via introspection to confirm the field is defined.
        """
        from src.state.portfolio import PortfolioState

        # The field must exist as a declared attribute.
        # If it does not exist, AttributeError fires here — correct RED.
        ps = PortfolioState(positions=[], bankroll=211.37)
        _ = ps.authority  # AttributeError if field absent

    def test_portfolio_state_authority_accepts_canonical_db(self):
        """R-AB (acceptance): authority='canonical_db' must be a valid value."""
        from src.state.portfolio import PortfolioState

        ps = PortfolioState(positions=[], bankroll=211.37, authority="canonical_db")
        assert ps.authority == "canonical_db"

    def test_portfolio_state_authority_accepts_degraded(self):
        """R-AB (acceptance): authority='degraded' must be a valid value."""
        from src.state.portfolio import PortfolioState

        ps = PortfolioState(positions=[], bankroll=211.37, authority="degraded")
        assert ps.authority == "degraded"

    def test_portfolio_state_authority_accepts_unverified(self):
        """R-AB (acceptance): authority='unverified' must be a valid value."""
        from src.state.portfolio import PortfolioState

        ps = PortfolioState(positions=[], bankroll=211.37, authority="unverified")
        assert ps.authority == "unverified"

    def test_load_portfolio_db_outage_returns_unverified_authority(self):
        """R-AB (rejection path): load_portfolio with DB connection error returns authority='unverified'.

        This is the B069 antibody: DB outage must not silently produce a state
        that looks canonical. The returned PortfolioState must have
        authority='unverified', not 'canonical_db'.
        """
        from src.state.portfolio import load_portfolio

        # load_portfolio does a local import of get_trade_connection_with_world
        # from src.state.db at call time — patch the source module, not the
        # importing module, or mock.patch raises AttributeError.
        # path="positions-live.json" sets mode_override="live" → calls
        # get_trade_connection_with_world() at portfolio.py:948.
        with patch("src.state.db.get_trade_connection_with_world", side_effect=OSError("forced DB outage")):
            with tempfile.TemporaryDirectory() as tmpdir:
                # No zeus_trades.db on disk → bypasses get_connection(trade_db) branch.
                state = load_portfolio(path=Path(tmpdir) / "positions-live.json")

        # The returned state must clearly declare it is not authoritative.
        assert state.authority == "unverified", (
            f"Expected authority='unverified' on DB-outage path, got {state.authority!r}"
        )

    def test_load_portfolio_healthy_db_returns_canonical_db_authority(self):
        """R-AB (acceptance path): load_portfolio with healthy empty DB returns authority='canonical_db'.

        An empty canonical DB is a valid state (zero positions). The returned
        PortfolioState must have authority='canonical_db', not 'unverified'.
        """
        from src.state.portfolio import load_portfolio
        from src.state.db import init_schema
        from src.state.schema.v2_schema import apply_v2_schema

        with tempfile.TemporaryDirectory() as tmpdir:
            trade_db = Path(tmpdir) / "zeus_trades.db"
            conn = sqlite3.connect(str(trade_db))
            init_schema(conn)
            apply_v2_schema(conn)
            conn.commit()
            conn.close()

            state = load_portfolio(path=Path(tmpdir) / "positions-live.json")

        assert state.authority == "canonical_db", (
            f"Expected authority='canonical_db' on healthy-empty DB, got {state.authority!r}"
        )


# ---------------------------------------------------------------------------
# R-AC: runtime-state truth-file authority
# ---------------------------------------------------------------------------


class TestRuntimeStateTruthAuthority:
    """R-AC: runtime truth reads must reject retired selector metadata."""

    def test_runtime_state_mismatch_error_is_importable(self):
        """R-AC (existence): RuntimeStateMismatchError is the fail-closed truth-file error."""
        from src.state.truth_files import RuntimeStateMismatchError

        assert issubclass(RuntimeStateMismatchError, Exception)

    def test_read_runtime_truth_json_rejects_retired_selector_tag(self):
        """R-AC (rejection): retired selector tags cannot be consumed as live truth."""
        from src.state.truth_files import read_runtime_truth_json, RuntimeStateMismatchError

        with tempfile.TemporaryDirectory() as tmpdir:
            fname = "positions.json"
            truth_path = Path(tmpdir) / fname
            truth_path.write_text(json.dumps({
                "positions": [],
                "truth": {"mode": "legacy_env", "generated_at": "2026-04-17T00:00:00+00:00"},
            }))

            with patch("src.state.truth_files.runtime_state_path", return_value=truth_path):
                with pytest.raises(RuntimeStateMismatchError):
                    read_runtime_truth_json(fname)

    def test_read_runtime_truth_json_live_state_succeeds_and_roundtrips(self):
        """R-AC (acceptance): live runtime-state metadata is returned to callers."""
        from src.state.truth_files import read_runtime_truth_json

        with tempfile.TemporaryDirectory() as tmpdir:
            fname = "positions.json"
            truth_path = Path(tmpdir) / fname
            truth_path.write_text(json.dumps({
                "positions": [],
                "truth": {"runtime_state": "live", "generated_at": "2026-04-17T00:00:00+00:00"},
            }))

            with patch("src.state.truth_files.runtime_state_path", return_value=truth_path):
                data, truth = read_runtime_truth_json(fname)

        assert truth.get("runtime_state") == "live", (
            f"Expected runtime_state='live' in truth metadata, got {truth.get('runtime_state')!r}"
        )


# ---------------------------------------------------------------------------
# R-AD: portfolio_loader_view emits temperature_metric; low/high distinguishable
# ---------------------------------------------------------------------------


class TestPortfolioLoaderViewMetricIdentity:
    """R-AD: portfolio_loader_view must emit temperature_metric context on every row.

    Empty low-book and empty high-book must be DISTINGUISHABLE via the view
    response — not a single undifferentiated empty default.

    B069 / DT metric-spine gap: the view currently omits temperature_metric;
    callers cannot route positions to the correct track without it.
    """

    def _make_world_db_with_schema(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        from src.state.db import init_schema
        from src.state.schema.v2_schema import apply_v2_schema
        init_schema(conn)
        apply_v2_schema(conn)
        return conn

    def test_query_portfolio_loader_view_result_has_temperature_metric_key(self):
        """R-AD (rejection): view result for a non-empty position must include temperature_metric key.

        If temperature_metric is absent from any position dict, the dual-track
        router cannot route correctly. This test fails RED until the view emits it.
        """
        from src.state.db import query_portfolio_loader_view

        conn = self._make_world_db_with_schema()

        # Insert a minimal position_current row.
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, trade_id, market_id, city, cluster,
                target_date, bin_label, direction, unit, size_usd, shares,
                cost_basis_usd, entry_price, p_posterior, chain_state,
                token_id, no_token_id, condition_id, order_id, order_status,
                updated_at, strategy_key, temperature_metric
            ) VALUES (
                'pos-001', 'active', 'trade-001', 'mkt-001', 'NYC', 'NYC_F_2',
                '2026-04-18', '70-71°F', 'buy_yes', 'F', 10.0, 10.0,
                10.0, 0.5, 0.6, 'CHAIN_SYNCED',
                'tok-001', 'notok-001', 'cond-001', 'ord-001', 'filled',
                '2026-04-17T00:00:00', 'settlement_capture', 'high'
            )
            """,
        )
        conn.commit()

        result = query_portfolio_loader_view(conn)
        conn.close()

        assert result.get("status") == "ok", f"Unexpected status: {result.get('status')}"
        positions = result.get("positions", [])
        assert len(positions) == 1

        pos = positions[0]
        assert "temperature_metric" in pos, (
            "portfolio_loader_view must emit temperature_metric on every position row — key absent"
        )
        assert pos["temperature_metric"] == "high", (
            f"Expected temperature_metric='high', got {pos['temperature_metric']!r}"
        )

    def test_empty_high_book_and_empty_low_book_are_distinguishable(self):
        """R-AD (rejection): two separate view calls for high/low with empty results must differ.

        The spec requires empty-low and empty-high to be DISTINGUISHABLE. A single
        generic empty response conflates them; callers cannot tell which track is
        absent vs. which track is truly empty.

        Concretely: query_portfolio_loader_view must accept a temperature_metric
        filter parameter, or the view result must carry track context so callers
        can differentiate.
        """
        from src.state.db import query_portfolio_loader_view

        conn = self._make_world_db_with_schema()

        # Both calls on empty DB: they must be distinguishable by track.
        result_high = query_portfolio_loader_view(conn, temperature_metric="high")
        result_low = query_portfolio_loader_view(conn, temperature_metric="low")

        conn.close()

        # Distinguishability: the results must carry track context.
        # A simplest implementation: the view result includes temperature_metric
        # in its top-level metadata or positions list is keyed by track.
        assert result_high.get("temperature_metric") == "high", (
            "Empty high-track view must carry temperature_metric='high' in result metadata"
        )
        assert result_low.get("temperature_metric") == "low", (
            "Empty low-track view must carry temperature_metric='low' in result metadata"
        )
        # Both may be empty, but they are NOT the same object / same default.
        assert result_high != result_low or result_high.get("temperature_metric") != result_low.get("temperature_metric"), (
            "Empty high-track and empty low-track views must not be identical (not distinguishable)"
        )

    def test_query_portfolio_loader_view_acceptance_correct_metric_passes_through(self):
        """R-AD (acceptance): filtering by temperature_metric='high' returns only high rows."""
        from src.state.db import query_portfolio_loader_view

        conn = self._make_world_db_with_schema()

        # Insert one high and one low position.
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, trade_id, market_id, city, cluster,
                target_date, bin_label, direction, unit, size_usd, shares,
                cost_basis_usd, entry_price, p_posterior, chain_state,
                token_id, no_token_id, condition_id, order_id, order_status,
                updated_at, strategy_key, temperature_metric
            ) VALUES
            ('pos-001','active','trade-001','mkt-001','NYC','NYC_F_2',
             '2026-04-18','70-71°F','buy_yes','F',10.0,10.0,10.0,0.5,0.6,
             'CHAIN_SYNCED','tok-001','notok-001','cond-001','ord-001','filled',
             '2026-04-17T00:00:00','settlement_capture','high'),
            ('pos-002','active','trade-002','mkt-002','NYC','NYC_F_2',
             '2026-04-18','50-51°F','buy_yes','F',10.0,10.0,10.0,0.4,0.5,
             'CHAIN_SYNCED','tok-002','notok-002','cond-002','ord-002','filled',
             '2026-04-17T00:00:00','settlement_capture','low')
            """,
        )
        conn.commit()

        result = query_portfolio_loader_view(conn, temperature_metric="high")
        conn.close()

        positions = result.get("positions", [])
        assert len(positions) == 1, f"Expected 1 high-track position, got {len(positions)}"
        assert positions[0]["temperature_metric"] == "high"


# ---------------------------------------------------------------------------
# R-AC supplement: build_truth_metadata / annotate_truth_payload authority round-trip
# (critic-alice pre-review note: B077 requires authority to propagate through truth payload)
# ---------------------------------------------------------------------------


class TestTruthMetadataAuthorityRoundTrip:
    """R-AC supplement: annotate_truth_payload must round-trip authority through truth metadata.

    B077 absorption requires that truth payloads carry an authority field so
    callers can distinguish VERIFIED from UNVERIFIED truth. The existing
    build_truth_metadata does not accept an authority argument; this test is RED
    until exec-emma adds it.
    """

    def test_build_truth_metadata_accepts_authority_kwarg(self):
        """R-AC-supp (rejection): build_truth_metadata without authority= arg must exist with the kwarg.

        If the function signature lacks authority=, calling with it raises TypeError — correct RED.
        """
        from pathlib import Path
        from src.state.truth_files import build_truth_metadata

        result = build_truth_metadata(Path("/tmp/positions.json"), authority="VERIFIED")
        assert result.get("authority") == "VERIFIED", (
            f"Expected authority='VERIFIED' in truth metadata, got {result.get('authority')!r}"
        )

    def test_build_truth_metadata_authority_defaults_to_unverified(self):
        """R-AC-supp (acceptance): omitting authority= must default to 'UNVERIFIED'."""
        from pathlib import Path
        from src.state.truth_files import build_truth_metadata

        result = build_truth_metadata(Path("/tmp/positions.json"))
        assert result.get("authority") == "UNVERIFIED", (
            f"Expected default authority='UNVERIFIED', got {result.get('authority')!r}"
        )

    def test_annotate_truth_payload_roundtrips_authority(self):
        """R-AC-supp (acceptance): annotate_truth_payload must propagate authority into payload['truth']."""
        from pathlib import Path
        from src.state.truth_files import annotate_truth_payload

        payload = {"positions": [], "bankroll": 211.37}
        result = annotate_truth_payload(payload, Path("/tmp/positions.json"), authority="VERIFIED")

        assert isinstance(result.get("truth"), dict), "annotate_truth_payload must inject 'truth' dict"
        assert result["truth"].get("authority") == "VERIFIED", (
            f"Expected truth['authority']='VERIFIED', got {result['truth'].get('authority')!r}"
        )


# ---------------------------------------------------------------------------
# R-AE: production call sites must not stamp UNVERIFIED on canonical writes
# (MAJOR-4 regression: save_portfolio + write_status must pass authority= through)
# ---------------------------------------------------------------------------

# Authority mapping per MAJOR-4 round-2 ruling (2026-04-17):
# canonical_db + degraded → "VERIFIED" (loaded from authoritative source, even if non-canonical)
# unverified → "UNVERIFIED" (DB connection failed; do not trust)
_PORTFOLIO_AUTHORITY_TO_TRUTH = {
    "canonical_db": "VERIFIED",
    "degraded": "VERIFIED",
    "unverified": "UNVERIFIED",
}


class TestAnnotateTruthPayloadProductionCallers:
    """R-AE: production writers must stamp truth['authority'] correctly.

    MAJOR-4 root cause: both save_portfolio (portfolio.py:1079) and write_status
    (status_summary.py:399) call annotate_truth_payload without passing authority=.
    Result: every JSON sidecar lands authority='UNVERIFIED' regardless of the
    actual PortfolioState.authority value. canonical_db state must produce
    truth['authority']='VERIFIED'; unverified state must produce 'UNVERIFIED'.
    """

    def test_save_portfolio_canonical_db_stamps_verified(self):
        """R-AE.1 (rejection): save_portfolio with canonical_db state must write truth['authority']='VERIFIED'.

        Currently fails because portfolio.py:1079 does not pass authority= to
        annotate_truth_payload — the written JSON always gets 'UNVERIFIED'.
        """
        import json
        import tempfile
        from pathlib import Path
        from src.state.portfolio import PortfolioState, save_portfolio

        state = PortfolioState(positions=[], bankroll=211.37, authority="canonical_db")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "positions-live.json"
            save_portfolio(state, path=path)
            written = json.loads(path.read_text())

        truth_authority = written.get("truth", {}).get("authority")
        assert truth_authority == "VERIFIED", (
            f"save_portfolio with authority='canonical_db' must write truth['authority']='VERIFIED', "
            f"got {truth_authority!r}"
        )

    def test_save_portfolio_unverified_stamps_unverified(self):
        """R-AE.1 (acceptance): save_portfolio with unverified state must write truth['authority']='UNVERIFIED'."""
        import json
        import tempfile
        from pathlib import Path
        from src.state.portfolio import PortfolioState, save_portfolio

        state = PortfolioState(positions=[], bankroll=211.37, authority="unverified")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "positions-live.json"
            save_portfolio(state, path=path)
            written = json.loads(path.read_text())

        truth_authority = written.get("truth", {}).get("authority")
        assert truth_authority == "UNVERIFIED", (
            f"save_portfolio with authority='unverified' must write truth['authority']='UNVERIFIED', "
            f"got {truth_authority!r}"
        )

    def test_save_portfolio_degraded_stamps_degraded_projection(self):
        """R-AE.1 (REVERSED): save_portfolio with degraded state must write truth['authority']='DEGRADED_PROJECTION'.

        2026-04-26 reversal of the 2026-04-17 MAJOR-4 round-2 ruling. PR #18
        (Refresh execution-state truth operations package) and INV-23 establish
        that a degraded loader signals lost canonical authority; the export must
        not collapse to VERIFIED.
        """
        import json
        import tempfile
        from pathlib import Path
        from src.state.portfolio import PortfolioState, save_portfolio

        state = PortfolioState(positions=[], bankroll=211.37, authority="degraded")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "positions-live.json"
            save_portfolio(state, path=path)
            written = json.loads(path.read_text())

        truth_authority = written.get("truth", {}).get("authority")
        assert truth_authority == "DEGRADED_PROJECTION", (
            f"save_portfolio with authority='degraded' must write truth['authority']='DEGRADED_PROJECTION' (INV-23), "
            f"got {truth_authority!r}"
        )

    def test_build_truth_metadata_default_is_fail_closed_unverified(self):
        """R-AE.3: build_truth_metadata default authority='UNVERIFIED' is intentional fail-closed.

        This test is a companion to test_build_truth_metadata_authority_defaults_to_unverified.
        It exists so a future refactor cannot quietly flip the default to 'VERIFIED' without
        a failing test surfacing immediately. The default MUST be fail-closed: unknown authority
        is treated as unverified, not as verified.
        """
        from pathlib import Path
        from src.state.truth_files import build_truth_metadata

        result_no_arg = build_truth_metadata(Path("/tmp/test.json"))
        result_explicit = build_truth_metadata(Path("/tmp/test.json"), authority="UNVERIFIED")

        # Both must be identical in authority — default == explicit UNVERIFIED
        assert result_no_arg.get("authority") == "UNVERIFIED", (
            "build_truth_metadata default must be fail-closed UNVERIFIED — "
            f"got {result_no_arg.get('authority')!r}"
        )
        assert result_no_arg.get("authority") == result_explicit.get("authority"), (
            "Default authority must equal explicit authority='UNVERIFIED' — fail-closed invariant"
        )
