# Wave 1 Pattern Lessons — durable critic + executor standing-orders

Created: 2026-04-26
Authority basis: 7 review passes by con-nyx@zeus-live-readiness-debate across 7 slices of `task_2026-04-26_live_readiness_completion/`. Each lesson cites the slice + commit where it was surfaced or applied.
Status: Operational evidence (NOT authority). Promotion to architecture/ requires explicit operator decision.
Companion: per-slice receipts under `docs/operations/task_2026-04-26_*/receipt.json`.

## Why this exists

Cross-slice wisdom rots when it lives only in slice-specific receipts. This doc consolidates 14 pattern lessons accumulated during Wave 1 so future critics + executors ingest the package at session start, not slice-specific tail.

5 of the 14 lessons were applied IN-FLIGHT to subsequent slices in the same packet. Each application surfaced a real defect that the lesson predicted (3 production violators, 1 BLOCKER, 1 saved 1.5h scope-creep). The lessons earned their durability by being load-bearing on first reuse.

## Cross-cutting framing — Fitz Constraints

These 14 lessons are concrete tactics under 4 abstract Fitz Constraints:

- **#1 Structural decisions > patches**: lessons #3, #6, #10
- **#2 Translation loss is thermodynamic**: lessons #4, #8, #9
- **#3 Immune system > security guard**: lessons #1, #5, #7, #11, #12, #14
- **#4 Data provenance > code correctness**: lessons #2, #5, #13

The lesson catalog is the immune-system patch repertoire. The Fitz Constraints are the disease theory.

---

## Lesson 1 — Cached-state-then-assert is a smell

**Origin**: G6 BLOCKER #1 (commit `26729fd`). Boot guard at `src/main.py:491` composed `enabled = {s for s in KNOWN_STRATEGIES if is_strategy_enabled(s)}` and asserted on it — but `is_strategy_enabled` reads `_control_state["strategy_gates"]` which is empty at module load. Guard fired unconditionally even when operator had set gates.

**Rule**: Whenever a boot guard composes its decision from a module-level cache (`_control_state`, `_settings`, etc.), the test must include an integration scenario that POPULATES the cache via the production path and asserts the guard fires correctly. Atom-shape + literal-arg helper tests are necessary but not sufficient.

**Cost of skipping**: Atom tests pass; production fails on every launch. Slice ships RED-then-GREEN cleanly but never works in production.

---

## Lesson 2 — String-grep ≠ relationship test

**Origin**: G6 MAJOR #1 (commit `26729fd`). `test_main_boot_wiring_imports_assert_helper` did `assert "assert_live_safe_strategies_under_live_mode" in main_src`. The string was present; the wiring was broken. Grep gave false confidence.

**Rule**: Default to monkeypatched-env + monkeypatched-DB import + invoke. Reserve string-grep only when the function under test cannot be invoked safely. For boot guards, extract a callable helper (`_assert_live_safe_strategies_or_exit`) that integration tests can invoke directly.

**Cost of skipping**: String passes; production composition path broken; defect ships.

---

## Lesson 3 — K<<N for drift surfaces

**Origin**: G6 MAJOR #2 (commit `211d0ec`). `KNOWN_STRATEGIES` lives in 3 places: `cycle_runner.py` (set), `strategy_tracker.py` (list), `portfolio.py` (string literal + docstring). Test #3 only pinned cycle_runner; other two surfaces silently drift.

**Rule**: When a name appears in N>1 places, the antibody slice should canonicalize to 1 source-of-truth in the same packet, not just pin the first occurrence found. Future T-series followup: `src/types/strategy_identity.py` mirroring `metric_identity.py`.

**Cost of skipping**: 3 surfaces means 3 silent-drift opportunities; antibody tests pass while reality diverges.

---

## Lesson 4 — Operator-runbook rehearsal in receipt framing

**Origin**: G6 MAJOR #4 framing (commit `26729fd`). Pre-fix receipt said "Daemon refuses unless operator disables non-safe strategies via set_strategy_gate" — but the gate write isn't read at boot, so operator action does nothing. Post-fix receipt walked the actual 9-step runtime sequence with corrected gate-hydration timing.

**Rule**: For any operator-visible-breaking-change, the receipt should walk the actual operator runbook against a fresh DB (no prior overrides) before claiming "operator action required." "Daemon refuses unless X" is a different statement than "Daemon refuses regardless."

**Cost of skipping**: Receipt becomes documentation of the wish; operator follows it and crashes.

---

