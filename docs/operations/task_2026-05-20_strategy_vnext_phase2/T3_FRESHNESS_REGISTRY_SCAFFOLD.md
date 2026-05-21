# T3 FreshnessRegistry — SCAFFOLD Design Doc

**Authority**: PHASE_2_ULTRAPLAN.md v3.1 §6, sha `e8b28793d9`
**Branch**: `feat/phase2-t3-freshness-registry-20260520`
**Base**: origin/main @ `e8b28793d9` (T1+T2 merged; SCHEMA_VERSION 15)
**Status**: PRODUCTION (migration complete — 10 callsites migrated, antibody GREEN)
**Created**: 2026-05-20

---

## §1 Problem Statement

T3 is code-only (no DB table, no SCHEMA_VERSION bump).

**Evidence from §6.1 grep-verified patterns** (re-derived against `git show origin/main` — all line numbers current as of SCAFFOLD commit):

~10 sites across `src/` perform ad-hoc freshness comparisons using locally-scoped threshold
constants instead of routing through a centralized policy:

| # | File | Line | Pattern | Threshold |
|---|---|---|---|---|
| 1 | `src/state/collateral_ledger.py` | 724 | `age_seconds > COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS` | 180.0 s (REFRESH_CADENCE + JITTER_BUDGET) |
| 2 | `src/engine/evaluator.py` | 733 | `age_hours > DAY0_EXECUTABLE_OBSERVATION_MAX_AGE_HOURS` | 1.0 h = 3600 s |
| 3 | `src/strategy/oracle_estimator.py` | 162 | `artifact_age_hours > STALE_AGE_HOURS` | 168 h = 604800 s |
| 4 | `src/riskguard/riskguard.py` | 1407 | `age_seconds > 300` | 300 s (hardcoded) |
| 5 | `src/control/heartbeat_supervisor.py` | 272 | `age_seconds > max_age_seconds` | dynamic (env: `ZEUS_HEARTBEAT_STATUS_MAX_AGE_SECONDS`, default 30 s) |
| 6 | `src/control/heartbeat_supervisor.py` | 348 | `age_seconds > self._max_age_seconds` | dynamic (same env, via `self._max_age_seconds`) |
| 7 | `src/state/db.py` | 7765 | `age_seconds > max_age_seconds` | dynamic (param default 300 s) |
| 8 | `src/state/venue_command_repo.py` | 1308 | `age_seconds > 60` | 60 s (hardcoded) |
| 9 | `src/state/venue_command_repo.py` | 1525 | `age_seconds > 60` | 60 s (hardcoded, duplicate clearance check) |
| 10 | `src/venue/polymarket_v2_adapter.py` | 2631 | `age_seconds > window_seconds` | dynamic (snapshot.freshness_window_seconds) |

**Note on ULTRAPLAN line citations vs SCAFFOLD findings**: The ULTRAPLAN §6.1 cited stale line numbers
(collateral_ledger.py:724, evaluator.py:705, etc.). This SCAFFOLD re-derived ALL line numbers against
`git show origin/main` per autopilot §11.1 step 2.1.G9 line-anchor rule. Actual evaluator.py site is
line 733 (not 705). Additionally, the ULTRAPLAN listed 9 "heartbeat_supervisor:221,297" and
"venue_command_repo:1291" — actual current lines are heartbeat_supervisor:272/348 and
venue_command_repo:1308/1525 (two sites, not one). Total confirmed freshness gates: **10**.

**Consequences of scattered thresholds**:
- System-wide freshness tuning requires grep-and-patch (recurrence risk)
- No unified observability (`freshness_<source>_<level>_total` counters absent)
- Three freshness tiers (FRESH/STALE/EXPIRED) conflated into binary gate
- Dynamic-threshold sources (heartbeat, snapshot) lack a consistent policy layer

---

## §2 Production Surface (4 Artifacts)

