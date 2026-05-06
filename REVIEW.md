# Zeus Review Doctrine (root)

This file is the entry point for any AI or human reviewer of a Zeus pull
request. It is self-contained: read it end-to-end and start reviewing without
opening another file. The long-form canonical doctrine lives at
`docs/review/code_review.md`; the path → tier table lives at
`docs/review/review_scope_map.md`. Open those only when this file is not enough.

Zeus is a live quantitative trading engine on Polymarket weather derivatives.
Every change has the potential to move real money, corrupt canonical truth, or
silently break a probability chain. Review budget is finite. Spend it where
runtime risk is.

---

## 1. Review mission

Find newly introduced bugs, regressions, semantic breaks, and missing tests
that matter for **production / runtime behavior**. Do not produce style
critiques as a substitute for runtime analysis.

A useful review identifies: live-money loss paths, identity/semantic breaks at
module boundaries, persistence/settlement violations, missing antibodies for
hazardous behavior, and authority confusion. A useless review enumerates
formatting, naming, and generic refactor preferences while runtime hazards go
unmentioned.

---

## 2. Severity model (canonical, identical across all reviewer files)

**Critical / Blocking** — must be fixed or explicitly accepted before merge.

- Live-money loss path or runtime-safety failure on the live execution lane.
- Venue/CLOB object-identity error: market_id, condition_id, token_id, YES/NO
  side, price-direction, fee/tick/min-order semantics.
- Settlement-value violation: bypass of `SettlementSemantics.assert_settlement_value()`
  or wrong rounding rule (`wmo_half_up` vs `oracle_truncate` for HKO only).
- Persistence corruption: torn write, transaction-boundary split (INV-08
  violation), append-first discipline broken (INV-03).
- Wrong P&L attribution; settlement vs exit confusion (INV-02).
- Broken kill switch or fail-closed risk: RED that does not cancel + sweep
  (INV-19), advisory-only risk (INV-05), authority-loss raising RuntimeError
  instead of read-only mode (INV-20).
- Irreversible chain mutation without reconciliation (INV-18); void on
  CHAIN_UNKNOWN.
- Schema or migration with data loss / unrecoverable shape change.
- Secret exposure (any credential, key, OAuth token, RPC URL with auth).
- Market-order substitution for limit-order in execution path.
- LLM output promoted to authority (INV-10).
- `place_limit_order` invoked outside the gateway (INV-24); V2 preflight
  bypassed (INV-25); side effect without `venue_commands` row (INV-28, INV-30).

**Important** — likely production regression or semantic break; reviewer
should request changes unless author justifies.

- Probability / economics layer crossing without provenance: posterior
  built from raw quote, executable cost confused with implied probability,
  vig/fee treatment inverted (INV-21, INV-33, INV-34, INV-35).
- Held-token quote crossing into posterior (INV-36).
- Authority confusion: derived JSON or report treated as canonical, archive
  promoted to truth without explicit promotion event (INV-17 violation).
- Exit-as-close confusion (INV-01); non-canonical lifecycle phase string
  (INV-07).
- `strategy_key` drift: parallel governance keys, `make_family_id` not
  routed through canonical helper (INV-04, INV-22).
- High/low dual-track contamination: shared rows in calibration, replay,
  or settlement rebuild identity.
- DB-before-JSON inversion: derived export written before canonical commit
  (INV-17).
- Missing test for risky behavior: a relationship invariant changed without
  a test that would have caught the regression.
- Agent-instruction misroute: planning-lock bypass, contradicting scoped
  `src/**/AGENTS.md`, redirecting reviewers around `topology_doctor` or
  authority docs.
- Degraded portfolio export labeled `authority="VERIFIED"` (INV-23).
- Forecast row without canonical cycle identity entering canonical training
  (INV-15); Day0 low using historical Platt instead of nowcast (INV-16).
- Risk-level branch that does not change behavior (INV-05 territory).
- non-NORMAL `runtime_posture` not blocking new entries (INV-26).
- Cycle summary missing execution-truth warnings (INV-27).

**Nit** — style, naming, formatting, generic refactor preference, docs typos,
comment polish.

**Suppression rule**: if Critical or Important findings exist on this PR,
defer all Nit comments to a separate pass or omit. Nits never crowd out
runtime-risk findings. If the diff has no Tier 0 / Tier 1 surface (see §4) or
zero Critical/Important findings after a complete pass, you may emit Nits
sparingly — never as filler.

Other ladders (e.g. internal evidence-bucketing scales used by review or
debate workflows) are orthogonal to this PR-review severity model. When
reporting on a PR, use this ladder.

---

## 3. Budget allocation — do not traverse in GitHub file order