## Lesson 5 — Live-DB violator scan in receipt for write-time gates

**Origin**: B4 MAJOR (commit `87a1d88` + `ccc1c6d`). con-nyx audited live `state/zeus-world.db` post-slice and found 3 existing legacy violators (Lagos 89°C / Warsaw 88°C / Houston 160°F, all wu_icao_history, all VERIFIED). Forward-protection slice didn't see them; receipt didn't mention them.

**Rule**: Any slice that adds a write-time gate (physical bounds, range checks, enum tightening) should include a 5-minute live-DB scan in the receipt: "N existing rows would now fail the gate; remediation plan: X." Pre-empts operator-Ask-#5-style questions and gives the followup explicit scope.

**Applied in-flight**: B4 amendments commit `ccc1c6d` added the 3 violators to receipt + opened `B4-legacy-quarantine` followup. Surfaced 3 production rows that would otherwise have remained `VERIFIED` indefinitely.

**Cost of skipping**: Slice ships forward-protection; legacy poison data keeps `authority='VERIFIED'` label; downstream ETL (re-derivation, re-calibration) silently consumes it.

---

## Lesson 6 — Forward-gate vs retroactive-gate are different antibodies

**Origin**: B4 MAJOR (commit `ccc1c6d`). con-nyx noted: B4 ships 1 antibody (forward-gate, complete). The legacy-quarantine work is a SECOND antibody (retroactive-cleanup), not an extension of the same work.

**Rule**: Keep forward-gate and retroactive-gate as separate slices. The first preserves "this slice's contract is forward-protection" framing. The second is operator-gated for production DB UPDATE.

**Cost of merging them**: Receipt loses precision; followup scope unclear; operator can't decide on retroactive cleanup independently of the forward-gate semantics.

---

## Lesson 7 — `*_BOUNDS_*` (or any constants) canonicalization debt

**Origin**: B4 NICE-TO-HAVE #1 + pattern feedback #7 (commit `ccc1c6d`). `_PHYSICAL_TEMP_BOUNDS_C/F` are duplicated in writer constants + schema CHECK literal. Drift catcher test added. Real fix: extract bounds into `src/contracts/physical_bounds.py` when second bound family appears (wind, pressure, humidity).

**Rule**: When typed constants appear in N>1 enforcement layers (writer + schema + linter), add a drift-catcher test immediately + file a canonicalization followup. Don't promote to a contracts module until N grows past 2 — premature abstraction is more expensive than the drift.

**Cost of skipping**: Bounds drift goes unnoticed until production data exposes it.

---

## Lesson 8 — "Empirically verified" requires production-shape state

**Origin**: G6 BLOCKER #2 review (commit `d0a9406`). Initial G6 fix claimed "empirically re-verified production path" — but used synthetic `_populate_strategy_gates` fixture with arbitrary keys (`reason`, `set_at`) that production never produces. Real production shape from `query_control_override_state` was bare bool — fixture diverged, BLOCKER #2 hidden.

**Rule**: When a helper composes from a hydrated cache, the test suite needs at least ONE round-trip integration test: write to DB → invoke real hydration → invoke helper → assert outcome. Synthetic state injection (literal-arg or hand-shaped fixture) is necessary for fast iteration but cannot prove DB-shape correctness.

**Applied in-flight**: G6 fix #2 (commit `d0a9406`) added `test_boot_helper_round_trips_real_db_gate` using real sqlite + real `init_schema` + real `upsert_control_override` + real `refresh_control_state`. Only `get_world_connection` monkeypatched. Caught the bool/dict mismatch directly; would fire on any future bool regression.

**Cost of skipping**: Synthetic fixture passes; production crashes on every operator-issued gate.

---

## Lesson 9 — Receipt language discipline for amendments

**Origin**: G6 BLOCKER #2 review (commit `d0a9406`). When a verification claim turns out wrong, AMEND the receipt with explicit acknowledgment. Don't silently overwrite. The `verification_AMENDED_post_blocker2` field reads:

> "Initial verification of operator-remediation round-trip was INACCURATE — used synthetic _populate_strategy_gates fixture that bypassed query_control_override_state and therefore did not exercise the bool/dict shape mismatch. CONDITION C2 redo via test_boot_helper_round_trips_real_db_gate ... now exercises the full production path."

**Rule**: Receipts that record what's verified vs what was claimed are durable artifacts. Future reviewers can reconstruct what was actually verified vs what was claimed. Silent overwrites destroy the audit trail.

