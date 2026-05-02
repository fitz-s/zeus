# Zeus Remaining Tasks (non-strategy)

**Created**: 2026-05-02
**Scope**: Everything that is NOT a trading-strategy design decision. For strategy-design decisions, see `STRATEGIES_AND_GAPS.md` in this folder.

> Categorized by priority and theme. Each task has a current task ID for cross-reference with the in-session task list.

---

## A. Live-blocking ops (fix soon)

These directly prevent or degrade live trading right now or imminently.

| ID | Title | Status | Notes |
|---|---|---|---|
| #50 | Restart data-ingest daemon + verify catch-up | DONE manually 2026-05-02 16:07 CDT | forecasts table now fresh; sonnet writing structural fix in #13 |
| #44 | TIGGE backfill `stage_failed:download_mx2t6_high` | OPEN | Operator: backfill is for HISTORICAL calibration only; not blocking live. Defer until catch-up structural fix lands |
| #45 | riskguard-live daemon fail-closed (DB + proxy) | **REOPENED P0** | **Earlier note WRONG**. cycle_runner.py:461 calls `get_current_level()` reading risk_state.db; cycle_runner.py:570 reads `get_force_exit_review()`. riskguard.py:1175 enforces 5-min staleness fail-closed RED. risk_state.db is load-bearing for live entries. See #61 for active flap diagnosis. |
| #61 | **NEW**: riskguard-live flapping GREEN↔DATA_DEGRADED every ~15s | OPEN P0 | Two riskguard PIDs (57490 + 57505); both fail with `py_clob_client_v2 Connection refused` (Errno 61) + `sqlite3.OperationalError: unable to open database file`. Live cycle entries probabilistically blocked when it samples a DATA_DEGRADED row. Operator decision needed: kill duplicate? fix bankroll cache tolerance? start the local clob proxy daemon? |
| #46 | Verify `ZEUS_HARVESTER_LIVE_ENABLED=1` propagated | **DONE 2026-05-02 16:30 CDT** | `ps eww` confirms env=1 in PID 9692 (live) + 30921 (data-ingest). harvester.py:24 + harvester_truth_writer.py:32 both use `os.environ.get(..., "0") != "1"` gate. ENABLED. |
| #47 | Restart live-trading daemon for opening_hunt 15min | OPEN | Bundles with sonnet's data-ingest resilience PR or with strategy-gap PRs |
| #15 | Live trading daemon restart (post oracle fix) | DONE 2026-05-02 15:30 CDT | Daemon GREEN, $199.40 wallet, no halts |

---

## B. Hardening / resilience (PRs in flight)

| ID | Title | Status |
|---|---|---|
| #13 | Data-ingest daemon: never-offline resilience | IN PROGRESS — sonnet writing PR for boot-time staleness detection + force daily_tick |
| #14 | Comprehensive data collection design (hourly+daily Max+Min all sources) | OPEN, design only |

---

## C. Audit follow-ups (from haiku audits 2026-05-02)

| ID | Title | Status | Source |
|---|---|---|---|
| #32 | Audit ExpiringAssumption configs for `halt_trading` action | **DONE 2026-05-02 16:30 CDT** | All 11 instances in `src/state/portfolio.py:1851-1981` hardcode `kill_switch_action="revert_to_fallback"`. No config drift risk (action is hardcoded, not config-derived). Optional cleanup: strip `halt_trading` branch from `src/contracts/expiring_assumption.py:18` entirely — deferred low-priority cosmetic. |
| #33 | PhysicalBounds graceful fallback to lat-band heuristics | **CONFIRMED NEEDS_FIX** | `src/data/ingestion_guard.py:113` does bare `open()` + `json.load()` for `config/city_monthly_bounds.json`. Lat-band fallback `_check_lat_band_bounds` exists but unreachable if constructor crashes. Currently file IS on disk so no live crash, but resilience gap. Fix: try/except → `self._bounds={}` so existing fallback fires. ~6-8 LOC. Live callers: `daily_obs_append.py:535` + `hourly_instants_append.py:71`. |
| #25 | PR-A: oracle path centralization to `~/.openclaw/storage/zeus/oracle/` | OPEN | Deferred half of original oracle plan; PR-B (gate removal) merged in #40 |