Default GitHub PR view sorts files alphabetically or by path. Reviewing in that
order on a large diff is the named failure mode this doctrine exists to prevent:
budget gets consumed by `.claude/`, `docs/archives/`, `logs/`, generated cache
before reaching `src/execution/` or `src/contracts/`.

The required order:

1. **Read the PR body's "AI Review Scope" section first** (template forces
   author to declare change-type, high-risk paths, deprioritize paths, and
   whether the PR is a large refactor).
2. **Group changed paths into Tiers** using §4 below.
3. **Exhaust Tier 0** (live money / runtime safety) before moving on.
4. **Exhaust Tier 1** (data / probability / persistence correctness) before
   moving on.
5. **Tier 2** (tests / validation) — verify tests cover Tier 0/1 changes.
6. **Tier 3** (docs / instructions / agent surfaces) — only if
   reviewer-budget remains and the change actually mutates instruction
   authority.
7. **Skip list** — only review if the change demonstrably alters runtime
   behavior. Burden of proof on the change.

If you cannot exhaust Tier 0 within reviewer budget, stop and produce a
**partial-coverage report** (see §5). Do not silently skip into Tier 3 to
look productive.

---

## 4. Project-specific priority surfaces (Zeus)

Detailed path table at `docs/review/review_scope_map.md`. Compressed map:

**Tier 0 — Live money / runtime safety / kill switch**
- `src/execution/**` — executor, exit_triggers, exit_lifecycle, collateral,
  settlement_commands, wrap_unwrap_commands, fill_tracker, harvester
- `src/venue/**` — Polymarket V2 adapter, CLOB submission, on-chain calls
- `src/contracts/{settlement_semantics,execution_price,venue_submission_envelope,fx_classification}.py`
- `src/state/{lifecycle_manager,chain_reconciliation,db,ledger,projection,collateral_ledger,venue_command_repo,readiness_repo}.py`
- `src/riskguard/**`, `src/control/**`, `src/supervisor_api/**`
- `src/main.py`, `src/engine/{cycle_runner,evaluator,monitor_refresh}.py`
- `migrations/**`, `architecture/2026_04_02_architecture_kernel.sql`

**Tier 1 — Data / probability / persistence correctness**
- `src/calibration/**` — Platt fitting, manager, replay
- `src/signal/**` — P_raw, ensemble, MC noise, ASOS rounding
- `src/strategy/**` — strategy_key grammar, market_phase, oracle interaction
- `src/data/**`, `src/ingest/**` — forecast ingest, dual-track integrity
- `src/contracts/{calibration_bins,edge_context,epistemic_context,vig_treatment,reality_contract,reality_contracts_loader,reality_verifier,provenance_registry}.py`
- `src/oracle/**`, `src/observability/**`, `src/types/**`, `src/runtime/**`,
  `src/risk_allocator/**`, `src/analysis/**`, `src/backtest/**`
- `src/state/{portfolio,portfolio_loader_policy,decision_chain,job_run_repo,source_run_repo,market_topology_repo}.py`

**Tier 2 — Tests and validation**
- `tests/contracts/**`
- `tests/test_*invariant*.py`, `tests/test_architecture_contracts.py`
- `tests/**` paired with Tier 0/1 paths

**Tier 3 — Docs / instructions / agent surfaces**
- `AGENTS.md` (root and scoped `src/**/AGENTS.md`, `docs/**/AGENTS.md`,
  `tests/**/AGENTS.md`, `architecture/**/AGENTS.md`)
- `.agents/**` (repo-local skills / handoff)
- `.claude/skills/**`, `.claude/agents/**`, `.claude/hooks/**`,
  `.claude/settings.json`, `.claude/CLAUDE.md`
- `.github/{copilot-instructions.md,instructions/**,pull_request_template.md,workflows/**}`
- `architecture/**` (invariants, manifests, source_rationale, history_lore,
  task_boot_profiles, fatal_misreads, runtime_modes)
- `docs/authority/**`, `docs/operations/current_*.md`, `docs/reference/**`,
  `docs/review/**`, `REVIEW.md`, `workspace_map.md`, `docs/archive_registry.md`

**Deprioritized — review only if change demonstrably alters runtime behavior**
- `.claude/orchestrator/**`, `.claude/worktrees/**`, `.code-review-graph/**`,
  `.omc/**`, `.omx/**`, `.zeus/**`, `.zeus-githooks/**`, `.zpkt-cache/**`
- `docs/archives/**`, `docs/artifacts/**`, `docs/reports/**`,
  `docs/operations/archive/**`, closed `docs/operations/task_*/**` packets