**Cost of skipping**: Receipts become wish-lists; future reviewers can't audit verification chain; defects re-surface because the original verification claim was wrong but never corrected.

---

## Lesson 10 — Defense-in-depth has a cost ceiling

**Origin**: G6 BLOCKER #2 fix (commit `d0a9406`). Path 1 (writer-side: `query_control_override_state` emits dict) alone would suffice for the operator-remediation scenario. Path 2 (reader-side: `strategy_gates()` accepts bool) added defense-in-depth — closed pre-existing red `test_backward_compat_bool_gate` as a bonus.

**Rule**: When a structural decision is covered at both ends, the fix is durable but adds maintenance surface. Justify each side: Path 2 here was justified by (a) latent red test documenting intent, (b) multiple cache-write paths, (c) low LOC cost. If a future fix proposes paths-on-paths without those preconditions, push back.

**Cost of skipping**: Maintenance surface inflates without proportional category-immunity gain.

---

## Lesson 11 — AST-walk catches direct imports only — needs subprocess transitive audit

**Origin**: G10-scaffold MAJOR #1 (commit `e5e6a30`). AST-walk antibody on `scripts/ingest/*` only checked DIRECT imports. `daily_obs_tick` imports `from src.data.daily_obs_append`, which transitively imports `from src.signal.diurnal` — forbidden. AST-walk was blind.

**Rule**: Whenever the antibody's stated intent is "module X does not depend on Y," the test must also include a SUBPROCESS-ISOLATED transitive-import audit. AST-walk is fast but blind to the import graph beneath the file's own imports. Subprocess audit is slower but actually answers "does the import graph contain Y?"

**Applied in-flight**: G10-helper-extraction (commit `e5e6a30`) added `test_no_forbidden_transitive_imports_in_ingest`. Each tick runs in fresh Python subprocess, sys.modules delta audited against `FORBIDDEN_IMPORT_PREFIXES`.

**Cost of skipping**: Decoupling premise breaks at import-graph level; daemon refactor of `src.signal.*` forces redeploy of "decoupled" ingest ticks.

---

## Lesson 12 — Scaffold slices need negative-detection tests

**Origin**: G10-scaffold pattern feedback (commit `e5e6a30`). For a pure scaffold (no runtime behavior change), RED phase isn't useful. But N/N GREEN doesn't prove anything about future regressions — only that current files happen to be clean.

**Rule**: For scaffold slices where the antibody must pass on first commit, include a negative-detection test that programmatically inserts a violation and asserts the antibody catches it.

**Applied in-flight**: G10-helper-extraction (commit `e5e6a30`) added `test_antibody_self_test_catches_synthetic_violation` — programmatically writes a fake tick with `from src.engine.cycle_runner import KNOWN_STRATEGIES` AND `from src.signal.diurnal import _is_missing_local_hour`, runs `_collect_imports`, asserts BOTH violations surface.

**Cost of skipping**: Antibody enforcement mechanism is unproven; future implementation regression silently passes.

---

## Lesson 13 — `-m` vs direct invocation matters for launchd / cron

**Origin**: G10-scaffold MAJOR #2 (commit `e5e6a30`). `python scripts/ingest/daily_obs_tick.py` (direct invocation) failed with `ModuleNotFoundError: No module named 'src'`. Default `sys.path[0]` is the SCRIPT's directory, not project root. The test suite always has project-root in pytest's sys.path, so the gap was hidden.

**Rule**: Project-root sys.path is a runtime contract that's easy to forget. Default to `sys.path.insert(0, str(Path(__file__).resolve().parents[N]))` at the top of every script under `scripts/`, matching the existing `scripts/live_smoke_test.py:23` convention. Add an antibody asserting it.

**Applied in-flight**: G10-helper-extraction (commit `e5e6a30`) added shim to all 5 ticks + `test_each_tick_script_self_bootstraps_syspath`. Both `python scripts/X.py` and `python -m scripts.ingest.X` invocation modes now work.

**Cost of skipping**: launchd plist work hits ModuleNotFoundError at deploy time; operator works around with PYTHONPATH ceremony or per-plist invocation hacks.

---

## Lesson 14 — Grep-gate before scoping multi-deliverable workbook entries

**Origin**: U1 slice plan §1 (commit `4fd18d9`). Workbook U1 entry specified 3 deliverables: HK floor code change + AGENTS.md constitutional amendment + antibody. L20 grep-gate audit before scoping found:
- Code half ABSORBED — `src.contracts.settlement_semantics.py:171` already uses `oracle_truncate` (= floor semantic) per commit `d99273a`.
- Constitutional half ABSORBED — `grep -inE "half.?up|WMO|asymmetric|universal" AGENTS.md` returns 0 matches; the universal claim is gone.
- Antibody half OPEN — only this remained.

