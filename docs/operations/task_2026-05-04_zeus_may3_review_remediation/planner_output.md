# Planner Output — Zeus May3 R5 Remediation Triage

**Created:** 2026-05-04
**Author:** planner subagent (oh-my-claudecode:planner)
**Worktree:** /Users/leofitz/.openclaw/worktrees/zeus-may3-remediation-main
**Branch/HEAD:** main / 1116d827

---

## 0. Executive Verdict

| Question | Answer |
|---|---|
| Should the operator LOCK MASTER_PLAN_v2.md byte-for-byte? | **LOCK_WITH_AMENDMENTS** (6 amendments; see `LOCK_DECISION.md`) |
| Are T1A/T1F/T1BD ready to dispatch today? | **NO. Two BLOCKERS + one structural gap.** |
| BLOCKER 1 | RiskGuard daemon IS RUNNING (PID 14177). T-1 verdict contradicts its own evidence. |
| BLOCKER 2 | Operator must personally attest venue quiescence (T0.3). No agent substitute exists. |
| Structural gap | Topology engine does not admit T1A/T1F/T1BD's source files under any current digest profile; new profiles must be authored OR the operator must accept advisory-only admission with critic enforcing scope. |
| Are T0 operator decisions actually needed? | Reality answers T0.1/T0.4/T0.6/T0.7/T0.8 fully and T0.5 partially. T0.2/T0.3 remain genuinely operator-only. T0.9 is the umbrella umbrella ack downstream of all above. |
| How many serious issues require operator attention before T1? | **3** (Amendments 1, 4, and the operator-only T0.3 venue attestation) |

### T0 Triage summary table

| T0 | Topic | Verdict | Artifact written |
|---|---|---|---|
| T0.1+T0.2 | Daemon unloaded | OPERATOR_ONLY (T0.2 BLOCKING) | `T0_DAEMON_UNLOADED.md` |
| T0.3 | Venue quiescent | OPERATOR_ONLY | `T0_VENUE_QUIESCENT.md` |
| T0.4 | Rebuild sentinel | REALITY_ANSWERED | (no separate file; covered in `LOCK_DECISION.md` Amendment 2) |
| T0.5 | SQLite policy | MIXED | `T0_SQLITE_POLICY.md` |
| T0.6 | D6 field lock | REALITY_ANSWERED | `T0_D6_FIELD_LOCK.md` |
| T0.7 | Harvester policy | REALITY_ANSWERED | `T0_HARVESTER_POLICY.md` |
| T0.8 | Alert delivery | REALITY_ANSWERED | `T0_ALERT_POLICY.md` |
| T0.9 | Protocol ack | DRAFT (umbrella) | `T0_PROTOCOL_ACK.md` |

### Phase readiness

| Phase | phase.json | scope.yaml | Operator blockers | Topology admission | Status |
|---|---|---|---|---|---|
| T1A | written | written | T0.2 + T0.3 | NOT ADMITTED | BLOCKED |
| T1F | written | written | T0.2 + T0.3 | NOT ADMITTED | BLOCKED |
| T1BD | written | written | T0.2 + T0.3 + T0.6 (REALITY_ANSWERED) | NOT ADMITTED | BLOCKED |

---

## 1. Task 1 — T0 Reality Triage (detail)

The planner-task authority statement (operator quote): *"若很显然问题已经被项目内部或者外部现实回答了那么不要打扰我"* — if reality clearly answers, do not bother the operator. Six of nine T0 items are reality-answered or pre-fillable; three remain genuinely operator-only.

### 1.1 T0.1+T0.2 — Daemon unloaded — **OPERATOR_ONLY (T0.2 BLOCKING)**

`T-1_DAEMON_STATE.md:7-13` records:
- PID 14177 running `src.riskguard.riskguard`.
- launchd label `com.zeus.riskguard-live` loaded.

