# WAVE-2 PLAN — 17 follow-up findings remediation

**Pre-condition**: PR #137 (WAVE-1) merged to main; audit worktree fetched + verified clean.
**Authority basis**: PR #137 PR body "NOT YET ADDRESSED" + Tasks #10/#22/#27/#33-49.
**Methodology**: Fitz Constraint #1 — find K structural decisions, not N patches. The 17 follow-ups collapse into **7 waves** by structural theme + dependency.

---

## Coverage matrix

| Wave | Tasks | Theme | Karachi live blast radius |
|---|---|---|---|
| WAVE-A | #35 F9, #47 F39, #46 F35, #10 RUN-12 | OPERATOR ACTIONS + verification (no code) | ZERO |
| WAVE-B | #27 F7-Position, #34 F6 | Lineage NULL completion (continues F2/F7/F25) | LOW (audit-only tables) |
| WAVE-C | #40 F21, #43 v1.F20, #38 F19(v2), #39 F20 | K1/dual-write cleanup (continues F40/F41/F42) | LOW (legacy table cleanup, additive registry) |
| WAVE-D | #37 F11(v2), #41 F22, #36 F10 | Concurrency / writer-lock infrastructure | MEDIUM (touches operator scripts daemon may run concurrently) |
| WAVE-E | #33 F3, #42 v1.F1, #22 F26 | Type-system safety + boot invariants | LOW-MEDIUM (boot-side asserts; type-level refactor of signal/calibration) |
| WAVE-F | #44 F33, #48 PR-I.5.b, #49 PR-I.5.c | Autonomous redeem (eliminates OPERATOR_REQUIRED) | LOW (signer wire is additive; web3 path opt-in via env) |
| WAVE-G | #45 F34 | Operator strategy policy (thin-book entry pricing) | OPERATOR DECISION — code change only after policy chosen |

---

## Dependency graph (must respect)

```
PR #137 (WAVE-1) merged
  ├─→ WAVE-A.F40-verify (manual bridge run; if non-empty artifact → unblock F35 cron)
  │     └─→ WAVE-A.F35 cron addition
  │
  ├─→ WAVE-B parallel (independent of A)
  │
  ├─→ WAVE-C
  │     ├─→ F21 (writer cleanup) FIRST
  │     │     └─→ v1.F20 (ensemble_snapshots DROP — requires zero readers)
  │     ├─→ F19(v2) (collateral schema register — independent)
  │     └─→ F20 (position_lots — independent)
  │
  ├─→ WAVE-D
  │     ├─→ F11(v2) BulkChunker (independent)
  │     ├─→ F22 operator scripts (5 scripts; each independent; bundle by category)
  │     └─→ F10 risk_state.db invariants (independent)
  │
  ├─→ WAVE-E
  │     ├─→ F3 unit-system (Type-system design; heavy)
  │     ├─→ v1.F1 boot assert wiring (5-line wire + env flag)
  │     └─→ F26 65-entry allowlist (audit + migrate)
  │
  ├─→ WAVE-F
  │     ├─→ PR-I.5.b signer wire (depends on Keychain integration)
  │     ├─→ F33 oracle escalation (depends on F35 cron live)
  │     └─→ PR-I.5.c web3 redeem (depends on PR-I.5.b)
  │
  └─→ WAVE-G F34 operator policy decision required FIRST; then code if chosen
```

---

## Worktree update protocol (when #137 merges)

```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus
git fetch origin main
# verify #137 commits in main
git log --oneline origin/main | head -5

# audit worktree branch (this worktree) — pull only docs updates if any
cd .claude/worktrees/zeus-deep-alignment-audit-skill
git pull --rebase origin worktree-zeus-deep-alignment-audit-skill  # if remote exists
# OR no-op if local-only

# remove old fix worktrees (now merged via #137)
cd /Users/leofitz/.openclaw/workspace-venus/zeus
for slug in migration-runner-2026-05-17 lineage-null-family-2026-05-17 \
           k1-reader-sweep-2026-05-17 sentinel-and-insert-ignore-2026-05-17 \
           worktree-hook-2026-05-17 lineage-command-id-f7-2026-05-17 \
           migrations-bundle-f8check-f15-f29-2026-05-17 k1-readers-batch-2-2026-05-17 \
           pr-i5a-winning-index-set-2026-05-17 post-karachi-remediation-wave-2026-05-17; do
  git worktree remove ".claude/worktrees/fix-${slug}" --force 2>/dev/null
  git branch -D "fix/${slug}" 2>/dev/null
done

# create new fix worktrees off updated origin/main per WAVE
git worktree add -b fix/wave-b-lineage-completion-2026-05-17 .claude/worktrees/fix-wave-b-lineage-completion-2026-05-17 origin/main
# ... etc per wave
```

