# PLAN — Post-Karachi 5/17 Remediation Wave

**Authority basis**: 11 audit runs (RUN_6 → RUN_11) + 7 parallel investigation reports + Karachi WIN status (~100% probability, ~7h until full cascade closure required).
**Operator constraint**: NO manual completion ever. First live order must complete programmatically per design. `operator_record_redeem.py` invocation creates the precedent that masks cascade-liveness defects on ALL future orders. Per user memory `feedback_first_live_order_no_manual_completion`.

**Karachi 5/17 status at PLAN write-time (2026-05-17T13:30Z)**:
- Position `c30f28a5-d4e`: phase=`day0_window`, 1.5873 shares, chain `synced`, partial order
- UMA resolution window in-flight (T+~90min past endDate; 1-3h normal)
- `settlements_v2` Karachi row: NOT YET WRITTEN (expected within next 1-2h)
- `settlement_commands` table: 0 rows total (consistent with no positions yet at `settled` phase since PR-126 merge 2026-05-15 — INNOCUOUS per CASCADE_DRY_TRACE.md verdict (a))
- `oracle_error_rates.json`: NOT generated (all cities at 0.5× Kelly fallback — applies to Karachi too but is conservative, not adversarial)

---

## §1 Critical path framing (Fitz Constraint #1: K structural decisions, not N patches)

The 42 catalogued findings collapse to **6 structural decisions incompletely executed**:

| K# | Decision | Symptom count | Fix wave |
|---|---|---|---|
| K-α | **Redeem cascade endpoint not autonomous** (adapter stub returns DEFERRED_TO_R1 → operator action needed) | 1 (but precedent-setting) | **WAVE-0 (PR-I.5)** |
| K-β | **K1 split incomplete on reader side** (writers updated 2026-05-14, ~37 reader callers still point at zeus-world.db archive) | F40+F41+F42+`main.py:1306-1308` boot test = 37 broken | WAVE-1 |
| K-γ | **decision_snapshot_id never threaded through early-rejection paths** | F25+F2+F7 (lineage NULL family) | WAVE-2 |
| K-δ | **Migration & writer-lock contracts uneven** (F23 framework missing, 12 operator-action scripts bypass writer lock, F22 + F26 dual-allowlist) | F22+F23+F26+F29+F30 | WAVE-3 |
| K-ε | **Silent-data antibodies absent** (sentinels not constrained, INSERT OR IGNORE swallowed errors, 1583-row settlement gap, dual-write windows) | F8+F15+F18+F19+F31 | WAVE-4 |
| K-ζ | **Deployment-readiness gates absent** (oracle bridge "writer ship without schedule", cal-transfer-eval plist "DO NOT LOAD" while loaded, persistent MISSING-everywhere never escalates) | F32+F33+F35+F39 | WAVE-5 |

**False findings retracted** (per RUN_11 + sonnet validation):
- F11 "add KeepAlive to heartbeat-sensor/calibration-transfer-eval plists" → REJECTED (calendar-fire jobs, not daemons)
- F36 "settlements live tables empty 10 days" → DEFECT-INVALID-PROVENANCE (RUN_10 read wrong DB)
- F38 "calibration_pairs_v2 empty" → DEFECT-INVALID-PROVENANCE (same root cause as F36)

---

## §2 WAVE-0 — Karachi 7h critical path: PR-I.5 NO-GO + operator decision required

**Verdict (PR_I5_WEB3_WIRE.md, agent `a5d440510965fc3b5`)**: **NO-GO** in 7h. Three hard blockers:

1. `settlement_commands` schema does NOT store `winning_index_set` — CTF.redeemPositions ABI requires which outcome bin to redeem; not currently materialized anywhere accessible to the adapter
2. `PolymarketV2Adapter()` ctor at `main.py:226` passes NO `signer_key` — current adapter has no signing path wired
3. No real-broadcast test path: Polygon Mumbai deprecated Feb 2024; Amoy testnet CTF deploy status unverified