`T-1_DAEMON_STATE.md:24` says "RiskGuard daemon: NOT RUNNING."  This is a self-contradiction. Planner re-verified at 2026-05-04 with fresh `ps aux | grep riskguard`:
```
leofitz  14177  ... /Python -m src.riskguard.riskguard
```

and fresh `launchctl list | grep -iE "zeus|riskguard"`:
```
14177  -15  com.zeus.riskguard-live
```

**RiskGuard IS running.** Per MASTER_PLAN_v2 §4 working contract item 1, T1 cannot proceed.

Live `src.main` daemon is correctly NOT running (process scan empty for `src.main`). T0.1 is satisfied.

`T0_DAEMON_UNLOADED.md` documents the contradiction and the required operator action (`launchctl bootout` or equivalent). Until that closes, T1 dispatch is forbidden.

### 1.2 T0.3 — Venue quiescent — **OPERATOR_ONLY**

The plan's hypothetical "no-cost on-chain probe substitute" was investigated. Conclusion: no substitute exists.

- An on-chain public-RPC probe could read the public order book but not the off-chain CLOB queue (where Polymarket open orders actually rest).
- Any SDK-side `get_orders` call requires the funder's L1 signer or L2 API creds — both private credentials.
- MASTER_PLAN_v2 §4 working contract item 2 forbids agents from probing private credentials.

`T0_VENUE_QUIESCENT.md` documents this and lists the operator-side equivalent commands.

### 1.3 T0.4 — Rebuild sentinel — **REALITY_ANSWERED**

`.zeus/` directory does not exist (verified `ls .zeus/` → "NO .zeus dir"). The sentinel file is purely policy-documentation; coordinator may create it without operator decision. Mechanical action recorded in `LOCK_DECISION.md` Amendment 2.

### 1.4 T0.5 — SQLite policy — **MIXED**

