# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Authority basis: midstream verdict v2 2026-04-23 (docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md T5.c NegRiskMarket — resolved as SDK-passthrough audit per verification finding)

"""T5.c NegRisk SDK-passthrough audit antibodies.

Plan row T5.c proposed a `NegRiskMarket` typed flag contract (new
`src/contracts/neg_risk.py`) BUT qualified: "verify py-clob-client
behavior first — may reduce to typed passthrough". Verification done
2026-04-23 resolved T5.c as pure passthrough:

1. py-clob-client auto-detects neg_risk per token via
   `ClobClient.get_neg_risk(token_id)` (client.py L441-448) with
   in-memory cache.
2. `create_order` consumes it in two places (client.py L517-520 and
   L572-575): if caller supplies `PartialCreateOrderOptions.neg_risk`
   use it, else fall back to the auto-detection path.
3. Zeus src/ has **ZERO** `neg_risk` / `negRisk` override paths —
   production trading relies entirely on the SDK auto-detection.
4. Polymarket weather markets (bins of one city-date high-or-low temp
   resolution) are conceptually neg-risk (exactly one bin resolves
   YES), but neg-risk semantics are already handled at the SDK /
   on-chain exchange-contract layer — Zeus has no semantic role to
   play.

A new `NegRiskMarket(value: bool)` dataclass with one Boolean field
and no invariants would add zero semantic guard beyond the existing
type annotation. Rather than landing a passthrough wrapper that
violates the Fitz K<<N principle ("don't add structure for problems
that don't exist"), T5.c is resolved as SDK-behavior audit.

These tests preserve the passthrough assumption across future Polymarket
SDK upgrades and Zeus refactors. If py-clob-client ever changes its
neg_risk contract OR if Zeus ever starts overriding explicitly, these
tests fire and force a re-evaluation (probably promote T5.c to a
real typed-contract slice at that point).

Coverage:
- SDK contract: ClobClient.get_neg_risk(token_id) exists and is
  callable.
- SDK contract: PartialCreateOrderOptions carries `neg_risk: Optional[bool]`.
- Zeus assumption: no `neg_risk` / `negRisk` string-literal references
  in src/ (passthrough unbroken).
- Zeus assumption: no `options.neg_risk=True` / `options.neg_risk=False`
  override patterns (would deviate from auto-detection and break the
  Phase1 SDK-trust assumption).
"""

from __future__ import annotations

import inspect
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"


class TestPolymarketSDKNegRiskContract:
    """py-clob-client must continue to expose auto-detected neg_risk."""

    def test_clob_client_exposes_get_neg_risk_method(self):
        """ClobClient.get_neg_risk(token_id) is the SDK's auto-detection
        entry point. Zeus relies on it being called implicitly from
        create_order. If the SDK renames or removes it, this test fires."""
        from py_clob_client.client import ClobClient

        assert hasattr(ClobClient, "get_neg_risk"), (
            "py-clob-client must expose ClobClient.get_neg_risk(token_id); "
            "Zeus relies on this auto-detection path (no explicit overrides)"
        )
        sig = inspect.signature(ClobClient.get_neg_risk)
        # Expect self + token_id params (at minimum)
        assert "token_id" in sig.parameters, (
            "ClobClient.get_neg_risk must accept a token_id parameter"
        )

    def test_partial_create_order_options_exposes_neg_risk_field(self):
        """PartialCreateOrderOptions.neg_risk is the caller-supplied
        override knob. Zeus never sets it (relies on auto-detection),
        but SDK's acceptance of Optional[bool] is the contract we
        depend on remaining stable."""
        from py_clob_client.clob_types import PartialCreateOrderOptions

        fields = inspect.get_annotations(PartialCreateOrderOptions)
        assert "neg_risk" in fields, (
            "PartialCreateOrderOptions must declare a neg_risk field"
        )


class TestZeusPassthroughAssumptionUnbroken:
    """Zeus must not introduce neg_risk overrides — the T5.c skip
    rationale depends on the auto-detection path being the sole
    resolver."""

    @staticmethod
    def _collect_py_files(root: Path) -> list[Path]:
        """Return every *.py under root, skipping __pycache__ and
        package install directories."""
        return [
            p
            for p in root.rglob("*.py")
            if "__pycache__" not in p.parts
            and ".venv" not in p.parts
        ]

    def test_no_neg_risk_string_literals_in_zeus_src(self):
        """A grep-style scan of src/ for neg_risk / negRisk / NegRisk.
        Hit count > 0 means Zeus has started referencing the concept
        directly, which breaks the T5.c-resolved-as-skip rationale
        and warrants promoting to a real typed-contract slice.

        The scan reads source bytes (not AST) so comments / docstrings
        / string literals are ALL caught — we want maximum sensitivity
        here because any form of `neg_risk` reference indicates the
        passthrough assumption is eroding."""
        needles = ("neg_risk", "negRisk", "NegRisk")
        hits: list[tuple[str, int, str]] = []
        for py_file in self._collect_py_files(_SRC_ROOT):
            text = py_file.read_text()
            for lineno, line in enumerate(text.splitlines(), start=1):
                for needle in needles:
                    if needle in line:
                        hits.append((str(py_file.relative_to(_REPO_ROOT)), lineno, line.strip()))
                        break

        assert not hits, (
            "Zeus src/ must not reference neg_risk — T5.c resolution "
            "depends on py-clob-client auto-detection as sole resolver. "
            "If this fires, promote T5.c to a real typed-contract slice "
            "that models the concept explicitly, and drop this audit. "
            f"Hits: {hits[:10]}" + (f" (+{len(hits) - 10} more)" if len(hits) > 10 else "")
        )

    def test_no_partial_create_order_options_override_pattern(self):
        """Defense against a subtler leak: Zeus passing
        PartialCreateOrderOptions(neg_risk=...) at order-create time.
        Even without the literal `neg_risk` substring, the
        PartialCreateOrderOptions constructor is the only way to
        inject an override. Scan for its import + instantiation in
        src/."""
        hits: list[tuple[str, int, str]] = []
        for py_file in self._collect_py_files(_SRC_ROOT):
            text = py_file.read_text()
            if "PartialCreateOrderOptions" in text:
                for lineno, line in enumerate(text.splitlines(), start=1):
                    if "PartialCreateOrderOptions" in line:
                        hits.append((str(py_file.relative_to(_REPO_ROOT)), lineno, line.strip()))
        assert not hits, (
            "Zeus src/ must not instantiate or import "
            "PartialCreateOrderOptions — doing so is the only vector "
            "for overriding neg_risk and breaks the T5.c passthrough "
            f"assumption. Hits: {hits}"
        )
