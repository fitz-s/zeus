# T5 Position.market_slug — SCAFFOLD Design Doc

**Created**: 2026-05-20 by executor (sonnet)
**Authority**: `PHASE_2_ULTRAPLAN.md` v3.1 §8 (branch `docs/phase2-ultraplan-20260520`, sha `00c2399742`)
**Status**: SCAFFOLD — wave-critic reviews before production GREEN pass

---

## §1 Problem (per ULTRAPLAN v3 §8.1, grep-verified)

`Position` dataclass (`src/state/portfolio.py`) carries `market_id` (condition_id alias)
and `condition_id` but has **zero `market_slug` references**. The monitor_refresh
Day0 nowcast path (`_refresh_day0_observation`) computes the full Day0 posterior but
cannot write to `day0_nowcast_runs` (which requires `market_slug` as its primary key).

Phase 2 T5 adds `market_slug: Optional[str] = None` as a **JSON-only field** (no SQL
ALTER, no SCHEMA_VERSION bump per G8a — Position is persisted in `positions.json`,
not in any SQL table on world DB) and wires the monitor_refresh nowcast call-site.

**Grep-verify results (AUTHOR-side G6/G8/G9, line-anchor check)**:

| Check | Path:Line | `sed -n '<line>p'` result (origin/main) | Status |
|---|---|---|---|
| G6-a | `portfolio.py:288` | `class Position:` | PASS |
| G6-b | `portfolio.py:60` | `POSITIONS_PATH = state_path("positions.json")` | PASS |
| G6-c | `portfolio.py:299` | `    market_id: str` | PASS |
| G6-d | `portfolio.py:381` | `    condition_id: str = ""` | PASS |
| G8a-1 | `portfolio.py:1432` | `    path = path or POSITIONS_PATH` (load) | PASS |
| G8a-2 | `portfolio.py:1457` | `    elif path == POSITIONS_PATH:` (load) | PASS |
| G8a-3 | `portfolio.py:1584` | `    path = path or POSITIONS_PATH` (save) | PASS |
| G8b-1 | `portfolio.py:1235` | `        pos = Position(**filtered)` (reflection load) | PASS |
| G8b-2 | `portfolio.py:1359` | `    return Position(**payload)` (projection load) | PASS |
| G8c | `monitor_refresh.py:838` (area) | `    extrema, hours_remaining = ...` (within `_refresh_day0_observation`) | PASS |

**Checklist: 10/10 PASS**

---

## §2 Production surface (SCAFFOLD)

| Artifact | Path | Type |
|---|---|---|
| Position field add | `src/state/portfolio.py:432–440` (after pnl, before `__post_init__`) | dataclass field + doc comment |
| JSON round-trip | reflection paths at 1235/1359 auto-flow via `fields(Position)` filter | no code change needed |
| monitor_refresh wiring | `src/engine/monitor_refresh.py:_refresh_day0_observation` end-of-function | `_maybe_write_day0_nowcast` call + helper stub |
| Antibodies | `tests/test_position_market_slug_persistence.py` + `tests/test_position_market_slug_backward_compat.py` + `tests/test_monitor_refresh_nowcast_wiring.py` | 3 test files |
| Design doc | this file | SCAFFOLD doc |
| source_rationale stub | `architecture/source_rationale.yaml` | T5 JSON-only entry |

---

## §3 Cross-file constructor status

Per ULTRAPLAN §1 T5 note: constructors at:
- `src/state/chain_reconciliation.py:1015` — kwargs, auto-default PASS
- `src/riskguard/riskguard.py:116` — kwargs, auto-default PASS
- `src/engine/cycle_runtime.py:~1811` — kwargs, auto-default PASS (line 1556 was stale in ULTRAPLAN, actual constructor at 1811)

**No explicit edits required to cross-file constructors.** P0-1 overlap: NONE — cycle_runtime.py not edited.

---

## §4 Antibody status

| Test | File | Expected verdict |
|---|---|---|
| JSON round-trip | `tests/test_position_market_slug_persistence.py` | GREEN (reflection path auto-flows) |
| Backward-compat (v1 load) | `tests/test_position_market_slug_backward_compat.py` | GREEN (default=None) |
| nowcast wiring gate | `tests/test_monitor_refresh_nowcast_wiring.py` | GREEN (write_nowcast_run wired, xfail removed, PR #236) |

`test_nowcast_write_called_when_gate_passes` is GREEN: `write_nowcast_run` is imported lazily,
`read_latest_platt_fit` is called to obtain `fit_run_id`, and the real DB write fires with all
required kwargs. xfail removed in the Phase 2 T5 GREEN commit (`9b76f8ba29`).

---

## §5 Phase 3 carryover

`position_events` trade-DB payload `market_slug` field deferred to Phase 3 per
ULTRAPLAN §8.5.

---

## §6 Backfill plan (one-shot, operator-dispatched)

Read `positions.json`, JOIN `market_events_v2.market_slug ON condition_id` (per-world-DB
single-conn), write back. Implemented as a separate operator-script; NOT in T5 scope.
