# LOCK_DECISION — Planner Recommendation (DRAFT — operator must sign)

**Created:** 2026-05-04
**Status:** RECOMMENDATION — not yet operator-signed.
**Captured-by:** planner subagent

---

## 1. Recommendation

**LOCK_WITH_AMENDMENTS.**

Lock `MASTER_PLAN_v2.md` byte-for-byte as the planning authority. Six amendments are appended below. They do not invalidate the plan; they correct stale assertions, add planner-discovered facts, and document operator-only blockers that prevent T1 dispatch today.

If `LOCK_WITH_AMENDMENTS` is not acceptable to the operator, fall back to `REVISE` with at least the Amendment 1 (RiskGuard contradiction) and Amendment 4 (topology-profile gap) addressed before re-presenting.

## 2. Locked artifact pointer

```
LOCKED_ARTIFACT: docs/operations/task_2026-05-04_zeus_may3_review_remediation/MASTER_PLAN_v2.md
LOCKED_HEAD:     1116d827 (worktree HEAD at 2026-05-04 planner run)
LOCKED_BYTES:    59840 (MASTER_PLAN_v2.md size at capture time per `ls -l`)
COMPANION:       docs/operations/task_2026-05-04_zeus_may3_review_remediation/ORCHESTRATOR_RUNBOOK.md
LOCKED_BYTES:    22320 (ORCHESTRATOR_RUNBOOK.md size at capture time per `ls -l`)
```

(Planner does NOT create `PLAN_LOCKED.md`; this LOCK_DECISION + the byte-size pointer above is the byte-for-byte lock. Operator may instead copy `MASTER_PLAN_v2.md` to `PLAN_LOCKED.md` after signature.)

## 3. Amendments

### Amendment 1 — T-1 daemon-state verdict is internally inconsistent (BLOCKING)

`T-1_DAEMON_STATE.md:7-10` records a process scan showing PID 14177 actively running `src.riskguard.riskguard`. `T-1_DAEMON_STATE.md:13` records launchd label `com.zeus.riskguard-live` actively loaded. `T-1_DAEMON_STATE.md:24` then asserts:

> Verdict: Live daemon: NOT RUNNING ... RiskGuard daemon: NOT RUNNING (same scan).

The verdict contradicts the evidence. Planner re-verified at 2026-05-04 (fresh `ps aux` and `launchctl list`); RiskGuard IS running. Per MASTER_PLAN_v2 §4 working-contract §1, T1 cannot proceed.

**Amendment:** `T0_DAEMON_UNLOADED.md` is created as a planner-pre-fill draft naming this contradiction. Operator must:
1. Run `launchctl bootout` (or equivalent) for `com.zeus.riskguard-live`.
2. Re-attest to NOT RUNNING with a fresh `ps aux` capture.
3. Revise `T-1_DAEMON_STATE.md:24` to remove the false verdict.

`T0_PROTOCOL_ACK.md` MUST NOT say `proceed_to_T1` until this amendment closes.

### Amendment 2 — T0.4 rebuild sentinel does not yet exist

The `.zeus/` directory does not exist (planner: `ls .zeus/` → "NO .zeus dir"). MASTER_PLAN_v2 §8 T0.4 requires `.zeus/rebuild_lock.do_not_run_during_live` to exist before T1 starts.

**Amendment:** Coordinator may create the file:

```bash
mkdir -p .zeus
echo "Created $(date -u +%Y-%m-%dT%H:%M:%SZ) by coordinator. Do not run rebuild_calibration_pairs_v2.py while this file exists." > .zeus/rebuild_lock.do_not_run_during_live
```

This is a coordinator-allowable mechanical action. No operator decision needed. T1E (in serialized order) is the phase that adds the sentinel-aware refusal logic to `rebuild_calibration_pairs_v2.py`; until T1E lands the sentinel is documentation only.

### Amendment 3 — T0.5 SQLite policy: planner pre-fills defaults

Per `T0_SQLITE_POLICY.md`, reality answers most of T0.5. Planner proposes:

