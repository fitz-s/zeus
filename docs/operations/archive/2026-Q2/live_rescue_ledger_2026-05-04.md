# Live Rescue Ledger — 2026-05-04

**Created**: 2026-05-04
**Last reused/audited**: 2026-05-04
**Authority basis**: `task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md` §A1 + D-5 (rescue inventory) + D-6 (cohort split is report-time computation, not DB column).
**Cohort discriminator**: `src/state/cohort_boundary.py::ZEUS_PR51_MERGE_INSTANT_UTC = 2026-05-04T03:57:08Z`. Rows whose `recorded_at` precedes this instant are `pre_utc_fix`; at-or-after are `post_utc_fix`.

---

## §1 Why this ledger exists

Between 2026-05-02 and 2026-05-04 the live daemon went through seven rapid PRs. Some closed bugs that were costing money in production; some rebuilt subsystems on first principles; some shipped emergency overrides whose final design is still pending. Performance attribution that compares cohorts across this window without bucketing by which rescues were live in each cohort will systematically mislabel the alpha signal: the PR #40 fail-closed-gate removal alone changed which candidates were eligible to enter live, so the entry distribution before and after is not the same population.

This ledger is the report-time index. Reporters bucket attribution rows by `recorded_at` against the cohort discriminator above and consult the table in §3 to interpret what was live in each cohort.

---

## §2 Cohort axis

| Cohort | Range | Scheduler | Authority changes live in this cohort |
|---|---|---|---|
| `pre_utc_fix` | rows with `recorded_at < 2026-05-04T03:57:08Z` | local-clock APScheduler (DST-sensitive) | #40, #44, #47, #49, #52 (#52 by 5min) — all rescue patches; oracle gate removed; Day0 forecast contract live; activation evidence gate live |
| `post_utc_fix` | rows with `recorded_at ≥ 2026-05-04T03:57:08Z` | UTC-pinned APScheduler | as `pre_utc_fix` plus #51, #53 — UTC scheduler live; MarketPhase axis plumbed (flag default OFF); P3+P4 dispatch flag-gated |

The cohort label is computed at report time via `src.state.cohort_boundary.cohort_label(recorded_at_utc)` — no DB column added. The boundary instant is git-log fact (`git log -1 e62710e6 --format=%cI`), so it can be re-derived lazily by any future reporter that imports the helper.

---

## §3 PR rescue / design entries

Each row covers seven fields per PLAN.md §A1 spec. **Category** distinguishes a rescue (emergency override of a still-broken design) from a design (planned rebuild).

### PR #40 — `5fb06141` merged 2026-05-02T21:41:50Z

| Field | Value |
|---|---|
| Title | Remove evaluator oracle fail-closed gate; live trades no longer halt on missing artifact |
| Category | **Rescue** |
| Live behavior changed? | **Yes** — candidates with missing oracle artifact now enter live. Pre-#40 they were rejected at the gate. |
| Emergency reason | Oracle artifact files were missing at scheduler startup on 2026-05-02; the fail-closed gate halted ALL live entries across all cities, not just the oracle-deficient ones. Operator had to choose: zero live entries, or a quick gate removal that re-enabled entries with no oracle penalty. |
| Not-final-design debt | The oracle penalty is now silently absent for missing artifacts (returns `_DEFAULT_OK = OracleStatus.OK, mult=1.0`). Bug review §A is the canonical "missing ≠ OK" finding. **Closed by**: `task_2026-05-04_oracle_kelly_evidence_rebuild §A3` (9-status enum + Beta-binomial posterior + LOW=METRIC_UNSUPPORTED). After §A3 ships, missing → status=MISSING + mult=0.5 (Beta(1,1) posterior_mean at N=0). |
| Validation evidence | Live daemon resumed entries at 2026-05-02T21:42Z; no halt observed since. |
| Expiry / review date | **EXPIRES** when `task_2026-05-04_oracle_kelly_evidence_rebuild §A3` lands. The `_DEFAULT_OK` constant must be deleted in §A3. |
| Rollback condition | If §A3 introduces a regression that re-halts live entries on missing oracle, revert §A3 and re-apply the #40 gate-removal — the gate-removal itself is forward-compatible, only the silent-OK semantics get rebuilt. |

### PR #44 — `6e3b6a53` merged 2026-05-02T23:57:42Z

