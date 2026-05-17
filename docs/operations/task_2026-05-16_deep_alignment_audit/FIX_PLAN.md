# Zeus Deep Alignment Audit — FIX_PLAN

**Authority anchor**: [FINDINGS_REFERENCE.md](FINDINGS_REFERENCE.md) (20 findings, 5 audit runs) + [POST_K1_DELTA.md](POST_K1_DELTA.md) (post-main-merge reverification, 2026-05-16).
**Branch state**: `worktree-zeus-deep-alignment-audit-skill` merged main @ `a924766c8a` on 2026-05-16; zero code conflicts (audit branch is doc-only).
**Date**: 2026-05-16
**Karachi 5/17 settlement**: T-24h at draft time.

---

## §1 — Karachi 5/17: PATH A LOCKED (operator policy 2026-05-16)

**Single Karachi-blocking finding**: F14 SEV-0 — `submit_redeem` defined at `src/execution/settlement_commands.py:310` has ZERO production callers. Cascade halts at `REDEEM_INTENT_CREATED`; auto-redeem never fires. Same for `reconcile_pending_redeems`. Capital exposure: $0.59. **Precedent exposure: the cascade contract for every future order.**

> CHOSEN PATH: **A — ship PR-I before T-0 (2026-05-17 12:00 UTC)**
> DECIDED AT: 2026-05-16 by operator (Fitz)
> POLICY ANCHOR: this is the first live smoke-test-passing order in Zeus history. It MUST complete programmatically per the designed cascade. Manual completion would set a precedent that "small enough = manual is fine" and permanently mask cascade-liveness defects (F14, F16) on every future order. Capital cap is irrelevant; precedent cost is the dominant cost.

### §1.1 Why the prior default flipped

Earlier draft had Path B (manual fallback) as default, citing $0.59 exposure + <24h window + no template. That analysis valued capital, not contract. Operator policy correctly reframes:

- **What's actually at stake**: not $0.59. The system's first programmatic settlement. If this one completes manually, the cascade-liveness antibody (F14 fix) is never forced into existence. Every future redeem inherits "manual rescue is available", which means F14 + F16 + the entire Cat-C/Cat-K cascade-liveness category stays open indefinitely.
- **Antibody framing (Fitz Methodology #1, #3)**: Path A is the only path that makes the F14 category permanently impossible. Path B is whack-a-mole — the same defect re-fires on every subsequent settlement until it's fixed reactively under fire.
- **Risk re-weight**: SEV-0 surface still applies. Mitigation = opus SCAFFOLD + opus critic + executor in that order, with hard gate at SCAFFOLD-pass-on-first-round per `feedback_opus_critic_on_architectural_scaffold_4_for_4_roi.md`. If SCAFFOLD fails first-round critic, fall back to Path C (PR-I best-effort + manual fallback armed), NOT Path B.

### §1.2 Path A execution gates

| Gate | Window | Owner | Pass criterion | Fail action |
|---|---|---|---|---|
| G1. SCAFFOLD draft | T-24h → T-20h | opus executor | Single architectural pass: APScheduler job IDs registered, `submit_redeem` / `reconcile_pending_redeems` polling spec, idempotency proof against `state='REDEEM_TX_HASHED'` retries, smoke test pseudocode against `c30f28a5` | Re-draft once; second fail → Path C |
| G2. SCAFFOLD critic | T-20h → T-18h | opus critic | All Tier-0 critic items pass per `feedback_opus_critic_on_architectural_scaffold_4_for_4_roi.md` (no SEV-1 issues raised); cascade-liveness antibody test stated explicitly | Re-draft from critic notes; if SCAFFOLD still fails → Path C |
| G3. Code | T-18h → T-10h | sonnet executor (per critic spec) | Implementation matches SCAFFOLD; all named tests green; scoped suite green | Diagnose; if root-cause is SCAFFOLD gap → escalate back to G1 |
| G4. Smoke | T-10h → T-8h | operator | Forced-state test: insert synthetic `REDEEM_INTENT_CREATED` row in test DB, run scheduler tick, assert state transitions to `REDEEM_TX_HASHED` within job interval | Diagnose; if structural → Path C |
| G5. Ship | T-8h → T-6h | operator | Merge PR-I to main; restart trading daemon; tail `logs/zeus-live.err` for 30 min for unexpected errors | Roll back if any error trace; arm manual fallback as Path C |
| G6. T-0 | 2026-05-17 12:00 | live system | Polymarket endDate; cascade runs autonomously per design | T+1h to T+9h: monitor per §8; if cascade silent at T+9h, manual fallback armed |

**Path C (hybrid)** is the ONLY sanctioned fallback path if any gate G1-G5 fails. Manual fallback (the prior Path B) becomes the strict failure mode under Path C — never a routine alternative.

---

## §2 — K-themes view (structural decisions, K=8)

Per Fitz Methodology Constraint #1 (`~/.claude/CLAUDE.md`): 20 findings collapse to ~8 structural decisions. Each PR ships an **antibody** (test/type/structural change that makes the category permanently impossible), not just a patch.

| Theme | Antibody (the structural decision) | Findings | PRs |
|---|---|---|---|
| **T1. Registry-deployed antibody** | `assert_db_matches_registry(conn, DBIdentity)` wired fail-closed at every boot; CI assertion every `db:X` table has no schema on other DBs. Audit-time vs runtime parity becomes machine-checked. | F1, F12, F19, F20 | PR-E (F1), PR-F (F12+F19+F20) |
| **T2. Cascade liveness contract** | Every state-machine table with `*_INTENT_CREATED` rows MUST have an APScheduler poller registered; smoke test asserts job-id existence; intent rows older than N min raise. | F14, F16 | PR-I (F14), PR-K (F16) |
| **T3. Lineage join-keys enforced** | Writer kwargs that are FK in downstream joins become positional-required; `ValueError` on falsy; parametrized regression over all lineage tables. | F2, F8 | PR-A (F2), PR-G (F8) |
| **T4. Harvester correctness** | Explicit `tag=weather` filter + bounded retry + integration test injecting non-weather market to confirm filter. City-matching is fortuitous, not antibody. | F4 residue, F7 | PR-A (F4), PR-C (F7) |
| **T5. Alarm channel coherence** | sensor severity → dispatcher channel: every RED escalation has a deterministic propagation rule; ≥3-consecutive-RED → push (APNs/Discord) regardless of dispatcher classification; plist or cron is canonical (not both); CI asserts every `com.zeus.*.plist` has `KeepAlive=true` OR matching cron. | F10, F11 | PR-D |
| **T6. Operational visibility** | `.log` / `.err` plist convention documented + enforced; `database is locked` storm metric exported via heartbeat; latent CollateralLedger raw-connect path routed through `get_trade_connection()` (WAL + busy_timeout). | F5, F6 | PR-B (F5), PR-E (F6) |
| **T7. Shadow-table sweep** | Every duplicated `X` vs `X_v2` reconciled or one side dropped; CI assert `count(X) == count(X_v2)` for live-canonical pairs; deny new shadow tables in registry. | F15, F18 | PR-J (F15), PR-M (F18) |
| **T8. Doctrine freshness** | `current_state.md` / `current_data_state.md` machine-anchored to HEAD + DB row counts; `tests/test_doctrine_freshness.py` fails if >14d stale or counts >5% drift. | F3, F13 (cosmetic) | PR-G (F3), PR-H (F13) |

**Plus 1 FALSE-POSITIVE**: F9 WU_API_KEY (operator override 2026-05-16, see `RUN_3_findings.md`). No PR.

---

## §3 — Branch hygiene (critical, easy to mis-execute)

**This audit branch (`worktree-zeus-deep-alignment-audit-skill`) is the DOC CARRIER, not the integration line.** It holds `FINDINGS_REFERENCE.md`, `POST_K1_DELTA.md`, this `FIX_PLAN.md`, deep-dive markdowns, the audit skill, and the merge of main. **It does not ship code fixes.**

Each PR cuts a **fresh branch from `main`**:

```
main
 ├── fix/audit-pr-a-data-quality-backfill        (F2 + F4 residue)
 ├── fix/audit-pr-b-collateral-ledger-wal        (F5 latent)
 ├── fix/audit-pr-c-harvester-tag-filter         (F7)
 ├── fix/audit-pr-d-alarm-channel-bridge         (F10 + F11)
 ├── fix/audit-pr-e-boot-antibody-wiring         (F1 + F6)
 ├── fix/audit-pr-f-ghost-table-sweep            (F12 + F19 + F20)
 ├── fix/audit-pr-g-doctrine-refresh             (F3 + F8)
 ├── fix/audit-pr-h-replay-honesty               (F13)
 ├── fix/audit-pr-i-redeem-cascade               (F14, IF Path A/C)
 ├── fix/audit-pr-j-settlements-reconcile        (F15)
 ├── fix/audit-pr-k-wrap-unwrap-decision         (F16)
 ├── fix/audit-pr-l-calibration-transfer-trap    (F17)
 └── fix/audit-pr-m-observation-instants-sweep   (F18)
```

PR description on each cites: (a) finding number, (b) link to FINDINGS_REFERENCE row, (c) antibody claim, (d) test name asserting category-immunity.

**Per `feedback_accumulate_changes_before_pr_open.md`**: each branch accumulates until its antibody test is green + scoped suite passes. No `gh pr create` before that.

---

## §4 — PR sequencing (dependency + risk + Karachi)

### §4.1 Pre-Karachi must-ship (Path A locked, §1)
- **PR-I** (F14 redeem cascade) — MANDATORY before T-0. Opus SCAFFOLD → opus critic → sonnet executor → smoke test against `c30f28a5` staged condition → live merge by T-8h. Gates G1-G6 in §1.2 are the only sanctioned execution path. **No other PR is started until PR-I clears G5 or escalates to Path C.**

### §4.2 Wave 1 — independent, ship in parallel after Karachi window closes
Order by Tier (Tier-0 first to clear critic-budget early in the wave):

| PR | Tier | Critic | Opens dependency | Why first |
|---|---|---|---|---|
| **PR-B** | T0 (`src/state/`) | opus SCAFFOLD | — | Latent SEV-2; structural-decision T6 (operational visibility) seed |
| **PR-F** | T0 (migrations on `state/zeus-world.db`) | opus SCAFFOLD | unlocks PR-A residue tightening | Ghost-table sweep is registry-deployed-antibody seed |
| **PR-I** | T0 (`src/execution/`) — IF deferred | opus SCAFFOLD | — | If Path B, this lands first post-Karachi |
| **PR-C** | T1 (`src/ingest/`) | sonnet SCAFFOLD | — | T4 (harvester correctness) — tag=weather + regression |
| **PR-A** | T1 (data cleanup) | sonnet SCAFFOLD | dep on PR-F migration helper | Residue purge after PR-F lands the migration pattern |

### §4.3 Wave 2 — depends on Wave 1 antibodies
| PR | Tier | Critic | Depends on | Note |
|---|---|---|---|---|
| **PR-E** | T0 (boot wiring) | opus SCAFFOLD | PR-F (registry must be self-consistent first) | Lands K1 P5 promised wiring (P3/P4 deferred — see POST_K1_DELTA.md §F1) |
| **PR-D** | T1 (ops/launchd) | sonnet SCAFFOLD | independent | Synthetic-RED injection test required |
| **PR-J** | T0 (migration on `state/zeus-forecasts.db`) | opus SCAFFOLD | independent | Pick reconcile direction; `_v2` reader switch is the decision |
| **PR-M** | T0 (migration on `state/zeus-world.db`) | opus SCAFFOLD | independent | Larger row delta (929k); audit readers exhaustively first |

### §4.4 Wave 3 — low-risk doc + cosmetic + decisions
| PR | Tier | Critic | Depends on | Note |
|---|---|---|---|---|
| **PR-K** | T1 (`src/execution/wrap_unwrap_commands.py`) | sonnet SCAFFOLD | Karachi cleared | Decision: delete (Z4-deferred) or wire poller (Z5-planned) |
| **PR-L** | T1 (calibration scheduler) | sonnet SCAFFOLD | independent | Wire `evaluate_calibration_transfer_oos.py` to APScheduler + flag preflight |
| **PR-G** | T2 (docs + minor data) | sonnet review | — | Doctrine rewrite + sentinel timestamp rejection |
| **PR-H** | T3 (docstring + traceability) | sonnet review | — | Cosmetic unless consumer found |

---

## §5 — Per-PR brief

Each PR follows the same shape:

```
TIER: T0 / T1 / T2 / T3
THEME: T1..T8 from §2
ANTIBODY: <the test/type/structural change that makes category impossible>
FILES: <source + test paths>
CRITIC: opus SCAFFOLD / sonnet SCAFFOLD / sonnet review
EXECUTOR MODEL: opus (Tier-0) / sonnet (Tier-1/2/3)
TEST: <pytest path + assertion name> — REQUIRED before commit
ACCEPTANCE GATE: zeus-deep-alignment-audit skill re-run scoped to <category>;
                 finding-count delta confirms category-immunity, not patch
BRANCH: cut from main
```

Below: per-PR antibody and scope. Finding-level evidence (file:line) is in [FINDINGS_REFERENCE.md](FINDINGS_REFERENCE.md). Don't re-derive — cite.

### PR-I — F14 redeem cascade (Path A/C only; else Wave 2 default)
- **Antibody**: APScheduler job registration test asserts `submit_redeem_poller` + `reconcile_pending_redeems_poller` IDs exist; integration test pushes `REDEEM_INTENT_CREATED` row and asserts state transitions through to `REDEEMED` within job-interval-bound seconds.
- **Files**: `src/main.py` (job registration), `src/execution/settlement_commands.py` (no change to behavior), `tests/test_redeem_cascade_liveness.py` (new), `KARACHI_2026_05_17_MANUAL_FALLBACK.md` (correct §1 description).
- **Critic**: opus SCAFFOLD pre-code; opus critic post-implementation per `feedback_opus_critic_on_architectural_scaffold_4_for_4_roi.md`.
- **Risk**: Karachi window proximity. Path A requires SCAFFOLD-pass first round.

### PR-B — F5 CollateralLedger WAL
- **Antibody**: All `CollateralLedger.__init__(db_path=...)` paths route through `get_trade_connection(write_class="live")` (already sets WAL + busy_timeout). Test: `tests/test_collateral_ledger_wal.py` asserts `PRAGMA journal_mode == "wal"` and `PRAGMA busy_timeout > 0` on persistent-mode ledger.
- **Files**: `src/state/collateral_ledger.py:160-169`, `tests/test_collateral_ledger_wal.py` (new).
- **Critic**: opus SCAFFOLD (T0 + Tier-0 file).
- **Note**: Lock storm symptom gone post-K1 (last occurrence 2026-05-14 07:06). This PR closes the latent vulnerability, not an active fire.

### PR-F — F12+F19+F20 ghost-table sweep
- **Antibody**: `tests/state/test_db_table_ownership_machine_check.py` extends `assert_db_matches_registry()` to assert: each `db:trades` table has no schema on `world.db` or `forecasts.db`; each `db:forecasts` table has no schema on `world.db` or `zeus_trades.db`. Migration: `scripts/migrations/202605_drop_world_trade_lifecycle_tables.py` + `..._drop_world_ensemble_snapshots.py` + `..._drop_zeus_trades_market_events_v2.py`. Each fails loud if `row_count > 0`.
- **Files**: `architecture/db_table_ownership.yaml` (tighten), `src/state/table_registry.py` (extend assertion), 3 migration scripts, 1 test.
- **Critic**: opus SCAFFOLD (Tier-0 truth-owning DBs).
- **Dependency**: prereq for PR-A (uses same migration pattern) and PR-E (registry self-consistent before boot wiring).

### PR-A — F2 + F4 residue
- **F2 antibody**: `log_selection_hypothesis_fact(decision_id=...)` positional-required; `ValueError` on falsy. `tests/state/test_lineage_join_keys.py` parametrized over 5 lineage tables × keys.
- **F4 antibody**: Migration drops 2,112 stranded rows on `world.market_events_v2` (writer already corrected on main per K1 fix). CI smoke asserts `world.market_events_v2` count stays 0 over 24h.
- **Files**: `src/engine/evaluator.py:1535-1561`, `src/state/db.py:5314`, `scripts/migrations/202605_purge_world_market_events_v2_residue.py`, `tests/state/test_lineage_join_keys.py`, `tests/state/test_no_world_market_events_v2_growth.py`.
- **Critic**: sonnet SCAFFOLD (Tier-1 + lineage-test pattern is well-established).
- **Note**: 693 historical NULL `decision_id` rows are unrecoverable — document in `current_data_state.md` as one-time historical hole.

### PR-C — F7 harvester tag filter
- **Antibody**: Explicit `tag=weather` filter at category source; integration test injects a fake non-weather event with a city-name in title (e.g. "Will NYC pass congestion pricing?") and asserts harvester skips it. City-matching alone proven insufficient (advisor verification 2026-05-16).
- **Files**: `src/ingest/harvester_truth_writer.py:670-720`, `src/data/market_scanner.py` (filter at fetch), `tests/test_harvester_tag_filter.py` (new).
- **Critic**: sonnet SCAFFOLD.

### PR-D — F10 + F11 alarm channel
- **Antibody**: `tests/test_heartbeat_channel_coherence.py` injects 3-consecutive synthetic RED ticks and asserts (a) dispatcher classifies RED (not degraded), (b) push notification dispatched (APNs/Discord mock asserted called), (c) launchd plist parity test asserts every `com.zeus.*.plist` has `KeepAlive=true` OR matching cron line. F11 decides: rename plist `.disabled` + document cron as canonical, OR add `KeepAlive=true` + remove cron.
- **Files**: `scripts/heartbeat_dispatcher.py`, `tests/test_heartbeat_channel_coherence.py`, `tests/test_launchd_plists.py`, plist files.
- **Critic**: sonnet SCAFFOLD.
- **Karachi note**: PR-D protects against operator-blind during the 5/17 settlement window. If Path B, this is post-Karachi but still highest-priority for the next live event.

### PR-E — F1 + F6
- **F1 antibody**: Wire `assert_db_matches_registry(world_conn, DBIdentity.WORLD)` + same for FORECASTS at boot in `src/main.py` between `init_schema(trade_conn)` and `_startup_world_schema_ready_check()`. Fail-closed. This is the **K1 followups P5-deferred wiring** (P3 and P4 both deferred per `task_2026-05-14_k1_followups/P3_IMPLEMENTATION_REPORT.md:108` and `P4:167-171`).
- **F6 antibody**: Plist `StandardOutPath` and `StandardErrorPath` documented in `docs/operations/launchd_logging.md`; CI asserts every launchd plist routes to a `.log` AND `.err` pair OR a merged path.
- **Files**: `src/main.py:860-863`, `src/ingest_main.py` (boot path), `docs/operations/launchd_logging.md`, `tests/test_launchd_logging_paths.py`, `tests/state/test_boot_registry_assertion.py`.
- **Critic**: opus SCAFFOLD (boot path is Tier-0).
- **Dependency**: PR-F (registry tightening) must land first so the boot assertion finds a clean registry.

### PR-J — F15 settlements reconcile
- **Decision in SCAFFOLD**: (a) drop `settlements_v2` + document `settlements` as canonical, OR (b) backfill `_v2` from `settlements` and switch reader.
- **Antibody**: CI assert `count(settlements) == count(settlements_v2)` (if both kept); reader-only-uses-canonical test.
- **Files**: migration script + reader update + `tests/state/test_settlements_reconcile.py`.
- **Critic**: opus SCAFFOLD (Tier-0 migration on production truth DB).

### PR-M — F18 observation_instants sweep
- **Antibody**: Grep + classify every reader of `observation_instants` (legacy=906,081) vs `observation_instants_v2` (1,835,645); drop legacy after all readers migrated; registry parity check (same antibody pattern as PR-F).
- **Files**: audit script + migration + readers.
- **Critic**: opus SCAFFOLD (Tier-0, larger row delta).

### PR-K — F16 wrap_unwrap decision
- **Decision in SCAFFOLD**: delete module + tables (Z4-deferred only) OR wire enqueue caller + APScheduler poll + reconcile (Z5-planned).
- **Antibody**: If kept, T2 cascade-liveness test asserts scheduler job ID exists; if dropped, registry assertion gains a "removed tables stay removed" check.
- **Critic**: sonnet SCAFFOLD.

### PR-L — F17 calibration trapdoor
- **Antibody**: Wire `evaluate_calibration_transfer_oos.py` into `src/ingest_main.py` APScheduler as daily cron after `forecast_skill` ETL. Preflight check at consumer: `if flag_on and count(validated_calibration_transfers)==0: raise`.
- **Critic**: sonnet SCAFFOLD.

### PR-G — F3 + F8
- **F3 antibody**: `tests/test_doctrine_freshness.py` fails if `current_state.md` HEAD anchor mismatches > 14d OR `current_data_state.md` row counts drift > 5% from live DB.
- **F8 antibody**: `position_events` INSERT rejects non-ISO `occurred_at`; migration replaces existing sentinel rows with NULL.
- **Critic**: sonnet review.

### PR-H — F13 replay docstring + traceability
- **Antibody**: Docstring at `src/engine/replay.py:1664` explaining neutralization; grep traceability check for downstream consumer.
- **Critic**: sonnet review.

---

## §6 — Critic / tier matrix (machine-readable)

| PR | Tier | Executor | SCAFFOLD critic | Code critic | Test critic |
|---|---|---|---|---|---|
| PR-A | T1 | sonnet | sonnet | sonnet | sonnet |
| PR-B | T0 | sonnet | **opus** | opus | sonnet |
| PR-C | T1 | sonnet | sonnet | sonnet | sonnet |
| PR-D | T1 | sonnet | sonnet | sonnet | sonnet |
| PR-E | T0 | sonnet | **opus** | opus | sonnet |
| PR-F | T0 | sonnet | **opus** | opus | sonnet |
| PR-G | T2 | sonnet | sonnet | sonnet (review) | sonnet |
| PR-H | T3 | sonnet | — | sonnet (review) | — |
| PR-I | T0 | opus | **opus** | opus | opus |
| PR-J | T0 | sonnet | **opus** | opus | sonnet |
| PR-K | T1 | sonnet | sonnet | sonnet | sonnet |
| PR-L | T1 | sonnet | sonnet | sonnet | sonnet |
| PR-M | T0 | sonnet | **opus** | opus | sonnet |

**Rule**: opus SCAFFOLD critic on every Tier-0 surface per `feedback_opus_critic_on_architectural_scaffold_4_for_4_roi.md` (4-for-4 ROI in 2026-05-15 session). Sonnet sufficient for Tier-1/2/3.

---

## §7 — Acceptance gate (audit skill re-run)

**Per PR, after merge to main**:

```
.claude/skills/zeus-deep-alignment-audit/SKILL.md  →  re-run scoped to category
```

Acceptance: the finding's category (T1..T8 in §2) shows **zero new instances of the same root pattern** in a follow-up audit run scoped to that category. The specific finding being patched is necessary but not sufficient — category-immunity is the bar, per Fitz Methodology Constraint #4 ("Make the category impossible, not just the instance").

**Finding-level acceptance**: the named pytest in each PR brief is green AND `tests/state/` scoped suite is green AND `architecture/topology.yaml` admission via `scripts/topology_doctor.py --planning-lock` passes.

---

## §8 — Pre-Karachi 5/17 timeline (canonical)

Pulled from [KARACHI_2026_05_17_MANUAL_FALLBACK.md](KARACHI_2026_05_17_MANUAL_FALLBACK.md) and [FINDINGS_REFERENCE.md](FINDINGS_REFERENCE.md) §Karachi prep checklist. Unchanged by this fix-plan unless Path A or C is chosen.

| Phase | UTC | Action |
|---|---|---|
| T-12h | 2026-05-17 00:00 | Health checks: position active (shares=1.5873), `ZEUS_HARVESTER_LIVE_ENABLED=1`, settlements_v2 VERIFIED rows in last 24h for other cities. |
| T-2h | 2026-05-17 10:00 | Eyeball Wunderground OPKC + Polymarket gamma-api outcomePrices. |
| T-0 | 2026-05-17 12:00 | Polymarket endDate. Auto-cascade window. |
| T+1h | 2026-05-17 13:00 | Probe `settlements_v2` for VERIFIED row + `position_events` SETTLED for `c30f28a5-d4e`. |
| T+3h | 2026-05-17 15:00 | If no settlement: investigate harvester ticks; do NOT write. |
| T+9h | 2026-05-18 04:00 | Manual fallback gate. 4 preflight checks (Polymarket closed; forecasts.db writable; OPKC VERIFIED; position still active). All four must pass. |

**Heartbeat caveat (Finding #10)**: expect dispatcher `degraded` while sensor `RED`. Trust neither in isolation; cross-check raw `logs/heartbeat-sensor.err`.

---

## §9 — Sequencing summary (one screen)

1. **NOW (T-24h → T-20h)**: PR-I G1 — opus SCAFFOLD draft for F14 redeem cascade. Per §1.2.
2. **T-20h → T-18h**: PR-I G2 — opus critic on SCAFFOLD. First-round pass required.
3. **T-18h → T-10h**: PR-I G3 — sonnet executor implements per SCAFFOLD.
4. **T-10h → T-8h**: PR-I G4 — smoke test against `c30f28a5`. Synthetic `REDEEM_INTENT_CREATED` → assert auto-transition.
5. **T-8h → T-6h**: PR-I G5 — merge to main, restart daemon, monitor 30 min.
6. **T-0 (2026-05-17 12:00 UTC)**: cascade runs autonomously. Code change moratorium during window per §8.
7. **T+9h or later**: Wave 1 PRs (B, F, C, A) — independent, parallel-able.
8. **Wave 1 antibodies green**: Wave 2 (E, D, J, M) — order PR-F → PR-E (dependency).
9. **Wave 3** (K, L, G, H): low-risk decisions + doc rewrites.
10. **Per PR**: acceptance gate = audit-skill scoped re-run + category-zero finding delta.
11. **Final**: full audit-skill re-run on main; confirm 20 → 0 (or 20 → 1 false positive F9).

**Path C escalation**: any G1-G5 failure triggers Path C (PR-I best-effort + manual fallback armed). Manual fallback (KARACHI_2026_05_17_MANUAL_FALLBACK.md) is strict failure mode under Path C, never default. Trigger conditions and rollback steps live in §1.2.

---

## §10 — What this plan deliberately does NOT do

- Does NOT batch all 13 PRs onto this audit branch. PRs cut from main; this branch is doc carrier.
- Does NOT re-derive evidence — cites FINDINGS_REFERENCE rows.
- Does NOT propose new findings — the 20-finding scope is closed at Run #5.
- Does NOT add critic tiers beyond methodology baseline.
- Does NOT promise PR-I before Karachi unless operator chooses Path A.

---

**End FIX_PLAN. Next action: operator marks §1 Path A/B/C, then PR sequencing in §4 starts.**
