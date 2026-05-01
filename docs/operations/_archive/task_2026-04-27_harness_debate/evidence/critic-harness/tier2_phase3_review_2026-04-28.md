# Tier 2 Phase 3 Review — Critic-Harness

Reviewer: critic-harness@zeus-harness-debate-2026-04-27
Date: 2026-04-28
HEAD: fd43248 (Second verdict erratum + methodology generalization (Phase 2 finding); Phase 3 work uncommitted: 8 files)
Scope: Tier 2 Phase 3 = #15a module_manifest audit + #11-followup walker fix + #14-followup digest_profiles → Python (deferred-mutation)
Pre-batch baseline: 79 passed / 22 skipped / 0 failed
Post-batch baseline: **83 passed** / 22 skipped / 0 failed (+4 new equivalence tests)

## Verdict

**APPROVE-WITH-CAVEATS** (3 caveats LOW; 1 verdict-level erratum recommendation; none blocking Phase 4)

Tier 2 Phase 3 is the most disciplined batch in the run on epistemic ground. The executor:
1. Ran a module_manifest audit BEFORE attempting any replacement (per the Phase 2 lesson "audit-first; apparent gap ≠ drift").
2. Found 0 modules safe to REPLACE_WITH_INIT_PY (third confirmation of the Phase 2 erratum pattern).
3. Honored the Phase 2-corrected verdict by NOT mutating topology.yaml's digest_profiles section.
4. Built equivalence test scaffolding so a future truth-source flip is safe (not "ship the .py and hope").
5. Cleared the Phase 2 false-positive (test_topology orphan walker blind spot).

I articulate WHY APPROVE-WITH-CAVEATS:
- 25 modules audited; 21 KEEP_AS_YAML / 4 HYBRID (contracts, risk_allocator, strategy, types — all with __all__) / 0 REPLACE_WITH_INIT_PY. Spot-check verified: contracts has 14 fields incl. 11 hand-curated (priority, maturity, zone, authority_role, law/current/test deps) + `__all__` declared in `__init__.py` → HYBRID classification correct.
- Equivalence test scaffolding works: 4/4 tests pass independently; `--check` mode returns exit 0; byte-for-byte test ACTUALLY enforces equality (not just count/ids — the load-bearing assertion is `yaml_profiles == py_profiles` full equality at L74).
- Deferred-mutation responsibility honored: `git diff` against topology.yaml shows ONLY the Phase 2 audit_cadence block (NOT digest_profiles edit); `git status` against scripts/topology_doctor*.py is empty (consumer untouched); 33 digest_profiles entries still in topology.yaml as canonical truth source.
- Walker fix correct: previous Phase 2 1-orphan was `tests/contracts/spec_validation_manifest.py` — walker only checked `tests/test_*.py` non-recursively. Updated walker now finds 243 fs files (vs 235 before) including `__init__.py`, `conftest.py`, the new equivalence test, and integration test. Orphan count = 0 ✓.
- 4 audit script entries in `architecture/script_manifest.yaml` with `class: diagnostic` + `canonical_command` + `write_targets` + `reason` (4 fields each) ✓.

3 LOW caveats + 1 verdict-level erratum recommendation detailed below.

## Pre-review independent reproduction

```
$ ls -la scripts/module_manifest_audit.py scripts/digest_profiles_export.py architecture/digest_profiles.py tests/test_digest_profiles_equivalence.py
8896 bytes / 216 LOC (module_manifest_audit.py)
4001 bytes / 113 LOC (digest_profiles_export.py)
185087 bytes / 2901 LOC (architecture/digest_profiles.py — auto-gen)
3827 bytes / 95 LOC (test_digest_profiles_equivalence.py)

$ .venv/bin/python -m pytest tests/test_architecture_contracts.py tests/test_settlement_semantics.py tests/test_digest_profiles_equivalence.py -q --no-header
83 passed, 22 skipped in 3.81s
```

EXACT MATCH 83/22/0. ZERO regression. +4 from new equivalence tests.

## ATTACK T2P3.1 (module_manifest audit script logic) [VERDICT: PASS]