---

## WAVE-A — Operator actions + verification (NO CODE)

| Task | Action | Verification |
|---|---|---|
| #46 F35 | After #137 merged, manually run `bridge_oracle_to_calibration.py --dry-run` → expect non-zero cities (vs prior 0-stub). If pass, add cron `5 10 * * *` to `/Users/leofitz/.openclaw/cron/jobs.json`. | `ls -la data/oracle_error_rates.json` shows mtime < 25h; `grep oracle_penalty_reloaded logs/zeus-live.log` shows non-empty reload after next 15-min cycle. |
| #47 F39 | `launchctl bootout gui/$(id -u)/com.zeus.calibration-transfer-eval` | `launchctl list \| grep calibration-transfer-eval` shows nothing. |
| #35 F9 | `.venv/bin/python scripts/promote_calibration_v2_stage_to_prod.py` (operator-attended) | `sqlite3 -readonly state/zeus-forecasts.db "SELECT COUNT(*) FROM calibration_pairs_v2 WHERE authority='VERIFIED'"` shows non-zero. |
| #10 RUN-12 | After Karachi or next position hits `settled` phase, verify `settlement_commands` table gets a row. | `sqlite3 -readonly state/zeus_trades.db "SELECT COUNT(*) FROM settlement_commands"` > 0. |

**Karachi safety**: zero — these are operator commands; daemon picks up changes on next reload cycle without restart.

---

## WAVE-B — Lineage NULL family completion

### #27 F7-follow-up Position dataclass
- **Discovery (EXECUTOR-F)**: `c30f28a5-d4e:exit decision_id=NULL` is structural — `Position` dataclass has no `decision_id` field, so `log_exit_lifecycle_event(pos)` cannot forward.
- **Fix shape**: 
  1. Add `decision_id: str | None = None` to Position dataclass (`src/state/positions.py`?)
  2. Thread from `EdgeDecision` → entry order → Position constructor
  3. `log_exit_lifecycle_event` passes Position.decision_id to log_execution_fact
- **Antibody**: parametrized test asserting exit-side execution_fact rows have non-NULL decision_id when entry-side had one.

### #34 F6 candidate_fact orphan rows
- **Probe first**: `sqlite3 -readonly state/zeus_trades.db "SELECT COUNT(*) FROM candidate_fact WHERE decision_id IS NULL OR snapshot_id IS NULL"`
- **Fix shape**: similar to F2 — find caller that omits the kwarg, thread it.
- Likely 1-line fix in src/selection/

**LOC est**: F7-follow-up ~30 LOC + F6 ~10 LOC. Karachi blast: LOW.

---

## WAVE-C — K1/dual-write cleanup

### #40 F21 legacy observation_instants writer
- **Probe**: `grep -n "INSERT.*INTO observation_instants[^_]" src/data/hourly_instants_append.py:229`
- **Options**: (a) delete the legacy write after confirming `observation_instants_v2_writer.py` covers same surface; (b) dual-write to v2 in same function
- **Recommended (a)**: stop legacy writes entirely; v2 is canonical
- **Antibody**: CI test that `INSERT INTO observation_instants` only occurs in backfill scripts (not live writers)

### #43 v1.F20 ensemble_snapshots DROP (zeus-world.db)
- **Depends on**: #40 F21 lands first (verify no readers query legacy table)
- **Migration**: `scripts/migrations/202605_drop_world_ensemble_snapshots.py` — DROP TABLE after `grep -rln "ensemble_snapshots[^_]" src/ scripts/` confirms zero readers
- **Karachi safety**: 116 dead rows; no reader; safe.

