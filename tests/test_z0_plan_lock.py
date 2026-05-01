# Lifecycle: created=2026-04-27; last_reviewed=2026-05-01; last_reused=2026-05-01
# Purpose: Post-R3 enduring V1 SDK live-path import gate (the only remaining
#          antibody after R3 plan-lock retirement).
# Reuse: Run when src/data/polymarket_client.py, src/execution/executor.py,
#        or src/execution/exit_triggers.py touches its imports.
# Created: 2026-04-27
# Last reused/audited: 2026-05-01
# Authority basis: docs/archives/packets/task_2026-04-26_ultimate_plan/r3/slice_cards/Z0.yaml
#                  + ultrareview25_remediation 2026-05-01 B4 phase-1 cleanup
"""V1 SDK live-path import gate (post-R3 enduring).

History
-------
This file originally hosted 5 R3-Z0 plan-lock antibodies that asserted
properties of the active R3 implementation packet at
`docs/operations/task_2026-04-26_polymarket_clob_v2_migration/` and
the ultimate-plan tracker at `docs/operations/task_2026-04-26_ultimate_plan/r3/`.

Both directories were ARCHIVED on 2026-04-29 by commit `2e2f5f19`
("Workspace cleanup: archive pre-launch packets"). After the archive,
4 of the 5 antibodies hit `FileNotFoundError` on the active path; the
5th (this V1-SDK gate) was gated on `_phase_status.yaml` which was also
archived, so it never ran its substantive check.

The 4 doc-only antibodies are retired in B4 phase 1 (their WHY is gone:
R3 is complete and the packet is archived). The V1-SDK import gate has
permanent forward-looking value (preventing a regression where a future
operator re-imports `py_clob_client` v1 in a live path) and is preserved
here unconditional — no longer gated on Z2-COMPLETE since R3 itself is
COMPLETE.

What this file enforces today
-----------------------------
The live placement code paths
(`src/data/polymarket_client.py`, `src/execution/executor.py`,
`src/execution/exit_triggers.py`) MUST NOT import the V1 Polymarket SDK
(`from py_clob_client `). The V2 SDK (`py_clob_client_v2`) is the only
permitted live placement path. This is the K1 frozen-kernel enforcement
that survives independent of any plan-lock packet structure.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_no_live_path_imports_v1_sdk() -> None:
    """The three documented live-placement paths must not import the V1
    SDK. Permanent post-R3 antibody (no longer gated on phase status —
    R3 itself is COMPLETE; the antibody is forward-looking forever).
    """
    live_paths = [
        ROOT / "src/data/polymarket_client.py",
        ROOT / "src/execution/executor.py",
        ROOT / "src/execution/exit_triggers.py",
    ]
    offenders = [
        str(path.relative_to(ROOT))
        for path in live_paths
        if "from py_clob_client " in _read(path)
    ]
    assert offenders == [], (
        f"V1 SDK import detected in live placement path(s): {offenders}. "
        "The V2 SDK (`py_clob_client_v2`) is the only permitted live "
        "placement path per R3 Z2 cutover. Refactor to V2 imports before "
        "merging."
    )
