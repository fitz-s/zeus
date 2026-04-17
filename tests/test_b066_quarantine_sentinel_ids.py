"""B066 regression: chain-only quarantine Position uses QUARANTINE_SENTINEL
for all identifier-like fields so downstream classifiers cannot confuse
it with a degraded-but-live position that happens to have empty IDs.
"""
from __future__ import annotations

from src.state.portfolio import QUARANTINE_SENTINEL


class TestB066QuarantinePositionSentinelIds:
    def _build_quarantine_position(self):
        from src.state.portfolio import Position
        from src.state.lifecycle_manager import enter_chain_quarantined_runtime_state

        return Position(
            trade_id=QUARANTINE_SENTINEL,
            market_id=QUARANTINE_SENTINEL,
            city=QUARANTINE_SENTINEL,
            cluster=QUARANTINE_SENTINEL,
            target_date=QUARANTINE_SENTINEL,
            bin_label=QUARANTINE_SENTINEL,
            direction="unknown",
            size_usd=0.0,
            entry_price=0.0,
            p_posterior=0.0,
            edge=0.0,
            entered_at="unknown_entered_at",
            token_id="token_xyz_chain_only",
            state=enter_chain_quarantined_runtime_state(),
            strategy="",
            edge_source="",
            cost_basis_usd=7.0,
            shares=10.0,
            chain_state="quarantined",
            chain_shares=10.0,
        )

    def test_source_code_does_not_use_empty_string_ids_for_quarantine(self):
        """B066 regression: scan the WHOLE src/state/ tree for the
        legacy empty-string synthesis pattern paired with
        QUARANTINE_SENTINEL. A copy-paste of the original bug into
        lifecycle_manager.py or portfolio.py would have slipped past
        the first-pass single-file guard; amendment per critic review.
        """
        import pathlib
        import re

        state_dir = pathlib.Path(__file__).resolve().parent.parent / "src" / "state"
        # Match any ``Position(...)`` constructor argument list that
        # sets both ``trade_id=""`` (or market_id="") AND mentions
        # QUARANTINE_SENTINEL within a 400-char window (same
        # constructor call). This catches the copy-paste pattern
        # without flagging legitimate empty-string defaults on
        # non-quarantine Position instances.
        pattern = re.compile(
            r'trade_id\s*=\s*""[^)]{0,400}QUARANTINE_SENTINEL'
            r'|market_id\s*=\s*""[^)]{0,400}QUARANTINE_SENTINEL'
            r'|QUARANTINE_SENTINEL[^)]{0,400}trade_id\s*=\s*""'
            r'|QUARANTINE_SENTINEL[^)]{0,400}market_id\s*=\s*""',
            re.DOTALL,
        )
        offenders = []
        for py in state_dir.rglob("*.py"):
            try:
                body = py.read_text()
            except OSError:
                continue
            if pattern.search(body):
                offenders.append(str(py.relative_to(state_dir.parent.parent)))
        assert not offenders, (
            "B066 regression: quarantine Position synthesis uses "
            "empty-string trade_id/market_id in these files "
            f"(must use QUARANTINE_SENTINEL): {offenders}"
        )

    def test_quarantine_position_is_quarantine_predicate_still_works(self):
        """Sentinel-valued city must still satisfy ``is_quarantine()``."""
        pos = self._build_quarantine_position()
        assert pos.is_quarantine_placeholder, (
            "Quarantine position classification must survive the sentinel "
            "convention change."
        )
        # And the identifier fields are now the sentinel, not empty string.
        assert pos.trade_id == QUARANTINE_SENTINEL
        assert pos.market_id == QUARANTINE_SENTINEL
        assert pos.cluster == QUARANTINE_SENTINEL
        assert pos.target_date == QUARANTINE_SENTINEL
        assert pos.bin_label == QUARANTINE_SENTINEL
