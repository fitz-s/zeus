# Phase 1 MAINLINE AUTOPILOT PLAN

**Purpose**: end-to-end auto-executable plan for remaining Phase 1 work after this session's compact. Designed for `/autopilot` (or fresh orchestrator session) ingestion — every step has entry/exit gates, stop conditions, and re-entry briefs.

**Created**: 2026-05-19 by orchestrator opus
**Scope**: Phase 1 Scope C remainder = PR-T1-B merge → Track 2 Day0Nowcast → W3 closure
**Authority**: `PHASE_1_ULTRAPLAN.md` v3 (path D natural-key reframe)
**Entry SHA**: origin/main = `ccafbf51f3` (PR-T1-A #214 merged)

---

## §0. Cold-start orientation (every wave entry)

Before any wave: orchestrator runs these 4 commands, expects PASS on all. Any FAIL → invoke "Re-entry recovery" §6.

```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus
# Gate A: marketplace cache healthy
grep -c "^model: claude-sonnet-4-6$" ~/.claude/plugins/marketplaces/omc/agents/*.md
# Expected: 0 (no literals)
# Gate B: preproxy alive
launchctl list | grep com.9router.preproxy | grep -v "^-"
# Expected: line with PID, status 0
# Gate C: ultraplan v3 reachable
test -f docs/operations/task_2026-05-19_strategy_vnext_phase1/PHASE_1_ULTRAPLAN.md && head -2 docs/operations/task_2026-05-19_strategy_vnext_phase1/PHASE_1_ULTRAPLAN.md
# Expected: file exists, header line mentions "v3" / "Path D"
# Gate D: main has PR-T1-A
git fetch origin main && git log origin/main --oneline | grep -i "decision_events foundation" | head -1
# Expected: commit line for #214 (ccafbf51f3 or descendant)
```

If running from PRIMARY worktree (not a phase1-t1-* worktree): `EnterWorktree` into a wave-specific worktree before any branch op.

---

## §1. WAVE 1 — PR-T1-B merge

**Entry condition**: PR-T1-B not yet on origin/main.
**Skip condition**: `git log origin/main --oneline | grep "Path D backfill"` returns a commit → wave already done; skip to Wave 2.

### §1.1 Pre-flight check

```bash
# Is PR-T1-B open or merged?
gh pr list --state all --search "head:claude/phase1-t1-b-decision-events-20260519" --json number,state,mergedAt
```
- If `state=MERGED`: skip Wave 1, go Wave 2.
- If `state=OPEN`: skip to §1.4 (PR-management).
- If empty: continue with Wave 1 from §1.2.

### §1.2 Worktree

EnterWorktree `phase1-t1b-resume` from origin/main. Branch: `claude/phase1-t1-b-decision-events-20260519` if not already present.

### §1.3 Dispatch executor

**Agent**: sonnet executor, fresh `Agent()` (NOT SendMessage — too poisoning-prone for cold dispatch).
**Brief**: paste contents of `BRIEFS/wave1_pr_t1b_executor_brief.md` (see §7.1 below).

Executor self-manages full PR lifecycle (commit + push + open + bot reviews + merge + tag) per Compound Protocol.

### §1.4 Operator-required decisions (executor must surface, NOT self-resolve)

| Trigger | Decision needed |
|---|---|
| `audit recovery_rate < 0.80` on PRIMARY DB | Continue reduced or pivot to forward-only Path C |
| `INV-37` cross-DB write surface required | NOT allowed — STOP, escalate |
| New SEV-1 in bot review (not pre-existing) | Critic dispatch or operator override |
| `cycle_runner.py` codereview-may19 P0-1 provenance unclear | Stash vs discard vs separate-PR |

### §1.5 Exit gates (all must hold to advance to Wave 2)

- PR-T1-B merged to origin/main, squash-merge SHA captured
- Tags pushed: `phase1_track1_b_landed`, `phase1_track1_landed`
- Audit recovery rate documented in PR body (or "vacuous + operator override" if applicable)
- Antibody `test_inv_decision_events_completeness.py` strict-pass (or pytest.skip on empty-window — acceptable)
- Regression delta: 0 new failures vs pre-existing 18 runtime_guards baseline

### §1.6 Wave 1 budget

- 1 executor dispatch (sonnet)
- 0 opus critic (operator chose path 2 — pragmatic; W3 covers cross-module audit)
- Time: 60-120 min typical; if >180 min surface to operator

---

## §2. WAVE 2 — Track 2 Day0Nowcast

**Entry condition**: Wave 1 exit gates ALL PASS (PR-T1-B merged + tags + antibody pass).
**Authority section**: ultraplan v3 §5 (Track 2 Day0Nowcast spec).

### §2.1 Pre-flight

```bash
git fetch origin main && git rev-parse origin/main  # capture wave-2 entry SHA
# Confirm Day0LowNowcastSignal still at expected path
test -f src/signal/day0_low_nowcast_signal.py
test -f src/contracts/day0_observation_context.py
# Confirm day0_nowcast_context helper still at forecast_uncertainty.py:524
grep -n "^def day0_nowcast_context" src/signal/forecast_uncertainty.py | head -1
```

### §2.2 Worktree + Branch

EnterWorktree `phase1-t2-day0-nowcast-20260520` from origin/main. Branch name: `claude/phase1-t2-day0-nowcast-20260520`.

### §2.3 SCAFFOLD pass

**Agent**: sonnet executor, fresh `Agent()`.
**Brief**: see `BRIEFS/wave2_scaffold_executor_brief.md` (§7.2 below).

Deliverables:
- `scaffolds/t2_scaffold.md`
- `src/signal/day0_nowcast.py` (NEW unified contract stub)
- `src/calibration/day0_horizon_calibration.py` (NEW horizon-aware Platt stub)
- Schema stub for `day0_nowcast_runs` table (forecasts DB)
- `tests/test_inv_nowcast_horizon_bound.py` xfail antibody
- Refactor outline for `Day0LowNowcastSignal` → `Day0Nowcast(metric=LOW, ...)` shim

Commit + push. STOP at SCAFFOLD.

### §2.4 Wave-level opus critic on T2 SCAFFOLD

**Agent**: opus critic, fresh `Agent()`.
**Brief**: see `BRIEFS/wave2_critic_brief.md` (§7.3 below).

Focus probes:
- P1 Day0LowNowcastSignal coexistence — refactor path correct? backward compat?
- P2 horizon-aware Platt single-fit math defensible? distinct from extended Platt?
- P3 forecast/nowcast fusion math (sigmoid weight) — bounds + edge cases
- P4 INV-nowcast-horizon-bound antibody (fail-closed on max_hours_to_resolution > 6)
- P5 day0_nowcast_runs table on forecasts DB + manifest entry
- P6 SCHEMA_FORECASTS_VERSION bump path (current 3 → 4)

Verdict APPROVED or NEEDS_REVISION.

If NEEDS_REVISION: fresh sonnet executor revises SCAFFOLD; ONE more critic round max. If SEV-1 persists across 2 critic rounds: STOP, surface to operator.

### §2.5 Production pass + PR-T2

After APPROVED SCAFFOLD: sonnet executor (fresh `Agent()`) implements production + opens PR-T2 + self-manages CI + threads + merge. Tag `phase1_track2_landed`.

Authority: ultraplan v3 §5 + §4.8 (cross-module relationship invariants apply equally to nowcast).

PR target ~600 LOC (single PR, no T2-A/T2-B split since scope smaller than T1).

### §2.6 Wave 2 budget

- 2 executor dispatches (SCAFFOLD + production), 1 critic dispatch
- Reserve: 1 critic if SCAFFOLD NEEDS_REVISION
- Time: 4-8 hours typical

### §2.7 Operator-required decisions

| Trigger | Decision |
|---|---|
| Day0LowNowcastSignal refactor breaks any downstream caller | Operator: keep old class as deprecation shim or hard-cut |
| Day0Nowcast LOW output diverges from legacy LowNowcastSignal (relationship test fails) | Operator: tolerance threshold or block PR |
| `provider_reported_time` writer wire-up requires new NOAA/METAR ingest source | Operator: in-scope T2 or defer Phase 2 |
| INV-nowcast-horizon-bound semantic ambiguity | Operator: 6h hard cap or graceful degradation |

---

## §3. WAVE 3 — Phase 1 closure verifier + handoff

**Entry condition**: Wave 2 exit gates ALL PASS. T1 + T2 tags on remote.
**Authority section**: ultraplan v3 §4.8 (W3 critic focus areas — operator directive 2026-05-19).

### §3.1 Pre-flight

```bash
# Confirm all expected tags present
git ls-remote --tags origin | grep -E "phase1_track1_(a_|b_)?landed|phase1_track2_landed"
# Expected: 3 lines
```

### §3.2 Dispatch closure opus critic

**Agent**: opus critic, fresh `Agent()`.
**Brief**: see `BRIEFS/wave3_closure_critic_brief.md` (§7.4 below).

**Focus per operator directive (ultraplan v3 §4.8)** — these 7 cross-module relationship invariants:

1. `market_slug` semantic identity across ALL tables that decision_events joins (no slug-vs-id-vs-condition-id confusion)
2. `condition_id` nullability honored — every join uses `IS NULL` not `= NULL`
3. `deid_v1_` namespace isolation from `dgid_v1_` (zero cross-namespace lookups)
4. artifact_json natural-key recovery rate honored at backfill execution
5. decision_seq race-closure under `db_writer_lock(LIVE)` per-DB-file lock
6. Backfill `DELETE WHERE source='phase0_backfill'` never touches `source='live_decision'` rows
7. `decision_event_id` sentinel `'deid_v1_BACKSTOP_NULL_WRITER_BYPASS'` count = 0 in production decision_events after a 7-day soak

Plus Day0Nowcast cross-module invariants (write own list if T2 introduced new ones).

### §3.3 Closure deliverables (orchestrator-authored after critic verdict)

If critic APPROVED:

1. Run G1-G8-equivalent post-flight gates against post-Phase-1 main
2. Write `PHASE_1_TO_PHASE_2_HANDOFF.md` (compact version, ≤200 lines, modeled on Phase-0 handoff)
3. Tag `phase1_closure_landed` on origin/main
4. Commit + push handoff doc

### §3.4 Closure handoff doc must include

- Phase 1 final state (origin/main SHA, all 3+ tags)
- 5 new invariants live (incl. INV-decision-events-completeness, INV-nowcast-horizon-bound)
- Carried Compound Protocol deltas (any new memories surfaced this session)
- Phase 2 scope: MarketAnalysisVNext + book_hash_transitions + NoTradeReason + freshness_registry + remaining Phase 2-7 items deferred by Scope C
- Open follow-ups (cycle_runner P0-1 if not folded in, mypy debt, audit recovery edge cases)
- Compact-survival next-session brief paragraph

### §3.5 Wave 3 budget

- 1 opus critic (closure verifier)
- 0 executor (orchestrator authors handoff + tags inline)
- Time: 30-60 min

---

## §4. Cumulative opus budget tracking

Phase 1 fresh budget = 9 dispatches. Already consumed this session:

| Dispatch | Used |
|---|---|
| Ultraplan v0 critic | 1 |
| T1 SCAFFOLD critic round 1 | 1 |
| T1 SCAFFOLD critic round 2 | 1 |
| **Used so far** | **3** |
| Wave 2 SCAFFOLD critic | budget 1 |
| Wave 2 revision critic (if needed) | budget 1 (reserve) |
| Wave 3 closure verifier | budget 1 |
| **Projected total** | **6 of 9** |
| Reserve | 3 |

Reserve is for emergency NEEDS_REVISION / cross-track investigation / unexpected pivot.

---

## §5. Decision delegation matrix

| Decision class | Who decides |
|---|---|
| Schema / migration design | orchestrator (after critic) |
| INV-37 compliance / cross-DB write surface | orchestrator + critic (block if violated) |
| Audit recovery rate <80% | **operator** |
| New SEV-1 found in bot review | critic (sonnet for verify; opus only if architectural) |
| `cycle_runner.py` P0-1 provenance | **operator** (executor surfaces evidence) |
| Day0LowNowcastSignal refactor breaks downstream | **operator** |
| Phase 2 scope reordering | **operator** |
| Stop conditions triggered | **operator** (orchestrator pauses + reports) |
| Compact-survival handoff content | orchestrator |
| Tag naming, branch naming, PR body content | orchestrator/executor |
| Mechanical fixes for bot-review style nits | executor (commit-then-resolve) |

---

## §6. Re-entry recovery (per failure mode)

### §6.1 If executor dies (any wave)

1. `git status` in worktree to see uncommitted work
2. If WIP is salvageable: orchestrator commits with `[skip-invariant]` (docs/scaffold) or runs invariants (code), notes commit SHA
3. Fresh `Agent()` dispatch with continuation brief: cite the WIP commit + remaining steps
4. NEVER SendMessage to dead agent — fresh dispatch is the working path

### §6.2 If SendMessage poisoning recurs

1. Verify marketplace cache (Gate A above)
2. Verify preproxy alive (Gate B)
3. Verify env var `ANTHROPIC_DEFAULT_SONNET_MODEL` still `cc/claude-sonnet-4-6`
4. Verify proxy dual routing: `grep -n "claude-sonnet-4-6\b" ~/.9router/preproxy/preproxy.js | head -3`
5. If all OK and SendMessage still fails → fresh `Agent()`. Always works.

### §6.3 If audit script returns vacuous pass (NO_TABLE on PRIMARY)

Confirms decision_log doesn't exist on PRIMARY. Two paths:
- (a) Phase 0 didn't populate decision_log post-deploy → backfill has nothing to do → Path C "forward-only" semantic is forced; Wave 1 PR-T1-B body becomes "no backfill rows" + antibody auto-skips. Document in PR body + closure handoff.
- (b) PRIMARY DB path mis-resolved → re-verify `primary_world_db_path()` returns the right file.

### §6.4 If wave-level critic returns NEEDS_REVISION twice

Per session-history pattern: NEEDS_REVISION on architectural SCAFFOLD is normal (4-for-4 ROI in Phase 0). Two rounds is acceptable. **Three rounds** → STOP, surface to operator with critic-recommended architectural pivot.

### §6.5 If infra issue (preproxy / proxy upstream) blocks dispatch

1. Check Gate B (preproxy alive)
2. Check `launchctl print gui/$(id -u)/com.9router.preproxy 2>&1 | head -20` for exit code / restart count
3. Surface to operator with diagnostic snapshot — DO NOT attempt `launchctl kickstart -k` from orchestrator (operator-territory)

---

## §7. EMBEDDED BRIEFS (paste-ready for executor dispatch)

### §7.1 BRIEF: Wave 1 PR-T1-B executor (sonnet, fresh Agent)

```
Working dir: <worktree path for phase1-t1b-resume>
Branch: claude/phase1-t1-b-decision-events-20260519 (from origin/main)
Venv: /Users/leofitz/.openclaw/workspace-venus/zeus/.venv

MISSION: PR-T1-B (backfill + completeness antibody + mypy fixes + path discipline helper). Single PR ~400 LOC. Self-manage CI + threads + merge + tag.

READ FIRST: docs/operations/task_2026-05-19_strategy_vnext_phase1/PHASE_1_ULTRAPLAN.md §4.3, §4.4, §4.8; §7.1 here.

OPERATOR DIRECTIVE: "All paths must point to PRIMARY production state/, never worktree-local." First task: create src/state/db_paths.py with primary_world_db_path() / primary_forecasts_db_path() / primary_trade_db_path() helpers. Migrate scripts/audit_artifact_json_natural_key_coverage_2026_05_19.py to use it; replace "vacuous pass" branch with sys.exit(2).

DELIVERABLES (PR-T1-B):
- src/state/db_paths.py (NEW, 3-line provenance header)
- scripts/audit_artifact_json_natural_key_coverage_2026_05_19.py (migrate path; loud-fail on missing table)
- scripts/backfill_decision_events_from_artifact_json.py (full body: DELETE WHERE source='phase0_backfill' AND <natural-key match> THEN INSERT per §4.3; chunks of 500 under db_writer_lock(BULK))
- tests/test_inv_decision_events_completeness.py (remove @pytest.mark.xfail; strict-pass body per §4.4; query by market_slug; read-only helpers from PR-T1-A)
- src/state/db.py lines 8685-8904 (13 mypy fixes, schema-adjacent)

GATE: re-run audit against PRIMARY DB; if recovery_rate < 0.80 → STOP, report, await operator.

CONSTRAINTS: heredoc commits; `git add <specific>` NOT `-A`; 3-line provenance headers; no [skip-invariant] on code commits; cycle_runner.py 36-LOC edit in PRIMARY working tree is OUT OF SCOPE — stash or discard, surface provenance verdict.

REPORT: PR# + merge SHA + audit rate + regression delta + cycle_runner provenance + duration.
```

### §7.2 BRIEF: Wave 2 SCAFFOLD executor (sonnet, fresh Agent)

```
Working dir: <worktree path for phase1-t2-day0-nowcast-20260520>
Branch: claude/phase1-t2-day0-nowcast-20260520 (from origin/main, post-T1)
Venv: /Users/leofitz/.openclaw/workspace-venus/zeus/.venv

MISSION: SCAFFOLD-only pass for Track 2 Day0Nowcast. STOP at scaffold doc commit; orchestrator dispatches critic next.

READ FIRST: PHASE_1_ULTRAPLAN.md §5 (full Track 2 spec), §4.8 (cross-module focus areas).

CRITICAL CONTEXT (pre-verified):
- src/signal/day0_low_nowcast_signal.py exists (LOW path); will be refactored as shim
- src/signal/day0_high_signal.py exists; NO HIGH-side nowcast equivalent today
- src/signal/forecast_uncertainty.py:524 defines day0_nowcast_context() helper (preserve)
- src/contracts/day0_observation_context.py:90 — Day0ObservationContext.daypart accessor
- Router src/signal/day0_router.py: HIGH/LOW dispatch unchanged; nowcast invocation is INSIDE each branch

DELIVERABLES:
- scaffolds/t2_scaffold.md — design doc (≤300 lines): unified Day0Nowcast contract; HIGH/LOW parametric path; single horizon-aware Platt fit (h-as-continuous-covariate, NOT 6 fits); forecast-nowcast fusion math; day0_nowcast_runs schema; antibody xfail
- src/signal/day0_nowcast.py (NEW, stub) — Day0Nowcast(temperature_metric, observation, daypart, market) -> P_nowcast, raises NotImplementedError
- src/calibration/day0_horizon_calibration.py (NEW, stub) — Platt fit module
- tests/test_inv_nowcast_horizon_bound.py — @pytest.mark.xfail(strict=True) — verifies NotApplicableHorizon raised for max_hours_to_resolution > 6
- Schema scaffold note (do NOT bump SCHEMA_FORECASTS_VERSION yet — production)
- 3-line provenance header on every new file

LOC budget: scaffold doc ≤300; stub code ≤200; antibody ≤50.

Commit: `scaffold(t2): Day0Nowcast unified contract + horizon-aware Platt + xfail antibody`. Push.

STOP at SCAFFOLD. Report SHA + summary + ambiguities for critic.
```

### §7.3 BRIEF: Wave 2 SCAFFOLD opus critic

```
Wave-level opus critic on T2 SCAFFOLD. Worktree: <path>. HEAD = <SCAFFOLD commit SHA>.

READ: scaffolds/t2_scaffold.md; src/signal/day0_nowcast.py; src/signal/day0_low_nowcast_signal.py (existing LOW); src/signal/forecast_uncertainty.py around line 524; tests/test_inv_nowcast_horizon_bound.py; PHASE_1_ULTRAPLAN.md §5.

VERIFICATION PROBES (empirical):
- P1 day0_nowcast_context() inputs/outputs — what does it return? Can unified Day0Nowcast consume identically?
- P2 horizon-aware Platt with continuous covariate — math defensible? Compare to existing Extended Platt at src/calibration/platt.py.
- P3 forecast/nowcast fusion sigmoid weight w = σ(-(hours_to_close - 3)) — boundary behavior at h=0, h=3, h=6, h=24
- P4 Day0Router.route() — does the SCAFFOLD's "router unchanged" claim hold? grep for invocations.
- P5 day0_nowcast_runs table proposed schema — does it cover ensemble forecast joins (Phase 1's market_events_v2 + ensemble_snapshots_v2)? K1 ownership = forecasts DB?
- P6 SCHEMA_FORECASTS_VERSION — current value? bump path?

FIND: any cross-module relationship gaps (Fitz §3 pattern — repeated mistake in T1 was identifier-semantic mismatch). Verify market_slug / temperature_metric usage is consistent with T1's natural key.

VERDICT: APPROVED (≤SEV-3) or NEEDS_REVISION. If NEEDS_REVISION, list concrete revision packet; orchestrator sends ONE more revision round, then APPROVED-or-STOP.

Output ≤300 lines. Adversarial. ROI per session-history: 4-for-4 SEV-1 catches on architectural SCAFFOLDs.
```

### §7.4 BRIEF: Wave 3 Phase 1 closure opus critic

```
Phase 1 closure verifier. origin/main = <wave-3 entry SHA>. All 3 track tags present.

READ:
1. PHASE_1_ULTRAPLAN.md §4.8 (the 7 cross-module relationship focus areas — operator directive 2026-05-19)
2. PR #214 (PR-T1-A) + PR-T1-B + PR-T2 merge commits
3. Run G1-G8-equivalent post-flight gates (see §3.1 of MAINLINE_AUTOPILOT_PLAN.md)
4. Live DB sanity probes (sqlite3 on PRIMARY zeus-world.db, zeus-forecasts.db, zeus_trades.db)

AUDIT FOCUS (per operator directive §4.8) — surface any finding with file:line citation:

1. market_slug semantic identity across all tables decision_events joins — run grep, list every reader/writer of decision_events.market_slug; confirm each populates from market_events_v2.market_slug (or equivalent verified-slug source). Flag any use of market_id or condition_id as proxy.
2. condition_id nullability handling — every JOIN clause that touches decision_events.condition_id must use `IS NULL` not `= NULL`. Run grep, list every offender.
3. Hash namespace isolation — `deid_v1_` never appears in calibration_pairs_v2.decision_group_id rows; `dgid_v1_` never in decision_events.decision_event_id. Run sqlite3 SELECT COUNT(*) probes.
4. artifact_json natural-key recovery rate — what rate was achieved at backfill time? Documented in PR-T1-B body? ≥80% gate honored?
5. decision_seq race closure under db_writer_lock(LIVE) — is the lock acquired BEFORE the MAX-then-INSERT? Inspect write_decision_event() body.
6. Backfill DELETE-by-source isolation — `DELETE WHERE source='phase0_backfill'` query at backfill_decision_events_from_artifact_json.py — confirm WHERE clause + scope.
7. decision_event_id sentinel `'deid_v1_BACKSTOP_NULL_WRITER_BYPASS'` — count in production decision_events. Expected: 0. Non-zero = writer bypass found.

PLUS T2 Day0Nowcast cross-module checks:
- INV-nowcast-horizon-bound fires correctly on max_hours_to_resolution > 6
- Day0Nowcast LOW output matches legacy Day0LowNowcastSignal within tolerance
- forecast/nowcast fusion math has no boundary singularities

VERDICT: APPROVED (Phase 1 ready to close) or NEEDS_REVISION (list defects → orchestrator handles per defect type).

Output ≤400 lines. Cite file:line for every finding.
```

---

## §8. Compact-survival continuation brief

(If orchestrator session compacts mid-Wave, paste this paragraph back to resume.)

"Resume Zeus Phase 1 mainline autopilot per `docs/operations/task_2026-05-19_strategy_vnext_phase1/MAINLINE_AUTOPILOT_PLAN.md`. Phase 0 closed `fc7704a9fd`. Phase 1 T1: PR-T1-A merged #214 `ccafbf51f3`, tag `phase1_track1_a_landed`. Run §0 cold-start gates A-D. If gates pass: check Wave 1 entry condition (PR-T1-B status via §1.1). If PR-T1-B not yet merged, execute Wave 1; else skip to Wave 2 (T2 Day0Nowcast SCAFFOLD per §2.3). Wave 3 closure per §3 + §4.8 7-invariant focus. Opus budget: 3/9 spent, 6 remaining; §4 ledger. Operator-required decisions per §5; orchestrator-decisions inline."

---

## §9. Open carried items (cross-wave)

Items that don't block any wave but must surface in Phase 1 closure handoff:

- **18 pre-existing `test_runtime_guards.py` failures** — separate debug thread post-Phase-1
- **Pre-existing linter failures**: `src/execution/harvester_pnl_resolver.py:73`, `tests/test_evaluator_strategy_key_failclosed.py:48`
- **`inject_may2021_markets_2026_05_19.py`** — operator-local script in allowlist; delete-or-wrap decision at closure
- **Phase 0 worktrees** — operator may want to clean post-Phase-1
- **Codehash drift CI cadence** — currently every CI sweep; consider daily cron if rate-limit pressure
- **`cycle_runner.py` 36-LOC edit** — if codereview-may19 P0-1 is real, separate PR after T1-B; if speculative, discard
- **`ANTHROPIC_AUTH_TOKEN` leaked to session transcript** — operator should rotate

---

## §10. Phase 2 scope preview (for handoff doc)

Per v4 §M + Scope C deferrals:
- `book_hash_transitions` (v4 §M Phase 1 deferred)
- `NoTradeReason` enum (v4 §M Phase 1 deferred)
- `freshness_registry` (v4 §M Phase 1 deferred)
- `MarketAnalysisVNext` + `wide_spread_display_substitution` consumer + `depth_at_best_ask` consumer + `spread_observed_window_ms` (v4 Phase 2-7 → deferred from Scope C)
- `market_end_anchor_source()` wire-up (depends on MarketAnalysisVNext)
- Shoulder strategy refinement (v4 Phase 2-7)
- EvidenceLadder promotion (v4 Phase 2-7)
- candidate stubs (v4 Phase 2-7)

Each becomes a Phase 2 ultraplan section.

---

— End of MAINLINE_AUTOPILOT_PLAN. Self-contained for cold-start orchestrator resumption.
