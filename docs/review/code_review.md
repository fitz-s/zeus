# Zeus code review — canonical doctrine

This is the long-form canonical review doctrine. It expands the compressed
root `REVIEW.md` and the path table in `review_scope_map.md`. Read it when
the root file is not enough.

Audience: Codex / OpenAI review agents, human PR reviewers, future
maintainers, manual Claude Code review sessions.

The compressed mirror of this doctrine for Claude Code Review / ultrareview
is `REVIEW.md`. The compressed mirror for GitHub Copilot Code Review is
`.github/copilot-instructions.md`. **Authority carve-out:**
`review_scope_map.md` is authoritative for path → tier mapping (the path
table only). This file (`code_review.md`) is authoritative for the doctrine
itself: severity ladder semantics, large-PR rule, evidence rule, reporting
template, skip-list semantics, reviewer behavior contract. The compressed
mirrors must be reconciled to whichever of these two files is authoritative
for the disagreement; mirror-vs-canonical drift is reconciled toward the
canonical.

---

## 1. Review goals

A Zeus review must answer four questions for the changed surface:

1. Does the change preserve canonical truth? (DB > derived JSON; chain >
   Chronicler > portfolio cache; append-first; one transaction boundary.)
2. Does the change preserve identity at boundaries? (`strategy_key`,
   market_id / condition_id / token_id, YES/NO side, high/low track,
   `temperature_metric` / `physical_quantity` / `observation_field` /
   `data_version`.)
3. Does the change preserve fail-closed behavior? (RED cancels + sweeps;
   authority-loss → read-only; missing data → DATA_DEGRADED, not optimistic
   continuation; non-NORMAL `runtime_posture` blocks new entry.)
4. Does the change preserve probability/economics layering? (P_raw →
   P_cal → P_posterior is one-way; executable-cost economics drives Kelly,
   not implied probability; held-token quote does not become posterior
   prior; vig/fee treatment is named, not implicit.)

Findings that do not relate to one of these four questions or to a Critical
severity item from §3 below are likely Nits.

---

## 2. Non-goals

- Style critique. Zeus review is not linting.
- Generic refactor preference. "This could be smaller / more elegant" is
  a Nit at best, often noise.
- Documentation prose polish. Treat docs review as authority review (§9),
  not copy-edit.
- Test-coverage cargo culting. "Add a test" with no specified test target
  is not a finding.
- Full-tree audit on every PR. Stay inside the diff.

---

## 3. Severity ladder

Identical to root `REVIEW.md`. Restated for self-containment:

### Critical / Blocking

Block merge until fixed or explicitly accepted by the operator.

- Live-money loss path or runtime-safety failure on the live execution lane
  (`src/execution/**`, `src/venue/**`, `src/main.py`, `src/engine/cycle_runner.py`).
- Venue/CLOB object identity error: market_id, condition_id, token_id,
  YES/NO side, price-direction inversion, fee/tick/min-order semantics
  drift, slippage rounding wrong direction (BUY rounds UP, SELL rounds DOWN).
- Settlement-value violation: bypass of
  `SettlementSemantics.assert_settlement_value()`; wrong rounding rule
  (`oracle_truncate` is HKO-only; `wmo_half_up` is the WU/NOAA/CWA default;
  swapping these silently mismatches the oracle).
- Persistence corruption: torn write, transaction-boundary split (INV-08),
  append-first discipline broken (INV-03), event append separated from
  projection fold across transactions, void on CHAIN_UNKNOWN (INV-18).
- Wrong P&L attribution; settlement vs exit confusion (INV-02).
- Fail-closed bypass: RED that does not cancel + sweep (INV-19), advisory-only
  risk (INV-05), authority-loss raising RuntimeError that kills the cycle
  instead of read-only mode (INV-20), non-NORMAL `runtime_posture` not
  blocking entry (INV-26).
- Schema or migration with data loss / unrecoverable shape change.
- Secret exposure (any credential, key, OAuth token, RPC URL with auth,
  Polymarket account key, MetaMask seed, oracle API token).
- Market-order substitution where Zeus law requires limit orders only.
- LLM output promoted to authority (INV-10).
- `place_limit_order` invoked outside the gateway (INV-24); V2 endpoint
  preflight bypassed (INV-25); venue side effect without `venue_commands`
  pre-row (INV-28, INV-30); `client.place_limit_order` not preceded by a
  venue-command (INV-30); cycle start not scanning unresolved venue command
  states (INV-31); position authority advancing before required write
  (INV-32).

### Important

