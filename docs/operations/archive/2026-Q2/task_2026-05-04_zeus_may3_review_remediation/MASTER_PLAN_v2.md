# Zeus MASTER_PLAN_v2 — Round-5 Repo/Reviewer-Validated LOCK Candidate

**Created:** 2026-05-04
**Status:** `LOCK_CANDIDATE` — not implementation authority until `LOCK_DECISION.md` exists.
**Supersedes for planning:** `MASTER_PLAN.md`, `PLAN_v1.md`, `PLAN_v2.md`, `PLAN_v3.md`. Historical files remain evidence, not live instructions.
**Execution objective:** safe corrected-live Polymarket execution, not merely a cleaner plan.
**Profit caveat:** this plan can make Zeus live-safe and promotion-evidence-capable; it cannot guarantee profitability. Model edge, liquidity, risk, and execution still decide realized profit after gates pass.

---

## 0. LOCK Rules For This Document

This file is the final Round-5 candidate. From here forward, agents must treat older plan files in this directory as historical evidence unless a later `LOCK_DECISION.md` explicitly revives part of them.

The plan may only become implementation authority after these artifacts exist:

1. `scope.yaml` in this packet directory.
2. Registration in `docs/operations/AGENTS.md`, with `PLAN.md` as the topology-admitted routeable entrypoint.
3. `ORCHESTRATOR_RUNBOOK.md` present as the orchestrated-delivery companion prompt/runbook.
4. `LOCK_DECISION.md` written by the operator, naming this file as the active plan.
5. `PLAN_LOCKED.md` copied from this file, or a byte-for-byte pointer decision in `LOCK_DECISION.md` stating that `MASTER_PLAN_v2.md` is the locked artifact.
6. T-1 and T0 artifacts complete and schema-valid.

`docs/operations/current_state.md` remains the live control pointer on current `main`. Do not edit it just to make this packet visible as a lock candidate; update it only when the operator freezes an active execution packet or explicitly wants this plan named as the current live pointer.

Until then, executor agents may read this plan but must not start Tier 1 implementation.

---

## 1. Evidence Posture

This plan integrates:

1. The original uploaded dossiers from the previous tribunal:
   - `ZEUS REALITY-SEMANTICS ASYMMETRY AUDIT`
   - `ZEUS PLAN-PRE5 ULTRA REVIEW AND REALITY-SEMANTICS REPAIR PACKET`
2. Local packet evidence:
   - `DRIFT_REPORT.md`
   - `PLAN_v1.md`
   - `PLAN_v2.md`
   - `PLAN_v3.md`
   - `MASTER_PLAN.md`
   - `CRITIC_PROMPT_v1.md`
3. Current repo surfaces verified locally or by targeted read-only review:
   - `docs/to-do-list/known_gaps.md`
   - `src/state/chain_reconciliation.py`
   - `src/engine/lifecycle_events.py`
   - `src/execution/harvester.py`
   - `src/execution/settlement_commands.py`
   - `src/state/db.py`
   - `src/state/portfolio.py`
   - `src/execution/executor.py`
   - `src/engine/cycle_runtime.py`
   - `src/contracts/execution_price.py`
   - `src/strategy/market_analysis.py`
   - `src/strategy/market_analysis_family_scan.py`
   - `src/venue/polymarket_v2_adapter.py`
   - `AGENTS.md`
   - `src/execution/AGENTS.md`
   - `src/state/AGENTS.md`
   - `src/venue/AGENTS.md`
   - `scripts/semantic_linter.py`
   - `.github/workflows/architecture_advisory_gates.yml`
   - `scripts/rebuild_calibration_pairs_v2.py`
4. Reviewer Round-5 constraints:
   - Tier 1 cannot claim five independent parallel commits.
   - Packet registration, scope sidecars, dirty-worktree handling, manifest updates, and topology gates must be part of the plan, not left to executor improvisation.
   - Greenfield alerting must name delivery owner and fallback.
   - Open operator decisions that can change tier hierarchy must be closed before LOCK, not handed to Tier 1 implementers.
5. Official Polymarket/CLOB venue facts:
   - Condition ID and token ID are distinct.
   - BUY executable price is best ask; SELL executable price is best bid.
   - All orders are limit orders; GTC/GTD can rest, FOK/FAK are immediate, post-only rejects if crossing.
   - Fee assumptions must be separated from realized maker/taker fill facts.
   - Market/event identity includes condition ID, question ID, and distinct YES/NO token IDs.

Hard limitations remain:

- Local daemon state, launchd plist contents, venue open orders, production DB state, and uncommitted co-tenant work require operator/local verification.
- Reviewer and executor agents cannot approve runtime quiescence, secret redaction, or dirty-worktree ownership from prose alone.
- Docs are not authority until runtime gates, tests, and CI prove the semantics.

---

## 2. Round-5 Verdict

Do not LOCK the older `MASTER_PLAN.md` as-is.

The prior MASTER_PLAN is directionally correct but still has live-money active breaches and one structural contradiction: it says Tier 1 can be five parallel-safe commits while later requiring atomic counter consistency that makes two of those commits interdependent.

This v2 plan replaces that claim with a serialized active-breach sequence:

```text
T-1 -> T0 -> T1A -> T1F -> T1BD -> T1C -> T1E -> T1G -> T1H -> T2 -> T3 -> T4
```

The most important correction is:

> Tier 1 is no longer "five parallel-safe commits." It is a serialized and paired set of active-breach packets because adapter live-bound assertion, counter atomicity, DDL/census sequencing, harvester learning side effects, and DB timeout behavior are interdependent.

---

## 3. Authority Stack

| Rank | Authority |
| ---: | --- |
| 1 | Official Polymarket/CLOB venue facts |
| 2 | Current runtime code and call graph |
| 3 | DB schema, command journal, position lots, migrations |
| 4 | Tests and CI gates |
| 5 | Operator runbook and state census artifacts |
| 6 | Docs/AGENTS/current plan |
| 7 | Historical plan prose |

If code and docs disagree, trust code and question docs. If code cannot answer, inspect data. If data cannot answer, ask the operator.

---

## 4. Non-Negotiable Working Contract

1. No live daemon or RiskGuard daemon may run during Tier 1 implementation.
2. No agent may call `launchctl`, place/cancel venue orders, probe private credentials, or take on-chain side effects on the operator's behalf.
3. No corrected live entry may submit until Tier 3 acceptance gates pass.
4. No report may claim corrected executable P&L until Tier 3 reporting gates pass.
5. No historical row may be reclassified as corrected executable economics without point-in-time executable snapshot, cost basis, order, fill, and exit/settlement evidence.
6. Operator environment variables may only brake or disable; they may not enable corrected live in the absence of per-position/per-intent evidence gates.
7. Compatibility code may remain only if provably non-live and non-promotion.
8. New scripts and tests must be registered in the appropriate manifests before they are used as closeout evidence.
9. Packet prompts must name exact allowed files, forbidden files, required companion manifest files, invariants, tests, commands, expected pre-fix failures, closeout evidence, rollback, and not-now constraints.
10. Executor agents must not opportunistically refactor, broaden scope, or delete uncertain live-reachable code. Quarantine first.

---

## 5. Reviewer-Amended Findings That Drive This Plan

### F1 — Adapter Compatibility Placeholder Submit Is Tier 1

`PolymarketV2Adapter.submit_limit_order()` can fabricate placeholder identity such as `legacy:{token_id}` and `legacy-compat` and forward into submit. A live SDK-contacting path must assert `VenueSubmissionEnvelope.assert_live_submit_bound()` before any SDK call.

**Plan amendment:** promote adapter live-bound assertion and compatibility helper quarantine to `T1F`.

### F2 — Tier 1B And Tier 1D Are Coupled

Chain-reconciliation mutation counters and D6 projection/drop counters must land together or CI/alerts can show misleading partial telemetry.