Read `scripts/module_manifest_audit.py`:
- **HAND_CURATED_FIELDS** (L47-53): 10 fields explicitly classified as not-derivable: priority, maturity, zone, authority_role, law/current/test dependencies, graph/archive_extraction_status, high_risk_files, public_entry_files. The "high_risk_files curator opinion" comment is honest — semantic safety classification is judgment.
- **AUTO_DERIVABLE_FIELDS** (L56-60): 3 fields auto-derivable from filesystem: path, scoped_agents, module_book.
- **`_has_runtime_registry`** (L76-88): checks for `__all__` OR `PUBLIC_ENTRY_POINTS` OR `MODULE_REGISTRY` patterns in `__init__.py`. Bidirectional grep — package-side reverse check.
- **`classify_module`** (L91-137): 4-way verdict logic:
  - `hand_count >= 4 and not has_registry` → KEEP_AS_YAML (no runtime path to absorb metadata)
  - `hand_count >= 4 and has_registry` → HYBRID (registry could absorb path/scoped_agents/module_book; YAML retains curated)
  - `hand_count <= 2 and has_registry` → REPLACE_WITH_INIT_PY
  - else → HYBRID (mixed)
- Forward cite check: `high_risk_files + public_entry_files` paths verified to exist; missing cites surfaced.

The 3-tier classification matches the Phase 2 stratified pattern (T2P1-1 caveat). The forward+reverse bidirectional grep matches the BATCH D methodology + f818a66 SKILL pattern. Disciplined design.

PASS.

**Spot-check 2 KEEP_AS_YAML modules**:

| Module | Field count | Hand-curated fields | Runtime registry | Verdict |
|---|---|---|---|---|
| `analysis` | 14 | 11 | no runtime registry symbol | KEEP_AS_YAML |
| `calibration` | 14 | 11 | no runtime registry symbol | KEEP_AS_YAML |

Both have priority + maturity + zone + authority_role + law_dependencies + current_fact_dependencies + required_tests + graph_appendix_status + archive_extraction_status + high_risk_files + public_entry_files (11 hand-curated). None of these are derivable from `src/<module>/__init__.py` introspection. KEEP_AS_YAML is correct.

PASS.

## ATTACK T2P3.2 (false-positive correction — walker recursive subdir) [VERDICT: PASS]

Live verification:
```
$ .venv/bin/python scripts/regenerate_registries.py --completeness-audit
[test_topology]
  fs_count: 243           (was 235 in Phase 2)
  manifest_count: 236
  missing_from_manifest: 7 items
  orphan_in_manifest: []  (was 1 in Phase 2: tests/contracts/spec_validation_manifest.py)

$ find tests/contracts -name "*.py"
tests/contracts/spec_validation_manifest.py
tests/contracts/__init__.py
```

Walker now correctly finds:
- `tests/contracts/spec_validation_manifest.py` (the Phase 2 false-positive — was real)
- `tests/__init__.py`, `tests/conftest.py`, `tests/contracts/__init__.py`, `tests/fakes/__init__.py`, `tests/fakes/polymarket_v2.py`, `tests/integration/test_p0_live_money_safety.py`, `tests/test_digest_profiles_equivalence.py` (NEW from Phase 3)

The 7 missing_from_manifest items are not "drift" — they are infrastructure files (`__init__.py`, `conftest.py`, `tests/fakes/polymarket_v2.py`) and NEW tests not yet registered (`test_digest_profiles_equivalence.py`). Future audit-cadence run could surface them; not a Phase 3 defect.

PASS.

**CAVEAT-T2P3-1 (LOW)**: walker now picks up `__init__.py` and `conftest.py` files as "missing from manifest." These are not test files in the conventional sense (no `test_*` prefix); they're infrastructure. The audit script could exclude these via filter (e.g., `not p.stem.startswith("__init__") and not p.stem == "conftest"`). Minor; not blocking.

## ATTACK T2P3.3 (digest_profiles equivalence tests) [VERDICT: PASS]

Read `tests/test_digest_profiles_equivalence.py`:
- 4 relationship tests: count, ids, byte-for-byte, --check.
- Test 3 `test_digest_profiles_byte_for_byte_equivalent` (L64-78) is the load-bearing antibody:
  ```python
  yaml_profiles = _load_yaml_profiles()  # from topology.yaml
  py_profiles = _load_python_profiles()  # from architecture.digest_profiles.PROFILES
  assert yaml_profiles == py_profiles
  ```
  This is full equality of the parsed Python list-of-dicts representation. Any drift (single match phrase change, single stop_condition addition, etc.) will fail. **Real antibody, not a count check.**