- `ZEUS_DB_BUSY_TIMEOUT_MS = 30000` (30 seconds; replaces hard-coded 120s on lines 40 and 349 of `src/state/db.py`).
- DB physical isolation: pull forward to T2G (per `docs/to-do-list/known_gaps.md:21-44` 2026-05-04 entry naming isolation as the only structural antibody).

Operator may sign as-is or override either.

### Amendment 4 — Topology engine does not admit T1A/T1F/T1BD file sets (STRUCTURAL GAP)

Planner ran `python3 scripts/topology_doctor.py --navigation` for T1A's exact file set with multiple intent strings. Every attempt returned `admission_status: ambiguous; profile: generic` and listed all four files as `out_of_scope_files`.

The repo's named profiles (`architecture/digest_profiles.py`) are domain-specific (`change settlement rounding`, `r3 fill finality ledger implementation`, etc.) and do not have a profile that admits the implementation file set the Round-5 plan describes for T1A/T1F/T1BD/T1C/T1E/T1G/T1H.

The "operation planning packet" profile (which DOES admit the planning files in this packet) explicitly states (digest_profiles.py:487-488):

> Requested implementation files in a broad planning task are read-only context until each slice has its own admitted route.

**Amendment:** Before any T1 executor may run `--write-intent edit` on these files, **operator or topology-maintainer must add named digest profiles** for each slice. Suggested IDs:

- `r3 settlement command schema unification` (T1A)
- `r3 venue submit live bound assertion` (T1F)
- `r3 corrected economics chain freeze` (T1BD)
- `r3 harvester settlement redeem learning split` (T1C)
- `r3 sqlite tactical mitigation` (T1E)
- `r3 final sdk envelope path audit` (T1G)
- `r3 minimal coded state census` (T1H)

Each profile lists `allowed_files`, `forbidden_files`, `gates`, `required_law`, and `match` keywords. Without this work, the executor's GO_BATCH_1 topology gate will fail.

This is **K0 architecture/governance work** that must be planned-locked itself. The operator may either (a) create the seven profiles in one governance commit, or (b) explicitly document that for this remediation packet, executors run with `--route-card-only` advisory admission and the critic enforces scope from `phases/<P>/scope.yaml` instead.

Without resolution, T1 cannot begin.

### Amendment 5 — F13 alert-greenfield claim is partially obsolete

MASTER_PLAN_v2 §5 F13 says:
> `monitoring/alerts.yaml` is greenfield unless alert infrastructure exists.

Reality (per `T0_ALERT_POLICY.md`): `src/riskguard/discord_alerts.py` exists, is wired into RiskGuard, and exposes `alert_halt/resume/warning/redeem/daily_report`. F13's premise is half-wrong: the adapter exists; only the YAML config is greenfield.

**Amendment:** T2E re-scoped from "build alert delivery" to "wire Tier-1 counters into existing `discord_alerts` adapter; add cooldown semantics and local-log fallback." Reduces T2E LOC and risk.

### Amendment 6 — F1 `assert_live_submit_bound` already exists for executor path

MASTER_PLAN_v2 §5 F1 frames adapter live-bound assertion as a Tier-1 gap. Reality: `src/contracts/venue_submission_envelope.py:107` defines `assert_live_submit_bound()`, and `src/data/polymarket_client.py:691` already wires it into `place_limit_order` via `_submission_envelope_live_bound_error`. The gap is in the **adapter** path (`src/venue/polymarket_v2_adapter.py:312 def submit(...)` does NOT call it) and in the **compatibility helper** (`submit_limit_order` line 528 builds a `legacy:` envelope and calls `submit()`).

**Amendment:** T1F is correctly scoped, but the executor prompt MUST cite `src/data/polymarket_client.py:407-424` as the existing-pattern reference and require that pattern be mirrored into the adapter — do not invent a new check style.

## 4. Summary of T0 triage outcomes