**Plan amendment:** merge them into `T1BD`.

### F3 — SQLite Timeout Must Be Configurable

The older plan's hard-coded five-second timeout was not evidence-based and could regress current behavior. Local DB code already has at least one explicit timeout surface, while other connection paths may not share it.

**Plan amendment:** `T1E` uses `ZEUS_DB_BUSY_TIMEOUT_MS`, selected by operator in T0, applied consistently, with graceful read-only degradation and explicit counters.

### F4 — Harvester Also Touches Learning Authority

Settlement/redeem split is necessary but insufficient. Harvester learning/calibration writes can rebrand live decision `p_raw` as training data unless source/lineage authority is proven.

**Plan amendment:** `T1C` includes `HarvesterLearningAuthority` brake.

### F5 — Chain Reconciliation Has More Than One Mutation Branch

The guard must cover every assignment of `entry_price`, `cost_basis_usd`, `size_usd`, and `shares` from chain facts when `corrected_executable_economics_eligible=True`, not only one advisory line range.

**Plan amendment:** T1BD begins with grep/review of every assignment site and tests all guarded sites.

### F6 — D6 Field List Must Be Locked Before Coding

The prior plan says "four D6 fields" but executor packets need exact names.

**Plan amendment:** add `T0.6 — D6 field authority lock` before T1BD.

### F7 — Existing CI Uses `semantic_linter.py`

The repo has `scripts/semantic_linter.py` and an architecture advisory workflow. A new `semantic_static_gates.py` is acceptable only if the workflow invokes it; otherwise it becomes dead local tooling.

**Plan amendment:** T2D/T2F target the existing semantic-linter/CI path or add a verified workflow invocation.

### F8 — Final SDK Envelope Persistence Is Partially Superseded, Not Closed

Executor has a final submission-envelope persistence helper, but every live submit route including exit, reject, FOK/FAK response, open-order ACK, and compatibility route must be audited.

**Plan amendment:** add `T1G` verification-first.

### F9 — Executor Price Authority Remains A Tier 3 Blocker

Legacy executor, `ExecutionPrice.with_taker_fee()`, `BinEdge`, VWMP, posterior, and complement paths remain semantic laundering risks. Tier 0/Tier 1 must freeze routes that could reach them before Tier 3 repair.

**Plan amendment:** no corrected/live route may reach legacy price derivation before T3.

### F10 — Known-Gaps Coverage Must Be Explicit

The final backlog cannot silently drop active known gaps.

**Plan amendment:** replace the shorter Tier 4 table with the expanded active-surface matrix in this file.

### F11 — Orchestrator Execution Needs Packet Registration

Topology navigation currently rejects unregistered task files. A plan that does not register its packet and scope sidecar is not orchestrator-executable.

**Plan amendment:** add T-1.0 packet registration and scope freeze requirements.

### F12 — New Scripts/Tests Need Manifest Work

`scripts/AGENTS.md` and `tests/AGENTS.md` require `architecture/script_manifest.yaml`, `architecture/test_topology.yaml`, and lifecycle headers.

**Plan amendment:** every packet that adds scripts/tests includes required companion files and header/manifest gates.

### F13 — Alerting Must Name Delivery Path

`monitoring/alerts.yaml` is greenfield unless alert infrastructure exists. The plan must name the alert owner, delivery adapter, cooldown, fallback, and test path.

**Plan amendment:** T2E must either wire existing Discord/riskguard notification surfaces or create a named delivery adapter with fallback.

---

## 6. Revised Tier Architecture

| Tier | Name | Purpose | Status Before Next Tier |
| ---: | --- | --- | --- |
| T-1 | Artifact/evidence and packet lock | Capture local reality, register packet, freeze scope without mutating runtime state | Required |
| T0 | Operator manual quiescence and protocol locks | Stop daemons, prove venue/order quiescence, lock D6, harvester, SQLite, alert choices | Required |
| T1 | Active live-money breach fixes | Serialized commits that stop ongoing or latent damage | Required |
| T2 | Structural control plane | Gates, census, drift checker, alerting, DB isolation decision, manifest durability | Required |
| T3 | Full same-object semantic spine | Contracts, Kelly, executor, FDR, exit, fills, reports, source identity | Required for corrected live |
| T4 | Calibration/source/observability backlog | Deferred but tracked; not silently dropped | Deferred, not discarded |

---

## 7. T-1 — Artifact/Evidence And Packet Lock

### Objective

Before operator actions or code execution, capture local facts and make this packet routable for orchestrators.

### T-1.0 — Packet Registration And Scope

Required files before LOCK:

| Artifact | Required Content |
| --- | --- |
| `scope.yaml` | Packet ID, branch, worktree, in-scope plan files, companion registry files, forbidden runtime/source files for plan-only phase. |
| `PLAN.md` | Topology-admitted operation planning entrypoint that points to `MASTER_PLAN_v2.md` and `ORCHESTRATOR_RUNBOOK.md`. |
| `ORCHESTRATOR_RUNBOOK.md` | Skill-derived coordinator prompt, role split, idle boot, critic gate, verifier receipt, and co-tenant staging protocol. |
| `docs/operations/AGENTS.md` row | Registers this packet folder, routeable entrypoint, final plan artifact, runbook, and scope sidecar. |
| `docs/operations/known_gaps.md` | Compatibility pointer to `docs/to-do-list/known_gaps.md`, matching the current operations registry. |
| `docs/operations/current_state.md` row | Not required for lock-candidate visibility. Required only if the operator freezes this packet as active execution control. |
| `LOCK_DECISION.md` | Operator decision: `LOCK`, `LOCK_WITH_AMENDMENTS`, or `REVISE`. Must name the locked artifact. |
| `PLAN_LOCKED.md` | Copy or pointer to the locked artifact after operator approval. |

No executor may treat this plan as implementation authority until `LOCK_DECISION.md` exists.

### T-1.1 — Worktree And Co-Tenant Baseline

Every executor/reviewer packet begins by capturing:

```bash
pwd
git rev-parse --show-toplevel
git branch --show-current
git rev-parse --short HEAD
git worktree list
git status --short
```

Artifact: `T-1_GIT_STATUS.md`.

If unrelated dirty files exist, the packet must either:

1. record them as co-tenant-owned and avoid them, or
2. stop and ask the operator if ownership is unclear.

No packet may use `git add -A`, `git add .`, `git commit -am`, or broad staging.

### T-1.2 — Daemon State Snapshot

Artifact: `T-1_DAEMON_STATE.md`.

Required sanitized content:

- launchd labels present/absent;
- process command lines matching `src.main` or `riskguard`;
- `ZEUS_MODE` value if visible;
- PID if present, but no hard-coded PID assumptions in the plan;
- redacted secrets and credential paths.

### T-1.3 — Schema Scan

Artifact: `T-1_SCHEMA_SCAN.md`.

Commands:

```bash
git grep -n "CREATE TABLE.*settlement_commands"
git grep -n "SETTLEMENT_COMMAND_SCHEMA"
```

### T-1.4 — Compatibility Submit Scan

Artifact: `T-1_COMPAT_SUBMIT_SCAN.md`.

Commands:

```bash
git grep -n "submit_limit_order\|assert_live_submit_bound\|legacy-compat\|legacy:" src
```

### T-1.5 — Known-Gaps Coverage Snapshot

Artifact: `T-1_KNOWN_GAPS_COVERAGE.md`.

Required content:

- every active OPEN/PARTIALLY-FIXED/STALE-UNVERIFIED gap;
- assigned tier;
- blocking status for corrected shadow, corrected live, corrected P&L, promotion;
- `LOCAL_REPO_VERIFY_REQUIRED` where public/main evidence is insufficient.

### T-1.6 — Topology Admission Packet

Artifact: `T-1_TOPOLOGY_ROUTE.md`.