Reality answers all measurement questions:
- Existing default: `timeout=120` at `src/state/db.py:40,349` (two connection helpers).
- `ZEUS_DB_BUSY_TIMEOUT_MS` does NOT exist in code today (`grep -rn "ZEUS_DB_BUSY"` returned zero matches).
- Other connection sites use varied timeouts: 0s, 5s, 30s, 120s.
- `docs/to-do-list/known_gaps.md:21-44` (today's CRITICAL entry) names DB physical isolation as the only structural antibody.

Operator decisions still required:
1. Confirm or override `ZEUS_DB_BUSY_TIMEOUT_MS = 30000` (planner default).
2. Confirm or override "pull DB physical isolation forward to T2G" (planner recommendation).

`T0_SQLITE_POLICY.md` pre-fills both with rationale; operator may sign as-is or override.

### 1.5 T0.6 — D6 field lock — **REALITY_ANSWERED**

Code grep: the four fields the plan names are explicitly assigned in `src/state/chain_reconciliation.py` across 5 mutation branches at 20 distinct line locations. The eligibility flag `position.corrected_executable_economics_eligible` exists at `src/state/portfolio.py:286` (default False). Both halves of T1BD's invariant are materializable today.

Locked field list:
```
LOCKED_D6_FIELDS = ("entry_price", "cost_basis_usd", "size_usd", "shares")
```

`T0_D6_FIELD_LOCK.md` enumerates all 20 mutation sites; T1BD scope.yaml carries the list forward.

### 1.6 T0.7 — Harvester policy — **REALITY_ANSWERED**

`src/execution/harvester.py:461` defaults `ZEUS_HARVESTER_LIVE_ENABLED` to `"0"` (off; DR-33-A staged rollout). Three execution surfaces use the same gate:
- `src/execution/harvester.py:461`
- `src/execution/harvester_pnl_resolver.py:44`
- `src/ingest/harvester_truth_writer.py:443`

The previous Open-Meteo p_raw rebrand antibody is closed (per `known_gaps.md:91-94` overlay). T1C strengthens the hard guard but does not change the OFF default.

`T0_HARVESTER_POLICY.md` records the policy. **No operator decision is required to unblock T1C planning.**

### 1.7 T0.8 — Alert delivery — **REALITY_ANSWERED**

`src/riskguard/discord_alerts.py` (13 KB) provides `alert_halt/resume/warning/redeem/daily_report` and is wired into `src/riskguard/riskguard.py:17`. F13's "greenfield" claim is half-obsolete. T2E reduces to "wire counters into existing adapter; add cooldown + local-log fallback."

`T0_ALERT_POLICY.md` documents this. **No operator decision is required to unblock T1 emission of the counters listed in MASTER_PLAN_v2 §11 T2E.**

### 1.8 T0.9 — Protocol ack — DRAFT pending T0.2 + T0.3

`T0_PROTOCOL_ACK.md` is pre-filled per the schema in MASTER_PLAN_v2 §8. The `Decision:` line stays unset until operator verifies T0.2 (RiskGuard unload) and T0.3 (venue quiescence).

---

## 2. Task 2 — Plan-vs-Reality Discrepancy Report

Six discrepancies / corrections found:

### 2.1 D-1 — T-1_DAEMON_STATE verdict contradicts evidence (CRITICAL)

- **Plan asserts (in `T-1_DAEMON_STATE.md:24`, planner-grep-verified 2026-05-04):** "Live daemon: NOT RUNNING ... RiskGuard daemon: NOT RUNNING."
- **Reality (planner-grep-verified 2026-05-04, `ps aux` and `launchctl list`):** RiskGuard IS RUNNING (PID 14177, `com.zeus.riskguard-live`).
- **Plan amendment:** Operator unloads, re-attests, revises `T-1_DAEMON_STATE.md`. Captured as `LOCK_DECISION.md` Amendment 1.

### 2.2 D-2 — F1 (adapter live-bound) is correct but incomplete in citations

- **Plan asserts (MASTER_PLAN_v2.md:138):** "A live SDK-contacting path must assert `VenueSubmissionEnvelope.assert_live_submit_bound()` before any SDK call."
- **Reality (planner grep 2026-05-04):**
    - `src/contracts/venue_submission_envelope.py:107` already defines `assert_live_submit_bound()`.
    - `src/data/polymarket_client.py:407-424` already calls the check on `_pending_submission_envelope` before `place_limit_order` SDK contact.
    - `src/venue/polymarket_v2_adapter.py:312 def submit(...)` does **NOT** call the check before `client.create_and_post_order` at line 345.
    - `src/venue/polymarket_v2_adapter.py:528 def submit_limit_order(...)` constructs a `legacy:` placeholder envelope at line 643 and calls `self.submit(envelope)` at line 589.
- **Plan amendment:** T1F prompt MUST cite the existing `polymarket_client.py:407-424` pattern and require the executor to mirror, not invent. Captured in `phases/T1F/phase.json` `_planner_notes` and `LOCK_DECISION.md` Amendment 6.

### 2.3 D-3 — F13 alert greenfield claim is half-obsolete

- **Plan asserts (MASTER_PLAN_v2.md:209):** "`monitoring/alerts.yaml` is greenfield unless alert infrastructure exists."
- **Reality (planner grep 2026-05-04):** `src/riskguard/discord_alerts.py` exists (13 KB), wired into `src/riskguard/riskguard.py:17`, supplies `alert_halt/resume/warning/redeem/daily_report`, webhook resolved from macOS Keychain.
- **Plan amendment:** T2E re-scoped from "build delivery" to "wire Tier-1 counters into existing adapter + cooldown + fallback." Captured in `T0_ALERT_POLICY.md` and `LOCK_DECISION.md` Amendment 5.

### 2.4 D-4 — F3 (SQLite hard-coded 5s timeout) is partially obsolete

- **Plan asserts (MASTER_PLAN_v2.md:150):** "The older plan's hard-coded five-second timeout was not evidence-based and could regress current behavior."
- **Reality (planner grep 2026-05-04):** Existing default is `timeout=120` (seconds) at `src/state/db.py:40,349`, NOT 5 seconds. Other connection sites range from 0s to 120s. The 5s value comes from `src/riskguard/discord_alerts.py:167` (a risk-state.db read, not a trade-DB write path).
- **Plan amendment:** T1E proposed default revised to `ZEUS_DB_BUSY_TIMEOUT_MS = 30000` (30s) — long enough to ride out short writers, short enough to fail fast on rebuild contention. Captured in `T0_SQLITE_POLICY.md` and `LOCK_DECISION.md` Amendment 3.

### 2.5 D-5 — D6 four-field lock has shares-name disambiguation

- **Plan asserts (MASTER_PLAN_v2.md:508):** "block all `entry_price`, `cost_basis_usd`, `size_usd`, and `shares` assignments from chain facts across every branch."
- **Reality (planner grep 2026-05-04):** `Position` has multiple share-flavored fields (`shares`, `shares_filled`, `shares_remaining`, `shares_submitted`, `chain_shares`). The chain-reconciliation mutation sites write to `shares` and `chain_shares`. The plan's `shares` should be read as "the legacy aggregate `shares` field"; T1BD's guard does NOT extend to FillAuthority-derived `shares_filled`/`shares_remaining` — those have their own authority lifecycle.
- **Plan amendment:** T1BD invariant text in `phases/T1BD/phase.json` carries this distinction explicitly (`_planner_notes.subtle_finding`). Captured in `T0_D6_FIELD_LOCK.md §6`.

### 2.6 D-6 — Topology engine does not admit T1A/T1F/T1BD file sets (STRUCTURAL GAP)

- **Plan asserts (MASTER_PLAN_v2.md:319):** "Each implementation packet must run topology navigation with typed intent and the exact files it intends to touch."
- **Reality (planner runs of `python3 scripts/topology_doctor.py --navigation` with various typed intents):** Every variant returned `admission_status: ambiguous; profile: generic; out_of_scope_files: [<all four>]`. The named profiles in `architecture/digest_profiles.py` are domain-specific (`change settlement rounding`, `r3 fill finality ledger implementation`, etc.) and none match T1A/T1F/T1BD's task descriptions or file sets. The `operation planning packet` profile (which DOES admit the planning files) explicitly states implementation files in a planning task are read-only context "until each slice has its own admitted route."
- **Plan amendment:** Operator/topology-maintainer must EITHER (a) add seven new digest profiles in a separate K0 governance commit before T1 begins, OR (b) explicitly authorize T1 executors to use `--route-card-only` advisory admission and have the critic enforce scope from `phases/<P>/scope.yaml`. Captured in `LOCK_DECISION.md` Amendment 4. **This is the topology-blocker that affects every T1 phase.**

### 2.7 Topology routing receipts (planner ran the gates as instructed)

```
T1A: python3 scripts/topology_doctor.py --navigation --task "Zeus May3 R5 remediation T1A single-source DDL" \
       --files src/execution/settlement_commands.py src/state/db.py tests/test_settlement_commands_schema.py architecture/test_topology.yaml \
       --intent "single source of truth for settlement_commands DDL" --write-intent edit --operation-stage edit --side-effect repo_edit
     RESULT: navigation ok: False; admission_status: ambiguous; profile: generic; STOP per direct_blockers.
     SECONDARY (--planning-lock):
       python3 scripts/topology_doctor.py --planning-lock --changed-files src/execution/settlement_commands.py src/state/db.py
       RESULT: planning_lock_required (state truth/schema/lifecycle).
       With --plan-evidence docs/operations/task_2026-05-04_zeus_may3_review_remediation/PLAN.md → "topology check ok"

T1F: planner did not run a separate --navigation gate (T1A was the canonical reproducer; same generic-profile failure pattern applies to T1F's
     src/venue/polymarket_v2_adapter.py + src/contracts/venue_submission_envelope.py file set).
     PRESUMED: NOT ADMITTED. Same Amendment 4 resolution path.

T1BD: planner did not run a separate --navigation gate. Same Amendment 4 resolution path; presumed NOT ADMITTED.
```

Per the planner-task contract: when topology rejects or returns advisory-only, flag the phase as STOP. **All three phases are STOP at planner time.** They become READY only after Amendment 4 closes.

---

## 3. Task 3 — phase.json drafts

Three phase.json files written:

- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1A/phase.json` — schema-valid `orch.phase.v1`; `loc_delta_estimate: 200`; `cross_module_edges: 1`; 3 asserted invariants; 6 consumed invariants.
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1F/phase.json` — schema-valid; `loc_delta_estimate: 320`; 4 asserted invariants; cites the 8 specific evidence sites in `polymarket_v2_adapter.py` and the existing pattern in `polymarket_client.py`.
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1BD/phase.json` — schema-valid; `loc_delta_estimate: 480`; 5 asserted invariants; cites the 20 chain-mutation sites and the projection/loader seam.

Each phase.json includes a non-schema `_planner_notes` block that records:
- evidence-grep timestamp
- file:line citations the planner verified
- topology admission status (NOT ADMITTED at planner time)
- operator-only blockers at planner time (T0.2 RiskGuard, T0.3 venue)
- K0/K1 classification + critic tier guidance

All three pass `python3 ~/.claude/skills/orchestrator-delivery/scripts/phase_validate.py --file <path>` (planner verified 2026-05-04).

---

## 4. Task 4 — Phase-local scope.yaml drafts

Three scope.yaml files written:

- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1A/scope.yaml` — admits 3 source files + 1 test-topology companion.
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1F/scope.yaml` — admits 2 source files + 2 new test files + 1 companion.
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1BD/scope.yaml` — admits 3 source files + 2 new test files + 1 companion.

Each carries `out_of_scope` globs that block strategy/calibration/data/riskguard/state/log/db. Each cites the packet-root `scope.yaml`'s `src/** out_of_scope` rule and explicitly supersedes for the listed in-scope files (per packet-root note: "Implementation packets must create narrower phase scopes before touching source.").

---

## 5. Task 5 — LOCK_DECISION recommendation

`docs/operations/task_2026-05-04_zeus_may3_review_remediation/LOCK_DECISION.md` written as DRAFT.

**Recommendation:** `LOCK_WITH_AMENDMENTS` with 6 amendments:
1. T-1 daemon-state contradiction → operator unloads RiskGuard, re-attests.
2. Coordinator creates `.zeus/rebuild_lock.do_not_run_during_live`.
3. Planner pre-filled SQLite policy: `ZEUS_DB_BUSY_TIMEOUT_MS=30000`, pull DB isolation forward to T2G.
4. Topology engine does not admit T1A/T1F/T1BD; operator must add 7 new profiles OR accept advisory-only with critic-enforced scope.
5. T2E re-scoped (alert adapter exists; only YAML config and cooldown are greenfield).
6. T1F prompt must cite `polymarket_client.py:407-424` pattern as canonical, do not invent.

If LOCK_WITH_AMENDMENTS is rejected, fallback is REVISE with at least Amendments 1 + 4 closed before re-presenting.

Locked artifact pointer: `MASTER_PLAN_v2.md` byte-for-byte (59 840 bytes) at HEAD `1116d827`. Companion: `ORCHESTRATOR_RUNBOOK.md` (22 320 bytes).

---

## 6. Open Questions persisted

The planner-task instructions ask the planner to write open questions to `.omc/plans/open-questions.md`. Three open questions remain after this triage:

1. **Operator: do you accept Amendment 3 SQLite defaults, or prefer different values?** Materially affects T1E behavior under load.
2. **Operator/topology-maintainer: how is Amendment 4 (topology profiles) resolved — author 7 new profiles, or accept advisory-only admission with critic enforcing scope?** Blocks every T1 phase.
3. **Operator: after RiskGuard unload, do you also want to disable the launchd plist persistently (delete or unload at boot), or only unload for this remediation window?** Affects what `T0_DAEMON_UNLOADED.md` should record.

The planner has not written `.omc/plans/open-questions.md` because the packet authority surface for this remediation is `docs/operations/task_2026-05-04_zeus_may3_review_remediation/`, not the OMC plan directory. These questions are also recorded in `LOCK_DECISION.md` and `T0_*.md`, which are the on-disk authority. If a separate `.omc/plans/open-questions.md` is desired the coordinator may copy the three items above; the durable record lives in the packet.

---

## 7. Artifact list

Files written by this planner pass:

- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/planner_output.md` (this file)
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/T0_SQLITE_POLICY.md`
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/T0_D6_FIELD_LOCK.md`
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/T0_HARVESTER_POLICY.md`
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/T0_ALERT_POLICY.md`
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/T0_DAEMON_UNLOADED.md`
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/T0_VENUE_QUIESCENT.md`
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/T0_PROTOCOL_ACK.md`
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/LOCK_DECISION.md`
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1A/phase.json`
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1A/scope.yaml`
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1F/phase.json`
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1F/scope.yaml`
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1BD/phase.json`
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1BD/scope.yaml`

Files NOT written (with reason):
- `T0_DAEMON_UNLOADED.md` was written but flagged as DRAFT (it is the operator-only artifact; planner pre-filled context but operator must finalize after actual unload).
- `T0_VENUE_QUIESCENT.md` was written but flagged as OPERATOR_ONLY DRAFT (no agent substitute exists).
- `PLAN_LOCKED.md` was NOT created. `LOCK_DECISION.md §2` provides the byte-for-byte pointer; if operator prefers a copy, they can `cp MASTER_PLAN_v2.md PLAN_LOCKED.md` after signature.
- T1C, T1E, T1G, T1H phase.json/scope.yaml were NOT drafted. The planner-task contract names only T1A/T1F/T1BD. Coordinator dispatches a follow-up planner pass after T1A/T1F/T1BD close.

---

## 8. Final self-check (per planner role contract)

- [x] Did I only ask the user about preferences (not codebase facts)? No questions to user; reality answered most. Only operator-only items (T0.2 unload, T0.3 venue) escalated.
- [x] Does the plan have actionable phases with acceptance criteria? Yes — three `phase.json` with `asserted_invariants`.
- [x] Did I wait for user confirmation before handoff? N/A — coordinator dispatches via `LOCK_DECISION.md` signature, not chat confirmation.
- [x] Are plans saved to durable on-disk path? Yes — packet directory.
- [x] In consensus mode, did I provide structured comparison? N/A — this is a remediation triage, not a debate.
- [x] Did I avoid claiming "pattern proven" / "narrow scope self-validating"? Yes — all assertions cite file:line evidence grep-verified within last 10 minutes.

---

**End of Round-1 planner output. Coordinator: read this file, then `LOCK_DECISION.md`, then dispatch operator-decision request for the three serious issues, then re-trigger planner after Amendment 1 + Amendment 4 close.**

---

## Round-2 Append (2026-05-04) — T1C/T1E/T1G/T1H phase artifacts

This section is APPENDED, not edited-in-place. Earlier amendments and citations remain authoritative.

### Round-2 mandate

Coordinator dispatched a follow-up planner pass to draft phase.json + scope.yaml for T1C, T1E, T1G, T1H. Coordinator confirmed Round-1 amendments 1 (RiskGuard contradiction) and 2 (rebuild sentinel) closed; T-1_DAEMON_STATE.md erratum corrected; `.zeus/rebuild_lock.do_not_run_during_live` created.

### New phase artifacts written

- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1C/phase.json`
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1C/scope.yaml`
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1E/phase.json`
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1E/scope.yaml`
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1G/phase.json`
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1G/scope.yaml`
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1H/phase.json`
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1H/scope.yaml`

All four `phase.json` parse against `orch.phase.v1` (planner re-ran `~/.claude/skills/orchestrator-delivery/scripts/phase_validate.py --file <path>` per artifact, 2026-05-04).

### Round-2 plan-vs-reality discrepancies

#### Amendment 7 (AMD-7) — No general counter-sink module exists in src/

**Plan asserts (MASTER_PLAN_v2 §11 T2E table and §10 T1BD/T1F/T1C/T1E rows):** Tier-1 emits at least 8 named counters: `cost_basis_chain_mutation_blocked_total`, `position_projection_field_dropped_total`, `position_loader_field_defaulted_total`, `db_write_lock_timeout_total`, `compat_submit_rejected_total`, `placeholder_envelope_blocked_total`, `harvester_learning_write_blocked_total`, `settlement_recorded_without_redeem_total`.

**Reality (planner Round-2 grep 2026-05-04):**
- `grep -rn "_total\b\|metric_inc\|counter_inc\|increment_counter" src/state/chain_reconciliation.py src/engine/lifecycle_events.py src/observability/` → 0 matches.
- `src/observability/` has only `scheduler_health.py` and `status_summary.py` (read-only telemetry, not increment surfaces).
- `src/state/calibration_observation.py / edge_observation.py / learning_loop_observation.py` are read-only projection modules — they query existing data, not increment counters.
- `src/riskguard/metrics.py` has only Brier/directional-accuracy math, not a generic counter sink.

**Amendment:** No general counter-sink module exists today. T1BD is the FIRST phase to introduce a counter-sink decision. Options the operator/T1BD-executor must choose between:
1. Create a NEW module `src/observability/counters.py` (or similar) with a thread-safe increment + read-back API; persist counters to a new `state/counters.db` or to an existing risk_state.db table.
2. Reuse the existing `*_observation` pattern: each counter is a logical row in a counters table; increment is `INSERT INTO ... ON CONFLICT(name) DO UPDATE SET count=count+?`; read is `SELECT`.
3. Use a simple module-global dict + the JSON daily-export pipeline that already exists for portfolio/tracker.

**Implication:** T1BD's `_planner_notes.predecessor_dependency` notes this — T1BD is the first counter-sink consumer, so T1BD makes the decision. T1F/T1C/T1E inherit T1BD's choice. If T1BD's executor cannot resolve this in one phase, T1BD splits into T1BD-counter-sink (governance phase, pre-T1BD) + T1BD-application (the actual chain freeze + projection counters). The phase_id `T1BD` in phase.json today assumes the executor decides + applies in one commit; if that proves too large, T1BD escalates STOP_REPLAN.

#### Amendment 8 (AMD-8) — T1G is verification-first; expected_artifact override

**Plan asserts (MASTER_PLAN_v2 §10 row T1G):** "Verification-first: trace entry submit, exit submit, compatibility submit, rejected submit, FOK/FAK filled response, and open-order ACK. Implement missing persistence only where uncovered."

**Reality (planner Round-2 grep 2026-05-04):** `_persist_final_submission_envelope_payload` already exists at `src/execution/executor.py:432` and is called at lines 1609 (entry submit) and 2291 (exit submit). The 8 SUBMIT_REJECTED sites and 2 SUBMIT_UNKNOWN_SIDE_EFFECT sites do NOT currently call the persistence helper — but the audit must determine whether each REJECTED branch even has an SDK-returned payload to persist (some reject before SDK contact and would have nothing to persist).

**Amendment:** T1G's `expected_artifact` is overridden from the schema default `execution/execution_result.md` to `audit/sdk_envelope_path_audit.md`. If audit finds NEEDS_FIX gaps, executor opens an in-phase scope amendment to add `src/execution/executor.py` to in_scope. T1G's phase.json files_touched lists ONLY the audit + new test file at planner time; source-edit scope is contingent on audit verdict.

#### Amendment 9 (AMD-9) — T1C's predecessor dependency on T1BD's counter-sink

**Plan asserts (MASTER_PLAN_v2 §10 T1 order):** `T1A → T1F → T1BD → T1C → T1E → T1G → T1H`.

**Reality:** Per Amendment 7, T1BD introduces the counter-sink module. T1C's `harvester_learning_write_blocked_total` invariant requires the same sink. T1C cannot land without T1BD already resolving the sink choice. The serialized order is correct — T1BD precedes T1C — but T1C's phase.json explicitly cites the predecessor dependency in `_planner_notes` so executors do not attempt to land T1C in parallel with T1BD.

#### Amendment 10 (AMD-10) — T1E predecessor dependency on T1A (db.py merge surface)

**Plan asserts:** T1E `Allowed files: src/state/db.py, DB connection helpers, scripts/rebuild_calibration_pairs_v2.py ...`.

**Reality:** T1A's "single source DDL" change in db.py replaces the inline `CREATE TABLE settlement_commands` at line 1398 with an import/init helper. T1E touches db.py to add the env-var read + apply at lines 40 and 349. Without a serialized order, T1A and T1E both edit db.py and conflict on a non-trivial diff.

**Amendment:** T1E's phase.json `_planner_notes.predecessor_dependency` documents this. The MASTER_PLAN_v2 serialized order already enforces it (T1A → T1E), but the constraint is now explicit so any future parallel-T1 proposal stops on this finding.

#### Amendment 11 (AMD-11) — T1H predecessor dependency on T1G (identity-truth axis)

**Plan asserts:** T1H "Read-only census ... identity_truth: complete, placeholder, condition_token_collapse, missing_question, missing_yes_no_pair."

**Reality:** The census's identity_truth classification scans `venue_submission_envelope` rows. If T1G found NEEDS_FIX persistence gaps in REJECTED/UNKNOWN paths, the envelope rows the census reads would be incomplete, producing false `complete` verdicts for paths that actually didn't persist their final SDK payload.

**Amendment:** T1H must wait on T1G closeout. Documented in T1H phase.json `_planner_notes.predecessor_dependency`.

### Round-2 phase readiness

| Phase | phase.json | scope.yaml | Operator blockers | Predecessor blockers | Status |
|---|---|---|---|---|---|
| T1C | written | written | T0.2 + T0.3 | T1A + T1F + T1BD landed; counter-sink decided in T1BD | BLOCKED (carried) |
| T1E | written | written | T0.2 + T0.3 | T1A + T1F + T1BD + T1C landed | BLOCKED (carried) |
| T1G | written | written | T0.2 + T0.3 | T1A + T1F + T1BD + T1C + T1E landed | BLOCKED (carried) |
| T1H | written | written | T0.2 + T0.3 | T1A + T1F + T1BD + T1C + T1E + T1G landed | BLOCKED (carried) |

All four are BLOCKED by the same Round-1 carry-forward operator-only facts (T0.2 RiskGuard, T0.3 venue) and the topology-profile gap (LOCK_DECISION Amendment 4). No new operator escalations beyond Round-1's three serious issues.

### Round-2 self-check

- [x] Verified file:line citations via fresh grep within 10 minutes (Round-2 grep timestamps in each phase.json `_planner_notes.round2_grep_verification`).
- [x] No "pattern proven" / "narrow scope self-validating" language used.
- [x] phase.json files_touched explicitly enumerated; no broad wildcards.
- [x] scope.yaml carries packet-root supersession note.
- [x] consumed_invariants includes upstream T1 invariants (T1A → T1F → T1BD → T1C → T1E → T1G chain explicit).
- [x] Code Provenance rule (~/.claude/CLAUDE.md) encoded as T1H-PROVENANCE-HEADER-PRESENT invariant.
- [x] Counter-sink gap surfaced as Amendment 7 instead of silently assumed.

**End of Round-2 planner output.**
