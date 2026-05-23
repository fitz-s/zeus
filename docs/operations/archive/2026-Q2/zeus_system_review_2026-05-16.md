# Zeus System Review — 2026-05-16

**Scope:** Runtime management, feedback systems, agent workflow, code quality  
**Method:** 7 parallel investigation agents across PR archaeology (20 PRs), feedback meta-analysis (45 entries), hook audit, calibration pipeline trace, runtime observability audit, fix(data) cluster forensics, compounding systems design analysis  
**Adversarial verification:** 3 verification agents challenge initial findings before conclusions are drawn

---

## Executive Summary

Zeus is operationally live and accumulating data correctly. The trading engine executes, settlements flow, calibration pairs accumulate. But three structural gaps mean the system is not yet compounding on its own history:

1. **The Platt calibration refit gate has never been activated.** The drift detector runs daily, signals `REFIT_NOW`, writes `state/refit_armed.json` — and nothing happens. The gate was deliberately designed (R3 F2, correct architecture) but never run in production. Zeus is trading on static probability coefficients.

2. **Calibration drift produces no production-observable signal.** `drift_detector.py` computes whether calibration is degrading. This result goes to a file. No Discord alert, no health probe field, no status_summary key. The system knows when it's degrading but doesn't say so.

3. **P&L attribution doesn't exist.** You can see that a trade won or lost. You cannot tell whether the win came from forecasting edge, calibration quality, or favorable market movement. The edge thesis is empirically unverifiable from production data.

Everything else in this report is secondary to these three.

---

## Part 1: The Calibration Learning Loop

### What Exists

The data accumulation half of the learning loop is working:

```
Market settles
→ harvester.write_calibration_pair() [automatic, per-settlement]
→ calibration_pairs_v2 row inserted [automatic]
→ _drift_detector_tick() [daily, UTC 06:00]
→ DriftReport: REFIT_NOW | WATCH | OK [computed correctly]
→ state/refit_armed.json written [automatic]
```

The computation half (`ExtendedPlattCalibrator`, `blocked_oos.py` OOS eval, `save_platt_model_v2()`) is fully implemented and correct.

### What's Missing

The bridge. `retrain_trigger_v2.py:11-13` explicitly says: *"The refit_armed.json is a signal file for an operator or scheduled refit script to act on. It does NOT trigger the refit itself."*

The gate requires four simultaneous conditions:
- `ZEUS_CALIBRATION_RETRAIN_ENABLED=1`
- HMAC token
- Dated evidence artifact at `calibration_retrain_decision_*.md`
- Frozen-replay antibody PASS

**No `calibration_retrain_decision_*.md` files exist in the repo.** The gate was built, tested, never activated.

### Why the Gate Exists (It's Correct Design)

The deliberate design (R3 F2) is correct: Platt models promote only on CONFIRMED-only corpus, with replay antibody as guard against overfitting on contaminated pairs. Auto-promotion without replay validation is a real risk — a bad batch of settlements could silently degrade the model.

### What's Actually Missing

Not automation of the gate — it's that the gate has never been walked through once. The question is: does `state/refit_armed.json` currently show `REFIT_NOW`? If yes, the edge is trading on stale coefficients and nobody knows.

Additionally, the four-condition gate is scriptable. The `retrain_trigger_v2.py` design accommodates "a scheduled refit script" — HMAC token generation, evidence file creation, frozen-replay run, and env flag can all be automated without changing the safety semantics.

### Silent Failure Modes

| Failure | Observable? | Current detection |
|---------|-------------|-------------------|
| Settlements stop flowing (venue downtime) | No | `n_settlements=0` → drift detector sees "OK" (no data to detect drift) |
| Platt slope A collapses toward 0 (calibration → identity) | No | Drift detector uses Brier score, not coefficient values |
| Wide CI from sparse city/season data | Partial | Used to widen edge bounds, not flagged as low-confidence |
| `refit_armed.json` shows REFIT_NOW for weeks | No | Only visible if operator reads the file |

---

## Part 2: Runtime Observability Gaps