| Artifact | Path | Type |
|---|---|---|
| Registry + Enum (PRODUCTION) | `src/contracts/freshness_registry.py` | `FreshnessRegistry` class + `FreshnessLevel(IntEnum)` + SOURCE_THRESHOLDS table + `evaluate(source_id, age_seconds) -> FreshnessLevel` |
| Antibody (GREEN) | `tests/test_inv_freshness_no_ad_hoc_checks.py` | Regex scan, xfail removed, 0 offenders — permanent GREEN antibody |
| Production tests | `tests/test_contracts_freshness_registry.py` | 23 tests: tier boundaries, DYNAMIC ValueError, counter emission |
| Schema bump | **NONE** — code-only, no DB surface | N/A |
| Manifest | `architecture/source_rationale.yaml` | T3 entry (production, no db_table_ownership) |

**All 10 callsite migrations complete** — see §3 for replacement patterns.

---

## §3 Migration Plan — 10 Callsite Table

Line numbers verified against `git show origin/main` at SCAFFOLD commit time.

| # | File | Line | Current Ad-Hoc Pattern | FreshnessRegistry Replacement | Notes |
|---|---|---|---|---|---|
| 1 | `src/state/collateral_ledger.py` | 724 | `if age_seconds > COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS:` | `if registry.evaluate("collateral_snapshot", age_seconds) >= FreshnessLevel.STALE:` | Preserve existing error log + return path |
| 2 | `src/engine/evaluator.py` | 733 | `if age_hours > DAY0_EXECUTABLE_OBSERVATION_MAX_AGE_HOURS:` | `if registry.evaluate("day0_executable_observation", age_seconds) >= FreshnessLevel.STALE:` | `age_seconds = age_hours * 3600`; confirm var available at site or derive from existing age_seconds above |
| 3 | `src/strategy/oracle_estimator.py` | 162 | `if artifact_age_hours is not None and artifact_age_hours > STALE_AGE_HOURS:` | `if artifact_age_hours is not None and registry.evaluate("oracle_artifact", artifact_age_hours * 3600) >= FreshnessLevel.STALE:` | None-guard preserved; multiply to seconds |
| 4 | `src/riskguard/riskguard.py` | 1407 | `if age_seconds > 300:` | `if registry.evaluate("riskguard_last_check", age_seconds) >= FreshnessLevel.STALE:` | Hardcoded 300 absorbed into SOURCE_THRESHOLDS |
| 5 | `src/control/heartbeat_supervisor.py` | 272 | `if age_seconds < 0 or age_seconds > max_age_seconds:` | `if age_seconds < 0 or registry.evaluate("heartbeat_restart_seed", age_seconds, override_threshold_seconds=max_age_seconds) >= FreshnessLevel.STALE:` | Sign check preserved; DYNAMIC_THRESHOLD source with override |
| 6 | `src/control/heartbeat_supervisor.py` | 348 | `if age_seconds > self._max_age_seconds:` | `if registry.evaluate("heartbeat_status", age_seconds, override_threshold_seconds=self._max_age_seconds) >= FreshnessLevel.STALE:` | DYNAMIC_THRESHOLD; override from env-configured self._max_age_seconds |
| 7 | `src/state/db.py` | 7765 | `if age_seconds is None or age_seconds > max_age_seconds:` | `if age_seconds is None or registry.evaluate("strategy_health", age_seconds, override_threshold_seconds=max_age_seconds) >= FreshnessLevel.STALE:` | None-guard preserved; DYNAMIC_THRESHOLD with caller-supplied override |
| 8 | `src/state/venue_command_repo.py` | 1308 | `if age_seconds < -5 or age_seconds > 60:` | `if age_seconds < -5 or registry.evaluate("venue_clearance", age_seconds) >= FreshnessLevel.STALE:` | Sign-check preserved; hardcoded 60 absorbed into SOURCE_THRESHOLDS |
| 9 | `src/state/venue_command_repo.py` | 1525 | `if age_seconds < -5 or age_seconds > 60:` | `if age_seconds < -5 or registry.evaluate("venue_clearance", age_seconds) >= FreshnessLevel.STALE:` | Duplicate clearance check; same source_id as site 8 |
| 10 | `src/venue/polymarket_v2_adapter.py` | 2631 | `if age_seconds > window_seconds:` | `if registry.evaluate("executable_snapshot", age_seconds, override_threshold_seconds=window_seconds) >= FreshnessLevel.STALE:` | DYNAMIC_THRESHOLD from snapshot.freshness_window_seconds |