**Rule**: When a workbook line specifies N deliverables, run targeted greps against the current codebase + AGENTS.md before scoping the slice. Predecessors absorb deliverables faster than workbooks update. The grep-gate audit becomes a slice-plan §1 ritual: list each deliverable, grep for it, mark ABSORBED / PARTIAL / TODO.

**Applied in-flight**: Saved ~1.5h of redundant code+constitutional work in U1. Slice scope reduced from triple-deliverable to antibody-only without losing safety.

**Promotion**: Lesson #14 elevated to critic standing-order per con-nyx U1 review Ask #5.

**Cost of skipping**: Tail-end scope creep when half the work is already done; receipt becomes confusing ("this slice changes nothing in production but is still 'closed'").

---

## How to use this catalog

### For executors (slice-planning phase)
1. Open the appropriate workbook entry.
2. Run grep-gate per Lesson #14. List deliverables; mark ABSORBED / PARTIAL / TODO.
3. For TODO items: design the antibody first (Fitz §1 — relationship tests before implementation).
4. If the antibody will compose from cached state OR add a write-time gate: apply Lesson #1 + #5 + #8.
5. If the slice is structural decoupling (scaffold without runtime behavior change): apply Lesson #12.
6. If N>1 enforcement layers: apply Lesson #7 (drift catcher inline).
7. Receipt should walk the operator runbook (Lesson #4) and acknowledge any in-place legacy violators (Lesson #5 again).

### For critics (review phase)
- **Pre-edit review**: scan slice plan against Lessons #1, #5, #8, #11, #12. Surface design risks before code lands.
- **Post-edit review**: empirical re-verification using production-shape state (Lesson #8). Subprocess audit for "decoupling" claims (Lesson #11).
- **Receipt review**: amendments where verification claims were wrong (Lesson #9). Drift catchers where N>1 (Lesson #7).
- **Followup tracking**: forward-gate vs retroactive-gate kept separate (Lesson #6). Defense-in-depth justified (Lesson #10).

### For session-start onboarding
Read this doc once at session start. The 14 lessons are the immune-system patch repertoire — applying them in-flight (as Wave 1 demonstrated 5 times) is the difference between fixing the instance and making the category impossible.

---

## Citations

| Lesson | Origin commit | Application commit |
|---|---|---|
| #1 | G6 BLOCKER #1 review | `26729fd` |
| #2 | G6 MAJOR #1 review | `26729fd` |
| #3 | G6 MAJOR #2 review | `211d0ec` (deferred to G6-MAJOR-2 followup) |
| #4 | G6 MAJOR #4 review | `26729fd` (receipt) |
| #5 | B4 MAJOR review | `ccc1c6d` (B4 amendments) |
| #6 | B4 review pattern feedback #6 | `ccc1c6d` (separate followup queue) |
| #7 | B4 NICE-TO-HAVE #1 + pattern #7 | `ccc1c6d` (drift catcher) + deferred canonicalization |
| #8 | G6 BLOCKER #2 review | `d0a9406` (round-trip test) |
| #9 | G6 BLOCKER #2 receipt | `d0a9406` (receipt amendment) |
| #10 | G6 BLOCKER #2 fix design | `d0a9406` (Path 1 + Path 2) |
| #11 | G10-scaffold MAJOR #1 review | `e5e6a30` (subprocess audit) |
| #12 | G10-scaffold pattern feedback #12 | `e5e6a30` (negative-detection) |
| #13 | G10-scaffold MAJOR #2 review | `e5e6a30` (sys.path shim + antibody) |
| #14 | U1 slice plan §1 + Ask #5 | `4fd18d9` (grep-gate audit) |

Wave 1 packet: 17 commits, 41+ antibody tests, regression delta=0 throughout. 5 lessons applied in-flight; each application surfaced a real defect.

## Promotion path

If operator approves promotion to durable law (architecture/ tier):
- Move this file to `architecture/critic_standing_orders.md` OR `docs/authority/agent_discipline.md`.
- Add hash-link from `AGENTS.md` so session-start onboarding includes it.
- Update `architecture/test_topology.yaml` if any of the lessons should gate at CI level.

Until promotion: this doc is operational evidence, not authority. Read it once per session if you're a critic or executor working a Wave-N+ slice.