**The fork (operator must decide)**:

Per user memory `feedback_first_live_order_no_manual_completion`: "First smoke-test-passing live order must complete programmatically per design; manual completion sets precedent that masks cascade-liveness defects on all future orders."

The investigation agent claims `REDEEM_OPERATOR_REQUIRED + operator_record_redeem.py` "is the designed backstop... explicitly NOT the prohibited manual completion." This rests on a semantic distinction:

- **Interpretation X** (agent's reading): `operator_record_redeem.py` is a *cascade-routed* operator hook — the state machine designates the operator step as a legitimate cascade link (L5 happy path step 9 per KARACHI_5_17 runbook). It is the auditable, logged completion path. NOT the same as "operator manually bypasses cascade".
- **Interpretation Y** (literal reading of user memory): ANY operator-typed CLI is "manual completion". The first order to complete by ANY operator CLI sets the precedent.

**Operator must explicitly choose X or Y**.

### If Interpretation X (operator approves the runbook step)
Karachi cascade proceeds:
1. UMA resolves → harvester writes `settlements_v2 VERIFIED` row
2. `harvester_pnl_resolver` enqueues `REDEEM_INTENT_CREATED` in `settlement_commands`
3. `redeem_submitter` calls adapter → returns `REDEEM_DEFERRED_TO_R1` → state → `REDEEM_OPERATOR_REQUIRED`
4. Log emits `[REDEEM_OPERATOR_REQUIRED] command_id=… condition_id=c30f28a5… action=run_operator_record_redeem` per cascade_liveness_contract antibody
5. Operator: claim YES on Polymarket UI (~$1.5873 USDC) → copy tx_hash → run `python -m scripts.operator_record_redeem 0xc5fa…44ae 0x<tx_hash>`
6. CLI atomic transitions OPERATOR_REQUIRED → TX_HASHED; audit row with `actor='operator'`
7. Reconciler picks up TX_HASHED — NO-OP today (web3 not wired), sits there until PR-I.5 ships

### If Interpretation Y (precedent prohibition strict)
Karachi position sits at REDEEM_OPERATOR_REQUIRED indefinitely. USDC remains on-chain claimable (`clob.redeem` idempotent per KARACHI_5_17 §5). $0.59 unredeemed for N days/weeks until PR-I.5 properly ships. No precedent created. **This is the default if user doesn't explicitly authorize X.**

### Recommended structural fix (parallel to either choice, post-Karachi)
PR-I.5 itself is a 3-blocker problem (schema + signer + testnet). Decompose:
- **PR-I.5.a** add `winning_index_set` to `settlement_commands` schema + populate at enqueue time (depends on F23 migration framework — see WAVE-3.A)
- **PR-I.5.b** wire `PolymarketV2Adapter(signer_key=...)` via Keychain resolver
- **PR-I.5.c** sign-but-don't-broadcast test mode; gated mainnet first-run via env flag
- Total: 2-3 PRs over 1-2 weeks, properly antibody-tested

**WAVE-0 next action**: surface this fork to operator immediately. Code work HALTS pending decision.

---

## §3 WAVE-1 — K1 reader side completion (post-Karachi)

**Scope**: 37 BROKEN_FORECAST_READER files per `K1_READER_SWEEP.md`.

### WAVE-1.A — Tier 0 live runtime (highest priority)
- `src/main.py:1306-1308` — daemon boot smoke-test reads ghost settlements → masks empty forecasts.db on boot (`FIX_MAIN_BOOT_SMOKETEST.md` pending agent return)
- Any other `src/` BROKEN entries (sweep pending classification)

### WAVE-1.B — Operator-consequential scripts
- `scripts/bridge_oracle_to_calibration.py` (F40, ready per `FIX_K1_READERS.md`)
- `scripts/evaluate_calibration_transfer_oos.py` (F41, ready per `FIX_K1_READERS.md`)
- `scripts/data_chain_monitor.sh` (ops monitoring blind to forecast state)
- `scripts/build_correlation_matrix.py`
- `scripts/promote_platt_models_v2.py`, `scripts/promote_calibration_pairs_v2.py`, `scripts/generate_monthly_bounds.py`, `scripts/force_cycle_with_healthy_gates.py`

### WAVE-1.C — ETL batch (deferred priority)
- 26 `etl_*.py` files — most are world-class readers (OK_WORLD_ONLY); a subset that ATTACHes forecasts may need touchup. Defer until WAVE-1.A/B done.

### Joint antibody (covers ALL 37 + future regressions)
`tests/test_k1_reader_isolation.py` — scans `scripts/*.py` + `src/**/*.py`:
- If file uses `get_world_connection` OR hardcodes `state/zeus-world.db`
- AND file contains string-literal name of any forecast_class table (load from `architecture/db_table_ownership.yaml`)
- THEN FAIL with diagnostic + allowlist exception

PR sequencing: antibody-first (xfail, allowlist all current broken) → fix individual files (flip xfail to pass per file).

---

## §4 WAVE-2 — Lineage NULL family (F25 + F2 + F7)

Per `FIX_F25_DSI.md` + `FIX_SEV1_BUNDLE.md`.

### Order:
1. **F2** ship first (8 LOC, isolated, 100% lineage hemorrhage stop). Per `FIX_SEV1_BUNDLE.md` §F2.
2. **F25 Strategy R** (net -7 LOC, 31 sites + helper + dataclass `__post_init__`). Per `FIX_F25_DSI.md`. Karachi blast LOW; deploy between cycles.
3. **F7** (~20 LOC + DDL migration via F23 runner). Defer until WAVE-3 lands F23.

### Joint antibody
`tests/state/test_lineage_join_keys.py` — parametrized:
- For each (helper × FK column) pair, write row → assert FK non-null
- Covers F2, F7, F25 in one test file

Per `FIX_SEV1_BUNDLE.md` §"Cross-finding antibody opportunity".

---

## §5 WAVE-3 — Writer-lock + migration framework (F22 + F23 + F26 + F29 + F30)

### WAVE-3.A — Migration runner first (F23, foundational)
~60 LOC per `FIX_SEV1_BUNDLE.md` §F23. Unblocks F7 + F15 backfills.

### WAVE-3.B — Allowlist unification (F26, trivial)
One-line fix in `tests/conftest.py:177`: import `SQLITE_CONNECT_ALLOWLIST` from `src.state.db_writer_lock`, delete duplicate.

### WAVE-3.C — Operator script writer-lock contract (F22)
TOP-5 dangers per `OPS_FORENSICS.md`:
1. `scripts/migrations/202605_add_redeem_operator_required_state.py` — touches all 4 DBs no lock
2. `scripts/migrate_world_observations_to_forecasts.py` — no lock enforcement
3. `scripts/bridge_oracle_to_calibration.py` — (already in WAVE-1.B)
4. `scripts/cleanup_ghost_positions.py` — read-classify race outside lock
5. `scripts/force_cycle_with_healthy_gates.py` — suppresses bid-safety

CI antibody per `OPS_FORENSICS.md` §F22.

### WAVE-3.D — REVIEW_REQUIRED unique index exclusion (F29)
DDL via F23 runner: add `REDEEM_REVIEW_REQUIRED` to `ux_settlement_commands_active_condition_asset` exclusion list.

### WAVE-3.E — Migration header drift enforcement (F30)
Bundle into F23 CLI runner: refuses to apply migration files lacking `last_reviewed=` header.

---

## §6 WAVE-4 — Silent-data antibodies (F8 + F15 + F18 + F19 + F31)

### F8 (sentinel string `unknown_entered_at`)
Per `OPS_FORENSICS.md`:
- Line 658 fix: use `now` (already in scope)
- Line 808 quarantine: case-specific
- CHECK constraint `occurred_at LIKE '____-__-__T%' OR occurred_at = 'quarantine_sentinel'` (via F23 migration)
- Backfill 3 sentinel rows (including Karachi `c30f28a5-d4e`)

### F15 (settlements vs settlements_v2 1583-row gap)
Per `FIX_SEV1_BUNDLE.md`: one-time backfill migration (idempotent via UNIQUE constraint). Requires F23 runner.

### F18 (INSERT OR IGNORE silent loss)
Per `FIX_SEV1_BUNDLE.md`: log on zero-insert with non-empty inputs. ~10 LOC.

### F19/F31 (market_events_v2 3-DB shadow)
Likely subsumed by WAVE-1 K1 sweep (the 3-DB divergence is K1 archive artifact); revisit after WAVE-1 lands.

---

## §7 WAVE-5 — Deployment readiness gates (F32 + F33 + F35 + F39)

### F39 (cal-transfer-eval plist) — OPERATOR ACTION NOW
Per `OPS_FORENSICS.md` recommendation (A): `launchctl bootout gui/$(id -u)/com.zeus.calibration-transfer-eval` immediately. 5-second idempotent operation. Reverts itself when Phase B trigger conditions are met.

### F32+F35 (oracle bridge schedule)
Depends on F40 (WAVE-1.B). After F40 ships:
- Operator manually invokes bridge once to verify non-empty `oracle_error_rates.json` written
- Then add cron `5 10 * * * cd <zeus> && .venv/bin/python scripts/bridge_oracle_to_calibration.py`
- Add antibody: CI test that `scripts/oracle_snapshot_listener.py` and `scripts/bridge_oracle_to_calibration.py` both have cron entries OR neither does (paired-existence invariant)

### F33 (no escalation on persistent MISSING)
When `oracle_penalty.reload()` reports `0 records, 0 blacklisted` AND last successful non-empty reload >24h ago → escalate via RiskGuard Discord. Hook into existing notification path.

---

## §8 Parallel execution plan

Non-conflicting wave parallelism (per Karachi 7h window IF PR-I.5 GO, otherwise post-Karachi):

| Wave | Files touched | Conflict risk | Parallel-OK with |
|---|---|---|---|
| WAVE-0 PR-I.5 | `src/execution/polymarket_v2_adapter.py` + tests | LOW | nothing else (critical path) |
| WAVE-1.A main.py | `src/main.py` (boot section) | LOW | WAVE-2, WAVE-3, WAVE-5 |
| WAVE-1.B scripts | 5 scripts/ files | LOW | WAVE-2, WAVE-3 |
| WAVE-2 F2+F25 | `src/engine/evaluator.py` + `src/state/db.py` | MEDIUM | WAVE-1 (different files) |
| WAVE-3.A F23 | `scripts/migrations/__init__.py` + new CLI | LOW | WAVE-1, WAVE-2 |
| WAVE-3.B F26 | `tests/conftest.py` | LOW | everything |
| WAVE-3.C F22 | various operator scripts | LOW | WAVE-1.B (different scripts) |
| WAVE-3.D F29 | F23 migration file | depends F23.A | sequential after F23.A |
| WAVE-4 F8/F15/F18 | `src/state/chain_reconciliation.py`, migrations, `src/data/market_scanner.py` | LOW | WAVE-1, WAVE-2 |
| WAVE-5 F32/F33/F35 | depends F40 | sequential after WAVE-1.B | WAVE-2, WAVE-3 |
| WAVE-5 F39 | operator launchctl command | NO CODE | now |

**4 parallel executors safe** (different file domains): WAVE-1.A + WAVE-2 + WAVE-3.A + WAVE-4 F8.

---

## §9 Antibody charter (Fitz Constraint #3: immune system)

Each WAVE ships at least one antibody. Aggregate after all waves:

1. `tests/test_k1_reader_isolation.py` (WAVE-1) — K1 reader misrouting impossible
2. `tests/state/test_lineage_join_keys.py` (WAVE-2) — lineage NULL key impossible
3. `tests/test_operator_script_lock_contract.py` (WAVE-3.C) — operator script bypass impossible
4. `tests/test_migration_runner_idempotent.py` (WAVE-3.A) — migration replay impossible
5. `tests/test_position_events_occurred_at_iso.py` (WAVE-4) — non-ISO sentinel impossible (DB CHECK constraint)
6. `tests/test_market_scanner_zero_insert_alert.py` (WAVE-4) — silent IGNORE losses surfaced
7. `tests/test_settlements_v2_parity.py` (WAVE-4) — settlements_v2 lag impossible
8. `tests/test_oracle_deployment_readiness.py` (WAVE-5) — writer-without-schedule pattern impossible

Each is a CATEGORY-killer per Fitz Constraint #4.

---

## §10 Outstanding investigations (parallel-launchable)

- **PR_I5_WEB3_WIRE.md** (`a5d440510965fc3b5` in flight) — feasibility verdict for WAVE-0
- **FIX_MAIN_BOOT_SMOKETEST.md** (`aeebe8039aabe0587` in flight) — fix for `main.py:1306-1308`
- Heartbeat-sensor RED `daemon_dead` alert root cause (84 occurrences in `logs/heartbeat-sensor.err` — what daemon? bound to F33)
- F25 sentinel format choice: generic `<pre_snapshot:rejected>` vs stage-carrying `<pre_snapshot:MARKET_FILTER>` (open question in `FIX_F25_DSI.md` §9)
- F34 (maker-only entry pricing) policy decision — pure operator decision, no code investigation needed

---

## §11 PR sequencing (final)

```
NOW (parallel, Karachi 7h window):
  ├── WAVE-0  PR-I.5 web3 wire     ← CRITICAL PATH (if GO)
  ├── F39     launchctl bootout    ← operator 5s, idempotent
  └── (all other code work HALTS until PR-I.5 verdict)

POST-KARACHI (parallel waves):
  ├── WAVE-1.A  main.py boot         (1 file, 1 PR)
  ├── WAVE-1.B  scripts K1 repoint   (5 PRs OR one bundled)
  ├── WAVE-2.1  F2 selection NULL    (1 PR)
  ├── WAVE-3.A  F23 migration runner (1 PR, foundational)
  ├── WAVE-3.B  F26 allowlist dedup  (1 PR, trivial)
  └── WAVE-4.1  F8 sentinel fix      (1 PR)

POST-F23 (sequential):
  ├── WAVE-2.2  F25 Strategy R        ← 31-site mechanical (1 PR)
  ├── WAVE-2.3  F7 lineage gap        (1 PR with DDL)
  ├── WAVE-3.C  F22 operator scripts (1-3 PRs)
  ├── WAVE-3.D  F29 unique index     (1 PR with DDL)
  ├── WAVE-3.E  F30 header drift     (folded into F23 PR)
  ├── WAVE-4.2  F15 backfill         (1 PR with DDL)
  ├── WAVE-4.3  F18 IGNORE alerting  (1 PR)
  └── WAVE-5    F32/F33/F35 oracle   (1 PR, after F40 verified)
```

Total PR count: 12-15 (sized for solo or small-team review).

---

## §12 Reference files

| File | Authority |
|---|---|
| `FIX_F25_DSI.md` | F25 Strategy R fix-shape, Karachi-safe |
| `FIX_SEV1_BUNDLE.md` | F2/F7/F15/F18/F23 fix-shapes |
| `OPS_FORENSICS.md` | F8/F11/F22/F39 current state |
| `FIX_K1_READERS.md` | F40+F41 ship-ready diffs |
| `K1_READER_SWEEP.md` | All 106 K1 callers classified |
| `CASCADE_DRY_TRACE.md` | Karachi auto-cascade chain verified |
| `PR_I5_WEB3_WIRE.md` | (pending) WAVE-0 feasibility |
| `FIX_MAIN_BOOT_SMOKETEST.md` | (pending) WAVE-1.A fix |
