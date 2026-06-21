# LAYER 2 — held-belief read-through recompute (implementation evidence)

Created: 2026-06-21
Last audited: 2026-06-21
Authority basis: docs/evidence/live_order_pathology/2026-06-21_forward_chain_diagnosis.md
  "CHOSEN FIX (consult-validated, two layers)" — LAYER 2. Base commit 95303d70
  (branch `fix/held-belief-freeze-reheal-20260621`, Layer 1). Implemented on
  branch `fix/held-belief-readthrough-L2-20260621`. NOT deployed, NOT merged.

## Problem (verified, live)

A non-day0 HELD position's replacement belief comes from a cached
`forecast_posteriors` row. When that row is stale/missing, the monitor
(`monitor_probability_refresh`, the `if not _would_use_day0_lane:` branch)
fail-closed to HOLD (BELIEF_AUTHORITY_FAULT) — correctly refusing a cold legacy
center — but NEVER recomputed, so the held belief was frozen and the conservative
`CI_SEPARATED_REVERSAL` exit was starved (positions rode physics reversals to full
loss = the live −$27.63). The forecast scan does not keep held-family posteriors
fresh (chronic; `monitor_refresh.py:389` "Karachi monitored its whole life with
stale belief").

## Fix (Layer 2, minimal scope = consult Stage 1+2 only)

Before fail-closing, attempt a SYNCHRONOUS single-family read-through recompute of
the held family's replacement posterior via the CANONICAL fusion compute using
whatever single_runs are CURRENTLY persisted. Fresh posterior → return it as FRESH
belief (is_fresh=True) so the exit organ can arm CI_SEPARATED_REVERSAL this cycle.
Insufficient inputs → fail-closed HOLD + durable retryable belief-debt marker +
existing reseed enqueue (never a silent permanent freeze).

## Files + functions changed (file:line)

### 1. Read-only compute entrypoint (the extraction)
`src/data/replacement_forecast_materializer.py`
- NEW `_PosteriorComputeResult` dataclass (~line 977): the pure, no-DB-write product
  of the posterior compute (q, q_lcb_map, q_ucb_map, mu_star, predictive_sigma_c,
  provider counts, capture_status, replacement_q_mode, + all values the INSERT and
  its identity hash / provenance consume), carrying an explicit `live_eligible` flag.
- NEW `_compute_posterior_payload(conn, request, *, metric, anchor_id) -> _PosteriorComputeResult`
  (~line 1738): the canonical multi-model Bayes-precision fusion + fused-q shape +
  certified bootstrap bounds, EXTRACTED VERBATIM from `_insert_posterior` (the value
  build + the `not live_layer` gate + the provenance payload assembly). ZERO DB
  writes — only the override's read paths touch `conn`. The historical
  `if not live_layer: return None` becomes a `_PosteriorComputeResult(live_eligible=False, ...)`
  return so a read-only caller can distinguish "fresh-but-not-live" from "blocked".
- `_insert_posterior(conn, request, *, metric, anchor_id) -> int | None` (~line 2588):
  now a thin wrapper — calls `_compute_posterior_payload`, maps `not live_eligible -> None`
  (byte-identical write contract), then runs ONLY the identity hash + INSERT (unchanged).
- NEW PUBLIC `compute_replacement_posterior_readonly(conn, request) -> _PosteriorComputeResult | None`
  (~line 2688): the read-only entrypoint. Runs the pure pre-compute guards
  (`_prewrite_block_reasons`, `_precision_guard_block_reason`) then
  `_compute_posterior_payload`. Writes nothing. `conn` MUST be a forecasts-MAIN conn.

### 2. Reusable request assembly (seed JSON -> dataclass)
`src/data/replacement_forecast_materialization_request_builder.py`
- NEW `build_materialize_request_dataclass(request_json, *, base_dir) -> ReplacementForecastMaterializeRequest`
  (~line 250): constructs the dataclass from a READY request-JSON (anchor extracted
  from the on-disk Open-Meteo payload + precision guard from on-disk metadata + bins).
  Single source of truth for the seed→dataclass conversion; NO network fetch, NO DB write.
- NEW `_TemperatureBin` + `_bins_to_temperature_bins` (~line 350): the bin shape the
  dataclass requires (mirrors the queue worker script's local bin type).

### 3. Monitor wiring (the insertion point)
`src/engine/monitor_refresh.py`
- NEW belief-debt ledger `_belief_debt_first_failed_at` / `_belief_debt_attempts` +
  `_record_belief_debt(pos, *, city, target_date, metric, reason)` /
  `_clear_belief_debt(...)` (~line 395): stamp a structured durable retryable marker
  `belief_debt;city=...;target_date=...;metric=...;reason=...;first_failed_at=...;attempts=N`
  onto `pos.applied_validations` (persisted to `position_events`, TRADES state, INV-37).
- NEW `_freshest_family_seed_on_disk(*, city, target_date, metric)` (~line 548):
  reads ONLY already-written seed JSON (pending + processed dirs) for the family; no
  write, no network. Picks the lexicographically-latest stamp.
- NEW `_attempt_held_belief_readthrough(pos, *, city, target_d, metric) -> float | None`
  (~line 600): freshest on-disk seed → `build_replacement_forecast_materialization_request`
  → `build_materialize_request_dataclass` → `get_forecasts_connection_read_only()` →
  `compute_replacement_posterior_readonly` → held-side prob for the bin (via
  `position_belief._match_bin` + `_held_side_probability_from_yes_bin_probability`).
  Fail-soft: ANY error / missing input → None.
- `monitor_probability_refresh` (`if not _would_use_day0_lane:` branch, ~line 2860):
  calls `_attempt_held_belief_readthrough` BEFORE fail-closing. Fresh → return
  `(prob, fresh_pos, True)` branded `belief_source=forecast_posteriors_readthrough_recompute`
  + clear belief-debt. Not eligible → record belief-debt + reseed + fail-close as before.

## How each hard constraint is satisfied (one line + evidence)

- **INV-37 (no independent write conn / no forecast_posteriors write from monitor):**
  the read-through uses `get_forecasts_connection_read_only()` (forecasts-MAIN, RO) —
  the SAME pattern this file already uses at `monitor_refresh.py:620-628` — and
  `compute_replacement_posterior_readonly` issues ZERO writes (test
  `test_readonly_entrypoint_returns_finite_posterior_and_ci_without_writing` asserts
  `COUNT(*) FROM forecast_posteriors == 0` after the read path).
- **PRESERVE BELIEF_AUTHORITY_FAULT intent (never act on stale OR cold-substituted):**
  the guard still fires (and `BELIEF_AUTHORITY_FAULT` is still appended) whenever the
  read-through cannot honestly recompute; the belief is made FRESH only via the
  canonical fusion (never the legacy ENS center) — `legacy_belief_substitution_suppressed`
  is appended only on the fail-close path, never on a fresh recompute.
- **NO FALSE EXIT (CI_SEPARATED_REVERSAL conservatism untouched):** the monitor only
  supplies a belief `(prob, pos, is_fresh)`; it never decides an exit. The exit's CI
  comes from the existing `monitor_refresh.py:3052-3140` band (gated only on
  `probability_authority_available`), and `src/state/portfolio.py:~1239` CI gate is
  unchanged. `last_monitor_prob` is used only as the urgency interrupt that triggers
  the recompute, never as exit evidence. Test
  `test_readthrough_does_not_itself_decide_an_exit` pins this.
- **NO over-engineering:** no new shadow/throttle/cap/allowlist/SLA gate; the
  belief-debt marker reuses the existing `position_events` validation sink; the reseed
  reuses the existing `_enqueue_single_family_belief_reseed_failsoft` repair lane; the
  read-through reuses the existing seed builder + request builder + RO forecasts conn.

## Design forks / deviations from the brief

1. **CI source (CAVEAT 2 from the coordinator/architect):** the brief asked the
   read-through to return "point q + q_lcb + q_ucb". The downstream
   `CI_SEPARATED_REVERSAL` band is NOT taken from the posterior's q_lcb/q_ucb — it is
   the monitor's existing bootstrap/`entry_ci_width` band (`monitor_refresh.py:3052-3140`),
   which arms as soon as `probability_authority_available` flips True (i.e. once the
   belief is fresh + finite). So the read-through returns the held-side POINT prob to
   flip freshness; the recompute's own q_lcb/q_ucb are computed and audited inside
   `compute_replacement_posterior_readonly` (provider counts → honestly wider CI) but
   are NOT threaded into `pos._bootstrap_context`. With `is_fresh=True` the band uses
   the conservative stale `entry_ci_width` fallback — finite, conservative, documented
   acceptable (`monitor_refresh.py:3045-3051`). **Open item:** to make the reversal use
   the RECOMPUTED band, populate `pos._bootstrap_context` from the read-through — a
   larger change deferred (consult Stage 3+, out of minimal scope).

2. **Connection (architect correction B):** the brief's first framing said "ATTACH
   read-only on the lifecycle connection". The monitor lifecycle conn is trades-MAIN;
   the fusion override's readers use BARE forecast-table names that would resolve to
   trades-MAIN → silent `no such table` / empty PRAGMA → permanent silent fail-close.
   The INV-37-correct fix (read-only single-DB forecasts conn) is used instead — INV-37
   governs cross-DB WRITES, and this read-only forecasts conn is the established
   in-module pattern. Documented in `_attempt_held_belief_readthrough`.

3. **Request assembly (architect correction C):** the request is NOT hand-built and the
   anchor is NEVER synthesized. The read-through reuses the freshest ON-DISK seed +
   `build_replacement_forecast_materialization_request` (which requires the on-disk
   anchor payload + precision metadata). When no usable on-disk seed/anchor exists, the
   read-through returns None → clean fail-close + belief-debt. So the read-through
   recovers a belief a cycle early WHEN the anchor artifacts are already on disk
   (download ran, materialize hasn't, or posterior expired but anchor present); when
   they are absent it cannot run (no fabricated center) and the existing fail-close
   floor holds. This is the honest bound on the fix's reach.

4. **Script DRY:** `scripts/materialize_replacement_forecast_live.py:main` still
   constructs its own request dataclass inline (live write path, left untouched for
   safety). The new `build_materialize_request_dataclass` is the importable twin used
   by the read-through; a parity test (`test_request_dataclass_builder_assembles_from_on_disk_seed`)
   guards against shape drift. DRY-refactoring the live script is deferred (avoid
   touching the live write path in a held-belief fix).

## Tests (RED then GREEN)

New antibody tests (registered in `architecture/test_topology.yaml` trusted_tests +
core_law_antibody):
- `tests/test_held_belief_readthrough_l2.py` (4 tests): read-only entrypoint returns
  finite q+q_lcb+q_ucb with NO posterior write; not-eligible on insufficient inputs;
  write path byte-identical (read-only q == written q); seed→dataclass request builder.
- `tests/engine/test_monitor_held_belief_readthrough.py` (3 tests): fresh recompute
  restores probability authority (is_fresh True); insufficient inputs fail-close +
  durable retryable belief_debt + reseed; monitor never decides an exit.

RED (before implementation):
```
ImportError: cannot import name 'compute_replacement_posterior_readonly' ...   # L2 file
AttributeError: ... has no attribute '_attempt_held_belief_readthrough'        # monitor file
```

GREEN (after implementation):
```
tests/test_held_belief_readthrough_l2.py ....                                  [4 passed]
tests/engine/test_monitor_held_belief_readthrough.py ...                       [3 passed]
```

Antibody (RED-on-revert) verified by mutation:
- Neuter `_attempt_held_belief_readthrough` call → 2/3 monitor tests FAIL
  (fresh-recompute + no-false-exit).
- Remove `_record_belief_debt` call → belief_debt test FAILS.

Regression suites (run from worktree, `.venv/bin/python3 -m pytest ... -q -p no:cacheprovider`):
- `tests/test_replacement_forecast_materializer.py` + `tests/test_cycle_monotone_materialization.py`:
  **47 passed** (byte-identical write path proven).
- `tests/engine/test_position_belief_authority.py`: GREEN (existing BELIEF_AUTHORITY_FAULT
  antibodies unchanged — the fail-soft read-through returns None in the test env, so the
  guard still fires).
- Monitor/exit sweep (`test_ci_separation_exit_live`, `test_monitor_refresh_ci_fallback`,
  `test_monitor_refresh_nowcast_wiring`, `test_live_safety_invariants`, `test_exit_authority`,
  `test_exit_safety`, `test_day0_hard_fact_exit`, `test_monitor_floor_parity`): **274 passed**.
- Consolidated final pass: **99 passed**.
- Differential vs clean base (stash compare): the materializer-dependent broad sweep was
  35 failed / 54 passed on BOTH base and branch (identical); `test_runtime_guards.py` was
  8 failed / 285 passed on BOTH; `test_replacement_forecast_materialization_request_builder.py`
  was 3 failed / 2 passed on BOTH. **Zero new failures introduced** — all pre-existing
  (stale test fixtures referencing removed `aifs_samples_json` / `_QLCB_SOFT_ANCHOR_BASIS`
  symbols + missing data artifacts, unrelated to this change).

## Open risks / live forward verification (auto-trader paused)

Live order/exit evidence is unavailable until the operator resumes the auto-trader.
When resumed, watch:
1. `BELIEF_AUTHORITY_FAULT` clears for held families whose anchor seeds are on disk
   (the read-through recomputes instead of freezing) — grep monitor logs for
   `held-belief READ-THROUGH recompute OK`.
2. `belief_debt;...` markers appear in `position_events` for families with NO on-disk
   anchor (and are RETRYABLE — `attempts=N` climbs, then clears when the anchor lands) —
   confirms it is not a silent freeze.
3. A genuine physics reversal on a held position now ARMS `CI_SEPARATED_REVERSAL` while
   a bid still exists (the live −$27.63 class). NOTE the CI-band open item (deviation 1):
   the first cut arms off the conservative stale `entry_ci_width` band; verify the
   reversal actually fills, and decide whether to thread the recomputed band into
   `_bootstrap_context` (Stage 3) if the conservative band proves too wide to fire.
4. No regression in the live write path: forecast_posteriors materialization counts and
   q values unchanged (the extraction is byte-identical, proven by the 47-test suite).