Each implementation packet must run topology navigation with typed intent and the exact files it intends to touch. If navigation is advisory-only or rejects files, executor must stop before editing.

### T-1 Gate

No T0 action and no Tier 1 implementation until all T-1 artifacts exist and the operator has accepted their contents.

---

## 8. T0 — Operator-Manual Quiescence And Protocol Lock

### Objective

Stop runtime side effects and lock local prerequisites that cannot be inferred from repo inspection.

### Required Steps

| Step | Action | Verification Artifact |
| --- | --- | --- |
| T0.1 | Operator unloads live trading daemon | `T0_DAEMON_UNLOADED.md`; no matching `src.main` live process; launchd label stopped. |
| T0.2 | Operator unloads RiskGuard daemon | Same artifact; no matching RiskGuard live process. |
| T0.3 | Operator verifies no in-flight Polymarket orders | `T0_VENUE_QUIESCENT.md`; screenshot or direct CLOB/on-chain output with secrets redacted. |
| T0.4 | Operator creates rebuild sentinel | `.zeus/rebuild_lock.do_not_run_during_live`; acknowledged in `T0_PROTOCOL_ACK.md`. |
| T0.5 | Operator chooses SQLite tactical timeout policy | `T0_SQLITE_POLICY.md`; exact `ZEUS_DB_BUSY_TIMEOUT_MS`, plus whether DB physical isolation is T2G or deferred T4 with live restrictions. |
| T0.6 | Operator locks exact D6 field list | `T0_D6_FIELD_LOCK.md`; exact field names and source evidence from code/DRIFT/known gaps. |
| T0.7 | Operator locks harvester live mode | `T0_HARVESTER_POLICY.md`; `ZEUS_HARVESTER_LIVE_ENABLED` state, learning-write policy, and whether live harvester remains disabled through T1C. |
| T0.8 | Operator chooses alert delivery owner | `T0_ALERT_POLICY.md`; Discord/riskguard/email/local-log fallback, cooldown, escalation owner. |
| T0.9 | Operator confirms no executor may call launchctl/venue tools | `T0_PROTOCOL_ACK.md`. |

### T0 Artifact Schemas

`T0_PROTOCOL_ACK.md` must include:

```text
Date:
Operator:
Repo path:
Branch/HEAD:
Live daemon unloaded: yes/no + evidence path
RiskGuard unloaded: yes/no + evidence path
Venue quiescent: yes/no + evidence path
Rebuild sentinel present: yes/no
SQLite busy timeout policy: <milliseconds> or <pull isolation forward>
D6 locked fields: <exact four fields>
Harvester live policy: <disabled/enabled-readonly/other>
Alert delivery policy: <adapter + fallback>
Executor launchctl permission: denied
Executor venue-action permission: denied
Decision: proceed_to_T1 | revise_plan | stop
```

### T0 Gate

Tier 1 executor agents must refuse to start if any T0 artifact is missing, malformed, or says `revise_plan` / `stop`.

---

## 9. Global Orchestrator Execution Protocol

This section applies to every packet.

### Coordinator Workflow

1. Coordinator reads `MASTER_PLAN_v2.md`, `scope.yaml`, `LOCK_DECISION.md`, T-1 artifacts, and T0 artifacts.
2. Coordinator generates one packet prompt from the packet skeleton below.
3. Executor implements only that packet.
4. Critic and code-reviewer run in separate contexts.
5. Verifier or test-engineer checks command evidence.
6. Coordinator stages explicit files only after inspecting `git diff --stat <files>` and the full diff for suspicious changes.
7. Operator approves before advancing to the next packet.

### Mandatory Packet Preamble

Every executor prompt must include:

```text
Do not opportunistically refactor.
Do not start if required prior-tier artifacts are missing.
Do not use docs as proof.
Do not mark historical rows corrected without executable snapshot/cost/fill evidence.
Do not touch live daemon control.
Do not enable corrected live by env var.
Do not delete uncertain live-reachable code; quarantine first.
Do not stage broad file globs.
Do not claim runtime quiescence unless T0 artifacts provide it.
Do not add scripts/tests without manifest/header updates.
```

### Packet Skeleton

| Field | Required |
| --- | --- |
| Role | Named packet role. |
| Scope | One packet only. |
| Required prior artifacts | T-1/T0/Tier predecessor artifacts. |
| Read first | Exact files and artifacts. |
| Allowed files | Explicit, no broad wildcards unless they are companion registries. |
| Forbidden files | Explicit. |
| Required companion updates | `architecture/script_manifest.yaml`, `architecture/test_topology.yaml`, `docs/operations/AGENTS.md`, etc. |
| Topology command | Exact command and expected admission result. |
| Invariants | Packet-specific. |
| Tasks | Concrete steps. |
| Tests | Fail-before tests and existing regression tests. |
| Commands | `pytest`, semantic linter, grep, census, topology/map checks. |
| Expected failures before fix | Named. |
| Closeout evidence | Artifact paths and command output. |
| Rollback | Git revert or config brake. |
| Not-now constraints | Explicitly restated. |

### Reviewer Limits

Critic/code-reviewer/verifier agents may approve code diffs, tests, and plan consistency. They may not approve:

- launchd state;
- venue open-order state;
- credential redaction;
- production DB truth;
- ownership of unrelated dirty worktree files;
- operator policy choices.

Those require explicit operator artifacts.

### Manifest And Header Rules

Whenever a packet adds or substantially touches:

- `scripts/*.py`: update `architecture/script_manifest.yaml`, include lifecycle/provenance header where required, declare class, lifecycle, read/write targets, dry-run/apply behavior, target DB, danger class, and reuse/disposal policy.
- `tests/test_*.py`: add lifecycle header, update `architecture/test_topology.yaml`, classify trust status, and cite packet authority basis.
- `docs/operations/**`: update `docs/operations/AGENTS.md` when new durable surfaces are introduced.
- `.github/workflows/**`: run planning-lock and topology gates before editing.

### Git Safety

Before every staged commit:

```bash
git diff --stat <explicit files>
git diff -- <explicit files> | head -200
```

If the diff is larger than the packet's mental model, stop. Do not commit contaminated changes.

---

## 10. T1 — Active Live-Money Breach Fixes

Tier 1 order is serialized:

```text
T1A -> T1F -> T1BD -> T1C -> T1E -> T1G -> T1H
```

T1 packets may be worked in separate worktrees only if their predecessors have landed and their T0/T-1 artifacts are copied into that worktree. They are not independent commits.

### T1A — Single Source Of Truth For `settlement_commands` DDL

| Field | Plan |
| --- | --- |
| Why first | Schema truth is required before coded census and harvester settlement fixes. |
| Current evidence | `settlement_commands.py` defines `SETTLEMENT_COMMAND_SCHEMA`; `db.py` also contains inline `CREATE TABLE IF NOT EXISTS settlement_commands`. |
| Allowed files | `src/execution/settlement_commands.py`, `src/state/db.py`, focused tests, required manifests for new tests. |
| Forbidden files | `src/execution/harvester.py`, `src/execution/executor.py`, `src/venue/**`, strategy/evaluator/reporting code, production DBs. |
| Change | `settlement_commands.py` remains the only DDL source. `db.py` imports schema/init helper at function scope if needed to avoid circular import. |
| Tests | `test_settlement_commands_single_source_of_truth`; existing DB init tests selected by topology. |
| Acceptance | Repo grep finds exactly one inline `CREATE TABLE settlement_commands` definition; `db.py` imports/uses canonical schema. |
| Rollback | Revert import/helper commit; no data migration. |

### T1F — Adapter Live-Bound Assertion And Compatibility Helper Quarantine

