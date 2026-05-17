# RUN_7_findings.md — post-PR-126 audit (2026-05-17)

Audit skill: `.claude/skills/zeus-deep-alignment-audit/SKILL.md`
Operator instruction (Chinese, translated): "Update findings into a new file package; refer back to the previously-fixed-part files."
Worktree: `.claude/worktrees/zeus-deep-alignment-audit-skill` (branch `worktree-zeus-deep-alignment-audit-skill`)
Baseline: main HEAD `9259df3e9c` (Run #6 baseline was `acaae2c242`; +5 commits incl PR #130/#132/#133)
Skepticism stance: assume PR-126 reviewers missed something. (Per operator brief.)

## §1 Probe summary

| Focus area                                           | Outcome           | Evidence                                            |
|------------------------------------------------------|-------------------|-----------------------------------------------------|
| A: forecasts.db user_version sentinel still 3       | HELD              | sqlite PRAGMA + `src/state/db.py:2427`              |
| B: PR-126 file scope (src/execution/settlement_commands.py, src/main.py, src/state/db.py + migrations + tests) | confirmed | `git diff 83e3f0140d^1..83e3f0140d --name-only`     |
| C: Karachi position c30f28a5 phase                  | `day0_window` 1.5873 shares | settle_status column ABSENT (F4 fix held) |
| D: F22 sibling — raw sqlite3.connect in src/         | 5 src sites + new module | grep across `src/`; see §3.2                  |
| E: F24 sibling — NULL FK rates on fact tables        | **3 tables affected** | python sqlite scan; see §2.1                    |
| F: F23 — migration runner architecture                | bare, 1 script only | `ls scripts/migrations/`                        |
| G: new module `src/state/db_writer_lock.py` (749 LOC) | declarative-only allowlist, no enforcement here | grep + read; see §2.2 |
| H: PR-126 unique-index update for new state          | **NOT UPDATED**   | `.schema settlement_commands` + read of `db.py`     |
| I: settlement_commands row count                     | 0 (empty table)   | `SELECT state, COUNT(*) … GROUP BY state` returned no rows |

Skepticism finding (per operator brief): PR-126 added the REDEEM_OPERATOR_REQUIRED state but the UNIQUE INDEX `ux_settlement_commands_active_condition_asset` only excludes `REDEEM_CONFIRMED` and `REDEEM_FAILED`. Documented behavior is "REDEEM_OPERATOR_REQUIRED is NOT terminal" (`src/state/db.py:90`). Result: an operator-required row blocks any new INSERT for the same `(condition_id, market_id, payout_asset)` until an operator intervenes. This is **F27**.

## §2 New findings

### F25 — Triple-NULL systemic snapshot-write failure (SEV-0)

**Statement.** Three fact tables on `zeus_trades.db` show correlated high-NULL rates on snapshot/decision FK columns. This is structurally worse than Run #6's F24 (single-table observation):

| table                       | column                  | NULL count | total  | NULL pct |
|-----------------------------|-------------------------|------------|--------|----------|
| selection_hypothesis_fact   | decision_id             | 1518       | 1518   | **100.00%** |
| opportunity_fact            | snapshot_id             | 19175      | 28117  | **68.20%** |
| probability_trace_fact      | decision_snapshot_id    | 19175      | 28307  | **67.74%** |
| execution_fact              | decision_id             | 1          | 53     | 1.89% |

**Why SEV-0.** The `selection→opportunity→trace` causal lineage is permanently broken for the majority of rows since (at minimum) Run #6. The 19175 row match between opportunity_fact and probability_trace_fact strongly implies a single upstream write path failing to populate `snapshot_id` (one shared call site, two downstream INSERTs). Without snapshot/decision lineage you cannot:
- audit *why* a hypothesis was selected over alternatives,
- replay the decision tree for forensic analysis,
- attribute PnL back to a snapshot generation.

**Hypothesis on root cause.** Either (a) `snapshot_id` is generated post-INSERT and the back-fill is silently dropping, or (b) the writer path opens a connection that doesn't see `snapshot_id` in scope, or (c) the column was added later and pre-existing INSERTs use a literal-NULL placeholder pattern.

**Antibody.** Add CHECK `snapshot_id IS NOT NULL` after backfill, or `NOT NULL` migration with `_orphan_snapshot` sentinel. Add CI invariant: `SELECT COUNT(*) FROM opportunity_fact WHERE snapshot_id IS NULL` must be 0 for new rows.

**Probe.** `sqlite3 state/zeus_trades.db` + `PRAGMA table_info(opportunity_fact)` and the SUM(col IS NULL) scan in §1.

---

### F26 — Two-truth SQLITE_CONNECT_ALLOWLIST divergence (SEV-2)

**Statement.** Two parallel allowlists exist, with diverging content and diverging authority:

1. `src/state/db_writer_lock.py:575` — `SQLITE_CONNECT_ALLOWLIST: frozenset[str]` — 8 entries, declared "Allowlists (populated as Phase 1.y migrates callers)" — **purely declarative, no enforcement**.
2. `tests/conftest.py:177` — `_WLA_SQLITE_CONNECT_ALLOWLIST` — ~40 entries (src + scripts), **enforced by pytest gate at lines 327, 352, 391-396**.

**Divergence examples** (entry in CI gate, missing from production module):
- `src/state/collateral_ledger.py` — present in conftest as `singleton_persistent_conn`; absent from db_writer_lock.py
- `src/data/market_scanner.py` — present in conftest as `pending_track_a6`; absent from db_writer_lock.py
- `src/main.py` — present in conftest as `read_only_ro_uri`; absent from db_writer_lock.py
- ~30 `scripts/` entries enforced by conftest; absent from db_writer_lock.py

**Why SEV-2.** A developer reading `src/state/db_writer_lock.py` (the *intuitively authoritative* module for write-lock policy) would conclude `collateral_ledger.py` is *not* an allowed raw-connect site and might attempt a "fix" that breaks the singleton ledger conn (incident-class — touches money path). The CI gate is the real authority but lives in `tests/conftest.py` — an unintuitive location.

**Antibody.** Either (a) delete the production declarative copy and add a comment pointing to `tests/conftest.py:_WLA_SQLITE_CONNECT_ALLOWLIST`, or (b) keep production as the single source and have `conftest.py` import it. Two-source = drift surface that already drifted.

**Probe.** `grep -rn 'SQLITE_CONNECT_ALLOWLIST\|_WLA_SQLITE_CONNECT_ALLOWLIST' src/ tests/` + read both blocks.

---

### F27 — REDEEM_OPERATOR_REQUIRED unique-index lockout (SEV-1) — PR-126 review gap

**Statement.** PR #126 (cascade liveness fix, commit `83e3f0140d`) added the `REDEEM_OPERATOR_REQUIRED` state and routes commands into it when the auto-redemption path is exhausted (`src/execution/settlement_commands.py:413`). Documentation explicitly notes "REDEEM_OPERATOR_REQUIRED is NOT terminal" (`src/state/db.py:90`). However, the PR did **NOT** update:

```
CREATE UNIQUE INDEX ux_settlement_commands_active_condition_asset
  ON settlement_commands (condition_id, market_id, payout_asset)
  WHERE state NOT IN ('REDEEM_CONFIRMED','REDEEM_FAILED');
```

This index excludes only the two terminal states. An OPERATOR_REQUIRED row therefore counts as an "active" row and **permanently blocks** any further INSERT for the same `(condition_id, market_id, payout_asset)` triple until an operator manually transitions the row to a different state (presumably re-submission or void).

**Why SEV-1.** For Karachi 2026-05-17 (`condition_id=0xc5faddf…`, `position_id=c30f28a5…`, currently `day0_window` with 1.5873 shares), if the redemption flow triggers OPERATOR_REQUIRED mid-cascade and the auto-retry tries to create a new command, it will fail with UNIQUE constraint violation. The cascade-liveness test (`tests/test_redeem_cascade_liveness.py`) was added by PR-126 but tests the *state transition* not the *index-lockout edge case*.

**Possible operator intent.** Two readings:
- (A, restrictive — likely intended): An operator-required command MUST be resolved before any new automated attempt. Then the index is correct and `OPERATOR_REQUIRED` should be treated as a temporary terminal for the index's purpose. But this should be tested explicitly and documented.
- (B, permissive — possibly intended): Auto-retry should be allowed to bypass an OPERATOR_REQUIRED if certain conditions met. Then the index needs `('REDEEM_CONFIRMED','REDEEM_FAILED','REDEEM_OPERATOR_REQUIRED')`.

**Antibody.** (1) Add explicit test: attempt second INSERT for same (cond,market,asset) while one is in OPERATOR_REQUIRED — assert behavior matches operator intent. (2) Document the index intent in `architecture/cascade_liveness_contract.yaml`. (3) Pick reading A or B and codify.

**Probe.** `.schema settlement_commands` + read of `src/state/db.py:37-90`, `src/execution/settlement_commands.py:406-440`.

---

## §3 Baseline-shift sweep

### §3.1 New commits since Run #6 baseline (`acaae2c242` → `9259df3e9c`)
- PR #112 — STAGE_DB to prod calibration_v2 promotion script (`scripts/promote_calibration_v2_stage_to_prod.py`)
- PR #113 — rollout_mode env override + flip-mode operator CLI
- PR #114 — K1 forecast DB split + ATTACH index helper
- PR #130 — ref-authority docs phase (large AGENTS.md/architecture YAML refresh)
- PR #132 — feat(state): SQLite live-vs-bulk writer-lock structural fix Phase 0/0.5/1 core (`src/state/db_writer_lock.py` 749 LOC)
- PR #133 — feat(state): Track A.6 db_writer_lock daemon-path retrofit (closes #246)
- Misc: `verify_reality_contracts_2026-05-17.py` verifier script

### §3.2 F22 sibling sweep — raw `sqlite3.connect(` in `src/`
```
src/observability/status_summary.py:81   (RO; allowlisted)
src/state/db.py:146, 798                 (canonical shim)
src/state/db_writer_lock.py:574          (allowlist literal in comment)
src/state/collateral_ledger.py:121       (singleton conn; CI-allowlisted, prod allowlist MISSING — see F26)
src/ingest_main.py:849                   (RO; allowlisted)
src/riskguard/discord_alerts.py:167      (risk_state.db; allowlisted)
src/main.py:976, 998                     (boot RO; allowlisted)
src/data/market_scanner.py:610           (WRITE market_events_v2; allowlisted as pending_track_a6 — F22 stands)
src/control/cli/promote_entry_forecast.py:97 (RO; allowlisted)
```

### §3.3 F21 sibling — observation_instants writers
- `src/data/hourly_instants_append.py:229` (legacy v1 writer, per Run #6) — not touched in this baseline shift.
- `architecture/db_table_ownership.yaml` declares BOTH `observation_instants` and `observation_instants_v2` as `world_class` on `world` db. Neither is `legacy_archived`. So the "legacy" framing of F21 needs sharpening: both writers are still considered canonical-on-world. The cutover is incomplete by design.

### §3.4 F23 — migration runner state
`ls scripts/migrations/` returns:
- `__init__.py`
- `202605_add_redeem_operator_required_state.py`

No `_migrations_applied` ledger table; no TARGETS declaration; no idempotency check beyond what the single script implements internally. F23 stands unchanged.

## §4 LEARNINGS deltas to apply

(See LEARNINGS.md update applied alongside this run.)

- Category K (PR review skepticism): **CONFIRMED HIGH.** PR-126 reviewers missed the index-lockout edge case (F27). Continue surfacing PR-merge baseline shifts as primary probe targets.
- Category J (token economics; lazy load): **CONFIRMED ACTIVE.** Reading the old master index + Run #6 findings up front exhausted ~30% of context before any probe. Going forward: STATUS.md-only on first pass; load Run files only when narrative needed.
- New category L (proposed): **Two-truth allowlist/registry anti-pattern.** Any policy duplicated in `src/` and `tests/conftest.py` or `architecture/` will drift. Treat any parallel allowlist/registry as automatic SEV-2 surface.
- New category M (proposed): **Schema-vs-state-machine co-evolution.** When a PR adds a new enum/state value (`REDEEM_OPERATOR_REQUIRED`), it must also touch every index/constraint/trigger that conditions on that enum. Surface as audit probe.

## §5 Carry-forward closeout
- Old package master index gets a "SUPERSEDED" header pointing here.
- AUDIT_HISTORY.md gets a Run #7 row.
- LEARNINGS.md gets Run #6 + Run #7 deltas appended.
- Karachi 2026-05-17 settle-window: F27 is the live-trading-relevant risk. Operator should monitor `settlement_commands` for any REDEEM_OPERATOR_REQUIRED row appearing during the cascade and resolve immediately to avoid lockout.

## §6 Commit shape
```
audit(run-7): bootstrap post-PR-126 task package + 3 new findings (1 SEV-0, 2 SEV-1/2)

- F25 (SEV-0): triple-NULL snapshot lineage failure across 3 fact tables (100%+68%+68% NULL)
- F26 (SEV-2): two-truth SQLITE_CONNECT_ALLOWLIST divergence (src/ vs tests/conftest.py)
- F27 (SEV-1): PR-126 unique-index review gap — REDEEM_OPERATOR_REQUIRED blocks retries
- STATUS.md: F1-F24 carry-forward (F4/F14/F16/F17 FIXED; F2/F13/F24 worse or new-scope)
- FINDINGS_REFERENCE_v2.md: new master index for post-PR-126 era
- Old package gets SUPERSEDED header
- LEARNINGS.md: cat L (two-truth) + cat M (schema/state-machine co-evolution) proposed
- AUDIT_HISTORY.md: Run #7 row
```
