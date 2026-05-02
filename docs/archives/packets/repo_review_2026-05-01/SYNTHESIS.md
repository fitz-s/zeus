# Zeus General Repo Review — Synthesis (4-lane)

**Branch**: ultrareview25-remediation-2026-05-01 (HEAD `355bcfcb`)
**Date**: 2026-05-01
**Lanes converged**: architect (opus, RO) · critic-opus · test-engineer · security-reviewer
**Lane outstanding**: verifier (live-running readiness — still running, not blocked on)
**Authored by**: team-lead synthesis

> **Working scope**: ignore the dirty git working tree / in-progress remediation commits. Review the repo as it stands on disk for **architecture & live-running risk** hardening.

---

## 0. The K-pattern (the meta-finding that explains everything else)

**Three of four lanes independently surfaced the same pattern:**

> **Zeus's law layer (AGENTS.md / invariants.yaml / fatal_misreads.yaml) is mature and disciplined. Producers stamp authority correctly. Consumer-side enforcement, runtime gates, and CI test execution are weaker than the law layer claims.**

This is K-pattern: *"the rule exists in the documentation plane and partly in the producer plane, but the consumer/runtime/test plane has the actual hole."* It produces ~12-15 individual findings across the 4 lanes — not 12 bugs, but ~5 structural decisions incompletely executed.

**Independent confirmations of K-pattern:**
1. **Architect**: `settle_market()` type guard built but zero production call sites; `DEGRADED_PROJECTION` produced but no consumer differentiates it; INV-05 cites a non-existent test
2. **Critic-opus**: AGENTS.md says "broken truth input → RED, fail-closed" but riskguard returns `DATA_DEGRADED` ranked **below** YELLOW; `cycle_runner` only sweeps on RED → **truth-input missing silently disables sweep**
3. **Test-engineer**: 22 of 271 test files in CI blocking gate; `test_cross_module_relationships.py` 10 tests always skip on empty DB; `test_db.py` 19 canonical-ledger write-path tests skipped with no replacement
4. **Security**: WU API key hardcoded was caught by 2026-04-14 backlog **but never acted on** (the gate that would catch a re-commit is itself missing — `.git/hooks/*.sample` only)

The K-pattern is also visible in the 2026-04-14 → 2026-05-01 timeline: **a known P0 secret leak survived 17 days** because the very gate that would re-flag it on commit is fail-open.

---

## 1. Five K-decisions (collapse N symptoms → K structural changes)

| # | K-decision | Symptoms it dissolves | Cost |
|---|---|---|---|
| **K-A** | **Two-ring enforcement everywhere**: lint+runtime, agent+operator, producer+consumer | Hooks fail-open for operator; `settle_market()` dormant; `place_limit_order` single-ring; `DEGRADED_PROJECTION` consumer-blind; INV-05 doc-only; INV-22 deprecated wrapper drift | Medium — touches hooks config, ~5 call-site rewires, 4 new tests |
| **K-B** | **CI blocking gate covers the money path** — run `tests/test_*.py` fully, fail on `pytest.skip` without explicit reason whitelist | 22/271 file CI coverage; 20+ always-skip relationship tests; INV-03/07/10 zero-antibody; hidden regressions | Low — config change + CI YAML; the regressions it surfaces are higher cost |
| **K-C** | **Provenance enters the type system**: closed `enum.StrEnum` for authority/risk-level/source-family + exhaustive `match` requirement | DATA_DEGRADED→RED-sweep gap; DEGRADED_PROJECTION consumer drift; bare `source: str` in contracts/; mode-leak hazards (residual) | Medium — enum + AST-grep test; consumer-side audit one-time |
| **K-D** | **Identity-column anti-default sweep + CI grep-gate** | `DEFAULT 'high'` at 4 sites; future identity-column drift (`physical_quantity`, `observation_field`, `data_version`) | Low — DDL edits + 1 CI grep |
| **K-E** | **Secret hygiene re-baseline**: rotate WU, all creds in keychain, plist holds only references, gitleaks fail-closed in real hooks | WU key exposure (P0); ZEUS_CUTOVER_OPERATOR_TOKEN_SECRET launchd gap; Polymarket WS L2 silent failure; future re-commit prevention | Medium — key rotation, plist edits, hook rewire |