| Field | Plan |
| --- | --- |
| Why now | Compatibility helper can create placeholder envelope and call submit; submit path must assert live-bound semantics before SDK contact. |
| Allowed files | `src/venue/polymarket_v2_adapter.py`, `src/contracts/venue_submission_envelope.py` only if validation gaps exist, adapter/envelope tests, required test manifest. |
| Forbidden files | Strategy, Kelly/evaluator, reports, DB schema, daemon control. |
| Change | At the first live submit boundary, call `envelope.assert_live_submit_bound()` before any SDK create/post call. `submit_limit_order()` must reject live/corrected mode or be explicitly fake/test-only. |
| Tests | `test_adapter_submit_calls_assert_live_submit_bound_before_sdk_contact`; `test_compatibility_envelope_rejected_in_live_submit`; `test_placeholder_envelope_does_not_call_sdk`. |
| Acceptance | Placeholder `legacy:*`, `legacy-compat`, or YES/NO-collapsed envelope cannot reach SDK; fake SDK call count remains zero. |
| Rollback | Disable compatibility helper entirely. |

### T1BD — Chain-Reconciliation Four-Field Freeze And D6 Projection/Loader Counters

| Field | Plan |
| --- | --- |
| Why paired | Counter atomicity makes separate T1B/T1D commits unsafe. |
| Required prior artifact | `T0_D6_FIELD_LOCK.md` with exact D6 fields. |
| Allowed files | `src/state/chain_reconciliation.py`, `src/engine/lifecycle_events.py`, `src/state/portfolio.py`, telemetry/counter module, focused tests, required manifests. |
| Forbidden files | Executor, venue adapter, strategy/evaluator, reports, DB migrations unless T0 explicitly re-locks plan. |
| Change | If `position.corrected_executable_economics_eligible is True`, block all `entry_price`, `cost_basis_usd`, `size_usd`, and `shares` assignments from chain facts across every branch; increment `cost_basis_chain_mutation_blocked_total{field}`. Add projection-drop and loader-default counters for exact T0.6 fields. |
| Tests | `test_chain_reconciliation_no_corrected_mutation_all_sites`; `test_legacy_chain_reconciliation_unchanged`; `test_position_projection_field_dropped_exact_d6_fields`; `test_position_loader_field_defaulted_exact_d6_fields`. |
| Acceptance | Corrected positions never have entry economics overwritten by chain facts; legacy behavior unchanged; counters present together. |
| Rollback | Revert paired commit; corrected live remains frozen. |

### T1C — Harvester Settlement/Redeem/Learning Separation

| Field | Plan |
| --- | --- |
| Why now | Harvester settlement and redeem semantics are coupled, and learning writes can rebrand live `p_raw` as training data without authority. |
| Required prior artifact | `T0_HARVESTER_POLICY.md`. |
| Allowed files | `src/execution/harvester.py`, `src/execution/settlement_commands.py` only if status enum integration needs it, `src/contracts/settlement_status.py` or equivalent, focused tests, required manifests. |
| Forbidden files | Entry executor, venue adapter submit, strategy scoring, DB migrations unless plan re-locks. |
| Change | Split `record_settlement_result`, `enqueue_redeem_command`, and `maybe_write_learning_pair`. Settlement recorded is not redeem confirmed. Learning/calibration writes require explicit source/lineage authority; otherwise disabled or diagnostic-only. |
| Tests | `test_harvester_settlement_does_not_imply_redeem`; `test_harvester_redeem_separate_lifecycle`; `test_harvester_does_not_rebrand_live_praw_as_training_without_lineage`; existing harvester smoke tests selected by topology. |
| Acceptance | Synthetic settled market records settlement only; redeem only on explicit lifecycle transition; no learning write without lineage. |
| Rollback | Keep harvester live disabled; mark settlement rows `REVIEW_REQUIRED`. |

### T1E — SQLite Single-Writer Tactical Mitigation

| Field | Plan |
| --- | --- |
| Why now | Known gaps report live daemon crashes caused by SQLite writer contention. |
| Required prior artifact | `T0_SQLITE_POLICY.md`. |
| Allowed files | `src/state/db.py`, DB connection helpers, `scripts/rebuild_calibration_pairs_v2.py`, focused tests, required script/test manifests. |
| Forbidden files | Trading strategy, executor pricing, settlement semantics unless test fixture setup requires a narrow helper. |
| Change | Add `ZEUS_DB_BUSY_TIMEOUT_MS`; apply consistently to all DB connection constructors; on timeout, daemon degrades to read-only monitor for the cycle and increments `db_write_lock_timeout_total`. Rebuild refuses to run if sentinel exists and shards transactions by city/metric. |
| Tests | `test_all_db_connections_use_busy_timeout_config`; `test_db_write_timeout_does_not_crash_daemon`; `test_rebuild_refuses_during_live`; `test_rebuild_shards_transactions`. |
| Acceptance | No crash on synthetic long writer; explicit counter; no live rebuild while sentinel exists. |
| Rollback | Restore old timeout; keep daemons unloaded until DB isolation decision. |

### T1G — Final SDK Envelope Persistence Path Audit

| Field | Plan |
| --- | --- |
| Why now | Existing executor persistence helper partially supersedes a known gap, but every live submit route must be verified. |
| Allowed files | `src/execution/executor.py`, `src/state/venue_command_repo.py`, focused tests, required manifests. |
| Forbidden files | Strategy, Kelly/evaluator, reports, unrelated adapter refactors unless T1F identifies a missing persistence hook. |
| Change | Verification-first: trace entry submit, exit submit, compatibility submit, rejected submit, FOK/FAK filled response, and open-order ACK. Implement missing persistence only where uncovered. |
| Tests | `test_every_live_submit_persists_final_sdk_envelope`; `test_rejected_submit_persists_reject_payload`; `test_exit_submit_persists_final_envelope`. |
| Acceptance | Every venue-contacting live path appends final SDK-returned envelope/order payload or explicit rejection fact. |
| Rollback | Disable affected live path. |

### T1H — Minimal Coded State Census

| Field | Plan |
| --- | --- |
| Why now | Open positions are unknown; no automated exit or report can be trusted without census. |
| Allowed files | `scripts/state_census.py`, DB read helpers only if needed, focused tests, `architecture/script_manifest.yaml`, `architecture/test_topology.yaml`. |
| Forbidden files | Any write path to production DB, executor, venue adapter, strategy, reports. |
| Change | Read-only census with axes for position truth, redeem truth, command truth, fill truth, quote/exit truth, and identity truth. Include `data_unavailable` for empty settlement queue. |
| Tests | `test_census_read_only`; `test_census_data_unavailable_on_empty_settlement_queue`; `test_census_detects_placeholder_identity`; `test_census_detects_corrected_row_without_fill_authority`. |
| Acceptance | Census classifies all open positions as legacy/corrected-shadow/corrected-submit-unknown/corrected-fill/chain-only/review-required and reports identity anomalies. |
| Rollback | Remove script; no state mutation. |

---

## 11. T2 — Structural Control Plane

### T2A — Corrected-Live Brake, Not Enable Switch

Design:

- `ZEUS_CORRECTED_EXECUTABLE_DISABLE=true` is a kill switch only.
- It cannot enable corrected live.
- Per-position/per-intent evidence gates set eligibility.
- Tests must prove env false does not enable ineligible positions.

Required tests:

- `test_disable_env_overrides_eligible_position`
- `test_env_false_does_not_enable_ineligible_position`
- `test_default_state_is_disabled`

### T2B — Full Multi-Axis Census Matrix

Expand T1H into:

| Axis | Values |
| --- | --- |
| Position truth | `legacy_open`, `corrected_shadow`, `corrected_submit_unknown`, `corrected_fill_authoritative`, `chain_only`, `review_required` |
| Redeem truth | `data_unavailable`, `no_redeem_queued`, `redeem_queued`, `redeem_confirmed`, `redeem_review_required` |
| Fill truth | `none`, `submitted_only`, `partial`, `full`, `cancelled_remainder`, `unknown_side_effect` |
| Exit truth | `no_exit`, `legacy_exit`, `held_token_quote_ready`, `exit_submitted`, `exit_unknown`, `settlement_only` |
| Identity truth | `complete`, `placeholder`, `condition_token_collapse`, `missing_question`, `missing_yes_no_pair` |