---

## D. PR review / merge backlog

| Item | Status |
|---|---|
| PR #40 (oracle gate removal) | OPEN, codex review fixes pushed at `bdd4be3a` |
| PR #41 (PR #37 followup: legacy projection upsert + m5 latch race + hook bypass) | OPEN, fixes pushed at `262c61b0` |
| (forthcoming) PR for `_k2_startup_catch_up` boot resilience | sonnet writing now |
| Local-only integration branch `live-restart-integration-2026-05-02` | At `3193ea56`. Cleanup after PR #40 + #41 merged |

---

## E. Operational data work

| ID | Title | Status |
|---|---|---|
| #12 | Mop up remaining ~231 DST gap fills (single-threaded, post Lagos batch failure) | OPEN |
| #18 | UMA timing alignment for shadow snapshot | OPEN |
| #23 | Bridge OGIMET snapshot parsing bug (Istanbul / Moscow / Tel Aviv corridors) | BACKLOG; observation-side noise, not settlement-blocking |

---

## F. BACKLOG (deferred — design decisions or low priority)

| ID | Title |
|---|---|
| #19 | Tier-based oracle degradation (T1-T5) |
| #20 | Floor file with source/authority/expires (Fitz Constraint #4) |
| #21 | availability_status propagation for degraded oracle |
| #22 | Alert channel for oracle stale events |

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
| #1-#11 | Various data backfill / DST work | Done |
| #16 | Critic adversarial review of oracle fix plan | Done |
| #17 | Fail-closed gate audit | Done; spawned #32 + #33 |
| #24 | PR-B: oracle gate removal | Done = PR #40 merge pending |
| #26 | git-master merge #38/#39, fast-forward #37 | Done |
| #27, #28 | PR #40 codex review fixes | Done |
| #29, #30 | PR #41 P1 fixes | Done |
| #31 | Hook redesign | Done |
| #34 | Sonnet PR review fixes | Done |
| #35 | $150 hardcode root cause finding | Done — finding moved to §G above as deferred structural debt |
| #36 | Wait for first opening_hunt cycle | Deleted (premise was wrong; cycle did fire normally) |
| #37, #38, #51, #52, #53, #55 | Strategy design gaps | Moved to `STRATEGIES_AND_GAPS.md` §3 |
| #39-#43 | Full-launch blocker audit haikus A-E | Done |
| #48, #49 | Scout investigations | Done |
| #54 | Full strategy catalog haiku | Done — output is `STRATEGIES_AND_GAPS.md` §1 |

---

## Summary

- **Live trading daemon is GREEN** ($199.40 wallet, oracle gate removed) **but riskguard-live is flapping** (#61) — every ~15s a DATA_DEGRADED row appears in risk_state.db, and cycle_runner.py:461 reads it. Live entries are throttled probabilistically.
- **No orders placed** because of TWO independent layers, not one:
  1. Strategy-coverage gaps (§A-D in STRATEGIES_AND_GAPS.md) — most days no DiscoveryMode is in its window
  2. riskguard flap (#61) — even when a strategy fires, there's a ~50% chance the risk_state row sampled is DATA_DEGRADED → reduce_only
- **Verification 2026-05-02 16:30 CDT**: #46 ENABLED ✓, #32 CLEAN ✓, #33 NEEDS_FIX (~6-8 LOC), #45 inversion corrected.
- **In-flight implementation work**: sonnet writing data-ingest boot resilience PR (branch `data-ingest-boot-resilience-2026-05-02`).
- **All other items** here are queued, prioritized, or deferred per operator-named structural debt rules.