### What Is Currently Observable

- `.err` log files (zeus-ingest.err: 109MB, zeus-live.err: 85.7MB, riskguard: 9.9MB) — note: `.log` files are all 0B, daemons write to stderr
- Discord alerts: halt/resume/warning/trade/heartbeat-missed (via `discord_alerts.py`)
- `scripts/live_health_probe.py`: single JSON blob — daemon heartbeat age, process liveness, forecast-live freshbeat, WS state, block_registry gates, lifecycle funnel counts, no_trade reasons, entry capability
- `status_summary.json`: written every cycle, includes calibration_serving status (ready/blocked)
- riskguard tick: `INFO:__main__:Tick complete: GREEN` per cycle

### What Silently Degrades

**Calibration drift.** `drift_detector.py` computes drift magnitude (rolling Brier delta). Output: `DriftReport` struct with `recommendation: REFIT_NOW | WATCH | OK`. This never reaches Discord, health probe, or status_summary. The system computes degradation and discards the signal.

**Ensemble quality.** Missing ENS members and stale snapshot age are not named health-probe fields. Effect is visible only as downstream `no_trade` reasons — cause is unattributed.

**Source data freshness.** Per-source last-good-fetch timestamps don't appear in health probe. WU/NOAA/Ogimet staleness appears after-the-fact as `no_trade` reasons, not as a leading indicator.

**Edge erosion over time.** No signal exists. `edge_observation.py` computes edge realization correctly but is explicitly `NO write path. NO JSON persistence` (line 10-11). Rolling P&L attribution doesn't accumulate.

### The Structural Observability Problem

The system produces a `GREEN` health status that answers "is everything running?" but not "is it running well?" These are different questions. A system trading on three-month-old Platt coefficients with calibration drift above the REFIT_NOW threshold reports `GREEN`.

---

## Part 3: Three Near-Complete Compounding Systems

These are systems where the hard computation already exists and only the persistence/emission layer is missing. Together they convert Zeus from "answers current state" to "accumulates performance history and surfaces degradation automatically."

### D. Edge Realization Log (Highest ROI, 90% built)

**What exists:** `src/state/edge_observation.py` — complete computation: `edge_realized = mean(outcome_i - p_posterior_i)` per strategy key, canonical settlement rows with deduplication, `metric_ready` gating, `SAMPLE_QUALITY_BOUNDARIES`. Module note: explicitly built to avoid phantom PnL drift of the deprecated JSON tracker.

**What's missing:** A write path. The module is read-only by design (`NO write path`), but this was an initial design choice, not a permanent constraint.

**Gap to close:** A post-settlement hook or daily job that calls `edge_observation.py` and appends results to `edge_realization_log` table (city, season, strategy_key, window_days, edge_realized, p_posterior_mean, n_trades, computed_at).

**Compounding mechanism:** Rolling 30-day edge realization tells you whether the edge thesis is decaying. After 90 days of data, you can tell per-city per-season whether Zeus is actually capturing the forecasted edge or whether it's mean-reverting to zero.

### B. Calibration Drift Time-Series (Medium cost)

**What exists:** `drift_detector.py` running daily, `DriftReport` with window_brier, baseline_brier, n_settlements_in_window, recommendation. `platt_models_v2` has `fitted_at` timestamps and `brier_insample`.

**What's missing:** Persistence. Can answer "is it drifting now?" Cannot answer "has it been trending worse for 14 days?"

**Gap to close:** `calibration_drift_log` table (city, season, checked_at, window_brier, baseline_brier, recommendation, n_settlements). Write on every `compute_drift` call. Wire `REFIT_NOW` to Discord alert.

**Compounding mechanism:** Slow model rot is the undetectable failure mode in calibration. Point-in-time Brier checks miss gradual drift. A time-series catches "degrading for 3 weeks" — this triggers a retrain decision before losses compound.

### A. Topology Improvement Queue (~10 lines)

**What exists:** `TopologyIssue` dataclass, `_issue_to_json()` serialization.

**What's missing:** Persistence. Issues go to stdout/return value only.

