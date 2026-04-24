# Phase 3 — Observation client `low_so_far` + source registry collapse

Team: `zeus-dual-track` (persistent across Phase 3..9)
Predecessors: Phase 0 `943e74d`, Phase 0b `df12d9c`, Phase 1 `b025883`, Phase 2 `16e7385`

## Scope

1. `src/data/observation_client.py` — every provider (WU, HKO, NOAA, Open-Meteo fallback) returns a unified `Day0ObservationContext` with `current_temp`, `high_so_far`, `low_so_far`, `source`, `observation_time`, `unit`. `low_so_far` is a required output, not optional. When a provider cannot produce it, the provider is fail-closed (raise specific exception) — never silently substitute.
2. `src/data/daily_obs_append.py` — stop maintaining the local `CITY_STATIONS` registry. Read station config from `cities.json` (single source of truth). Delete the parallel map.
3. The low Day0 reject path at `src/engine/evaluator.py:794` no longer triggers for cities with a valid `low_so_far`.

## R-invariants (land as failing tests FIRST)

- **R-F (provider closure)**: For every live provider path that returns a valid observation for a given (city, target_date), the returned dict contains both `high_so_far: float` AND `low_so_far: float` (both non-None). A provider that cannot fetch one of them must raise a typed exception; returning `low_so_far=None` is forbidden at the public seam.
- **R-G (single source of station truth)**: `src/data/daily_obs_append.py` no longer declares a local CITY_STATIONS map. `cities.json` via `src.config.cities_by_name` is the only place station identifiers live.
- **R-H (evaluator low unblock)**: After Phase 3, a test using a city with a valid low_so_far from the new `Day0ObservationContext` no longer hits the `rejection_stage == "OBSERVATION_UNAVAILABLE_LOW"` branch at `evaluator.py:794`.

## Out of scope

- Day0 low nowcast signal (Phase 6).
- Kelly executable-price (pre-Phase 9).
- Schema v2 live writer migration (Phase 4).
- New cities / station re-keying.
- Any change to `Position.temperature_metric: str` boundary (Phase 6).

## Team pipeline for this phase

1. **scout-dave**: inventory `observation_client.py` provider paths + all CITY_STATIONS-style registries (grep parallel maps); deliver provider-by-provider report with current return contract. A2A → testeng-emma.
2. **testeng-emma**: write R-F / R-G / R-H failing tests. A2A → exec-bob + exec-carol when done.
3. **exec-bob**: implement `Day0ObservationContext` typed dataclass + unify all providers in `observation_client.py`.
4. **exec-carol**: rewrite `daily_obs_append.py` to consume `cities.json`; delete the parallel CITY_STATIONS map; update any direct importers.
5. **exec-bob ⇄ exec-carol**: a2a cross-validate each other's diff before reporting to critic.
6. **critic-alice**: wide pass (find fixes + surface new issues). Report to team-lead.

## Deliverables back to team-lead

Each teammate sends a ≤150-word summary to `team-lead` when done. Main-thread only synthesizes critic's verdict and commits.

## Evidence archive

- `docs/operations/task_2026-04-16_dual_track_metric_spine/phase3_evidence/` — this brief + scout inventory + critic verdict.

## Gate B (opens with this phase)

Evaluator stops rejecting low Day0 candidates for cities with a working provider path.