Request changes unless author justifies.

- Probability/economics crossing without provenance (INV-21, INV-33,
  INV-34, INV-35): posterior built from raw quote; executable cost confused
  with implied probability; Kelly using bare `entry_price` instead of
  executable-price distribution; `FinalExecutionIntent` carrying posterior
  / VWMP / BinEdge / `p_market` / `entry_price` recompute inputs.
- Held-token quote/proceeds crossing into posterior (INV-36).
- Authority confusion (INV-17): derived JSON written before canonical DB
  commit; archive promoted to truth without explicit operator promotion;
  reports treated as canonical; "VERIFIED" label on a degraded portfolio
  projection (INV-23).
- Exit-as-close confusion (INV-01); non-canonical lifecycle phase string
  (INV-07); inventing phase strings outside `LifecyclePhase` enum.
- `strategy_key` drift (INV-04): parallel governance keys; `make_family_id`
  not delegated to canonical helper (INV-22).
- Dual-track contamination: high and low temperature rows mixed in
  calibration, Platt fitting, replay bin lookup, or settlement rebuild
  identity. Same-day high vs low must not share `data_version` semantics or
  `temperature_metric` fields incorrectly.
- Forecast row without canonical cycle identity entering canonical training
  (INV-15); Day0 low using historical Platt instead of nowcast when
  `causality_status != 'OK'` (INV-16).
- Missing relationship test for risky behavior — see §10.
- Agent-instruction misroute: planning-lock bypass on
  `architecture/**` / `docs/authority/**` / `.github/workflows/**` /
  `src/state/**` truth ownership / `src/control/**` / `src/supervisor_api/**`;
  contradicting scoped `src/**/AGENTS.md`; redirecting reviewers around
  `topology_doctor` or authority docs.
- Risk-level branch that does not change behavior (INV-05 territory).
- Cycle summary missing execution-truth warnings (INV-27).
- Reality contract drift: stale or unverified `architecture/reality_contracts/**`
  entry referenced from production code without freshness gate.
- Provenance-registry violation (INV-13): emitting a constant without
  registration; cascade safety broken.
- `runtime_posture` mutated at runtime (INV-26 says it is read-only at
  runtime).

### Nit

- Style, naming, formatting, generic refactor preference, docs typos,
  comment polish, import ordering, line length, choice between equivalent
  idioms.

### Uncertain

Mark a finding as Uncertain when you cannot resolve it without information
the diff does not contain (a file you have not read, a test you have not
run, a clarifying answer from the author). State the resolution path
explicitly.

### Suppression rule

If Critical or Important findings exist, defer all Nits to a separate pass
or omit. If the diff has no Tier 0 / Tier 1 surface and zero
Critical/Important after a complete pass, you may emit a small set of
Nits (≤ 5). Never use Nits as filler. Never let Nits drown a Critical
finding by sheer volume.

### Other ladders are orthogonal

Internal review or debate workflows may use a separate evidence-bucketing
scale (e.g. LOW / MED / HIGH / CRITICAL) for their own purposes. Those
scales govern intra-workflow evidence accumulation and are orthogonal to
the Critical / Important / Nit ladder above. PR review reporting uses
this ladder; do not merge.

---

## 4. Project-specific risk map

Zeus is a live quantitative trading engine on Polymarket weather derivatives.
The money path is causal:

```
contract semantics → source truth → forecast signal → calibration →
edge → execution → monitoring → settlement → learning
```

The probability chain is:

```
51 ENS members → per-member daily max → Monte Carlo (sensor noise + ASOS rounding) →
P_raw → Extended Platt (A·logit + B·lead_days + C) → P_cal →
α-weighted Market Fusion → P_posterior → Edge & Double-Bootstrap CI →
Fractional Kelly → Position Size
```

A break anywhere in either chain is at least Important and often Critical.

The truth path is:

```
Chain (Polymarket CLOB) > Chronicler (event log) > Portfolio (local cache) >
canonical DB → derived JSON / status / reports
```

Direction is one-way. Reverse direction (JSON outranking DB; portfolio cache
outranking chain) is Critical.

Strategy families (4):
- Settlement Capture (slow alpha, observation-speed bound)
- Shoulder Bin Sell (moderate alpha)
- Center Bin Buy (fast-decay alpha)
- Opening Inertia (fastest-decay alpha)

`strategy_key` is the sole governance identity. `edge_source`,
`discovery_mode`, `entry_method` are metadata, not parallel keys (INV-04).

---

## 5. Runtime semantic invariants

