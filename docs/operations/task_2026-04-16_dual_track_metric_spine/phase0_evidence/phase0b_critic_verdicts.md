# Phase 0b Critic Verdicts

## First pass: ITERATE

Findings (2 CRITICAL + 4 MAJOR):
1. CRITICAL — INV-13 ID collision (new YAML entry overwrote existing §9 aspirational cascade-constants law with Kelly executable-price).
2. CRITICAL — DT#3 FDR family canonicalization had no machine-manifest entry.
3. MAJOR — INV-19 referenced INV-05's test, not a RED-sweep-specific one.
4. MAJOR — INV-17, INV-18, INV-20 had no enforcement hooks.
5. MAJOR — NC-11..NC-14 referenced semgrep rules and tests that did not exist on disk.
6. MINOR — several style drifts in empty `negative_constraints: []` keys and thin `enforced_by` blocks.

## Main-thread prose fixes before second pass

- `zeus_current_architecture.md` §18: names INV-22 as DT#3 identifier.
- §20: clarifies INV-21 is new, separate from the §9 aspirational INV-13.
- `zeus_dual_track_architecture.md` §6 DT#5: introduces INV-21 explicitly.
- `AGENTS.md` forbidden-moves: `INV-13 / DT#5` → `INV-21 / DT#5` (with note that INV-13 remains separate).

## Executor subagent YAML + stub fixes

- Renamed YAML INV-13 → INV-21.
- Added INV-22 for DT#3.
- Fixed INV-19 test reference; dropped empty `negative_constraints: []`.
- Added enforcement hooks to INV-17/18/20/21.
- Added NC-15 for DT#3.
- Created 4 new semgrep placeholder rules with Phase-N TODO markers.
- Created `tests/test_dual_track_law_stubs.py` with 8 pytest.skip stubs, each naming the deferral Phase.

## Second pass: PASS (with 1 MINOR resolved in same commit)

- All 5 prior findings RESOLVED.
- 1 new MINOR: stale Phase-0b range "INV-14..INV-20 and NC-11..NC-14" in `zeus_dual_track_architecture.md` §10 — fixed to INV-14..INV-22 and NC-11..NC-15 before commit.
- DT#1..#7 coverage: DT#1→INV-17+NC-13, DT#2→INV-19, DT#3→INV-22+NC-15, DT#4→INV-18, DT#5→INV-21+NC-14, DT#6→INV-20, DT#7 intentionally prose-only (policy law).
- No law weakened vs prose; cross-references symmetric; stubs name real functions; YAML valid; 8 skipped 0 errors.

## Commit

df12d9c governance: Phase 0b machine manifests for dual-track + death-trap law