**Gap to close:** After issue collection in `topology_doctor.py`, append to `state/topology_improvement_queue.jsonl`. A periodic cron consumes entries and patches topology.yaml.

**Compounding mechanism:** Every agent session that hits a navigation gap appends one entry. Over time, topology becomes self-healing.

### Implementation Priority

D + B together: one schema migration, one post-settlement hook, one Discord wire. A is a separate 10-line patch to topology_doctor.py. C (source reliability ledger) is future work after 90+ days of settlement data.

---

## Part 4: Agent Workflow Failure Taxonomy

From meta-analysis of 45 feedback entries across all sessions.

### The Eight Failure Categories

| Category | Entry count | Upstream root |
|----------|-------------|---------------|
| Orchestrator tier misuse | 8 | R2: Tier pressure |
| Critic/review process gaps | 8 | R1: No verification discipline |
| Authority/truth source violations | 6 | R1 |
| Dispatch/workflow hygiene | 6 | R2 + R3 |
| Citation/reference rot | 7 | R1 |
| PR/commit workflow | 5 | R3: Context isolation is policy not mechanism |
| Multi-phase orchestration state loss | 5 | R3 |
| Premature generalization from single observation | 5 | R1 |

### Three Upstream Root Causes

**R1 — No structural pre-action verification discipline.** Citation rot, single-observation verdicts, 50% audit self-error rate, stash recovery with wrong content — all share one root: agents act on stated premises without verifying against ground truth. The rule "grep-verify every citation within 10 min" is documented but applies to multiple failure surfaces. It hasn't been internalized because it competes with time pressure at the worst moment.

**R2 — Tier pressure with no enforcement.** Opus coordinators running Bash lookups ($0.14/turn vs $0.001 haiku). Long opus briefs timing out (3/4 dispatches in one documented session). Orchestrators writing dead-worker post-mortems instead of respawning (25x tier-mismatch cost). Documented waste: $5-15 per long session. The rules exist; no mechanism enforces them.

**R3 — Context isolation is policy, not mechanism.** File-based handoff, subagent reuse via SendMessage, brief conciseness — all require active resistance to the path of least resistance. Five-plus feedback entries on this pattern indicates will power isn't sufficient.

### Feedbacks That Likely Haven't Stuck

- **`subagent_reuse_via_sendmessage`** — requires deliberate pause before spawn decision, under time pressure
- **`file_based_agent_handoff`** — requires resisting immediate inline reading of agent output
- **`one_failed_test_is_not_a_diagnosis`** — named "dominant orchestrator failure mode" in its own feedback; recurs despite documentation

### Blind Spots (Pain Without a Feedback Entry)

**Session-to-session reasoning continuity loss.** Sessions re-derive conclusions the previous session already reached, paying full investigation cost twice. No artifact captures "what did the previous session already verify?" `semantic_boot_before_forensics` covers manifest loading; it doesn't cover prior reasoning.

**Scope estimation mismatch.** Agent estimates 2 hours, task takes 8, operator reprioritizes. Adjacent feedbacks touch parts of this; scope estimation failure specifically has no antibody.

**Live-daemon change propagation.** Agents make changes without tracing all live-daemon consumers. `live_launch_multi_surface` covers launch-readiness; ongoing-change propagation is unaddressed.

### What Actually Works

The critic-loop methodology (scaffold → opus critic → REVISE) is functioning. 4/4 architectural SCAFFOLDs in a documented session had a SEV-1 caught before shipping. This is a working mechanism that should be institutionalized in the `orchestrator-delivery` skill template as a mandatory phase, not left as a memory item.

---

## Part 5: What Is Not a Problem (Verified)

### PR #74 Hook Downgrade — Deliberate Fix

Initial claim: "PR #74 systematically downgraded blocking hooks — a significant regression."

Verified: PR #74 (commit `d99bbf9500`, 2026-05-07) fixed three active blocking hook bugs including a full-Bash crash (#57). Two purpose-built blocking hooks were added in PR #91 for high-value enforcement points (LOC gate A1, merge comment check B2). Recommending "restore blocking hooks" without understanding those 3 bugs risks recreating the self-DoS crash.

