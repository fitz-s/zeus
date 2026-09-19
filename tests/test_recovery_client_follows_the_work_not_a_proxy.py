"""Capital blockers are resolved by READING the venue, so the read needs a client.

`_edli_command_recovery_cycle` built its authenticated client only when a screen
CANCEL was due. With no cancel pending, `reconcile_unresolved_commands` ran with
`client=None`, and every pass that resolves an ACKED or SUBMIT_UNKNOWN_SIDE_EFFECT
row needs a venue lookup -- `_lookup_unknown_side_effect_order` returns
"unavailable" without one. The cycle therefore scanned nothing and, because
`_consume_edli_command_recovery_summary` only logs `if summary.get("scanned")`,
returned SILENTLY once a minute.

Measured 2026-09-19 over a 3 MB log tail: `edli_command_recovery` emitted exactly
two messages -- "reserving reactor handoff" x132 and "terminal EXIT residual
priority" x124 -- and never a live_tick summary, while two commands sat
non-terminal for 11.9 h with zero order facts and zero trades. Those rows then
refuse every live-trading restart (`nonterminal_commands > 0`), which is what kept
four landed fixes from ever loading and left the book without a single new order.
"""

from __future__ import annotations

import ast
from pathlib import Path

_MAIN = Path(__file__).resolve().parents[1] / "src" / "main.py"


def _cycle_source() -> str:
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_edli_command_recovery_cycle":
            return ast.get_source_segment(_MAIN.read_text(encoding="utf-8"), node) or ""
    raise AssertionError("_edli_command_recovery_cycle not found")


class TestRecoveryClientGate:
    def test_the_client_is_built_for_capital_blockers_too(self):
        """Gating an authenticated READ on a pending WRITE is a proxy, not the need."""

        source = _cycle_source()
        assert "if screen_cancel_due or capital_blockers:" in source, (
            "an ACKED / SUBMIT_UNKNOWN_SIDE_EFFECT row can only be resolved by "
            "an authenticated venue lookup; building the client solely for a "
            "screen cancel leaves those rows non-terminal forever"
        )

    def test_a_missing_adapter_no_longer_aborts_a_read_only_cadence(self):
        """A cancel still owns the cadence; a read-only blocker must not forfeit it."""

        source = _cycle_source()
        prewarm = source.index("authenticated adapter unavailable")
        tail = source[prewarm : prewarm + 900]
        assert "if screen_cancel_due:\n                return" in tail, (
            "without an adapter the DB-only recovery passes still do useful work, "
            "so only a pending cancel may abort the whole cadence"
        )

    def test_the_prepare_failure_path_clears_the_client_before_continuing(self):
        """Continuing with a half-built client would be worse than none."""

        source = _cycle_source()
        failure = source.index("authenticated adapter preparation failed")
        tail = source[failure : failure + 900]
        assert "recovery_client = None" in tail
        assert "if screen_cancel_due:\n                return" in tail
