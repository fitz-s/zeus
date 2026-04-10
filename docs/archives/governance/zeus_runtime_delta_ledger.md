File: docs/governance/zeus_runtime_delta_ledger.md
Disposition: NEW
Authority basis: current repo runtime inspection; foundation package inspection; Session 1 and Session 2 dossiers.
Supersedes / harmonizes: untracked migration mismatch memory.
Why this file exists now: the repo needs one explicit place where target-law and current-runtime mismatch are recorded honestly.
Current-phase or long-lived: Current-phase only.

# Zeus Runtime Delta Ledger

## Rule
Every item below is a current-vs-target mismatch that must remain explicit until resolved.

| ID | Mismatch | Status | Class | Why it matters | Action now | Later action |
|---|---|---|---|---|---|---|
| DELTA-01 | Foundation package references files not present in the uploaded foundation zip (`tests/test_cross_module_invariants.py`, `docs/KEY_REFERENCE/zeus_first_principles_rethink.md`, `ZEUS_PROGRESS.md`) | open | foundation completeness | blocks claims that the uploaded foundation package is fully self-contained | record openly; do not make CI assumptions silently | restore files or update manifests/workflows |
| DELTA-02 | Current repo has no root/scoped `AGENTS.md` | resolved | instruction surface | kept `.claude/CLAUDE.md` as de facto primary | root/scoped `AGENTS.md` installed | keep AGENTS authoritative and retire shim later if desired |
| DELTA-03 | `.claude/CLAUDE.md` still routes to old architectural authority | resolved | authority drift | kept parallel authority active | patched to compatibility shim | retire or retain by human choice |
| DELTA-04 | `WORKSPACE_MAP.md` still overclaims active/highest authority roles | resolved | documentation drift | zero-context agents could read the wrong source first | patched to orientation-only | keep synced or sunset |
| DELTA-05 | `position_current` target table is absent from current runtime reality | open | target/runtime split | canonical projection not yet landed | keep target-law, do not fake convergence | land migration packet later |
| DELTA-06 | `src/state/portfolio.py` still describes positions JSON/state-object as source of truth | open | runtime truth drift | preserves old truth-center language | classify as transitional in docs/AGENTS | replace-as-primary later |
| DELTA-07 | `src/state/strategy_tracker.py` still defaults unknown strategy into `opening_inertia` | open | governance drift | violates frozen attribution grammar | patch in `P-STATE-01` | keep regression test |
| DELTA-08 | `src/data/observation_client.py` still uses `date.today()` fallback in one authority-sensitive path | open | semantic drift | conflicts with explicit timezone-aware decision context | patch in `P-STATE-01` | keep regression test |
| DELTA-09 | Checked-in `state/` artifacts do not match the code’s expected `state/zeus.db` plus mode-qualified files | open | false-complete illusion | the zip is not reliable as live runtime-state evidence | record as non-authoritative snapshot | verify actual deployment state separately |
| DELTA-10 | Repo audit script assumes external workspace files not provided in this session | narrowed | boundary verification gap | boundary design is possible, full workspace verification is not | keep external workspace assumptions advisory; do not let them become repo-authority blockers | verify against actual workspace later |
| DELTA-11 | Historical docs still exist with load-bearing rationale | open | transition cost | deleting them too early loses rationale; keeping them active preserves drift | demote, do not delete immediately | archive later |

## Current verdict
- Instruction-surface drift is materially reduced (`DELTA-02` to `DELTA-04` resolved).
- Boundary audit drift is narrowed (`DELTA-10` advisory-only).
- Runtime/current-vs-target drift is still actionable (`DELTA-05` to `DELTA-09`, `DELTA-11` open).
- The delta is not an excuse to postpone authority install.
- It is the reason authority install and rollout planning must remain truthful.
