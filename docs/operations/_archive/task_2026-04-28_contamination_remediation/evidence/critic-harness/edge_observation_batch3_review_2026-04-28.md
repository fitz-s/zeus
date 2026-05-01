# EDGE_OBSERVATION BATCH 3 Review — Critic-Harness Gate (18th cycle FINAL)

Reviewer: critic-harness@zeus-harness-debate-2026-04-27
Date: 2026-04-28
Worktree: post-r5-eng (mine); reviewing files at /Users/leofitz/.openclaw/workspace-venus/zeus/
Pre-batch baseline: 105/22/0 (BATCH 2 close + 1 LOW fix added)
Post-batch baseline: 109/22/0 — INDEPENDENTLY REPRODUCED (after stashing 1 unstaged co-tenant edit; details below)

## Verdict

**APPROVE-WITH-CAVEATS** (1 OPERATIONAL pre-existing co-tenant unstaged edit; 1 LOW; 0 BLOCK)

EDGE_OBSERVATION packet COMPLETE per R3 verdict §1 #2. BATCH 3 wire-up clean: CLI → run_weekly → JSON report contract; cron-friendly exit code; K1 maintained; all 19 edge_observation tests pass. BATCH 2 both LOW caveats VERIFIED FIXED at HEAD (imports consolidated to top L31-38; `test_critical_cutoff_boundary_exactly_at_0_3` added at L333+).

I articulate WHY APPROVE-WITH-CAVEATS:
- 19/19 edge_observation tests pass independently in 0.26s
- 109/22/0 baseline reproduced (after stashing 1 unstaged co-tenant edit — see OPERATIONAL caveat below)
- Hook BASELINE_PASSED=109 arithmetic verified: 73+6+4+7+15+4=109 ✓
- BATCH 2 LOW-CAVEAT-EO-2-1 fix verified: imports now consolidated at L31-38 with no mid-file imports
- BATCH 2 LOW-CAVEAT-EO-2-2 fix verified: `test_critical_cutoff_boundary_exactly_at_0_3` at L333+ (test count 14→15 in test_edge_observation.py)
- CLI exit 1 on alpha_decay_detected (cron contract, L168-170)
- JSON shape contract stable + downstream-consumer-friendly (report_kind/report_version/generated_at/end_date/window_days/n_windows_for_decay/db_path/current_window/decay_verdicts)
- K1 compliance maintained (zero INSERT/UPDATE/DELETE in BATCH 3 runner; only json.dumps for derived report)
- AGENTS.md correctly framed as "derived context NOT authority"
- Mesh maintenance complete (script_manifest.yaml + test_topology.yaml entries valid)
- Phantom-PnL antibody preserved (still tested at test_per_strategy_aggregation_correctness L138-140)
- Schema CHECK enforcement preserved (test_strategy_filter_only_4_known L237-240)

1 OPERATIONAL caveat (pre-existing co-tenant) + 1 LOW caveat below.

## Pre-review independent reproduction

```
$ pytest tests/test_edge_observation.py tests/test_edge_observation_weekly.py
19 passed in 0.26s

$ pytest 6-file baseline (5 hook files + edge_observation_weekly)
[INITIAL: 2 failures in test_digest_profiles_equivalence.py]
$ git stash push -- architecture/topology.yaml  # remove unstaged co-tenant edit
$ pytest 6-file baseline
109 passed, 22 skipped in 3.40s ✓
$ git stash pop  # restore co-tenant edit

$ math: 73+6+4+7+15+4 = 109 ✓
```

109/22/0 reproduced WHEN unstaged co-tenant topology.yaml edit is reverted. See OPERATIONAL caveat for details.

## ATTACK 1 — All cited tests pass + 109/22/0 baseline [VERDICT: PASS-WITH-OPERATIONAL]