### T2C — Symbol-Anchored Drift Checker And Invariants Ledger

Files:

- `scripts/packet_drift_check.py`
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/INVARIANTS_LEDGER.md`
- script/test manifests as companions.

Validation order:

1. Symbol anchor required.
2. Sentinel comment required.
3. Line number is warning-only.

Tests:

- symbol shifted +200 lines still passes with warning;
- symbol removed fails.

### T2D — Static Gates Through Existing CI Path

Acceptable implementations:

1. Extend `scripts/semantic_linter.py` with Zeus live-money semantic gates; or
2. Create `scripts/semantic_static_gates.py` and modify `.github/workflows/architecture_advisory_gates.yml` so the new script runs blocking in CI.

Required gates:

- placeholder envelope cannot reach live submit;
- corrected executor cannot call `compute_native_limit_price`;
- corrected Kelly cannot consume `p_market`, `vwmp`, `entry_price`, or `BinEdge`;
- buy-NO live cannot use complement as executable price;
- reports cannot aggregate mixed cohorts;
- chain reconciliation cannot mutate corrected entry economics;
- docs cannot contain “guaranteed fill” without legacy/diagnostic qualifier;
- Tier 1 counters cannot be partially installed.

### T2E — Alert Delivery, Not Just Counter Definitions

T0 chooses alert policy. T2 implements it.

If existing Discord/riskguard alert infrastructure is used, wire into that. If not, create a named adapter and fallback.

Alert contract must specify:

- delivery path;
- severity;
- cooldown;
- retry/failure behavior;
- local-log fallback;
- test fixture or dry-run command.

Counters requiring alerts:

| Counter | Severity |
| --- | --- |
| `cost_basis_chain_mutation_blocked_total` nonzero | HIGH |
| `position_projection_field_dropped_total` nonzero | HIGH |
| `position_loader_field_defaulted_total` nonzero | MEDIUM |
| `db_write_lock_timeout_total` nonzero | HIGH |
| `compat_submit_rejected_total` nonzero | HIGH |
| `placeholder_envelope_blocked_total` nonzero | HIGH |
| `harvester_learning_write_blocked_total` nonzero | HIGH |
| `settlement_recorded_without_redeem_total` nonzero | MEDIUM |

### T2F — Sentinel Durability And Ledger Consistency

Every `# TIER-*` sentinel comment must have a row in `INVARIANTS_LEDGER.md`; every ledger row must point to a live sentinel or be marked retired with rationale. Deleting either side requires review.

### T2G — DB Physical Isolation Decision

| Operator Choice | Consequence |
| --- | --- |
| Tactical only | T1E remains sufficient for T3 implementation, but corrected live cannot be enabled until DB timeout telemetry is stable during shadow. |
| Pull isolation forward | Add `T2G-DBISO` before T3: separate live trading DB writes from calibration rebuild DB writes; update runbook, config, tests, and migration/backfill policy. |

### T2H — Live-Control Side-Effect SLA

Required gates:

- RED side effect must be direct venue command, not only proxy intent.
- ORANGE favorable exit must be constrained by operator policy.
- DATA_DEGRADED hold must not claim safe execution.
- Sweep commands must persist command facts and final SDK payloads.

---

## 12. T3 — Full Same-Object Semantic Spine

T3 is the original dossier roadmap, re-prioritized after T0-T2.

### T3.1 — Complete Semantic Tests And CI Gates

Add semantic counterfactual tests:

- raw quote is not Kelly cost;
- fee-adjusted implied probability cannot become executable cost;
- executor no repricing;
- buy-NO native NO quote required;
- FDR materialization drift rejected/amended;
- compatibility helper non-live;
- held-token SELL bid exit;
- partial fill/cancel/unknown fill;
- command same-object proof;
- mixed report cohorts hard-fail;
- settlement vs redeem status split.

### T3.2 — Contract Package

Implement or complete:

- `MarketIdentity`
- `MarketPriorDistribution`
- `ExecutableEntryCostBasis`
- `ExecutableTradeHypothesis`
- `FinalExecutionIntent`
- `ExitExecutableQuote`
- `OrderPolicy`
- `VenueSubmissionEnvelope`
- `PositionLot`
- `EntryEconomicsAuthority`
- `FillAuthority`
- `PricingSemanticsVersion`
- `ReportingCohort`
- `CityIdentity`
- `TimeIdentity`
- `SettlementStatus`
- `RedeemLifecycle`

### T3.3 — Kelly Executable Cost Basis Only

Corrected Kelly consumes `ExecutableEntryCostBasis` only. Legacy raw `entry_price` helper is diagnostic-only. No `BinEdge.entry_price`, `p_market`, `vwmp`, posterior scalar, or fee-adjusted implied probability can feed corrected Kelly.

### T3.4 — Immutable Final Intent; Executor No Repricing

Corrected executor accepts only `FinalExecutionIntent` and validates it. It cannot derive price, amend selected token/side/limit, or silently reprice stale snapshots.

### T3.5 — Native NO Quote; Complement Quarantine

Buy-NO executable entry requires native NO token best ask/depth/hash. Complement is allowed only as diagnostic belief/payoff math.

### T3.6 — FDR/Executable Hypothesis Binding

Statistical FDR hypothesis ID is separate from executable hypothesis ID. Executable hypothesis binds token, condition, question, snapshot hash, cost basis hash, order policy, and venue.

### T3.7 — OrderPolicy Normalization

| Zeus Policy | Polymarket Behavior |
| --- | --- |
| `POST_ONLY_PASSIVE_LIMIT` | GTC/GTD + post-only + reject if crossing |
| `MAY_REST_LIMIT_CONSERVATIVE` | GTC/GTD + non-post-only + may rest or take |
| `IMMEDIATE_LIMIT_SWEEP_DEPTH_BOUND` | FOK/FAK + non-post-only + immediate bounded sweep |

Post-only cannot be conflated with FOK/FAK, and “jump to ask for guaranteed fill” must be removed or qualified as stale/legacy.

### T3.8 — Same-Object Command Journal

Hash chain:

```text
selection hypothesis
-> executable hypothesis
-> executable snapshot/hash
-> cost_basis/hash
-> final_intent/hash
-> venue_submission_envelope/hash
-> command id
-> SDK order payload / venue order id
-> fill facts
-> position lot
-> exit quote / settlement status
-> report row
```

Any mismatch means `REVIEW_REQUIRED`.

### T3.9 — Held-Token SELL Quote Exit

Corrected exit value is held-token SELL best bid with depth/freshness/hash, not `current_market_price`, VWMP, midpoint, posterior, or entry price.

### T3.10 — PositionLot / FillAuthority / Partial Fills

Corrected P&L requires confirmed fill facts. Target notional and submitted shares are not fill.

Required states:

- partial fill;
- cancel remainder;
- unknown side effect;
- fill-derived cost basis;
- exit partial fill exposure reduction.

### T3.11 — DB Migration And Semantic Cohorts

Additive fields only. No fake backfill.

Cohorts:

- `legacy_price_probability_conflated`
- `model_only_diagnostic`
- `corrected_executable_shadow`
- `corrected_submit_unknown_fill`
- `corrected_executable_live_partial`
- `corrected_executable_live_full`
- `manual_emergency_exit_excluded`
- `chain_only_quarantined`
- `review_required`

### T3.12 — Reporting/Backtest/Promotion Gates

Rules:

- mixed cohorts hard-fail;
- diagnostic replay is not promotion evidence;
- model skill is not executable economics;
- no corrected historical economics without depth/snapshot/fill;
- no warning-only mixed report.

