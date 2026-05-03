# Zeus Remaining Tasks (non-strategy)

**Last refreshed**: 2026-05-02 21:05 CDT
**Scope**: Everything that is NOT a trading-strategy design decision. For strategy-design decisions, see `STRATEGIES_AND_GAPS.md` in this folder.

> Categorized by priority and theme. Each task has a current task ID for cross-reference with the in-session task list.

---

## A. Live-blocking ops (fix soon)

These directly prevent or degrade live trading right now or imminently.

| ID | Title | Status | Notes |
|---|---|---|---|
| #50 | Restart data-ingest daemon + verify catch-up | **DONE** | forecasts table now fresh; structural fix landed in #42 |
| #44 | TIGGE backfill `stage_failed:download_mx2t6_high` | OPEN | Operator: backfill is for HISTORICAL calibration only; not blocking live. Defer until catch-up structural fix lands |
| #45 | riskguard-live daemon fail-closed (DB + proxy) | **DONE** | Readiness substrate and fail-closed logic merged in PR #45 |
| #61 | riskguard-live flapping GREEN↔DATA_DEGRADED | **RESOLVED** | Duplicate stale PIDs from launchd kickstart issue; current `riskguard-live` PID writes Tick complete: GREEN |
| #46 | Verify `ZEUS_HARVESTER_LIVE_ENABLED=1` propagated | **DONE** | `ps eww` confirms env=1 in PID 9692 (live) + 30921 (data-ingest). harvester.py:24 + harvester_truth_writer.py:32 both use `os.environ.get(..., "0") != "1"` gate. ENABLED. |
| #47 | Restart live-trading daemon for opening_hunt 15min | **DONE** | Landed with PR #44; opening_hunt cadence now 15min |
| #15 | Live trading daemon restart (post oracle fix) | DONE 2026-05-02 15:30 CDT | Daemon GREEN, $199.40 wallet, no halts |

---

## B. Hardening / resilience (PRs in flight)

| ID | Title | Status | Notes |
|---|---|---|---|
| #13 | Data-ingest daemon: never-offline resilience | **DONE** | Boot-time staleness detection + force daily_tick merged in PR #42 |
| #14 | Comprehensive data collection design (hourly+daily Max+Min all sources) | OPEN, design only | |
| — | TIGGE T+2 same-day extraction | IN FLIGHT | Operator-claimed; same-day extraction catch-up; not for sub-agent pickup |

---

## C. Audit follow-ups (from haiku audits 2026-05-02)

| ID | Title | Status | Source |
|---|---|---|---|
| #32 | Audit ExpiringAssumption configs for `halt_trading` action | **DONE** | All 11 instances hardcode `kill_switch_action="revert_to_fallback"`. |
| #33 | PhysicalBounds graceful fallback to lat-band heuristics | **DONE** | try/except wrapper for IngestionGuard merged in PR #44 (`ebdc77e0`) |
| #25 | PR-A: oracle path centralization to `~/.openclaw/storage/zeus/oracle/` | OPEN | Deferred half of original oracle plan; PR-B (gate removal) merged in #40 |

---

## D. PR review / merge backlog

| Item | Status | Notes |
|---|---|---|
| PR #40 (oracle gate removal) | **MERGED** | `5fb06141` |
| PR #41 (PR #37 followup: legacy projection upsert + m5 latch race) | **MERGED** | `ef9ff379` |
| PR #42 (data-ingest boot-time catch-up resilience) | **MERGED** | `82fda114` |
| PR #43 (pre-commit hook skip-invariant marker) | **MERGED** | `07658bbb` |
| PR #44 (P0/P1 live blockers) | **MERGED** | `6e3b6a53` |
| PR #45 (data daemon readiness) | **MERGED** | `47d11d45` |
| PR #46 (healthcheck-riskguard-live-label) | OPEN | Operator's own PR |
| Local-only integration branch `live-restart-integration-2026-05-02` | **CLOSED** | Reconciled into main via PR #40-45 |

---

## E. Operational data work

| ID | Title | Status |
|---|---|---|
| #12 | Mop up remaining ~231 DST gap fills | OPEN |
| #18 | UMA timing alignment for shadow snapshot | OPEN |
| #23 | Bridge OGIMET snapshot parsing bug (Istanbul / Moscow / Tel Aviv corridors) | BACKLOG |