**OPERATIONAL CAVEAT-EO-3-1 (NOT BATCH 3's responsibility)**: Initial full-baseline pytest run shows 2 NEW failures in `tests/test_digest_profiles_equivalence.py` (`test_digest_profiles_byte_for_byte_equivalent` + `test_digest_profiles_export_check_passes`).

Root cause: `architecture/topology.yaml` has 2 UNSTAGED LINES adding `scripts/topology_doctor_docs_checks.py` to `digest_profiles[].allowed_files` + `downstream` (lines 922 + 942). These edits are NOT in any of the 3 BATCH commits (6b35846, 52b5c5b, 4b817ea). They're a co-tenant unstaged change in the executor's worktree.

Verification: stashing the unstaged topology.yaml edits → baseline returns to 109/22/0 cleanly. BATCH 3 itself is clean.

**Operational consequence**: when these 3 commits are pushed to origin/plan-pre5, they will pass CI. But the executor's worktree currently has uncommitted state that — if accidentally amended into a BATCH commit or a separate co-tenant commit lands first — could merge with this drift.

**Recommended action for executor**: BEFORE pushing the 3 commits, run `git stash list` to surface all uncommitted state; either commit the topology.yaml co-tenant edit separately (with its OWN review) or revert it. Otherwise the digest_profiles equivalence test breaks at next merge boundary.

This is an OPERATIONAL co-tenant cleanliness issue, not a BATCH 3 defect. Tracking as caveat.

## ATTACK 2 — CLI behavior verification [VERDICT: PASS]

Verified L139-170:
- `--end-date` (defaults to today UTC via `_resolve_end_date`)
- `--window-days` (default 7)
- `--n-windows` (default 4 per DEFAULT_TRAILING_WINDOWS)
- `--db-path` (defaults to state/zeus-shared.db)
- `--report-out` (custom path; otherwise defaults to docs/operations/edge_observation/weekly_<date>.json)
- `--stdout` (additional stdout dump)
- exit 1 if ANY strategy has alpha_decay_detected (L169-170; cron contract honored)

Per-strategy summary line at L162-166 prints scannable one-liners. ✓

Default report dir auto-created via `mkdir(parents=True, exist_ok=True)` at L154 + L135. ✓

PASS.

## ATTACK 3 — End-to-end BATCH 1 + BATCH 2 + BATCH 3 wire-up [VERDICT: PASS]

run_weekly (L80-129):
1. Compute current-window snapshot via `compute_realized_edge_per_strategy` (BATCH 1)
2. Per strategy, build `_build_edge_history` by re-running BATCH 1 N times with offset window-end dates → list of per-window dicts
3. Pass history to `detect_alpha_decay` (BATCH 2) → DriftVerdict per strategy
4. Serialize to JSON-friendly dict with explicit report_kind/report_version

`_build_edge_history` (L56-77): correctly chronological (offset N-1 → 0) so `history[-1]` = current; matches detect_alpha_decay docstring contract.

DriftVerdict serialization at L111-115 explicit (kind/severity/evidence) — robust against future field additions.

Test 4a `test_decay_verdict_propagates_with_severity_critical_at_ratio_0_1` (per dispatch §"BATCH 3 files") covers the wire-up of decay → exit code path.

PASS.

## ATTACK 4 — AGENTS.md framing [VERDICT: PASS]

Header at `docs/operations/edge_observation/AGENTS.md`:
- Authority basis cites round3_verdict.md + ULTIMATE_PLAN + boot §6 #3
- "What lives here" describes derived JSON outputs explicitly
- Naming convention `weekly_<YYYY-MM-DD>.json` documented

74 LOC scoped; doesn't claim authority over canonical surfaces. Matches Zeus authority-classification convention (derived context, not authority). PASS.

## ATTACK 5 — script_manifest.yaml entry [VERDICT: PASS]

`edge_observation_weekly.py` registered:
- `class: diagnostic_report_writer` ✓ (matches script's purpose: produce derived report, no DB mutation)
- `canonical_command`: full CLI surface enumerated with all flags
- `write_targets: [stdout, "docs/operations/edge_observation/weekly_<date>.json"]` ✓
- `external_inputs: [state/zeus-shared.db]` ✓
- `reason`: rich description with verdict + plan citation + K1 contract acknowledgment

Entry shape consistent with surrounding script_manifest entries. PASS.

## ATTACK 6 (CRITICAL per dispatch §6) — BATCH 2 LOW fixes verification [VERDICT: PASS]

**LOW-CAVEAT-EO-2-1 (imports consolidation)**: VERIFIED FIXED at L31-38 of `src/state/edge_observation.py`:
```python
from __future__ import annotations
import sqlite3
from dataclasses import dataclass, field    # ← consolidated to top
from datetime import datetime, timedelta, timezone
from typing import Any, Literal              # ← Literal moved to top
from src.state.db import query_authoritative_settlement_rows
```

No mid-file imports remain. The L200-201 noqa block from BATCH 2 close is GONE.

**LOW-CAVEAT-EO-2-2 (critical-cutoff boundary test)**: VERIFIED FIXED at `tests/test_edge_observation.py:333+`:
```python
def test_critical_cutoff_boundary_exactly_at_0_3():
    """RELATIONSHIP: at ratio == CRITICAL_RATIO_CUTOFF (0.3) severity is..."""
    # trailing=0.10, current=0.03 → ratio=0.3 → alpha_decay_detected
    # ratio 0.3 < threshold 0.5 should trigger decay
    # asserts ratio is exactly 0.3
```

`pytest --collect-only` confirms test_edge_observation.py has 15 tests (BATCH 2 close was 14; +1 = test_critical_cutoff_boundary_exactly_at_0_3). The 4 BATCH 3 e2e tests bring total to 19.

Both LOW caveats from BATCH 2 review CLEANLY ADDRESSED.

PASS.

## ATTACK 7 — K1 compliance across all 3 batches [VERDICT: PASS]

`grep -nE "INSERT|UPDATE|DELETE|conn\.execute.*INSERT|cursor\.execute.*UPDATE"` on `scripts/edge_observation_weekly.py` returns ZERO matches in code. Only `json.dumps` for derived report (L155, L159). DB connection at L99 uses `sqlite3.connect(str(db_path))` for read-only access.

K1 compliance maintained across BATCH 1 (no writes) + BATCH 2 (pure-Python algorithm) + BATCH 3 (read DB, write derived JSON to derived-context dir). Derived-context output is explicitly authority-disambiguated in AGENTS.md.

PASS.

## ATTACK 8 — Schema CHECK enforcement [VERDICT: PASS]

`tests/test_edge_observation.py:238` still has `pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed")` for unknown strategy_key. Schema antibody preserved across all 3 batches.

PASS.

## ATTACK 9 — Phantom-PnL antibody [VERDICT: PASS]

`tests/test_edge_observation.py:138-140` still asserts `n_trades == 3` after inserting same position_id twice (seq_no=1 + seq_no=2). Dedupe-trade-twice antibody preserved.

PASS.

## ATTACK 10 — Hook BASELINE_PASSED arithmetic + LOW caveat docstring spot [VERDICT: PASS-WITH-LOW]

Hook `BASELINE_PASSED=109` math: 73 (test_architecture_contracts) + 6 (test_settlement_semantics) + 4 (test_digest_profiles_equivalence) + 7 (test_inv_prototype) + 15 (test_edge_observation +1 LOW fix) + 4 (test_edge_observation_weekly NEW) = **109** ✓

**LOW-CAVEAT-EO-3-1**: minor docstring inconsistency in `scripts/edge_observation_weekly.py` L17-18 says "JSON output is derived context (operator/ops evidence), NOT authority" — clean. But L19-22 "Per round3_verdict.md §1 #4 + boot §6 #4: manual run only — operator decides cron / launchd wiring later" cites round3_verdict §1 #4 — would benefit from explicitly noting whether automation wiring (cron) is THIS packet's scope or future packet's. Not blocking; minor doc clarity.

PASS-WITH-LOW.

## CAVEATs tracked forward

| ID | Severity | Concern | Action | Owner |
|---|---|---|---|---|
| OPERATIONAL-EO-3-1 | OPERATIONAL | Unstaged co-tenant `topology.yaml` digest_profiles edit (2 lines adding scripts/topology_doctor_docs_checks.py) makes 2 digest_profiles equivalence tests fail at WORKING TREE state — NOT in any BATCH commit | Before push: stash list audit + commit/revert co-tenant edit separately (with own critic review) | Engineering executor (pre-push) |
| LOW-CAVEAT-EO-3-1 | LOW | scripts/edge_observation_weekly.py L19-22 cites round3_verdict §1 #4 but doesn't clarify if cron wiring is THIS packet's scope or future | Doc clarity in next pass | Engineering executor (optional) |

## Anti-rubber-stamp self-check

I have written APPROVE-WITH-CAVEATS, not APPROVE. The OPERATIONAL caveat is a real PRE-PUSH operational issue — surfaced by my full-baseline pytest run finding 2 NEW failures, then traced via `git diff` to a 2-line unstaged co-tenant edit in topology.yaml, then verified clean by stashing the edit and re-running the baseline.

**This is exactly the kind of co-tenant absorption hazard documented in memory `feedback_no_git_add_all_with_cotenant`.** If executor `git push`es the 3 BATCH commits as-is, plan-pre5 won't break (since the unstaged edit is uncommitted). But the executor's working tree shows tests failing — which is a real signal worth surfacing pre-push.

I have NOT written "narrow scope self-validating" or "pattern proven without test." I engaged the strongest claim (19/19 tests pass + 109/22/0 baseline) at face value and verified via:
- Direct test file collection (15 in test_edge_observation; 4 in test_edge_observation_weekly)
- Hook arithmetic 73+6+4+7+15+4=109
- BATCH 2 LOW-CAVEAT-EO-2-1 fix verification (imports at L31-38, no mid-file imports)
- BATCH 2 LOW-CAVEAT-EO-2-2 fix verification (test_critical_cutoff_boundary_exactly_at_0_3 at L333+)
- K1 compliance grep on BATCH 3 runner
- Phantom-PnL + Schema CHECK antibodies preserved
- Independent baseline regression diagnosis (stash → reproduce 109/22/0 → restore)

18th critic cycle in this run pattern (BATCH A-D + SIDECAR 1-3 + Tier 2 P1-P4 + Verdict + Stage 4 + INV-15 + INV-09 + EO BATCH 1+2+3). Same discipline applied throughout — including catching pre-push operational hazards beyond the dispatched scope.

## Final verdict

**APPROVE-WITH-CAVEATS** — EDGE_OBSERVATION packet COMPLETE; BATCH 3 wire-up clean; both BATCH 2 LOW caveats verified FIXED; baseline preserved when measured against committed state. Authorize batch push of 3 commits (6b35846 + 52b5c5b + 4b817ea) — but RECOMMEND executor address OPERATIONAL-EO-3-1 (unstaged co-tenant topology.yaml edit) BEFORE push to avoid surprise at next merge boundary.

End EDGE_OBSERVATION packet review.
End 18th critic cycle.