### T3.13 — Settlement/Source/City/Time Identity

Required contracts:

- `SettlementStatus`
- `RedeemLifecycle`
- `CityIdentity`
- `TimeIdentity`
- `MetricIdentity`
- `ObservationAuthority`
- `SettlementSourceAuthority`

### T3.14 — Performance/Staleness Telemetry

Required:

- snapshot age;
- submit deadline;
- monitor quote age;
- stale quote rejection;
- DB lock timeout counters;
- live-control side-effect counters.

### T3.15 — Orphan Cleanup And `BinEdge` Quarantine

`BinEdge` must shrink to selection evidence, with executable economics moved to contracts. Compatibility helpers, complement fallbacks, legacy price compute in corrected paths, diagnostic replay complement, stale report paths, and docs-only claims are cleanup only after tests prove quarantine.

### T3.16 — Docs/AGENTS Rewrite

Rewrite stale docs after runtime gates pass. Do not use docs rewrite to claim behavior before code/test gates exist.

### T3.17 — Final Promotion Runbook

Required before corrected live/promotion:

- full pytest;
- semantic linter/static gates;
- state census;
- migration dry-run;
- report cohort dry-run;
- shadow soak;
- live canary;
- rollback protocol;
- operator signoff.

---

## 13. T4 — Expanded Backlog Coverage Matrix

| Item | Source Status | Tier Decision |
| --- | --- | --- |
| DB physical isolation | CRITICAL SQLite lock | T2G if pulled forward; otherwise T4 but live requires stable telemetry. |
| Executable snapshot producer/refresher symmetry | OPEN P1 | T3.2/T3.9/T3.14. |
| V2 compatibility envelope | OPEN P1 | Promoted to T1F. |
| RED force-exit direct venue side-effect SLA | OPEN/PARTIAL | T2H + T3.8/T3.9. |
| ORANGE favorable exit policy | OPEN/PARTIAL | T2H + operator policy artifact. |
| DATA_DEGRADED hold semantics | OPEN/PARTIAL | T2H; no safe-execution claim. |
| Exit partial fills exposure reduction | OPEN P1 | T3.10. |
| Settled pending-exit exposure skip | OPEN P1 | T3.10/T3.13. |
| Harvester HIGH-only-for-LOW / metric/source/station | OPEN older slice / local overlay conflict | T1C/T3.13, `LOCAL_REPO_VERIFY_REQUIRED`. |
| Harvester source rebrand to training data | OPEN P2 | T1C. |
| Calibration maturity edge threshold live path | OPEN P2 | T4 unless operator ties to no-trade gate before live. |
| Stale collateral snapshots | OPEN/POSSIBLY FIXED | T2/T3.14 verification gate. |
| ENS local-day NaNs | OPEN P2 | T3.13/T4; promotion blocker if settlement identity affected. |
| Day0 stale/epoch observations | OPEN P2 | T3.13/T3.14. |
| Final SDK envelope persistence | OPEN older / partially superseded | T1G verification. |
| DST historical aggregate rebuild | OPEN | T3.13/T4; promotion blocker for affected historical claims. |
| Open-Meteo quota contention | STALE-UNVERIFIED | T4 unless live feed currently affected. |
| HK HKO floor rounding / PM resolution | OPEN source mismatch | T3.13/T4; settlement authority. |
| HK 03-13/03-14 source mismatch | OPEN | T3.13/T4. |
| WU API hourly max vs website daily summary | OPEN | T3.13/T4. |
| Taipei per-date source routing | OPEN | T3.13/T4. |
| D3 entry price typed through execution economics | OPEN residual | T3.3/T3.4. |
| D4 entry-exit epistemic asymmetry | OPEN structural | T3.9. |
| s3 climate_zone | OPEN | T4. |
| s4 calibration weighting antibody tests | OPEN | T4. |
| s6 cluster-alpha PoC | BLOCKED by s3 | T4. |
| s7/s8 vectorize MC and rebuild | OPEN | T4. |
| Two-System Independence Phase 4 | deferred | T4. |
| backfill script unification | OPEN | T4. |
| `hourly_observations` deletion | cleanup | T4 cleanup only. |
| ACP/router fallback pre-dispatch gate | workspace-level | T4/OpenClaw surface, not Zeus corrected-live blocker. |
| Cron/time-of-day interactions with live jobs | reviewer blind spot | T-1/T2 census; add to known gaps if active jobs can mutate trading state. |
| Discord/operator command path side effects | reviewer blind spot | T-1/T2 audit; no corrected-live enable through chat commands. |

---

## 14. Paste-Ready Orchestrator Packets For Revised T1

### T1A Prompt — Settlement Command DDL Single Source

```text
Role: Zeus schema-single-source agent.

Scope: Resolve dual settlement_commands DDL. No trading logic changes.

Required prior artifacts:
- LOCK_DECISION.md naming MASTER_PLAN_v2.md or PLAN_LOCKED.md.
- T-1_GIT_STATUS.md
- T-1_SCHEMA_SCAN.md
- T0_PROTOCOL_ACK.md with proceed_to_T1.

Read first:
- AGENTS.md
- src/execution/AGENTS.md
- src/state/AGENTS.md
- T-1_SCHEMA_SCAN.md
- src/execution/settlement_commands.py
- src/state/db.py
- tests around DB initialization

Allowed files:
- src/execution/settlement_commands.py
- src/state/db.py
- tests/test_settlement_commands_schema.py
- architecture/test_topology.yaml if a new test file is added

Forbidden files:
- src/execution/harvester.py
- src/execution/executor.py
- src/venue/**
- strategy/evaluator/reporting code
- migrations that alter existing production rows

Required companion updates:
- tests/test_*.py lifecycle header if new/touched.
- architecture/test_topology.yaml registration if new/touched.

Invariants:
- Exactly one inline CREATE TABLE settlement_commands definition in repo.
- db.py may import/init the schema but must not duplicate it.
- Behavior unchanged for current schema.

Tasks:
1. Run topology navigation and stop if not admitted.
2. Make src/execution/settlement_commands.py the sole DDL source.
3. Replace db.py inline DDL with import/init helper.
4. Avoid circular import by function-scope import if needed.
5. Add grep-based relationship test.

Tests:
- test_settlement_commands_single_source_of_truth
- existing DB init tests selected by topology

Commands:
- pytest -q tests/test_settlement_commands_schema.py
- git grep -n "CREATE TABLE IF NOT EXISTS settlement_commands"

Expected failure before fix:
- Grep finds inline DDL in both settlement_commands.py and db.py.

Closeout evidence:
- Test output.
- Grep output showing one DDL source.
- git diff --stat for explicit files.

Rollback:
- Revert this commit only.

Do not opportunistically refactor.
```

### T1F Prompt — Adapter Live-Bound Assertion And Compatibility Quarantine