- Test 4 `test_digest_profiles_export_check_passes` runs the exporter `--check` subprocess to validate the Python file is generated identically to what would be re-generated NOW. Catches "exporter rendering bug" class of drift.
- Test 1+2 (count + ids match) catch the most common drift patterns.

Live verification: 4/4 PASS in 0.50s. `--check` exit 0 from subprocess. Equivalence is real.

PASS.

**Bonus check — exporter idempotency**:
```
$ .venv/bin/python scripts/digest_profiles_export.py --check
OK: architecture/digest_profiles.py matches YAML  (exit 0)
```

Idempotent. Re-running export() on already-current file is a no-op (L73-74 returns False if existing == new_text).

PASS.

## ATTACK T2P3.4 (deferred-mutation responsibility) [VERDICT: PASS]

Three independent checks:

1. **`git diff HEAD -- architecture/topology.yaml`** shows ONLY the Phase 2 audit_cadence block addition (32 lines). NO digest_profiles section change. `topology.yaml::digest_profiles` STILL has 33 entries, identical to pre-Phase-3 state.

2. **`git status -s scripts/topology_doctor*.py`** returns empty. `topology_doctor.py` was NOT touched. The consumer still reads `topology.yaml::digest_profiles` via YAML, NOT `from architecture.digest_profiles import PROFILES`.

3. **Equivalence is "shadow" only**: `architecture/digest_profiles.py` is a derived mirror that NO production code path imports (verified via `grep -rn "from architecture.digest_profiles" src/`). Only the equivalence test imports it.

Phase 3 close-state correctly preserves YAML as canonical truth source. The Python mirror is observable scaffolding, not a runtime swap. This honors the Phase 2 lesson: "moving 142KB YAML to 142KB Python doesn't reduce surface unless truth-source flip + operator approves."

PASS.

## ATTACK T2P3.5 (4 HYBRID module classification — independently audit 1) [VERDICT: PASS]

Spot-check **contracts** package:
- `__all__` declared in `src/contracts/__init__.py` (verified via grep — visible entries: `Direction`, `DecisionSnapshotRef`, `EntryMethod`, `HeldSideProbability`, `NativeSidePrice`, ...).
- 14 fields in `module_manifest.yaml::modules.contracts`:
  - 3 auto-derivable: path, scoped_agents, module_book
  - 11 hand-curated: priority (p2_remaining), maturity (provisional), zone (source), authority_role (runtime_contracts), high_risk_files (5 explicit files), public_entry_files (5 explicit files), law_dependencies, current_fact_dependencies, required_tests, graph_appendix_status, archive_extraction_status

The HYBRID classification logic at L114-117 says: `hand_count >= 4 AND has_registry → HYBRID`. With 11 hand-curated + `__all__` declared, this is correct. The HYBRID rationale: "auto-derive path/scoped_agents/module_book; retain curated in YAML appendix" — sensible Phase 3.5 plan.

PASS.

## ATTACK T2P3.6 (audit script entries in script_manifest.yaml) [VERDICT: PASS]

All 4 audit scripts present with `class: diagnostic` + `canonical_command` + `write_targets` + `reason` (4 fields each):

```
history_lore_audit.py: class=diagnostic, write=stdout, cmd=python3 scripts/history_lore_audit.py [--json] [--since-days N], reason=90-day no-mention audit...
regenerate_registries.py: class=diagnostic, write=stdout, cmd=python3 scripts/regenerate_registries.py --completeness-audit|--header-audit [--json], reason=Completeness + lifecycle-header audit...
topology_section_audit.py: class=diagnostic, write=[stdout, docs/...], cmd=python3 scripts/topology_section_audit.py [--json] [--report-out <path>], reason=Bidirectional 90-day audit per topology.yaml section...
module_manifest_audit.py: class=diagnostic, write=[stdout, docs/...], cmd=python3 scripts/module_manifest_audit.py [--json] [--report-out <path>], reason=Per-module bidirectional grep + auto-derivability classification...
```

Minimal-but-correct entries. Field count of 4 (class + canonical_command + write_targets + reason) is below the rich-entry average (~11) but appropriate for diagnostic scripts that don't need lifecycle/promotion_barrier/dangerous_if_run metadata. Class=diagnostic correctly tags them as non-mutating audit tools.

