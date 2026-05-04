# A2 Audit — Silent except-Exception Loggers (PR-A scope)

**Run:** 2026-05-04T03:26Z (post-compact, pre-fix)
**Authority:** `FIX_PLAN.md` v2 PR-A pre.1 audit step
**Scope:** `src/engine/*.py` + `src/control/*.py` + `src/engine/evaluator.py`
**Method:** `grep -nA5 'except Exception' ... | grep -B1 'logger.\(error\|warning\)' | grep -v 'exc_info=True\|logger.exception'`

---

## Categorization

| Severity | Action |
|---|---|
| **ERROR sites in entry/discovery/monitor path** | Fix in PR-A — these are the swallow-traceback shape that hid D1 |
| **ERROR sites in cycle/boot prechecks** | Fix in PR-A — same shape, same risk |
| **WARNING sites** | DEFER to PR-B AST invariant — too many to bundle without scope creep |

---

## PR-A targets (12 sites — all `logger.error` without `exc_info=True`)

| # | File:Line | Snippet (truncated) | Why fix |
|---|---|---|---|
| 1 | `src/engine/cycle_runtime.py:2988` | `deps.logger.error("Evaluation failed for %s %s: %s", city.name, candidate.target_date, e)` | **Primary target.** This site swallowed every D1 ValueError for 16 days. |
| 2 | `src/engine/cycle_runtime.py:1712` | `deps.logger.error("Monitor failed for %s: %s", pos.trade_id, e)` | Sister to #1 in monitor loop. Same swallow shape; could hide a parallel ValueError category. |
| 3 | `src/engine/cycle_runtime.py:2198` | `deps.logger.error("telemetry write failed, cycle flagged degraded: %s", exc)` | Marks cycle DEGRADED — operator needs traceback to diagnose. |
| 4 | `src/engine/cycle_runner.py:504` | `logger.error("PortfolioGovernor cycle-start refresh failed: %s; blocking new entries fail-closed", _governor_start_exc)` | Multi-line. Fail-closed entry block — traceback critical. |
| 5 | `src/engine/cycle_runner.py:528` | `logger.error("Chain sync FAILED — entries will be blocked: %s", exc)` | Entry-blocking. |
| 6 | `src/engine/cycle_runner.py:557` | `logger.error("command_recovery raised; continuing cycle: %s", exc)` | Silent recovery — traceback essential. |
| 7 | `src/engine/cycle_runner.py:633` | `logger.error("runtime_posture read raised unexpectedly: %s; treating as NO_NEW_ENTRIES", _posture_exc)` | Multi-line. Posture flip silently. |
| 8 | `src/engine/cycle_runner.py:642` | `logger.error("CutoverGuard summary failed: %s; blocking new entries fail-closed", _cutover_exc)` | Multi-line. Fail-closed. |
| 9 | `src/engine/cycle_runner.py:656` | `logger.error("HeartbeatSupervisor summary failed: %s; blocking new entries fail-closed", _heartbeat_exc)` | Multi-line. Fail-closed. |
| 10 | `src/engine/cycle_runner.py:670` | `logger.error("WS user-channel guard summary failed: %s; blocking new entries fail-closed", _ws_gap_exc)` | Multi-line. Fail-closed. |
| 11 | `src/engine/cycle_runner.py:694` | `logger.error("PortfolioGovernor summary failed: %s; blocking new entries fail-closed", _governor_exc)` | Multi-line. Fail-closed. |
| 12 | `src/engine/evaluator.py:2627` | `logger.error("Full-family hypothesis scan unavailable; failing closed for entry selection: %s", exc)` | Entry-path fail-closed. |

**All 12 are ERROR-level, all are entry-blocking or cycle-degrading paths, all swallow the traceback.**

---

## DEFERRED to PR-B (WARNING sites — counted, not fixed today)

`logger.warning` sites without `exc_info=True` exist throughout `src/engine/cycle_runner.py`, `cycle_runtime.py`, and `evaluator.py` (orphan-cancel, microstructure, telemetry warnings, fee rate fallbacks, etc.). Count is non-trivial (~25-30 sites by spot grep). These are intentional soft warnings; many would produce log noise if traceback added. PR-B's `tests/test_logger_exc_info_invariant.py` (per FIX_PLAN B5) installs an AST-based gate with explicit allow-list — that is the right venue, not PR-A.

---

## Sites EXCLUDED from PR-A (already correct or intentional)

- `cycle_runtime.py:840` (`no such table` warning) — intentional pattern-match suppression
- `cycle_runtime.py:2089` (`isinstance(e, ObservationUnavailableError)`) — type-narrowed warning, message text is the diagnostic
- All `logger.warning("Could not query trade_decisions for orphan guard: %s", exc)` shape sites (cycle_runtime.py:807, 868, 966, etc.) — soft warnings

---

## Decision: PR-A applies `exc_info=True` to all 12 ERROR sites

Single atomic change. Diff: ~12 lines. Test surface: same code paths, no behavior change beyond logging shape. No regression risk beyond noisier ERROR logs (which is the entire point).