```text
Role: Zeus Polymarket adapter live-bound safety agent.

Scope: Prevent placeholder compatibility envelopes from reaching live SDK submit.

Required prior artifacts:
- T1A closeout evidence.
- T-1_COMPAT_SUBMIT_SCAN.md
- T0_PROTOCOL_ACK.md with proceed_to_T1.

Read first:
- AGENTS.md
- src/venue/AGENTS.md
- src/contracts/AGENTS.md
- T-1_COMPAT_SUBMIT_SCAN.md
- src/venue/polymarket_v2_adapter.py
- src/contracts/venue_submission_envelope.py
- src/execution/executor.py only to identify caller expectations
- MASTER_PLAN_v2.md venue constraints

Allowed files:
- src/venue/polymarket_v2_adapter.py
- src/contracts/venue_submission_envelope.py if validation gaps exist
- tests/test_venue_envelope_live_bound.py
- tests/test_polymarket_adapter_submit_safety.py
- architecture/test_topology.yaml if new/touched tests

Forbidden files:
- Strategy logic
- Kelly/evaluator
- Reports
- DB schema
- daemon/launchctl controls

Invariants:
- condition_id must not be legacy:* for live.
- question_id must not be legacy-compat for live.
- YES and NO token IDs must not collapse for live.
- assert_live_submit_bound must run before any SDK create/post call.
- Compatibility helper may be test/fake only, not live/corrected.

Tasks:
1. Run topology navigation and stop if not admitted.
2. Insert envelope.assert_live_submit_bound() at the first live submit boundary.
3. Make submit_limit_order reject in live/corrected contexts or force fake/test mode.
4. Add fake SDK tests proving no SDK call on placeholder envelope.
5. Add counter placeholder_envelope_blocked_total or compat_submit_rejected_total.

Tests:
- test_adapter_submit_calls_assert_live_submit_bound_before_sdk_contact
- test_compatibility_envelope_rejected_in_live_submit
- test_placeholder_envelope_does_not_call_sdk

Commands:
- pytest -q tests/test_venue_envelope_live_bound.py tests/test_polymarket_adapter_submit_safety.py

Expected failure before fix:
- Placeholder compatibility envelope can call submit path.

Closeout evidence:
- Fake SDK call count remains zero.
- Counter asserted.
- git diff --stat for explicit files.

Rollback:
- Disable submit_limit_order helper.

Do not opportunistically refactor.
```

### T1BD Prompt — Chain Freeze And D6 Projection Counters

```text
Role: Zeus corrected-economics mutation firewall agent.

Scope: Stop chain reconciliation from mutating corrected entry economics and instrument D6 projection/loader drops.

Required prior artifacts:
- T1F closeout evidence.
- T0_D6_FIELD_LOCK.md
- T0_PROTOCOL_ACK.md with proceed_to_T1.

Read first:
- AGENTS.md
- src/state/AGENTS.md
- src/engine/AGENTS.md
- T0_D6_FIELD_LOCK.md
- src/state/chain_reconciliation.py
- src/engine/lifecycle_events.py
- src/state/portfolio.py

Allowed files:
- src/state/chain_reconciliation.py
- src/engine/lifecycle_events.py
- src/state/portfolio.py
- telemetry/counter module identified by topology
- tests/test_chain_reconciliation_corrected_guard.py
- tests/test_position_projection_d6_counters.py
- architecture/test_topology.yaml if new/touched tests

Forbidden files:
- Executor
- Venue adapter
- Strategy/evaluator
- Report promotion code
- DB migrations

Invariants:
- corrected_executable_economics_eligible positions cannot have entry_price, cost_basis_usd, size_usd, or shares overwritten by chain reconciliation.
- Legacy positions preserve current behavior.
- D6 exact fields come from T0_D6_FIELD_LOCK.md.
- Tier 1B and Tier 1D counters land together.

Tasks:
1. Run topology navigation and stop if not admitted.
2. Grep all assignments to entry_price/cost_basis_usd/size_usd/shares in chain_reconciliation.py.
3. Guard every assignment for corrected-eligible positions.
4. Increment cost_basis_chain_mutation_blocked_total{field}.
5. Add projection-drop counters for exact D6 fields.
6. Add loader-default counters if _position_from_projection_row defaults any D6 field.

Tests:
- test_chain_reconciliation_no_corrected_mutation_all_sites
- test_legacy_chain_reconciliation_unchanged
- test_position_projection_field_dropped_exact_d6_fields
- test_position_loader_field_defaulted_exact_d6_fields

Commands:
- pytest -q tests/test_chain_reconciliation_corrected_guard.py tests/test_position_projection_d6_counters.py
- git grep -n "entry_price\|cost_basis_usd\|size_usd\|shares" src/state/chain_reconciliation.py

Expected failure before fix:
- Corrected positions can be overwritten by chain facts.

Closeout evidence:
- Tests pass.
- Grep list reviewed in closeout.
- git diff --stat for explicit files.

Rollback:
- Revert paired commit.

Do not opportunistically refactor.
```

### T1C Prompt — Harvester Settlement/Redeem/Learning Separation

```text
Role: Zeus harvester lifecycle separation agent.

Scope: Separate settlement, redeem, and learning side effects.

Required prior artifacts:
- T1BD closeout evidence.
- T0_HARVESTER_POLICY.md

Read first:
- AGENTS.md
- src/execution/AGENTS.md
- T0_HARVESTER_POLICY.md
- src/execution/harvester.py
- src/execution/settlement_commands.py
- known_gaps harvester source/redeem sections
- settlement/redeem contract tests

Allowed files:
- src/execution/harvester.py
- src/execution/settlement_commands.py only if status enum integration needed
- src/contracts/settlement_status.py or equivalent
- tests/test_harvester_settlement_redeem.py
- tests/test_harvester_learning_authority.py
- architecture/test_topology.yaml if new/touched tests

Forbidden files:
- Entry executor
- Venue adapter submit
- Strategy scoring
- DB migrations unless plan re-locks

Invariants:
- Settlement recorded is not redeem confirmed.
- Redeem command issuance is a separate lifecycle transition.
- Learning/calibration write requires explicit source/lineage authority.
- Harvester live disabled remains respected.

Tasks:
1. Run topology navigation and stop if not admitted.
2. Extract settlement-record function.
3. Extract redeem-enqueue function.
4. Extract learning-write function with authority guard.
5. Add SettlementStatus enum if absent.
6. Block live p_raw rebranding as training data without lineage.

Tests:
- test_harvester_settlement_does_not_imply_redeem
- test_harvester_redeem_separate_lifecycle
- test_harvester_does_not_rebrand_live_praw_as_training_without_lineage
- existing harvester smoke tests

Commands:
- pytest -q tests/test_harvester_settlement_redeem.py tests/test_harvester_learning_authority.py

Expected failure before fix:
- Settlement/redeem/learning side effects are coupled.

Closeout evidence:
- Tests pass.
- Closeout states whether harvester live remains disabled.

Rollback:
- Disable harvester live.

Do not opportunistically refactor.
```

### T1E Prompt — SQLite Tactical Mitigation

```text
Role: Zeus SQLite single-writer crash mitigation agent.

Scope: Tactical timeout/degrade/sharding; not full DB physical isolation.

Required prior artifacts:
- T1C closeout evidence.
- T0_SQLITE_POLICY.md

Read first:
- AGENTS.md
- src/state/AGENTS.md
- scripts/AGENTS.md
- T0_SQLITE_POLICY.md
- src/state/db.py
- scripts/rebuild_calibration_pairs_v2.py
- known_gaps SQLite section

Allowed files:
- src/state/db.py
- DB connection helper modules identified by topology
- scripts/rebuild_calibration_pairs_v2.py
- tests/test_sqlite_busy_timeout.py
- tests/test_rebuild_live_sentinel.py
- architecture/script_manifest.yaml
- architecture/test_topology.yaml

Forbidden files:
- Trading strategy
- Executor pricing
- Settlement logic unless tests need fixture setup

Invariants:
- Long writer must not crash live daemon.
- Timeout value comes from ZEUS_DB_BUSY_TIMEOUT_MS and T0_SQLITE_POLICY.md.
- All DB connection surfaces must use the same busy timeout policy.
- Rebuild refuses during live sentinel.
- Rebuild is sharded into bounded transactions.

Tasks:
1. Run topology navigation and stop if not admitted.
2. Add config for ZEUS_DB_BUSY_TIMEOUT_MS.
3. Apply to all sqlite3.connect surfaces in live/db helpers.
4. Catch OperationalError database locked and degrade cycle to read-only monitor.
5. Add db_write_lock_timeout_total counter.
6. Add rebuild sentinel refusal.
7. Split rebuild transactions by city/metric.

Tests:
- test_all_db_connections_use_busy_timeout_config
- test_db_write_timeout_does_not_crash_daemon
- test_rebuild_refuses_during_live
- test_rebuild_shards_transactions

Commands:
- pytest -q tests/test_sqlite_busy_timeout.py tests/test_rebuild_live_sentinel.py
- git grep -n "sqlite3.connect"

Expected failure before fix:
- Some connections lack timeout; rebuild can hold full-run savepoint.

Closeout evidence:
- Tests pass.
- Grep reviewed.
- Timeout policy value cited from T0_SQLITE_POLICY.md.

Rollback:
- Revert commit; keep daemons unloaded.

Do not opportunistically refactor.
```