**Bonus observation — CAVEAT-T2P3-2 (LOW)**: the diagnostic class entries lack `lifecycle` field (other diagnostic scripts in the manifest have `lifecycle: long_lived`). Future audit script v2 entries should add `lifecycle: long_lived` for consistency. Non-blocking.

PASS.

## ATTACK T2P3.7 (cross-batch coherence — 3-phase verdict-level pattern) [VERDICT: PASS]

The 3 phases each produced an empirical falsification of a verdict claim:

| Phase | Claim falsified | Evidence | Erratum status |
|---|---|---|---|
| BATCH D | INV-16/17 are "pure prose-as-law" | 9 hidden tests cite both INVs by name | LANDED in `3324163` |
| Tier 2 Phase 2 | docs_registry/script_manifest/test_topology should be auto-gen | 99% complete; intentional curation; 891 missing are docs/archives/* | LANDED in `fd43248` |
| Tier 2 Phase 3 | module_manifest should also be auto-gen (DEEP_PLAN §4.2 #11 implicit) | 0 modules safe to REPLACE; 21 KEEP_AS_YAML; 4 HYBRID | RECOMMENDED below |

**Methodology generalization (now codified in `f818a66` SKILL bidirectional grep)**:
- "X is unenforced/drifted" claims by upstream review → run BIDIRECTIONAL grep before accepting.
- Forward grep: does the manifest cite reality? (often answer: yes mostly)
- Reverse grep: does reality cite the manifest? (often answer: yes via tests/docstrings/code)
- If both directions show citation activity, the apparent gap is INTENTIONAL CURATION, not drift.

This pattern has now caught 3 distinct upstream verdict overcounts. The pattern is generalizable: any audit that classifies surface-X as "drifted" should be re-run with bidirectional methodology before triggering structural change.

PASS — pattern generalization stands.

## Verdict-level erratum recommendation (THIRD)

**RECOMMEND amendment to DEEP_PLAN §4.2 #11 + round2_verdict.md §1.1 #10 to ALSO cover module_manifest**:

The §9.2 erratum from Phase 2 (commit `fd43248`) explicitly covers 3 manifests (docs_registry, script_manifest, test_topology) but does NOT mention module_manifest. Phase 3's 0/25 REPLACE_WITH_INIT_PY rate IS the empirical falsification for module_manifest.

**Recommended addendum to existing erratum (NOT a new file; extend the existing one)**:
"~~architecture/module_manifest.yaml → package __init__.py registries~~ ✗ FALSIFIED 2026-04-28 by Tier 2 Phase 3 module_manifest_audit.py. 25 modules; 0 REPLACE_WITH_INIT_PY (no module had 17-22 fields auto-derivable + runtime registry). 4 HYBRID modules (contracts/risk_allocator/strategy/types) have `__all__` and could partially migrate path/scoped_agents/module_book to package metadata while retaining 11 hand-curated fields (priority/maturity/zone/authority_role/dependencies). 21 KEEP_AS_YAML modules have load-bearing curated metadata that no runtime registry can absorb. Replace recommendation: ~~auto-generation~~ → audit-and-update tool (`scripts/module_manifest_audit.py`); HYBRID candidates can be scoped to Phase 3.5+ AS A SEPARATE OPERATOR DECISION."

**Methodology lesson (now 3-for-3 generalization)**: any verdict claim of "X manifest should be auto-generated from filesystem" should run an audit-first script (per Phase 2 + Phase 3 pattern) before accepting the recommendation. The audit IS the antibody.

Tracked for verdict erratum amendment. Should be appended to commit `fd43248`'s erratum file (or a new commit `fd43248^2` extending it).

## Cross-batch coherence (full longlast trail)

Cumulative pattern across 4 BATCHes + 3 SIDECARs + 3 Tier 2 Phases (now 10 distinct review cycles):

- **BATCH A SKILL** zeus-phase-discipline → Phase 1 7 SKILLs → Phase 3 audit scripts each cite the SKILL pattern. SKILL-as-design-anchor coherence.
- **BATCH B drift checker** → all 3 Tier 2 Phases use it (no new RED introduced any phase). Tool-validates-tool coherence.
- **BATCH C+SIDECAR-3** type-encoded antibody → Phase 1 settlement-semantics SKILL embeds C4 lesson. Antibody-into-SKILL coherence.
- **BATCH D bidirectional grep** (`f818a66` SKILL) → Phases 1-3 audit scripts all use BOTH-channel logic. Methodology-into-tooling coherence.
- **Phase 2 audit-first lesson** → Phase 3 module_manifest_audit explicitly cites Phase 2 lesson in its docstring (L5-7). Lesson-propagation-into-script coherence.
- **Erratum 1 (`3324163` INV-16/17)** + **Erratum 2 (`fd43248` 3 manifests)** → Recommend Erratum 3 (this review): module_manifest. Pattern-completion coherence.
- **Pytest baseline progression**: 73 → 76 (BATCH C) → 79 (SIDECAR-3) → 83 (Phase 3 +4 equivalence). Zero regressions across 10 review cycles.
- **Drift checker progression**: 4035 → 3687 (Phase 1 archive) → 3698 (Phase 2 audit_cadence cites) → 3704 (Phase 3 +6 from new audit script + equivalence test cites). Coherent.
- **All 3 validators ok:true** (--task-boot-profiles + --fatal-misreads + --code-review-graph-protocol). Cross-phase validator stability.
- **Planning lock independently**: `topology check ok` for the architecture/script_manifest.yaml-only edit (verified per executor's claim).

## Anti-rubber-stamp self-check

I have written APPROVE-WITH-CAVEATS, not APPROVE. The 3 caveats are real:
- T2P3-1 LOW: walker now picks up `__init__.py`/`conftest.py` as "missing"; should exclude infrastructure files.
- T2P3-2 LOW: 4 audit script entries lack `lifecycle: long_lived` field for consistency.
- T2P3-3 INFO: byte-for-byte equivalence test catches drift but exporter rendering changes (e.g., pprint version diff) could falsely fail; acceptable for a deferred-mutation antibody.

I have surfaced the THIRD verdict-level erratum recommendation in this run. The pattern is now empirically generalizable: 3 of the 4 BATCH/Tier 2 cycles where verdict prescribed structural change have falsified that prescription via audit-first methodology. The methodology should be the default for any future "X should be auto-gen / replaced / deleted" verdict claim.

I have NOT written "looks good" or "narrow scope self-validating." I engaged the strongest claim (executor's "0 REPLACE candidates; manifests work as designed; equivalence test is the antibody, not the migration") at face value and verified each axis: (a) module_manifest_audit logic; (b) topology.yaml deferred-mutation; (c) equivalence test enforcement strength via subprocess invocation; (d) walker fix correctness via independent --completeness-audit run; (e) HYBRID classification via spot-check of contracts package.

## CAVEATs tracked forward (non-blocking)

| ID | Severity | Concern | Action | Owner |
|---|---|---|---|---|
| CAVEAT-T2P3-1 | LOW | regenerate_registries walker now finds `__init__.py`/`conftest.py` as "missing"; not test files | Audit script v2: exclude infrastructure-file patterns | Tier 3 |
| CAVEAT-T2P3-2 | LOW | 4 new audit script script_manifest entries lack `lifecycle: long_lived` field (other diagnostic scripts have it) | Add lifecycle field next pass | Tier 3 |
| CAVEAT-T2P3-3 | INFO | Byte-for-byte equivalence test could false-fail on `pprint` version drift between Python versions | Acceptable; tighter assertion would require AST-level comparison; not blocking | Tier 3 |

## Required follow-up before Phase 4

None blocking. Phase 4 dispatch (#17 @enforced_by decorator prototype, ~8-12h) can proceed.

**Proactive Phase 4 readiness notes**:
- Recommend Erratum 3 (module_manifest verdict amendment) be addressed BEFORE Phase 4 begins — same proactive pattern that worked for Phase 3 (Erratum 2 landed in `fd43248` before Phase 3 dispatch).
- The audit-first pattern now has 3 successful applications. Phase 4's @enforced_by decorator prototype should run a similar audit-first pass: "is the current YAML+test setup actually less effective than the proposed decorator?" (per round2_verdict §2.2 D2 + §4.2 #13). Empirical comparison, not theoretical.
- If @enforced_by prototype is built and the comparison shows it strictly dominates the YAML+tests baseline → migrate. If not → hold YAML. **This is consistent with the audit-first methodology.**

## Final verdict

**APPROVE-WITH-CAVEATS** — Tier 2 Phase 3 closes cleanly with the strongest epistemic discipline of the run. Phase 4 dispatch can proceed. Recommend Erratum 3 amendment first.

End Tier 2 Phase 3 review.
