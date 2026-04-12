"""Relationship tests for P1 — $5 Live Safety Cap.

Cross-module property under test:
    When kelly_size() computes a raw USD proposal that exceeds live_safety_cap_usd,
    the value returned MUST be <= live_safety_cap_usd (= 5.0).
    The clip is unconditional arithmetic (min(proposal, cap)) — never branched on mode.

Step 4 pre-conditions (before Atlas implements):
    test_clip_engages_above_cap         — MUST FAIL  (no clip, no safety_cap_usd param)
    test_pass_through_below_cap         — MUST FAIL  (no safety_cap_usd param)
    test_kelly_remains_mode_unaware     — MUST PASS  (kelly.py currently clean)

Step 6 post-conditions (after Atlas implements):
    All three tests MUST PASS.

See: .omx/context/p1-safetycap_2026-04-11_freeze.md §4.2, §4.3, §4.4
"""

import re
from pathlib import Path

import pytest

from src.strategy.kelly import kelly_size


# live_safety_cap_usd from config/settings.json (closeout criterion §4.1)
CAP = 5.0

# Inputs yielding a raw Kelly proposal well ABOVE cap.
# f* = (0.9 - 0.1) / (1 - 0.1) = 0.889; size = 0.889 * 0.25 * 5000 ≈ 1111.11
_LARGE_KWARGS = dict(
    p_posterior=0.9,
    entry_price=0.1,
    bankroll=5_000.0,
    kelly_mult=0.25,
)
_LARGE_RAW_SIZE = (0.9 - 0.1) / (1.0 - 0.1) * 0.25 * 5_000.0  # ≈ 1111.11

# Inputs yielding a raw Kelly proposal well BELOW cap.
# f* = (0.52 - 0.5) / (1 - 0.5) = 0.04; size = 0.04 * 0.25 * 100 = 1.0
_SMALL_KWARGS = dict(
    p_posterior=0.52,
    entry_price=0.5,
    bankroll=100.0,
    kelly_mult=0.25,
)
_SMALL_RAW_SIZE = (0.52 - 0.5) / (1.0 - 0.5) * 0.25 * 100.0  # = 1.0


class TestKellyLiveSafetyCap:
    """P1 relationship tests: Kelly output → execution sizing boundary."""

    def test_clip_engages_above_cap(self, caplog):
        """kelly_size proposal > cap must be clipped to exactly cap.

        Relationship: kelly_size() [K3] → execution sizing boundary.
        Invariant: output <= live_safety_cap_usd when raw proposal exceeds it.

        FAILS pre-implementation: kelly_size has no safety_cap_usd parameter;
        calling with it raises TypeError. Even without that kwarg, the raw return
        (~1111) would fail the assert result == CAP.
        """
        with caplog.at_level("INFO", logger="src.strategy.kelly"):
            result = kelly_size(**_LARGE_KWARGS, safety_cap_usd=CAP)

        assert result == pytest.approx(CAP), (
            f"Expected kelly_size to clip output to {CAP} USD, "
            f"got {result:.4f} (raw would be {_LARGE_RAW_SIZE:.4f})"
        )

        cap_records = [
            r for r in caplog.records
            if getattr(r, "capped_by_safety_cap", False)
        ]
        assert cap_records, (
            "Expected a log record with capped_by_safety_cap=True when clip engages"
        )

        # The log record must carry the original pre-clip size (> cap) as some numeric field.
        record_dict = cap_records[0].__dict__
        numeric_extras = {
            k: v for k, v in record_dict.items()
            if isinstance(v, float) and k not in ("created", "relativeCreated")
        }
        assert any(v > CAP for v in numeric_extras.values()), (
            f"Expected a numeric log field carrying original pre-clip size > {CAP}. "
            f"Log record extras: {numeric_extras}"
        )

    def test_pass_through_below_cap(self, caplog):
        """kelly_size proposal <= cap must pass through unchanged; no cap log emitted.

        Relationship: kelly_size() [K3] → execution sizing boundary.
        Invariant: sub-cap proposals are not modified, no log noise introduced.

        FAILS pre-implementation: kelly_size has no safety_cap_usd parameter;
        calling with it raises TypeError.
        """
        with caplog.at_level("INFO", logger="src.strategy.kelly"):
            result = kelly_size(**_SMALL_KWARGS, safety_cap_usd=CAP)

        assert result == pytest.approx(_SMALL_RAW_SIZE), (
            f"Expected kelly_size to return raw proposal {_SMALL_RAW_SIZE} unchanged "
            f"(below cap), got {result}"
        )

        cap_records = [
            r for r in caplog.records
            if getattr(r, "capped_by_safety_cap", False)
        ]
        assert not cap_records, (
            "Expected NO capped_by_safety_cap log record for sub-cap proposal, "
            f"got {cap_records}"
        )

    def test_kelly_remains_mode_unaware(self):
        """kelly.py must contain zero references to mode/paper/live/ZEUS_MODE.

        Relationship invariant: kelly_size [K3] is a pure math module — mode
        routing is K1/K2 concern. If this pattern appears in kelly.py after the
        P1 clip is added, the live-only axiom has been violated.

        PASSES pre-implementation (kelly.py currently clean per §11 Amendment 1
        grep evidence). MUST STILL PASS after Atlas adds the clip.
        """
        kelly_path = Path(__file__).parent.parent / "src" / "strategy" / "kelly.py"
        content = kelly_path.read_text()
        pattern = re.compile(r"\b(mode|paper|live|ZEUS_MODE)\b")
        violations = [
            (i + 1, line.rstrip())
            for i, line in enumerate(content.splitlines())
            if pattern.search(line)
        ]
        assert not violations, (
            "kelly.py contains mode-discriminator references "
            "(live-only axiom violation, see freeze §4.4):\n"
            + "\n".join(f"  L{lineno}: {line}" for lineno, line in violations)
        )
