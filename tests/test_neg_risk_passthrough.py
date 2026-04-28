# Lifecycle: created=2026-04-23; last_reviewed=2026-04-27; last_reused=2026-04-27
# Purpose: Constrain neg-risk provenance to the venue adapter/envelope boundary.
# Reuse: Run when venue adapter, submission envelope, or Polymarket SDK boundary changes.
# Created: 2026-04-23
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z2.yaml
"""Neg-risk provenance boundary antibodies for the R3 V2 adapter.

The 2026-04-23 V1 antibody enforced "no Zeus neg_risk references" because the
legacy SDK owned auto-detection. R3 Z2 intentionally changes the boundary:
Zeus may now carry `neg_risk` only as venue/snapshot provenance inside
`VenueSubmissionEnvelope` and the V2 adapter. It still must not invent a hard
coded override or leak the concept into pricing/settlement logic.
"""

from __future__ import annotations

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
_ALLOWED_NEG_RISK_FILES = {
    "src/contracts/executable_market_snapshot_v2.py",
    "src/contracts/execution_intent.py",
    "src/contracts/venue_submission_envelope.py",
    "src/execution/exit_lifecycle.py",
    "src/execution/executor.py",
    "src/strategy/candidates/__init__.py",
    "src/strategy/candidates/neg_risk_basket.py",
    "src/state/db.py",
    "src/state/snapshot_repo.py",
    "src/state/venue_command_repo.py",
    "src/venue/polymarket_v2_adapter.py",
}


class TestZeusV2NegRiskBoundary:
    """Zeus may carry neg-risk as V2 provenance, not as strategy logic."""

    @staticmethod
    def _collect_py_files(root: Path) -> list[Path]:
        return [
            p
            for p in root.rglob("*.py")
            if "__pycache__" not in p.parts
            and ".venv" not in p.parts
        ]

    def test_neg_risk_references_are_confined_to_v2_provenance_boundary(self):
        needles = ("neg_risk", "negRisk", "NegRisk")
        offenders: list[tuple[str, int, str]] = []
        for py_file in self._collect_py_files(_SRC_ROOT):
            rel = py_file.relative_to(_REPO_ROOT).as_posix()
            text = py_file.read_text()
            for lineno, line in enumerate(text.splitlines(), start=1):
                if any(needle in line for needle in needles) and rel not in _ALLOWED_NEG_RISK_FILES:
                    offenders.append((rel, lineno, line.strip()))

        assert offenders == [], (
            "R3 Z2 allows neg_risk only in VenueSubmissionEnvelope and the "
            "PolymarketV2Adapter provenance boundary. Other references are "
            f"potential strategy/settlement leakage. Offenders: {offenders[:10]}"
        )

    def test_adapter_passes_envelope_neg_risk_without_boolean_override(self):
        adapter = _SRC_ROOT / "venue" / "polymarket_v2_adapter.py"
        text = adapter.read_text()
        assert "neg_risk=envelope.neg_risk" in text
        forbidden = ("neg_risk=True", "neg_risk = True", "neg_risk=False", "neg_risk = False")
        hits = [pattern for pattern in forbidden if pattern in text]
        assert hits == [], f"Adapter must not hard-code neg_risk overrides: {hits}"

    def test_no_partial_create_order_options_override_pattern(self):
        hits: list[tuple[str, int, str]] = []
        for py_file in self._collect_py_files(_SRC_ROOT):
            text = py_file.read_text()
            if "PartialCreateOrderOptions" in text:
                for lineno, line in enumerate(text.splitlines(), start=1):
                    if "PartialCreateOrderOptions" in line:
                        hits.append((str(py_file.relative_to(_REPO_ROOT)), lineno, line.strip()))
        assert hits == [], (
            "Zeus src/ must not instantiate or import PartialCreateOrderOptions; "
            "the V2 adapter passes the snapshot/envelope neg_risk value rather "
            f"than constructing an SDK override object. Hits: {hits}"
        )