- `logs/**`, `raw/**`, `state/**`, `evidence/**`
- `*.lock`, `.DS_Store`, `*.log`, `__pycache__/**`, `*.pyc`,
  `.gitleaks.toml`, `.importlinter` (configs — review only when changed)
- Generated files, fixture data, prompt archives, large model outputs

---

## 5. Large PR rule

If the diff is too large for full coverage within reviewer budget:

1. State explicitly which Tier 0 / Tier 1 paths were reviewed and which were
   not.
2. **"No findings" with partial coverage is not a clean pass.** Report as
   "partial coverage; no Critical/Important findings on the reviewed slice
   (paths X, Y, Z); paths A, B, C not reviewed."
3. Recommend **semantic-slice review**: ask author to split the PR by Tier
   (execution slice, contracts slice, calibration slice, docs slice) or
   re-open as multiple PRs. Reviewing one Tier per pass is acceptable.
4. Do **not** scan the file tree alphabetically and stop when budget runs
   out; this guarantees Tier 3 review of a Tier 0 change.
5. If a PR is flagged as "large refactor / mechanical" in the AI Review Scope,
   the reviewer should sample-verify a representative subset of Tier 0/1
   files and then state coverage explicitly. Mechanical refactors still admit
   identity-bearing breakage (e.g., a rename that crosses `strategy_key`
   semantics).

---

## 6. Evidence rule

- Every finding must cite a concrete `path:line` (or `path` + named
  changed-behavior) and the relevant invariant/contract if applicable.
- No speculative warnings without code evidence ("consider adding tests"
  with no specified test target is noise).
- Prefer fewer high-confidence findings over many low-confidence ones.
- If uncertain whether a finding is real, mark it **Uncertain** and state
  what would resolve it (specific file to read, specific test to run, or a
  clarifying question to the author).
- When citing an invariant, cite the ID (`INV-NN`) and the file
  `architecture/invariants.yaml` for the canonical statement.

---

## 7. Reporting rule

Each finding has:

```
Severity: Critical | Important | Nit | Uncertain
Path:     <path>:<line>  (or <path> + behavior-name)
What:     one-sentence description of the change and the break
Why:      runtime/economic/identity consequence
Fix:      minimal direction or a concrete question for the author
Evidence: invariant ID, related test, or cited rule
```

Top of the review carries a **coverage statement**:

```
Reviewed: <Tier 0 paths>; <Tier 1 paths>; <Tier 2 paths>; <Tier 3 paths>
Skipped:  <skip-list paths>; reason: <one line>
Coverage: full | partial (state which Tiers complete)
Findings: <N Critical, N Important, N Nit, N Uncertain>
```

Empty findings + partial coverage is reported as such, not as a clean pass.

---

## 8. Pre-review local commands (for human reviewers)

```
git diff --stat                                    # diff shape
git diff --name-only | sort                        # changed paths
git diff --numstat | sort -nr | head -50           # largest changes first
git diff -- src/execution/ src/venue/ src/contracts/   # Tier 0 first
git diff -- src/calibration/ src/signal/ src/strategy/ # Tier 1
```

For Zeus-specific guard checks the operator may run before requesting review:

```
python3 scripts/topology_doctor.py --navigation --task "<task>" --files <files>
python3 scripts/topology_doctor.py --planning-lock --changed-files <files>
python3 scripts/topology_doctor.py --map-maintenance --changed-files <files>
```

These commands are not required for AI reviewers; they are tools the human
author or human reviewer can use to pre-prune the review surface.

---

## 9. Reviewer behavior contract

- Read the PR body's AI Review Scope first; trust author-declared
  high-risk paths.
- Do not traverse alphabetically.
- Do not flood the PR with Nit comments when Critical/Important findings exist.
- Do not assert findings without `path:line` evidence.
- Empty findings + partial coverage is partial coverage, not clean pass.
- For docs/instructions changes, review by **what authority will read this**
  (which agent reads which file), not by prose quality.
- For migration changes, focus on data-loss reversibility and existing-row
  preservation.
- For test changes, ask: does this test catch the regression class the
  paired source change introduces?

---

## 10. When in doubt

- Trust code over docs (root `AGENTS.md` rule, applies to review too).
- Trust canonical DB / event log over derived JSON.
- Trust scoped `src/**/AGENTS.md` for module-specific danger levels.
- Cite `architecture/invariants.yaml` for invariant statements.
- If still unresolved, mark **Uncertain** and ask the author a single
  concrete question.

The deeper doctrine — full path priority, how-to-review-migrations, how to
build a synthetic-slice review, examples of useful vs useless findings —
lives at `docs/review/code_review.md`. Open that only when this file does
not answer your immediate question.