These 5 K-decisions, executed end-to-end, dissolve **18 of 25** distinct findings across the 4 lanes.

---

## 2. P0 immediate clip (this week, in order)

### ~~P0-1 — Rotate WU API key~~ — **WITHDRAWN (FALSE POSITIVE)**
- **Operator ruling 2026-04-21 (re-affirmed 2026-05-01)**: `_WU_PUBLIC_WEB_KEY` is wunderground.com's own browser-embedded public key, visible in any DevTools Network tab on a public ICAO history page. NOT a leaked secret. Prior "Security S1 fix" mis-classified it and broke the daemon by removing the public fallback.
- **Action taken 2026-05-01**: Added `[REVIEW-SAFE: WU_PUBLIC_KEY]` banner + inline tag at all three use sites (`src/data/observation_client.py:103`, `src/data/daily_obs_append.py:95`, `src/data/wu_hourly_client.py:50`) to break the false-positive review loop.
- **Durable companion fix (P3)**: add `[REVIEW-SAFE: WU_PUBLIC_KEY]` to gitleaks allowlist + add the constant to a project-level `SECURITY-FALSE-POSITIVES.md` so future code-review agents/scanners don't re-raise it.
- **Net P0 count after withdrawal**: 5 (was 6).

### P0-2 — Wire real git hooks (kills the recurrence root)
- **Where**: `.git/hooks/` (only `*.sample` exists); `.claude/hooks/*.sh` (works only on Bash-channel agent commits per `.claude/settings.json:14-29`)
- **Why**: Operator-direct `git commit` bypasses every invariant test, gitleaks scan, and forbidden-pattern check. P0-1 was committed because of this gap.
- **Fix**: `git config core.hooksPath .claude/hooks` + rename scripts to git-hook names (`pre-commit`, `pre-merge-commit`, `pre-push`). Verify with a deliberate failing-invariant test commit.

### P0-3 — Add the missing INV-05 antibody (`test_risk_actions_exist_in_schema`)
- **Where**: `tests/test_architecture_contracts.py` (3 lanes independently identified this gap)
- **Why**: `architecture/invariants.yaml:54-56` cites a test that does not exist. The cornerstone "advisory-only risk is forbidden" invariant has zero pytest enforcement.
- **Fix**: Parse `architecture/2026_04_02_architecture_kernel.sql`, assert `risk_actions` table exists with non-advisory action columns. Run in blocking CI.

### ~~P0-4 — Close the `DATA_DEGRADED` → RED-sweep gap~~ — **RECLASSIFIED P1 (DOC-VS-CODE DRIFT, CODE IS CORRECT)**
- **Depth audit 2026-05-01**: AGENTS.md:81-83 over-states a nuanced design. Code intentionally distinguishes:
  - GENUINE compute error → RED (riskguard.py:1058/1067/1074) — truly fail-closed, sweep fires
  - MISSING/STALE truth input → DATA_DEGRADED (riskguard.py:269-277/286-292/1030) — block new entries, preserve held positions, alert; **do NOT force-sell at unfavorable prices on a transient glitch**