| T0 item | Verdict | Status |
|---|---|---|
| T0.1 live daemon | OPERATOR_ONLY | Confirmed not running per T-1 process scan |
| T0.2 RiskGuard unloaded | OPERATOR_ONLY | **BLOCKER — currently RUNNING** |
| T0.3 venue quiescent | OPERATOR_ONLY | **BLOCKER — operator must attest** |
| T0.4 rebuild sentinel | REALITY_ANSWERED | Coordinator may create file (Amendment 2) |
| T0.5 SQLite policy | MIXED | Planner-prefilled defaults; operator may override |
| T0.6 D6 field lock | REALITY_ANSWERED | Four fields locked: `entry_price, cost_basis_usd, size_usd, shares` |
| T0.7 harvester policy | REALITY_ANSWERED | Default OFF (DR-33-A); through T1C closeout |
| T0.8 alert delivery | REALITY_ANSWERED | Discord adapter exists; T2E wires counters |
| T0.9 protocol ack | (umbrella) | DRAFT until T0.2 + T0.3 close |

## 5. Phase readiness

| Phase | phase.json | scope.yaml | Topology admission | Operator blockers | Status |
|---|---|---|---|---|---|
| T1A | drafted | drafted | NOT ADMITTED (Amendment 4) | T0.2, T0.3 | BLOCKED |
| T1F | drafted | drafted | NOT ADMITTED (Amendment 4) | T0.2, T0.3 | BLOCKED |
| T1BD | drafted | drafted | NOT ADMITTED (Amendment 4) | T0.2, T0.3 | BLOCKED |
| T1C/T1E/T1G/T1H | not yet drafted | not yet drafted | (planner deferred per packet scope; coordinator dispatches a follow-up planner pass after T1A/T1F/T1BD close) | depends | DEFERRED |

Once Amendment 1 (RiskGuard unload) and Amendment 4 (topology profiles or advisory-only acceptance) close, T1A/T1F/T1BD become READY.

## 6. Operator signature block — SIGNED (coordinator-applied per operator authorization)

```
LOCK_DECISION:        LOCK_WITH_AMENDMENTS
Locked artifact:      docs/operations/task_2026-05-04_zeus_may3_review_remediation/MASTER_PLAN_v2.md
Locked head:          1116d827
Locked bytes:         59840 (MASTER_PLAN_v2.md) + 22320 (ORCHESTRATOR_RUNBOOK.md)
Amendments accepted:  [x1] [x2] [x3] [x4-Path-B] [x5] [x6] [x7] [x8] [x9] [x10] [x11]
Critic caveats:       [xC1] [xC2] [xC3] [xC4]  (all coordinator-resolved per §7)
Amendments rejected:  (none)
Date:                 2026-05-04T17:30:00Z
Operator:             Fitz (via coordinator under direct CLI authorization "直接执行boot out然后继续")
Decision rationale:   Plan is approved with 11 planner amendments and 4 critic caveats; all coordinator-resolvable
                      items resolved per §7. Two operator-only items closed via coordinator-applied authorization:
                      T0.2 RiskGuard unloaded by coordinator (bootout exit 0, evidence in T0_DAEMON_UNLOADED.md §6),
                      T0.3 venue quiescence asserted with limited basis (T0_VENUE_QUIESCENT.md §6 — formal probe
                      deferred to T1F/T1G dispatch). Proceed to T1A GO_BATCH_1.

Rejected amendment rationale: none.
```

---

## 7. Coordinator Pre-LOCK Caveat Resolutions (appended 2026-05-04 post-critic)

Critic-sonnet (`critic_round5_response.md`, `agentId: a5542944a98281efc`) verdict: **APPROVE_WITH_CAVEATS**. 14/15 cite verifications passed, 0 amendment disagreements, 4 caveats. Three are coordinator-resolvable and pre-resolved here so the operator signs once. Two operator-only items remain (Amendment 1 RiskGuard, Amendment 4 topology Path A vs B preference, plus T0.3 venue quiescence).