### #38 F19(v2) collateral ledger schema register
- **Fix**: add `risk_state.db.collateral_ledger` to `architecture/db_table_ownership.yaml` with `schema_class: risk_class`
- **Antibody**: extend `test_table_registry_coherence.py` to assert risk_state.db tables registered.

### #39 F20 position_lots reconciliation
- **Investigation first**: what's the divergence between position_lots and on-chain position?
- Probe: `sqlite3 ... "SELECT position_id, COUNT(*) FROM position_lots GROUP BY position_id"` vs `position_current.shares`
- **Fix**: likely reconciliation helper + invariant test
- Defer if probe shows zero divergence

**LOC est**: F21 ~20, v1.F20 ~30 (migration), F19 ~5 (yaml + test), F20 ~50 (helper + test). Karachi blast: LOW.

---

## WAVE-D — Concurrency / writer-lock

### #37 F11(v2) BulkChunker LIVE chunk boundary
- **Issue**: BulkChunker yields chunks to writer; readers see chunks ONLY at boundary. Intra-chunk staleness window.
- **Fix shape**: WAL checkpoint after each chunk OR explicit COMMIT per chunk; trade off vs throughput.
- **Antibody**: stress test with concurrent reader; assert at-most-1-chunk-behind invariant
- HEAVY work — operator may defer.