- **Why code is right**: sweeping requires the portfolio data we just said is missing; force-selling on every transient glitch amplifies risk rather than reducing it; `risk_level.py:17` LEVEL_ACTIONS explicitly states "YELLOW-equivalent safety without declaring loss boundary breach". The design is intentional.
- **Action taken 2026-05-01**:
  1. Added `tests/test_dual_track_law_stubs.py::test_data_degraded_does_not_trigger_force_exit_sweep` (INV-19a sibling antibody) that pins three properties: rank-order `YELLOW > DATA_DEGRADED > GREEN`, rank-order `RED > DATA_DEGRADED`, and strict-equality sweep predicate (so future agents can't tighten `==` to `>=` silently).
  2. Filed `docs/operations/repo_review_2026-05-01/AGENTS_MD_DATA_DEGRADED_clarification.md` for operator review — proposes a one-paragraph AGENTS.md amendment to bring law into line with code (or reverses the design if operator chooses).
- **Operator action**: ruling on AGENTS.md amendment.

### ~~P0-5 — Activate or strip `settle_market()`~~ — **RECLASSIFIED P1 (DOC-VS-CODE DRIFT, SOCIAL GATE COVERS REAL FAILURE MODE)**
- **Depth audit 2026-05-01**: `settle_market()` is dormant (zero `src/` call sites) BUT the cross-city wrong-rounding failure mode is already structurally enforced by the SOCIAL gate:
  - `SettlementSemantics.for_city(city)` factory dispatches HK → `oracle_truncate`, others → `wmo_half_up`.
  - Production code reaches the class only through `for_city()` — verified by exhaustive grep: zero direct `SettlementSemantics(...)` calls and zero `rounding_rule='...'` kwarg literals outside the canonical module.
- **Why activation now is the wrong move**: wiring `settle_market()` into `assert_settlement_value` would touch every settlement DB write (calibration, evaluator, replay, monitor_refresh, harvester, ensemble_signal, day0_signal — 11+ call sites). Marginal type-layer benefit over the already-working SOCIAL gate. Author's own note (`settlement_semantics.py:194`) explicitly defers this to "Tier 3 P8 territory."
- **Action taken 2026-05-01**:
  1. Added `tests/test_settlement_semantics.py::test_settlement_semantics_construction_routes_through_for_city` that LOCKS the SOCIAL discipline — no `src/` file outside the canonical module may construct `SettlementSemantics(...)` directly or pass `rounding_rule='...'` as a kwarg literal. Future direct-construction bypasses fail at test time.
  2. Added a 30-line PRODUCTION-CALLER STATUS docstring header to `settle_market()` itself (`src/contracts/settlement_semantics.py:265-298`) recording the dormancy + the SOCIAL gate that covers for it. Future agents reading the function know its status without re-deriving it.
  3. Filed proposal: `architecture/fatal_misreads.yaml:141`'s claim "unconstructable at compile/import time" is overstated for production. Operator should amend to reflect the SOCIAL gate (runtime dispatch via for_city) + TYPE gate (compile-time via settle_market for new code) layered defense.
- **What's NOT done (deferred)**: actual `settle_market()` activation. This is a Tier 3 P8 migration with non-trivial regression surface; do it on its own slice with full test coverage of every settlement DB write. Not justified as a P0 today.

### P0-6 — Make CI blocking gate cover the money path — **AUDIT-COMPLETE, DEFERRED TO DEDICATED TRIAGE SLICE**
- **Depth audit 2026-05-01**: not a config flip; **120 failures** behind the flip per verifier `pytest -m ""` run.
  - 10 governance violations in production (`test_structural_linter`)
  - 17 stale-stub regressions (`test_pnl_flow_and_audit`)
  - 4 INV-25/INV-26 enforcement gaps (`test_p0_hardening`)
  - 16 `live_topology`-marker excluded-by-default
  - ~73 unclassified, requiring per-file triage
  - Plus 10+ always-skip-on-empty-DB tests (`test_cross_module_relationships`) per test-engineer lane.
- **Why I'm not auto-flipping**: turning on the gate today blocks every operator commit for unknown reasons. That's worse than the current state. This needs a dedicated 3-5 day triage slice.
- **Action taken 2026-05-01**: filed `docs/operations/repo_review_2026-05-01/CI_GATE_TRIAGE_PROPOSAL.md` with a 4-phase triage path (Per-category fix-or-delete → Re-baseline → CI workflow promotion → Lock-the-discipline).
- **Operator action**: schedule the triage slice when bandwidth allows. Until then, the new pre-commit hook (P0-2) provides BASELINE_PASSED=219 regression protection on the 14 file groups it watches.

---

## 3. P1 hardening clip (next two weeks)

| # | Item | Lane | Reference |
|---|---|---|---|
| P1-1 | Inject `ZEUS_CUTOVER_OPERATOR_TOKEN_SECRET` + Polymarket WS L2 creds (`POLYMARKET_API_KEY/SECRET/PASSPHRASE`) into launchd plists | security | live user-channel WS silently failing today |
| ~~P1-2~~ | **PARTIAL DONE 2026-05-01**: scanner + regression gate landed (`scripts/check_identity_column_defaults.py` + `tests/test_identity_column_defaults.py`, baseline 229→231). Locked the 5-site baseline (4× `DEFAULT 'high'` + 1× `DEFAULT 'v1'` for `data_version` — sibling-default discovered during depth audit). The actual DDL repair requires editing `architecture/2026_04_02_architecture_kernel.sql` under `ARCH_PLAN_EVIDENCE`; per-site recipe filed in `P1_2_DEFAULT_HIGH_REPAIR.md`. **A 5th DEFAULT cannot land silently.** Operator unblock + repair is a small follow-up. | architect (K-D) | `scripts/check_identity_column_defaults.py`, `tests/test_identity_column_defaults.py`, `P1_2_DEFAULT_HIGH_REPAIR.md` |
| ~~P1-3~~ | **DONE 2026-05-01 (Option a, MINIMAL per audit)**: closed `TruthAuthority(StrEnum)` in `src/types/truth_authority.py`; `_TRUTH_AUTHORITY_MAP` migrated to enum members (wire-compat via StrEnum). 5 antibody tests in `tests/test_truth_authority_enum.py` lock the 4-member set + producer-side closure + JSON/set/equality wire-compat + unknown-string rejection. **Why option (a) not (b)/(c)**: architect audit (`P1_3_TRUTH_AUTHORITY_AUDIT.md`) found zero `src/` consumers read `truth['authority']` (K-C risk is forward-looking, not retroactive); 5 of 10 "TWO-VALUE-BOOLEAN" sites are grammar C (`ScanAuthority`) not grammar A (`TruthAuthority`) — invasive rewrite would be category error. Behavior-neutral 3-file diff. | architect (K-C) | `src/types/truth_authority.py`, `src/state/portfolio.py`, `tests/test_truth_authority_enum.py`, `docs/operations/repo_review_2026-05-01/P1_3_TRUTH_AUTHORITY_AUDIT.md` |
| ~~P1-4~~ | **DONE 2026-05-01**: All `>=` floors converted to exact pins matching the active venv baseline. `cryptography==46.0.6` + `pydantic==2.12.5` promoted from transitive to explicit (was security review §10 concern). `web3` / `websockets` / `aiohttp` confirmed unused via repo-wide import scan — NOT added (don't list deps you don't import). `dash`/`plotly` left in with bounded ranges (verified unused in `src/`+`scripts/`; flagged for operator confirm-or-remove). pip-audit wiring deferred to P3 follow-up. `pip install --dry-run -r requirements.txt` resolves clean. | security | `requirements.txt` |
| ~~P1-5~~ | **DONE 2026-05-01**: Runtime call-stack guard `_enforce_inv24_caller_allowlist()` at `src/data/polymarket_client.py` module level + invocation at `place_limit_order` entry. Auto-allows `PYTEST_CURRENT_TEST` so existing tests keep working. Operator override `INV24_CALLSTACK_GUARD_SKIP=1` audit-logged to `.claude/logs/inv24-overrides.log`. 3 antibody tests in `test_p0_hardening.py` (block / pytest-context allow / override-with-audit). Two-ring K-A enforcement complete (lint via semgrep + runtime via stack-walk). | architect (K-A) | `src/data/polymarket_client.py`, `tests/test_p0_hardening.py` |
| ~~P1-6~~ | **DONE 2026-05-01**: Deprecated `make_family_id()` wrapper deleted from `src/strategy/selection_family.py`. Production callers were already at zero (verified by AST guard `tests/test_no_deprecated_make_family_id_calls.py`). The wrapper-period test class `TestMakeFamilyIdDeprecatedWrapper` rewritten as `TestMakeFamilyIdRetired` with one antibody asserting the wrapper is GONE + one positive antibody confirming the two canonical helpers remain. INV-22 ("one canonical family grammar") now satisfied structurally — wrapper has no callers AND no definition. | architect | `src/strategy/selection_family.py`, `tests/test_fdr_family_scope.py`, `tests/test_fdr.py` |
| ~~P1-7~~ | **DONE 2026-05-01**: `_resolve_credentials` rewritten to import `bin.keychain_resolver` in-process via a small `_import_keychain_resolver()` helper (auto-adds `OPENCLAW_HOME` to `sys.path`). The prior `subprocess.run(["python3", "-c", f"...{root!r}..."])` code-string pattern is gone. Same external behaviour, no eval-on-strings shape, proper Python tracebacks on failure, no subprocess overhead. Module imports clean, all 29 `test_v2_adapter.py` tests pass under runtime guard. | security | `src/data/polymarket_client.py` |
| ~~P1-8~~ | **DONE 2026-05-01**: Cross-ref consistency check landed as `scripts/check_invariant_test_citations.py` + `tests/test_invariant_citations.py` (gated; baseline 219→222). Found 6 broken cites across INV-13/30/32 (filed in `INVARIANT_CITATION_DRIFT_REPAIR.md` for operator YAML repair). **Future INV-05-shaped doc-only failures will fail at pre-commit time, not at next review.** | architect (K-A) | `scripts/check_invariant_test_citations.py`, `tests/test_invariant_citations.py` |
| ~~P1-9~~ | **DONE 2026-05-01**: 7 new antibodies in `tests/test_architecture_contracts.py`: INV-03 (×2 — append-only triggers fire at runtime + projection VIEW reflects appended events), INV-07 (×2 — SQL CHECK ↔ Python LifecyclePhase consistency + fold rejects invented phase strings), INV-10 (×3 — no LLM SDK imports in src/ + no LLM SDK in requirements.txt + governance artifacts exist). All 3 invariants moved from doc-only to real-test-backed. | test-engineer | `tests/test_architecture_contracts.py` |
| P1-10 | F12 (INV-23 ↔ NC-17 anchor) operator ruling — sitting in law layer one week | architect | `PLAN.md:79`, `invariants.yaml:233-241` |

---

## 4. P2 hardening (when bandwidth allows)

- Type-wrap `source: str` / `verification_source: str` fields in `src/contracts/` with `ExternalParameter[T]` per global epistemic_scaffold gate (~9 dataclass call sites)
- Whitelist enforcement on the 30+ f-string SQL interpolations (today safe, refactor-fragile)
- FM-08 row in forbidden-patterns: either add the semgrep rule or delete the row (claim-vs-enforcement parity)
- `architecture/inv_prototype.py:73,247` idempotency fix (already in PLAN.md F5+F10) + regression test that calls `validate()` twice and asserts `all_drift_findings()` equality

---

## 4.5. Action log — 2026-05-01 P0 remediation session

After the initial 5-lane review, the operator authorized a depth-of-impact audit + fix pass for the P0 clip. The following landed in this session:

### Landed code changes

| Surface | Change | File:line | Test antibody |
|---|---|---|---|
| WU public key false-positive loop | `[REVIEW-SAFE: WU_PUBLIC_KEY]` banner + inline tag at all 3 use sites | `src/data/observation_client.py:103-122`, `src/data/daily_obs_append.py:95-114`, `src/data/wu_hourly_client.py:50` | grep-discoverable tag |
| Durable false-positive index | New file (root) | `SECURITY-FALSE-POSITIVES.md` | — |
| gitleaks allowlist (positions for future scan) | New file (root) | `.gitleaks.toml` | — |
| Dual-channel git hooks | Refactored `pre-commit-invariant-test.sh` + `pre-merge-contamination-check.sh` to detect git vs agent channel; new `pre-commit-secrets.sh` orchestrator wrapper; symlink `pre-merge-commit` → contamination check; added secrets entry to `settings.json` | `.claude/hooks/*` | smoke-tested 4-channel matrix (agent + non-commit / agent + commit / git via env / git via orchestrator) all exit clean |
| One-time operator hook installer | New script | `scripts/install_hooks.sh` | self-smoke-test |
| INV-05 antibody (was doc-only across 3 reviews) | New test parsing kernel SQL `risk_actions` CHECK constraint | `tests/test_architecture_contracts.py::test_risk_actions_exist_in_schema` | passing; baseline 217→218 |
| INV-19a sibling antibody (DATA_DEGRADED design lock) | New test asserting rank order + strict-equality sweep predicate | `tests/test_dual_track_law_stubs.py::test_data_degraded_does_not_trigger_force_exit_sweep` | passing; not yet in TEST_FILES |
| INV-X for_city routing antibody | New test asserting no `src/` file outside `settlement_semantics.py` constructs `SettlementSemantics(...)` directly or passes `rounding_rule='...'` kwarg literal | `tests/test_settlement_semantics.py::test_settlement_semantics_construction_routes_through_for_city` | passing; baseline 218→219 |
| settle_market dormancy doc | 30-line PRODUCTION-CALLER STATUS docstring header | `src/contracts/settlement_semantics.py:265-298` | — |
| Pre-commit hook baseline updated | `BASELINE_PASSED 217 → 219` (+2 net new antibodies) | `.claude/hooks/pre-commit-invariant-test.sh:68` | hook smoke-tested |

### Filed proposals (operator action)

| File | Contents | Decision required |
|---|---|---|
| `docs/operations/repo_review_2026-05-01/AGENTS_MD_DATA_DEGRADED_clarification.md` | Three-option proposal (clarify / reverse / defer) for AGENTS.md "Risk levels" paragraph | Choose (a)/(b)/(c) |
| `docs/operations/repo_review_2026-05-01/CI_GATE_TRIAGE_PROPOSAL.md` | 4-phase triage path for the 120-failure cleanup behind a CI gate flip | Schedule slice |
| Architectural `fatal_misreads.yaml:141` wording correction (mentioned in P0-5 above) | Reword "unconstructable at compile/import time" → SOCIAL gate (runtime via for_city) + TYPE gate (compile-time via settle_market for new code) | Operator-blessed amendment |

### What requires the operator next

1. **Run `bash scripts/install_hooks.sh`** to point `core.hooksPath` at `.claude/hooks` so operator-direct commits gate too. (I am structurally forbidden from running `git config` per global CLAUDE.md.)
2. **Optional**: `brew install gitleaks` so `pre-commit-secrets.sh` becomes active rather than gracefully no-op.
3. **Decisions on the two filed proposals** above.

### Verifier lane integration (lane finished after synthesis)

The verifier lane (live_running.md, 23KB on disk) finished after the initial SYNTHESIS landed. Headlines: 5 READY / 5 SOFT / 0 NOT-READY across 10 subsystems. The two findings most relevant to the P0 work:
- **SOFT verdict #5**: `log_settlement_v2()` has no internal gate, only the SOCIAL gate from callers. → Validates the P0-5 reclassification (SOCIAL gate is the load-bearing layer; future Tier 3 P8 migration would add type-layer protection, but the SOCIAL antibody now landed protects what's actually exposed today).
- **Most critical finding**: full `pytest -m ""` shows **120 failures**. → This was the "scary scope" behind P0-6; CI_GATE_TRIAGE_PROPOSAL.md responds.

Other SOFT verdicts to triage in their own slices: KeepAlive=false on trading daemon, WS reconnect lacks backoff/jitter+dedup, 403 ERRORs at boot, F5 venue heartbeat unresolved on this branch.

## 5. Lane reports on disk

- `architecture.md` — K-decisions K1-K4, 10 findings, INV-## drift sample (1/7 doc-only), provenance survey, 8 hardening recipes
- `test_topology.md` — 36 INV inventory, 33 real / 3 doc / 1 weak, **83 skip + 50 runtime-skip ledger**, relationship-vs-function ratio (17/83), CI blocking gate coverage analysis
- `security.md` — 2 P0, 5 P1, 6 P2, per-category breakdown including SQL/subprocess/WS/RPC/launchd
- `adversarial.md` — 0 P0, 3 P1, 1 P2, 5 P3 (DATA_DEGRADED→RED-sweep finding lives here)
- `architecture.md` and `SYNTHESIS.md` (this file) — written by team-lead because architect's prompt blocked Write/Edit

---

## 6. Final note

Zeus's law layer is one of the most disciplined I've audited — 36 invariants, fatal misreads cataloged, negative constraints declared, topology doctor as a routing gate. **The fragility is at the seams between layers.**

The 5 K-decisions above turn one-ring enforcement into two-ring at exactly the seams where the law-vs-runtime drift accumulates. Pre-live-trading discipline says: do P0-1 through P0-6 before any further forward motion on r3 / EDGE / LEARNING_LOOP work.

This is a system one structural-decision-pass away from production-grade. The remediation branch is already 60% there.