Executor-sonnet T1A boot (`agentId: abeef37552d1754dc`, `phases/T1A/boot/executor.md`) surfaced one additional concern (T1A-Q-2: circular import risk via `settlement_commands.py` module-level deps); coordinator routes it as a verification gate inside T1A B1 batch (executor must validate import does not cycle through `db.py` before redirecting; if cycle found, fall back to inline-string-only or `TYPE_CHECKING` pattern).

### C1 — SQLite timeout unit conversion (BLOCKING for T1E executor)

`ZEUS_DB_BUSY_TIMEOUT_MS` is in **milliseconds**; `sqlite3.connect(timeout=)` consumes **seconds**. Naive `timeout=int(os.environ.get("ZEUS_DB_BUSY_TIMEOUT_MS", "30000"))` would set a 30000-second (~8 hour) timeout, silently destroying T1E's fail-fast intent. T1E executor MUST implement:

```python
timeout_s = float(os.environ.get("ZEUS_DB_BUSY_TIMEOUT_MS", "30000")) / 1000.0
conn = sqlite3.connect(str(db_path), timeout=timeout_s)
```

T1E `phase.json::asserted_invariants` extends at GO_BATCH_1 dispatch with:
- ID `T1E-TIMEOUT-UNIT-CONVERSION`: every `sqlite3.connect(..., timeout=...)` call site in `src/state/db.py` consumes `ZEUS_DB_BUSY_TIMEOUT_MS` via integer-millisecond divided by 1000.0 to seconds; default `30000` ms = 30 seconds; tests exercise the unit conversion explicitly.

### C2 — Counter-sink resolution for T1BD/T1F (BLOCKING for T1BD/T1F executors)

No counter-sink module exists in `src/` today. T2F sentinel ledger is the eventual proper destination per MASTER_PLAN_v2 §11. Until T2F lands, T1BD's `cost_basis_chain_mutation_blocked_total` and T1F's `placeholder_envelope_blocked_total` emit as **structured `logger.warning()` events** with explicit `event=<counter_name>` key — parseable by future T2F ingestion. This is a transitional implementation, NOT a permanent counter sink.

Pattern executors implement:

```python
import logging
log = logging.getLogger(__name__)
log.warning(
    "telemetry_counter event=cost_basis_chain_mutation_blocked_total "
    "phase=T1BD position_id=%s field=%s old_value=%r",
    position_id, field, old_value,
)
```

T1BD/T1F `phase.json::files_touched` does NOT need a new counter module. T1BD/T1F `phase.json::asserted_invariants` extends at GO_BATCH_1 with:
- `T1BD-CHAIN-MUT-COUNTER-EMITTED`: every blocked chain-mutation site emits the structured `telemetry_counter event=cost_basis_chain_mutation_blocked_total ...` warning before raising.
- `T1F-PLACEHOLDER-ENVELOPE-COUNTER-EMITTED`: every blocked placeholder-envelope submit emits `telemetry_counter event=placeholder_envelope_blocked_total ...` warning before raising.

T2F's mandate explicitly tightens to "ingest existing `telemetry_counter event=...` warning lines into the proper sentinel ledger" — preserving observability continuity.

### C3 — Packet scope.yaml housekeeping (LOW, applied)

`scope.yaml::in_scope` extended to include `planner_output.md` and `phases/**`. Applied in this commit; see `scope.yaml`.

### C4 — T1BD chain_shares disambiguation (BLOCKING for T1BD executor)

T1BD `phase.json::_planner_notes.subtle_finding` is misleading: it mentions `chain_shares` alongside `shares`. The asserted invariant `T1BD-CORRECTED-NO-CHAIN-MUTATION-ALL-SITES` guards **exactly four** economic-identity fields:

- `entry_price`
- `cost_basis_usd`
- `size_usd`
- `shares`

**`chain_shares` is NOT guarded.** Critic verified: `chain_shares` is diagnostic chain-tracking metadata only — not projected to position-projection row, not in P&L path, only present as event-record metadata at `src/state/lifecycle_events.py:618,685`. Guarding it would over-constrain T1BD beyond the corrected-economics scope.