Authoritative source: `architecture/invariants.yaml`. The current set
spans INV-01 through INV-36 (with some gaps). Cite by ID in findings.

The invariants every reviewer should know:

| ID | Statement (compressed) |
|---|---|
| INV-01 | Exit is not local close. |
| INV-02 | Settlement is not exit. |
| INV-03 | Canonical authority is append-first and projection-backed. |
| INV-04 | `strategy_key` is the sole governance key. |
| INV-05 | Risk must change behavior; advisory-only risk forbidden. |
| INV-06 | Point-in-time truth beats hindsight truth. |
| INV-07 | Lifecycle grammar is finite (only 9 phases + 3 terminals). |
| INV-08 | Canonical write path has one transaction boundary. |
| INV-09 | Missing data is first-class truth. |
| INV-10 | LLM output is never authority. |
| INV-13 | Provenance registry: constants must be registered. |
| INV-14 | Temperature-family rows carry full identity (`temperature_metric`, `physical_quantity`, `observation_field`, `data_version`). |
| INV-15 | Forecast rows without canonical cycle identity → degrade only, not training. |
| INV-16 | Day0 low with `causality_status != 'OK'` → nowcast only, not historical Platt. |
| INV-17 | DB authority writes COMMIT before any derived JSON export update. |
| INV-18 | Chain reconciliation is three-valued; void requires CHAIN_EMPTY. |
| INV-19 | RED risk must cancel pending and sweep active; advisory RED forbidden. |
| INV-20 | Authority-loss → read-only mode, not RuntimeError. |
| INV-21 | Kelly requires executable-price distribution, not bare `entry_price`. |
| INV-22 | `make_family_id()` must resolve to one canonical family grammar. |
| INV-23 | Degraded portfolio projection never exports `authority="VERIFIED"`. |
| INV-24 | `place_limit_order` is gateway-only. |
| INV-25 | V2 preflight failure → no live `place_limit_order` for that cycle. |
| INV-26 | `runtime_posture` is read-only at runtime; non-NORMAL blocks new entry. |
| INV-27 | Cycle summary surfaces execution-truth warnings. |
| INV-28 | Every venue order side effect must be journaled. |
| INV-29 | `VenueCommand` and `IdempotencyKey` are frozen dataclasses. |
| INV-30 | `client.place_limit_order` preceded by a `venue_commands` row. |
| INV-31 | Cycle start scans `venue_commands` for unresolved states. |
| INV-32 | Position authority advances only after the required write. |
| INV-33 | Corrected posterior consumes calibrated belief + named market prior; raw quote vectors are legacy-only. |
| INV-34 | Corrected Kelly/FDR sizing uses executable cost-basis economics, not implied probability. |
| INV-35 | Corrected `FinalExecutionIntent` is submit-ready without posterior / VWMP / BinEdge / `p_market` / `entry_price` recompute inputs. |
| INV-36 | Monitor and exit lanes keep held-token quote/proceeds separate from posterior belief. |

When the diff touches any module that an invariant cites, the reviewer
should at minimum check that the invariant is not weakened.

---

## 6. Path priority map

See `review_scope_map.md` for the full per-path table. Compressed:

- **Tier 0 — Live money / runtime safety / kill switch.** Every PR
  reviewer reaches Tier 0 first.
- **Tier 1 — Data / probability / persistence correctness.** After Tier 0.
- **Tier 2 — Tests and validation.** Verify Tier 0/1 changes are tested.
- **Tier 3 — Docs / instructions / agent surfaces.** Authority review only
  if reviewer-budget remains.
- **Skip list.** Review only if change demonstrably mutates runtime.

---

## 7. Skip / deprioritize rules

Default-skip paths (review only if change mutates runtime behavior):

- `.claude/orchestrator/**`, `.claude/worktrees/**`, `.code-review-graph/**`
- `.omc/**`, `.omx/**`, `.zeus/**`, `.zeus-githooks/**`, `.zpkt-cache/**`
- `docs/archives/**`, `docs/artifacts/**`, `docs/reports/**`,
  `docs/operations/archive/**`, closed `docs/operations/task_*/**` packets
- `logs/**`, `raw/**`, `state/**`, `evidence/**`
- `*.lock`, `.DS_Store`, `*.log`, `__pycache__/**`, `*.pyc`
- Generated files, fixture data, prompt archives, large model outputs

Burden of proof: a PR touching a skip-list path needs the AI Review Scope
section to explicitly call out why it merits review (e.g., a hook file
in `.claude/hooks/` whose change materially affects pre-commit / pre-merge
enforcement, or a `.code-review-graph/` schema bump that changes the
review graph contract).