### NIT1 (from PR #137 code-review) — legacy migration interface refactor
- **Carry-forward**: `scripts/migrations/202605_add_redeem_operator_required_state.py` uses legacy `_migrate_one_db`/`main(argv)` interface; runner sidesteps via `_BOOTSTRAP_APPLIED` bootstrap allowlist. Two-style ecosystem is a footgun for future migration authors.
- **Fix shape**: refactor legacy migration to expose `def up(conn)` matching the F23 runner contract; drop bootstrap allowlist entry; add CI scan asserting every `scripts/migrations/*.py` defines `up` at module level.
- **Bundle with**: WAVE-D F22 operator-script writer-lock work (entry-point file already in F22's TOP-5 scope at item #1).
- **LOC**: ~40 (refactor + CI scan).

### #41 F22 operator script writer-lock contract (5 scripts)
- **Per OPS_FORENSICS TOP-5**:
  1. `migrations/202605_add_redeem_operator_required_state.py` — touches 4 DBs no lock → wrap each in `db_writer_lock` context
  2. `migrate_world_observations_to_forecasts.py` — no lock → wrap
  3. `bridge_oracle_to_calibration.py` — already K1-fixed in #137; ADD writer-lock here
  4. `cleanup_ghost_positions.py` — has lock but read-classify race outside → move classify INSIDE lock window
  5. `force_cycle_with_healthy_gates.py` — has lock but writes control_overrides → audit-only fix (document precondition)
- **Antibody**: CI scan per OPS_FORENSICS — `tests/test_operator_script_lock_contract.py`

### #36 F10 risk_state.db separate-process drift
- **Fix shape**: assert at risk_state.db reader-side: collateral_snapshots.captured_at within freshness window of zeus_trades.db position_current.updated_at
- **Antibody**: invariant test cross-DB

**LOC est**: F11(v2) ~80 + tests, F22 ~50 × 5 scripts, F10 ~30. Karachi blast: MEDIUM (operator scripts daemon-concurrent). Deploy between cycles.

---

## WAVE-E — Type system + boot invariants

### #33 F3 unit-system co-mingling (HEAVY — Fitz Constraint #1)
- **Goal**: make °C/°F mixing TYPE-IMPOSSIBLE — `TemperatureC` and `TemperatureF` distinct types with no implicit conversion
- **Touch points**: src/signal/, src/calibration/, ensemble snapshot interfaces
- **Strategy**: introduce typing wrappers + `mypy --strict` on those modules; refactor incrementally
- DEEP work — likely its own PR with multiple commits.

### #42 v1.F1 assert_db_matches_registry boot wiring
- **Fix**: 5-line addition to `src/main.py:1134-1138` calling `assert_db_matches_registry(world_conn, DBIdentity.WORLD)` + `(trade_conn, DBIdentity.TRADES)` guarded by `ZEUS_BOOT_REGISTRY_ASSERT_ENABLED` env (default true after shadow run)
- 8 tests at `tests/state/test_table_registry_coherence.py` already exercise the helper

### #22 F26 65-entry SQLITE_CONNECT_ALLOWLIST migration
- **Investigation first**: audit each of 74 `_WLA_SQLITE_CONNECT_ALLOWLIST` entries in `tests/conftest.py`. Classify: legitimate writer-lock-aware (move to source-of-truth) vs migration-debt (legacy, file for removal).
- **Migration**: move 65 legitimate entries into `src.state.db_writer_lock.SQLITE_CONNECT_ALLOWLIST` with rationale tags; then conftest can become thin import.
- HEAVY operator review; can chunk into batches (10-15 entries per PR).

**LOC est**: F3 deep (~hundreds + tests), v1.F1 ~5, F26 ~70 (entries + rationale comments). Karachi blast: LOW-MEDIUM (boot asserts could refuse start; F3 affects sizing math).

---

## WAVE-F — Autonomous redeem (PR-I.5)

### #48 PR-I.5.b PolymarketV2Adapter signer wire
- Integrate `bin/keychain_resolver.py` to retrieve private key from macOS Keychain
- Initialize web3 client + signer at adapter ctor
- Antibody: assert signer initialization at boot with operator-confirmation flag

### #44 F33 oracle escalation on persistent MISSING
- Hook into RiskGuard Discord notify path
- Trigger: `oracle_penalty.reload()` reports `0 records` AND last-non-empty > 24h
- Debounce: at most 1 page per hour
- **Depends on**: F35 cron landing first (otherwise persistent MISSING is the natural state)

### #49 PR-I.5.c web3 eth_signTransaction redeem
- Implement `PolymarketV2Adapter.redeem(condition_id, winning_index_set)` via real web3 call
- Sign-but-don't-broadcast mode first (env-gated: `ZEUS_REDEEM_WEB3_BROADCAST=false` default)
- Once verified: flip default to true
- **Depends on**: PR-I.5.a (column in #137) + PR-I.5.b (signer wired)

**LOC est**: PR-I.5.b ~80 + tests, F33 ~30, PR-I.5.c ~150 + integration test. Karachi blast: LOW (opt-in via env until operator flips). 

**Net effect**: Karachi-style positions auto-redeem without REDEEM_OPERATOR_REQUIRED → eliminates the "first live order manual completion precedent" risk forever.

---

## WAVE-G — F34 operator strategy decision

Pure policy question — code change only AFTER operator answers:
> Design intent = "never pay taker fee" OR "minimize cost-of-fill including opportunity cost"?

Current 89% non-fill rate suggests current design over-rotates to first interpretation. Defer code to operator decision turn.

---

## Execution sequence

Once #137 merges:
1. **Day 0**: Update worktrees + WAVE-A operator actions (5 minutes total). Verify F40 in live → unblock F35.
2. **Day 1-3**: WAVE-B + WAVE-C in parallel (independent file domains). 2-3 parallel sonnet executors. Phase critic at end.
3. **Day 3-7**: WAVE-D + WAVE-E.boot-wire + WAVE-E.F26 in parallel. F3 (unit-system) gets dedicated track with opus design first.
4. **Day 7-14**: WAVE-F (autonomous redeem). Critical path; opus design + sonnet implementation.
5. **Operator-gated**: WAVE-G F34 (whenever operator answers).

Each wave: ≤5 parallel executors, phase critic at close, antibody coverage required. PR sizing per `feedback_pr_unit_of_work_not_loc`: bundle coherent units.

---

## Antibody surface inventory (post-WAVE-2)

After all waves land, antibody coverage should include:
- Type-level °C/°F separation (WAVE-E F3)
- Boot registry coherence (WAVE-E v1.F1)
- BulkChunker visibility invariant (WAVE-D F11)
- Operator-script writer-lock CI scan (WAVE-D F22)
- Cross-DB freshness invariants (WAVE-D F10)
- Position dataclass decision_id propagation (WAVE-B F7-follow)
- Legacy-writer absence in live paths (WAVE-C F21)
- Autonomous redeem dry-run contract (WAVE-F PR-I.5.c)
- Oracle escalation debouncer (WAVE-F F33)

Total antibodies post-WAVE-2: ~13 new + 13 from WAVE-1 = **26 antibodies** covering distinct bug categories.