### T1G Prompt — Final SDK Envelope Path Audit

```text
Role: Zeus venue-provenance path auditor.

Scope: Verify all live venue submit paths persist final SDK-returned envelope/payload.

Required prior artifacts:
- T1E closeout evidence.

Read first:
- AGENTS.md
- src/execution/AGENTS.md
- src/venue/AGENTS.md
- src/execution/executor.py
- src/venue/polymarket_v2_adapter.py
- src/state/venue_command_repo.py
- src/state/db.py
- tests/test_executor_command_split.py

Allowed files:
- src/execution/executor.py
- src/state/venue_command_repo.py
- tests/test_final_sdk_envelope_persistence.py
- architecture/test_topology.yaml if new/touched tests

Forbidden files:
- Strategy
- Kelly/evaluator
- Reports

Invariants:
- Intent/envelope before submit is not enough.
- SDK-returned order payload, status, orderID, and rejection must be persisted.
- Exit submit paths are included.

Tasks:
1. Run topology navigation and stop if not admitted.
2. Trace every live SDK create/post call.
3. Assert each path calls final envelope persistence.
4. Add tests for entry, exit, rejection, FOK/FAK filled response, open order ACK.
5. Implement only missing path persistence.

Tests:
- test_every_live_submit_persists_final_sdk_envelope
- test_rejected_submit_persists_reject_payload
- test_exit_submit_persists_final_envelope

Commands:
- pytest -q tests/test_final_sdk_envelope_persistence.py
- git grep -n "create_and_post_order\|post_order\|_persist_final_submission_envelope_payload"

Expected failure before fix:
- At least one path may not persist final payload.

Closeout evidence:
- Path map in test or artifact.
- Tests pass.

Rollback:
- Disable uncovered live path.

Do not opportunistically refactor.
```

### T1H Prompt — Minimal Read-Only State Census

```text
Role: Zeus state-census read-only agent.

Scope: Build minimal read-only census. No writes, no live side effects.

Required prior artifacts:
- T1G closeout evidence.
- T0_PROTOCOL_ACK.md

Read first:
- AGENTS.md
- scripts/AGENTS.md
- src/state/AGENTS.md
- src/execution/settlement_commands.py
- src/state/portfolio.py
- src/state/db.py
- T-1_KNOWN_GAPS_COVERAGE.md

Allowed files:
- scripts/state_census.py
- tests/test_state_census.py
- architecture/script_manifest.yaml
- architecture/test_topology.yaml

Forbidden files:
- DB write paths
- executor
- venue adapter
- strategy/evaluator
- reports

Invariants:
- Census is read-only.
- Empty settlement command queue means data_unavailable, not no_redeem_queued.
- Placeholder identity is an anomaly.
- Corrected rows without fill authority are review_required.

Tasks:
1. Run topology navigation and stop if not admitted.
2. Implement read-only census.
3. Register script and tests.
4. Add fixture-backed tests.

Tests:
- test_census_read_only
- test_census_data_unavailable_on_empty_settlement_queue
- test_census_detects_placeholder_identity
- test_census_detects_corrected_row_without_fill_authority

Commands:
- pytest -q tests/test_state_census.py
- python scripts/state_census.py --read-only --json-out /tmp/zeus_state_census.json

Expected failure before fix:
- scripts/state_census.py missing.

Closeout evidence:
- Test output.
- Census JSON schema sample.

Rollback:
- Remove script and tests; no state mutation.

Do not opportunistically refactor.
```

---

## 15. Acceptance Gates For Live Polymarket Trading

### Corrected Shadow Allowed

Allowed only after:

- T0 artifacts complete;
- T1F adapter placeholder submit blocked;
- T1E no live DB crash path;
- shadow cannot contact venue;
- output labeled `corrected_executable_shadow`;
- no P&L or promotion evidence.

### Corrected Live Entry Allowed

Allowed only after:

1. T0-T2 complete.
2. T3.1-T3.8 complete.
3. Native token/condition/question identity proven.
4. Buy-NO native NO quote required.
5. Kelly consumes executable cost basis only.
6. Final intent immutable.
7. Executor does not reprice.
8. Adapter live-bound assertion runs before SDK.
9. Final SDK envelope persisted.
10. Command hash chain proves same object.
11. DB lock telemetry stable during shadow.
12. State census shows no unresolved open-position blocker.

### Automated Economic Exit Allowed

Allowed only after:

- T3.9 and T3.10 complete;
- held-token SELL best bid/depth/hash/freshness exists;
- partial fills and cancel remainders represented;
- manual/emergency exits excluded unless evidence criteria pass.

### Corrected P&L Allowed

Allowed only after:

- semantic cohort gate;
- entry cost basis hash;
- final intent/envelope/command hash chain;
- fill authority;
- exit quote or settlement/redeem status;
- no unknown fill/submit/redeem state.

### Strategy Promotion Allowed

Allowed only after:

- corrected live cohort only;
- no diagnostic/backtest skill as economics;
- no mixed cohort;
- no fake historical backfill;
- no active `REVIEW_REQUIRED` rows in promoted cohort;
- settlement/source/time identity certified for the included markets.

---

## 16. What Must Not Be Done

Do not:

- run Tier 1 while daemons are active;
- let an agent call `launchctl` on behalf of operator;
- let an agent place/cancel/probe live venue orders on behalf of operator;
- enable corrected live with an env var;
- use `ZEUS_CORRECTED_EXECUTABLE_DISABLE=false` as proof of eligibility;
- treat a placeholder `legacy:*` envelope as live evidence;
- use complement as executable NO cost;
- let chain reconciliation overwrite corrected entry economics;
- treat settlement recorded as redeem confirmed;
- let harvester write calibration pairs without lineage/source authority;
- report corrected P&L from submitted notional or unknown fill;
- aggregate mixed cohorts;
- use docs as runtime proof;
- delete uncertain live-reachable code without quarantine;
- move DB physical isolation to Tier 4 if T1E telemetry shows ongoing contention;
- treat backtest ROI or model skill as live promotion evidence;
- add new scripts/tests without manifest and lifecycle-header compliance;
- stage broad worktree changes or absorb co-tenant dirty files.

---

## 17. Final Self-Check

### Did this plan preserve Round-5 content rather than shrink it?

Yes. It keeps the Round-5 tier structure, T1A/T1F/T1BD/T1C/T1E/T1G/T1H sequence, T2 controls, T3 semantic spine, T4 matrix, acceptance gates, and not-now constraints. It adds packet registration, scope, topology, manifest, alert, dirty-worktree, reviewer-limit, and artifact-schema requirements.

### Is this orchestrator-executable?

It is executable only after `LOCK_DECISION.md`, T-1, and T0 artifacts exist. The plan now says this explicitly and gives packet prompts with exact scope, allowed/forbidden files, manifests, tests, closeout, and rollback.

### Final decision

Replace the older MASTER_PLAN as live planning authority with `MASTER_PLAN_v2.md` after operator writes `LOCK_DECISION.md`. Then execute:

```text
T-1 -> T0 -> T1A -> T1F -> T1BD -> T1C -> T1E -> T1G -> T1H -> T2 -> T3 -> T4
```

Do not advance to T3 corrected-live semantics until T2 gates are locked and local state census is clean.