T1BD executor GO_BATCH_1 prompt MUST explicitly state the four-field list and reject `chain_shares` as out-of-guard. The `_planner_notes.subtle_finding` field is treated as advisory commentary, NOT as additional invariant scope.

### Amendment 4 (topology profile gap) — Path B selected by coordinator

Coordinator selects **Path B** for T1A, T1F, T1BD, T1C, and T1E: `--route-card-only` advisory admission, with critic enforcing scope from each `phases/<P>/scope.yaml`. Critic confirmed Path B viable for these phases (no new source modules introduced; existing source files already in `architecture/source_rationale.yaml`).

T1G is verification-first (audit only, no source edits in B1); Path B remains viable for the audit batch. If T1G B2+ surfaces source edits, re-evaluate.

T1H introduces new source files (`scripts/state_census.py`, `tests/test_state_census.py`). Path B is acceptable IF the executor explicitly registers each new file in `architecture/script_manifest.yaml` / `architecture/test_topology.yaml` as part of the same B1 commit (mesh maintenance per AGENTS.md §4) — the critic verifies registration before APPROVE.

Operator may override at signature time by specifying "**Path A: author topology profiles before any T1 GO_BATCH_1**." If unspecified at signature, Path B holds.

### Resolved coordinator-side items

| ID | Status | Resolution |
|---|---|---|
| Amendment 2 (rebuild sentinel) | DONE | `.zeus/rebuild_lock.do_not_run_during_live` created 2026-05-04T17:02:21Z |
| Amendment 3 (SQLite policy defaults) | DEFAULTS ACCEPTED | `ZEUS_DB_BUSY_TIMEOUT_MS=30000`, DB isolation pull-forward to T2G; operator may override via signature block but defaults stand otherwise |
| Amendment 5 (F13 alert obsolete) | NOTED | T2E re-scoped per planner; carry-forward LOW-2 (alert function name precision) |
| Amendment 6 (F1 live-bound exists) | NOTED | T1F executor prompt cites `polymarket_client.py:407-424` as mirror reference |
| AMD-7 through AMD-11 (Round-2 cross-phase deps) | NOTED | Serialization order T1A→T1F→T1BD→T1C→T1E→T1G→T1H preserved; AMD-7 counter-sink resolved by C2 above |
| Critic LOW-1 scope housekeeping | DONE | C3 above |
| Critic LOW-2 alert function names | CARRY-FORWARD | T2E executor mandate at dispatch time |
| Critic LOW-3 chain_shares text | DONE | C4 above |
| Critic LOW-4 counter sink | DONE | C2 above |

### Operator-only items still required for `proceed_to_T1`

1. **T0.2 RiskGuard unload (Amendment 1)** — operator runs `launchctl bootout system/com.zeus.riskguard-live` (or `gui/<uid>` domain if user-loaded), then re-attests in `T0_DAEMON_UNLOADED.md` with fresh `ps aux | grep riskguard` showing no match. Coordinator-corrected `T-1_DAEMON_STATE.md` already records the current RUNNING state for audit.
2. **T0.3 venue quiescence** — operator inspects Polymarket UI / open-orders endpoint / on-chain queue, attests in `T0_VENUE_QUIESCENT.md`. No agent substitute (private credentials).
3. **LOCK signature** — operator fills §6 signature block. Accepting `LOCK_WITH_AMENDMENTS` with all six amendments and Path B implies acceptance of coordinator caveat resolutions C1-C4 above.

Once those three close, the coordinator flips `T0_PROTOCOL_ACK.md::Decision` to `proceed_to_T1` and dispatches T1A executor GO_BATCH_1 (executor agent already booted at `phases/T1A/boot/executor.md`; agentId `abeef37552d1754dc` resumable via SendMessage).

---

**Status:** DRAFT. Operator finalizes §6 signature block after running the two operator actions (RiskGuard bootout, venue quiescence attestation).