**Registry import for production pass**:
```python
from src.contracts.freshness_registry import FreshnessRegistry, FreshnessLevel
registry = FreshnessRegistry()  # module-level singleton or inline
```

**ULTRAPLAN §13 default decision** (threshold values): initial defaults from existing callsite
literals as above; Phase 3+ may tune via `FreshnessRegistry(thresholds={...})` override.

---

## §4 Production-Pass Checklist

- [x] Fill `FreshnessRegistry.evaluate()` body: look up source_id in self._thresholds; handle
      DYNAMIC_THRESHOLD check (raise ValueError if override_threshold_seconds not supplied);
      derive degraded/stale/expired boundaries; return FreshnessLevel
- [x] Wire `_emit_counter()` to `_cnt_inc(f"freshness_{source_id}_{level}_total")`
      (src/observability pattern — verify existing `_cnt_inc` import path)
- [x] Migrate all 10 callsites per §3 table
- [x] Run antibody `test_inv_freshness_no_ad_hoc_checks.py` — must report XPASS (all offenders
      cleared); remove @pytest.mark.xfail to harden as GREEN
- [x] Add production tests: evaluate() returns FRESH/DEGRADED/STALE/EXPIRED at tier boundaries;
      DYNAMIC_THRESHOLD source raises ValueError when override absent; counter emission
- [x] Add `_cnt_inc` to observability counter list if not already present
- [ ] Verify full regression suite passes: `python -m pytest tests/ -x`
- [x] Grep-verify: `grep -rn "age_seconds\s*>\|age_hours\s*>" src/` returns zero non-allowlisted hits

---

## §4.1 Phase-3 Carryover — Deferred Freshness Gates

The following freshness gates are **out of scope for T3** (they use different variable names,
timedelta objects, or per-source budget loops). They remain as ad-hoc comparisons pending a
Phase-3 registry expansion pass.

| # | File | Line | Pattern | Notes |
|---|------|------|---------|-------|
| 1 | `src/control/freshness_gate.py` | 177 | `written_at_age > ABSENT_MID_RUN_THRESHOLD_SECONDS` | `written_at_age` not in T3 scope |
| 2 | `src/control/freshness_gate.py` | 185 | `written_at_age > 90` | same variable family |
| 3 | `src/control/freshness_gate.py` | 206 | per-source `FRESHNESS_BUDGETS` loop | `age <= budget_seconds` — budget dict |
| 4 | `src/control/live_health.py` | 104 | `age > STATUS_FRESH_BUDGET_SECONDS` | heartbeat surface |
| 5 | `src/control/live_health.py` | 174 | `age > STATUS_FRESH_BUDGET_SECONDS` | status_summary surface |
| 6 | `src/runtime/bankroll_provider.py` | 128 | `cached_age > fail_closed_after_seconds` | **PRIORITY: safety-adjacent fail-closed gate** |
| 7 | `src/ingest_main.py` | 479 | `staleness_h > threshold_h` | forecast boot staleness |
| 8 | `src/ingest_main.py` | 506 | `solar_staleness_h > threshold_h` | solar_daily boot staleness |
| 9 | `src/riskguard/riskguard.py` | 327 | `staleness > TRAILING_LOSS_REFERENCE_STALENESS_TOLERANCE` | timedelta gate |

**`bankroll_provider.py:128` is PRIORITY** — it guards the fail-closed path for live bankroll
reads.  Any staleness bypass here could let the system trade with a stale/zero bankroll.
Phase-3 must add `cached_age` / `staleness_h` / `staleness` to the registry scan scope AND
migrate this gate before any bankroll-adjacent code changes.