---

## 8. Large PR strategy

Zeus PRs sometimes batch multiple slices intentionally — the operator
batches to amortize per-PR review cost (every PR open triggers automated
review). Reviewers must not punish batching by failing to cover the
batched diff.

When a PR exceeds reviewer budget:

1. Triage the diff using `review_scope_map.md`.
2. **Exhaust Tier 0 first.** Do not move to Tier 1 until Tier 0 is
   reviewed.
3. **Synthetic slice review.** If multiple Tier 0 surfaces are present,
   review them as named slices (`execution slice`, `state slice`,
   `contracts slice`, `venue slice`). Produce one finding-set per slice
   plus an aggregate coverage statement.
4. **Coverage statement at top of review** (see §11 reporting template).
5. Recommend slice-PRs only if the operator has not already explained
   why the batch is needed in the PR body.
6. Do **not** alphabetically traverse and stop when budget runs out;
   that is the named failure mode this doctrine prevents.

---

## 9. Reviewer budget allocation

Default budget split (rough heuristic; reviewer adjusts to PR shape):

- Tier 0 paths: 60% of budget.
- Tier 1 paths: 25% of budget.
- Tier 2 paths: 10% of budget.
- Tier 3 paths: 5% of budget.

If the diff has no Tier 0 surface, redistribute toward Tier 1 then Tier 3.
A docs-only PR justifies Tier 3 review.

---

## 10. Reviewing tests

A test change is reviewed by asking:

1. **Does this test catch the regression class the paired source change
   could introduce?** A test that asserts what the new code does (tautology)
   is not a regression test; it is a snapshot.
2. **Is this a relationship test or a function test?** Per the project
   methodology (`AGENTS.md` references): relationship tests verify
   "when Module A's output flows into Module B, what property holds across
   the boundary?" Function tests verify single-function behavior. Both
   matter; a Critical / Important source change usually demands a
   relationship test.
3. **Is this a contract test?** `tests/contracts/**` and
   `tests/test_architecture_contracts.py` enforce invariants. Removing or
   weakening one is at least Important and usually Critical.
4. **Is this `xfail` / `skip` removal honest?** "Activating" a test means
   removing a specific `@pytest.mark.xfail` or `skip` and the assertion
   is now expected to hold. "Extending" means adding a new assertion. The
   PR should be honest about which.

A test deletion without a replacement covering the same regression class
is at least Important.

---

## 11. Reviewing migrations / schema changes

`migrations/**` and `architecture/2026_04_02_architecture_kernel.sql` are
Tier 0. Review by:

1. Is the migration reversible? Is the rollback path explicit?
2. Does the migration preserve existing rows? Loss without explicit
   operator-acknowledged data-loss-acceptance is Critical.
3. Does it cross identity columns (`temperature_metric`,
   `physical_quantity`, `observation_field`, `data_version`,
   `strategy_key`)? Identity-column changes are Tier 0.
4. Does the migration commit before any derived JSON export change
   (INV-17)?

---

## 12. Reviewing docs / instruction changes

For changes to `AGENTS.md`, `.claude/**`, `docs/authority/**`,
`architecture/**`, `.github/copilot-instructions.md`, this `code_review.md`,
`REVIEW.md`, `docs/review/review_scope_map.md`, the PR template:

1. **Authority direction.** Does the change introduce contradiction with
   another authority surface? Authority order is `code > docs > derived`;
   docs do not invert that.
2. **Scope creep.** Does the doc grow beyond what it is supposed to do?
   E.g., a "review" doc absorbing routing rules, or a "skill" doc
   becoming a universal ritual.
3. **Sunset / staleness.** Is the doc dated, scoped, and self-aware about
   when it can be retired?
4. **Reader contract.** Each doc has a named reader (Codex, Copilot,
   Claude session, human). Does the change preserve or break that
   contract? E.g., bloating Copilot instructions past 4000 chars breaks
   the Copilot contract.
5. **Path-routing.** If the doc points readers to other files, are those
   files current? Stale citations are Important.

A docs change that flips an invariant or rewrites a runtime contract is
not a docs change — it is a runtime change in disguise.

---

## 13. Handling uncertainty

When a finding is real-but-unverifiable from the diff alone:

- Mark **Uncertain**.
- State what would resolve the uncertainty: "Reading
  `src/state/lifecycle_manager.py:apply_transition` would confirm whether
  this new caller honors INV-07," or "running
  `tests/contracts/test_strategy_key_governance.py` would confirm INV-04."