---

## F. ACTIVE WORK (Newly Proposed)

| Task | Status | Plan Dir |
|---|---|---|
| Opening-hunt entry data contract | PROPOSED | `docs/operations/task_2026-05-02_live_entry_data_contract/` |
| Strategy update execution sequence | PROPOSED | `docs/operations/task_2026-05-02_strategy_update_execution_plan/` |

---

## G. Structural debt (operator-named, deferred)

These are real structural problems where a band-aid would just kick the can:

| Topic | Why deferred |
|---|---|
| **$150 capital_base_usd hardcode** (haiku #35 found it) | Operator: "150 hardcoded is a complete mistake". The fix is NOT bumping 150 → 200; it's making wallet_balance the canonical truth and removing the metadata baseline entirely. Currently mitigated because daemon is GREEN post-restart. Real fix is a structural redesign of `load_portfolio` / `riskguard.py:1162` / `cycle_runner.py:466-485` consistency_lock direction |
| **Bankroll truth chain P0** (operator's 2026-05-01 finding) | Same root as $150 hardcode. RiskGuard runs on $150 fiction; consistency_lock compares canonical-DB to itself. Today live trading is "safe by accident" because the $5 safety_cap_usd masks the upside-asymmetry. Cannot raise the cap until the truth chain is rebuilt |

---

## H. Closed/superseded (no action needed)

| ID | Title | Notes |
|---|---|---|
| #40 | Oracle gate removal | Merged PR #40 (`5fb06141`) |
| #41 | PR #37 followups | Merged PR #41 (`ef9ff379`) |
| #42 | Boot catch-up resilience | Merged PR #42 (`82fda114`) |
| #43 | [skip-invariant] pre-commit support | Merged PR #43 (`07658bbb`) |
| #44 | Live blocker fixes | Merged PR #44 (`6e3b6a53`) |
| #45 | Data daemon readiness | Merged PR #45 (`47d11d45`) |
| #61 | riskguard flap | Resolved (stale launchd PIDs) |
| #33 | PhysicalBounds graceful fallback | Merged PR #44 (`ebdc77e0`) |
| #1-#11 | Various data backfill / DST work | Done |
| #16 | Critic adversarial review of oracle fix plan | Done |
| #17 | Fail-closed gate audit | Done; spawned #32 + #33 |
| #24 | PR-B: oracle gate removal | Done = PR #40 merged |
| #26 | git-master merge #38/#39, fast-forward #37 | Done |
| #27, #28 | PR #40 codex review fixes | Done |
| #29, #30 | PR #41 P1 fixes | Done |
| #31 | Hook redesign | Done |
| #34 | Sonnet PR review fixes | Done |
| #35 | $150 hardcode root cause finding | Done — finding moved to §G |
| #36 | Wait for first opening_hunt cycle | Deleted (premise was wrong) |
| #37, #38, #51, #52, #53, #55 | Strategy design gaps | Moved to `STRATEGIES_AND_GAPS.md` |
| #39-#43 | Full-launch blocker audit haikus A-E | Done |
| #48, #49 | Scout investigations | Done |
| #54 | Full strategy catalog haiku | Done |

---

## Sub-Agent Eligible (non-strategy, non-data-daemon)

Concrete tasks available for pickup without operator/daemon conflict:

1. **#12 DST gap fills mop-up**: ~231 remaining entries (single-threaded, low risk).
2. **#18 UMA timing alignment**: Align shadow snapshot timings with UMA resolution windows.
3. **#25 PR-A oracle path centralization**: Move all oracle artifacts to `~/.openclaw/storage/zeus/oracle/`.
4. **Live_entry_data_contract Phase 6**: Split `source_health` / `data_coverage` / `entry_readiness` in healthcheck (partial overlap with PR #46; check PR #46 status first).

---

## Summary

- **Live trading daemon is GREEN** ($199.40 wallet, oracle gate removed, 15min cadence).
- **riskguard-live is GREEN** (flap #61 resolved).
- **Data daemon is GREEN** (catch-up resilience merged in #42; readiness substrate merged in #45).
- **Next major workstreams**: opening-hunt entry data contract and strategy update execution.
