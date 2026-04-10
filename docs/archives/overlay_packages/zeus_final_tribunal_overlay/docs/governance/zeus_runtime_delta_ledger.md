File: docs/governance/zeus_runtime_delta_ledger.md
Disposition: NEW
Authority basis: current repo runtime inspection; foundation package inspection; Session 1 and Session 2 dossiers.
Supersedes / harmonizes: untracked migration mismatch memory.
Why this file exists now: the repo needs one explicit place where target-law and current-runtime mismatch are recorded honestly.
Current-phase or long-lived: Current-phase only.

# Zeus Runtime Delta Ledger

## Rule
Every item below is a current-vs-target mismatch that must remain explicit until resolved.

| ID | Mismatch | Class | Why it matters | Action now | Later action |
|---|---|---|---|---|---|
| DELTA-01 | Foundation package references files not present in the uploaded foundation zip (`tests/test_cross_module_invariants.py`, `docs/reference/zeus_first_principles_rethink.md`, `ZEUS_PROGRESS.md`) | foundation completeness | blocks claims that the uploaded foundation package is fully self-contained | record openly; do not make CI assumptions silently | restore files or update manifests/workflows |
| DELTA-02 | Current repo has no root/scoped `AGENTS.md` | instruction surface | keeps `.claude/CLAUDE.md` as de facto primary | install AGENTS | retire shim later |
| DELTA-03 | `.claude/CLAUDE.md` still routes to old architectural authority | authority drift | keeps parallel authority active | patch to compatibility shim | retire or retain by human choice |
| DELTA-04 | `WORKSPACE_MAP.md` still overclaims active/highest authority roles | documentation drift | zero-context agents may read the wrong source first | patch to orientation-only | keep synced or sunset |
| DELTA-05 | `position_current` target table is absent from current runtime reality | target/runtime split | canonical projection not yet landed | keep target-law, do not fake convergence | land migration packet later |
| DELTA-06 | `src/state/portfolio.py` still describes positions JSON/state-object as source of truth | runtime truth drift | preserves old truth-center language | classify as transitional in docs/AGENTS | replace-as-primary later |
| DELTA-07 | `src/state/strategy_tracker.py` still defaults unknown strategy into `opening_inertia` | governance drift | violates frozen attribution grammar | patch now | keep regression test |
| DELTA-08 | `src/data/observation_client.py` still uses `date.today()` fallback in one authority-sensitive path | semantic drift | conflicts with explicit timezone-aware decision context | patch now | keep regression test |
| DELTA-09 | Checked-in `state/` artifacts do not match the code’s expected `state/zeus.db` plus mode-qualified files | false-complete illusion | the zip is not reliable as live runtime-state evidence | record as non-authoritative snapshot | verify actual deployment state separately |
| DELTA-10 | Repo audit script assumes external workspace files not provided in this session | boundary verification gap | boundary design is possible, full workspace verification is not | keep audit assumptions advisory | verify against actual workspace later |
| DELTA-11 | Historical docs still exist with load-bearing rationale | transition cost | deleting them too early loses rationale; keeping them active preserves drift | demote, do not delete immediately | archive later |

## Current verdict
The delta is still actionable.
It is not an excuse to postpone authority install.
It is the reason authority install must be truthful.