- Do not promote Uncertain to Critical without resolution.
- Do not bury Critical findings as Uncertain to avoid blocking the PR.

---

## 14. Required coverage statement

Every Zeus review concludes with a coverage statement. Template:

```
Coverage:
  Reviewed (Tier 0): src/execution/executor.py, src/contracts/settlement_semantics.py
  Reviewed (Tier 1): src/calibration/manager.py
  Reviewed (Tier 2): tests/contracts/test_settlement_semantics.py
  Reviewed (Tier 3): -
  Skipped:           docs/archives/**, .claude/orchestrator/** (deprioritized)
  Unreviewed:        src/state/chain_reconciliation.py (out of budget; recommend slice review)

Findings: 1 Critical, 2 Important, 0 Nit, 1 Uncertain
```

If `Unreviewed` is non-empty, the review is **partial coverage**, not
clean pass.

---

## 15. Useful vs useless findings

**Useful.**

> **Critical** — `src/execution/executor.py:1422` — new branch sets
> `phase = "holding"` directly instead of routing through
> `lifecycle_manager.apply_transition()`. This invents a lifecycle phase
> string outside `LifecyclePhase` enum and bypasses the canonical
> transition authority. Violates INV-07 and INV-01. Fix: route through
> `apply_transition(... target=LifecyclePhase.<correct-value>)`.

> **Important** — `src/calibration/manager.py:540` — Day0 low slot
> rebuild is reading historical Platt without checking `causality_status`.
> If `causality_status != 'OK'`, this should route to nowcast per
> INV-16. Add the gate before the historical lookup.

**Useless.**

> "Consider adding tests" (no specified test target).
> "This file is too long; please refactor" (no specific runtime concern).
> "Variable name unclear" on a Tier 0 file when no Critical/Important
> findings have been raised.

---

## 16. Pre-review commands (human / local)

```
git diff --stat
git diff --name-only | sort
git diff --numstat | sort -nr | head -50
git diff -- src/execution/ src/venue/ src/contracts/      # Tier 0
git diff -- src/calibration/ src/signal/ src/strategy/    # Tier 1
git log --oneline main..HEAD
```

Zeus-specific (operator-side, optional):

```
python3 scripts/topology_doctor.py --navigation --task "<task>" --files <files>
python3 scripts/topology_doctor.py --planning-lock --changed-files <files>
python3 scripts/topology_doctor.py --map-maintenance --changed-files <files>
```

These are not required for AI reviewers; they are pre-prune tools the
operator may have run before opening the PR.

---

## 17. Synthetic slice review (massive PRs)

If the diff is genuinely too large for any single review pass, the
reviewer cuts the diff into named slices and reviews each independently:

1. Group changed paths into slices by Tier and module
   (e.g., `execution slice`, `contracts slice`, `state slice`,
   `calibration slice`, `data slice`, `venue slice`, `governance slice`,
   `docs slice`).
2. Review each slice with its own findings + coverage statement.
3. Aggregate at the top: which slices were reviewed, which were not.
4. Recommend the operator open follow-up PRs for unreviewed slices, OR
   re-open as multiple PRs.

The pattern is "depth-first per slice, breadth-first across slices,"
not "alphabetical traversal."

---

## 18. Default behavior on contradiction

If two surfaces disagree:

- **Code vs docs**: code wins; flag the doc as stale (Important if doc is
  Tier 3 authority, Nit if it's a comment).
- **DB vs derived JSON**: DB wins; INV-17 violation is Important.
- **Chain vs portfolio cache**: chain wins; INV-18 territory.
- **Scoped `src/**/AGENTS.md` vs root `AGENTS.md`**: scoped wins for that
  module's domain rules; root `AGENTS.md` wins for cross-cutting routing.
- **Test vs implementation**: implementation drift from a test that was
  passing is the regression; the test is the spec.

---

## 19. Ownership and escalation

This document is owned by `docs/review/AGENTS.md` (router). Substantive
changes to severity, Tier definitions, skip-list, or large-PR rule
require operator approval. Lock-step changes to root `REVIEW.md`,
`.github/copilot-instructions.md`, and `.github/instructions/*.instructions.md`
are required to keep the cross-AI surface consistent. Drift between any
two of these surfaces is itself an Important finding on the next review.

Sunset: this doctrine is reviewed for staleness whenever
`architecture/invariants.yaml` adds a new invariant ID class or whenever
a new Tier 0 module is added under `src/`.