| Field | Value |
|---|---|
| Title | Close P0+P1 live blockers per Zeus_May2_review.md (§17 Stage 1) |
| Category | **Mixed** (rescue + design) |
| Live behavior changed? | **Yes** — multiple call sites; entry topology corrections + monitor lane restoration. |
| Emergency reason | Zeus_May2_review.md §17 Stage 1 enumerated launch blockers; #44 was the batch close. |
| Not-final-design debt | Inherited per-finding debt; not a single isolated rescue surface. Subsequent PRs (#47, #49, #53) closed the residual design items individually. |
| Validation evidence | Stage 1 launch criteria cleared; Zeus_May2 review §17 §18 marked closed. |
| Expiry / review date | **CLOSED** — succeeding PRs subsumed each item; no residual override remains. |
| Rollback condition | Per-call-site rollback; no global revert path. |

### PR #47 — `cd882ee9` merged 2026-05-04T01:45:56Z

| Field | Value |
|---|---|
| Title | Live entry forecast target coverage contract |
| Category | **Design** |
| Live behavior changed? | **Yes** — a new contract gates entries by forecast target coverage; candidates failing coverage now reject pre-evaluator. |
| Emergency reason | Not an emergency rescue. Zeus_May3 review §6 identified silent forecast undercoverage entering live; #47 closed it as a contract. |
| Not-final-design debt | None at the contract surface. **However**: Paris/HK/Lagos site-specific corrections were follow-ups (closed in #49). |
| Validation evidence | Contract test green; live daemon shows entries rejecting on coverage-fail with the documented reason code. |
| Expiry / review date | **NO EXPIRY** — final design. |
| Rollback condition | Operator can flip the contract gate via env override (`ZEUS_FORECAST_COVERAGE_REQUIRED`) if a calibration regression appears. |

### PR #49 — `06656b0d` merged 2026-05-04T03:29:47Z

| Field | Value |
|---|---|
| Title | Post-PR47 follow-ups: Paris/HK closure + Codex P2 fix + probes relocation + activation evidence gating |
| Category | **Mixed** (rescue + housekeeping) |
| Live behavior changed? | **Yes** — Paris and HK were re-included after coverage corrections; activation evidence gate flipped ON for Phase C flags. |
| Emergency reason | Paris/HK had been quarantined by #47's coverage contract; the re-inclusion was a same-day correction once the source-routing fix landed. |
| Not-final-design debt | Activation evidence gate is operator-authorized and final. Probes relocation is housekeeping. Paris/HK source-routing corrections are final per `architecture/paris_station_resolution_2026-05-01.yaml`. |
| Validation evidence | Activation dry-run evidence bundles committed at `2109b456`; Paris/HK closures verified via the source_validity doc. |
| Expiry / review date | **NO EXPIRY** — final design. |
| Rollback condition | Activation gate flip is reversible via env override. |

### PR #51 — `e62710e6` merged 2026-05-04T03:57:08Z **← cohort boundary**

| Field | Value |
|---|---|
| Title | P0(scheduler-tz): pin APScheduler to UTC — LIVE BUG fix (cron will shift) |
| Category | **Rescue (with antibody)** |
| Live behavior changed? | **Yes — globally**. Every scheduled live cycle, every cron job, every daemon refresh after this instant runs on UTC. The previous local-clock schedule was DST-sensitive; pre-#51 cron firings near DST boundaries had silent ±1h shifts. |
| Emergency reason | DST-sensitive scheduler caused systematic ±1h shifts in cycle firing; observed live during the 2026-03-30 spring-forward investigation. The shift mislabeled per-city "decision time" used in calibration. |
| Not-final-design debt | None — the fix IS the final design. The antibody is `tests/test_scheduler_tz_pin.py` + `architecture/runtime_modes.yaml::scheduler_tz`. |
| Validation evidence | Post-merge cron-job timestamp audit shows uniform UTC firing; `tests/test_scheduler_tz_pin.py` green. |
| Expiry / review date | **NO EXPIRY** — final design. **The merge instant is the cohort boundary**: pre-#51 attribution data is non-commensurable with post-#51 because the schedule itself shifted. |
| Rollback condition | NONE — UTC is the only correct schedule for this system. Reverting would re-introduce the DST shift bug. The cohort split exists because the data on either side is regime-different, NOT because the rollback is desired. |

### PR #52 — `33c5ec54` merged 2026-05-04T03:51:53Z (5min before #51)

| Field | Value |
|---|---|
| Title | fix(producer): gate produce_all() on test_activation_flag_combinations.py |
| Category | **Test gate** |
| Live behavior changed? | **No** — test-only. The producer's `produce_all()` gating is a CI/test correctness fix, not a runtime path change. |
| Emergency reason | Test-suite ordering issue surfaced in the PR47 merge sequence. |
| Not-final-design debt | None. |
| Validation evidence | Test suite green post-merge. |
| Expiry / review date | **NO EXPIRY** — final design. |
| Rollback condition | None needed (test-only). |
| Cohort note | Despite merging chronologically before #51's UTC-pin (03:51:53Z vs 03:57:08Z), #52 did NOT change runtime behavior — so attribution data near the boundary is regime-defined by #51 alone. The 5-minute gap between #52 and #51 is unattributable test-only territory. |

### PR #53 — `dbe32273` merged 2026-05-04T07:40:14Z

| Field | Value |
|---|---|
| Title | P2-P5: strategy redesign Day0-as-endgame (MarketPhase axis + plumbing + Kelly) |
| Category | **Design** |
| Live behavior changed? | **No** (intentional). Every dispatch site is gated by `ZEUS_MARKET_PHASE_DISPATCH`, default OFF. With the flag OFF, P3+P4 are byte-equal to pre-#53. |
| Emergency reason | Not an emergency. Day0-as-endgame redesign per `task_2026-05-04_strategy_redesign_day0_endgame/PLAN_v3.md` §6.P2-P5. |
| Not-final-design debt | The flag-gated dual-path is intentional scaffolding. **Closed by**: `task_2026-05-04_oracle_kelly_evidence_rebuild §A6` (flag default flips ON; legacy branch retired). 7th unmigrated dispatch site (`evaluator.py:1427`) closed by `task_2026-05-04_oracle_kelly_evidence_rebuild §A4` (StrategyProfile registry rewires EntryMethod selection). |
| Validation evidence | 73/73 phase-axis tests green on synced main; critic R5 verdict APPROVED-WITH-CAVEATS at `docs/operations/task_2026-05-04_strategy_redesign_day0_endgame/CRITIC_REVIEW_R5_PR53_P4_and_full_stack.md`. |
| Expiry / review date | **EXPIRES** when `task_2026-05-04_oracle_kelly_evidence_rebuild §A6` ships (flag default flip + legacy branch removal). |
| Rollback condition | Flip `ZEUS_MARKET_PHASE_DISPATCH` env to OFF — flag-OFF path is byte-equal to pre-#53 by construction. |

---

## §4 Reporter usage pattern

```python
from src.state.cohort_boundary import cohort_label
from datetime import datetime, timezone

# Given a probability_trace_fact row with `recorded_at` text:
recorded_at_utc = datetime.fromisoformat(row["recorded_at"].replace("Z", "+00:00"))
bucket = cohort_label(recorded_at_utc)  # "pre_utc_fix" | "post_utc_fix"

# Bucket attribution metrics by `bucket` before computing per-cohort mean,
# variance, hit-rate, etc. Cross-cohort aggregates are not meaningful for
# the 2026-05-02 ↔ 2026-05-04 window.
```

Reporters MUST consume cohort_label as a stratification axis when reporting on attribution data spanning the window. Reporters that fold pre and post into a single mean are subject to the regime-change confound documented in §1.

---

## §5 What this ledger is NOT

- Not a complete change-log — only PRs that affected live behavior or define a cohort boundary.
- Not a permanent record — entries with **EXPIRES** clauses get archived (or deleted) when the closing PR ships.
- Not a substitute for git history — `git log` is authoritative. This ledger is the human-readable reporter-side index.

---

## §6 Cross-references

- Cohort helper: `src/state/cohort_boundary.py`
- Cohort tests: `tests/test_attribution_cohort_boundary.py`
- Closing plan: `docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md` §A1, §A3 (oracle), §A4 (registry), §A6 (Kelly + flag flip)
- Bug review (external): `/Users/leofitz/Downloads/Zeus_May4_review_bugs.md` Findings A/B/C/D/E/F
- Predecessor plan: `docs/operations/task_2026-05-04_strategy_redesign_day0_endgame/PLAN_v3.md` §6.P0-P5
- Methodology: `~/.claude/CLAUDE.md` Universal Methodology §3 (immune system) + §4 (data provenance)