### 1.9:1 Fix:Feat Ratio — Methodology Artifact

Verified: Majority of "fix" commits are critic-loop REVISE responses, K1 DB migration steps, and operations proof additions. ~20-25% are genuine reactive fixes. The ratio is expected for the scaffold→critic→fix methodology. The `fix(data)` cluster (17 commits) is a naming artifact for a single ECMWF rootfix migration (`595be61`, 3900 lines, 29 files) — not a repeat-offender category.

### healthcheck.py vs live_health_probe.py — Intentional Separation

Verified: `live_health_probe.py` is imported by `healthcheck.py` (line 100). Different callers, different outputs, different inspection depths. Real and narrow issue: path sets in `PROCESS_CODE_SURFACES` are identical but key names differ (`live_trading` vs `daemon`). Fix: one pytest assertion that path sets match across both scripts.

---

## Part 6: Priority Matrix

### Tier 0 — Runtime Risk

| Issue | Evidence | Action |
|-------|----------|--------|
| Calibration refit never activated | No `calibration_retrain_decision_*.md` exists; `state/refit_armed.json` status unknown | Check refit_armed.json immediately. If REFIT_NOW, run the gate. Evaluate scripting the scheduled retrain path |
| Drift signal not reaching production | `drift_detector.py` output discarded; system reports GREEN while potentially degraded | Wire REFIT_NOW → Discord alert; add drift_magnitude to health probe JSON |

### Tier 1 — Compounding Value (Infrastructure 90% Ready)

| System | Gap | Cost |
|--------|-----|------|
| Edge realization log (D) | Write path + `edge_realization_log` table | Low |
| Calibration drift time-series (B) | Persist DriftReport to `calibration_drift_log` table | Medium |
| Topology improvement queue (A) | ~10 lines in topology_doctor.py | Trivial |

### Tier 2 — Workflow Institutionalization

| Change | Evidence | Action |
|--------|----------|--------|
| SCAFFOLD critic as mandatory template step | 4/4 empirical SEV-1 catch rate | Add to `orchestrator-delivery` as named phase |
| Session-close handoff artifact | Blind spot, no mechanism | Stop hook → `state/session_handoff.md` |
| Three-probe verdict protocol | Named "dominant orchestrator failure mode" | BLOCKED/GREEN templates require `probes: [p1, p2, p3]` field |

### Tier 3 — Narrow Code Issues

| Issue | Fix |
|-------|-----|
| PROCESS_CODE_SURFACES path-set sync | One pytest assertion |
| digest_profiles.py drift | pytest with `--check` mode |
| ws_gap_guard.py test gap | Direct WSGapStatus state transition tests |
| Bot review (Copilot/Codex) | Retire; noise with per-push trigger cost |

### Not Recommended

- Restoring blocking hooks without understanding the 3 bugs that caused PR #74
- healthcheck/probe architectural consolidation
- Fix:feat ratio interventions

---

## Part 7: Adversarial Observations

**The calibration finding implies Zeus has been trading with static Platt coefficients since go-live.** The learning infrastructure was built correctly (R3 F2) and then left unarmed. Settlement patterns change — seasonal effects, WU rounding behavior, ECMWF bias shifts. The drift detector is producing daily signals that nobody is acting on. This is the most structurally important gap.

**The `GREEN` health status is answering the wrong question.** "Is the daemon alive?" barely needs asking. "Is the model fit?" and "Is the edge positive over the last 30 trades?" are the questions that matter for a live trading system. The health model is misaligned with what actually drives returns.

**The 45-entry feedback system is write-only.** Entries accumulate but there's no mechanism to measure which rules are being triggered, which are dormant, or which have been superseded. Without a read path that observes rule violations, MEMORY.md is a growing list that agents skim rather than internalize. The compounding value that was intended is not being realized.

---

*Produced by 7 investigation agents + 3 verification agents, 2026-05-16. All major claims verified against primary sources (commit messages, code, PR bodies) before inclusion.*
