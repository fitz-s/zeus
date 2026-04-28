# docs/operations/edge_observation/

Created: 2026-04-28
Authority basis: round3_verdict.md §1 #2 + ULTIMATE_PLAN.md L297-301 +
EDGE_OBSERVATION packet boot §6 #3 (operator chose option (b): tracked dir
+ small AGENTS.md).

## What lives here

JSON reports emitted by `scripts/edge_observation_weekly.py` — one file per
weekly drift-assertion run, named `weekly_<YYYY-MM-DD>.json` where the date
is the inclusive window-end day.

Each report carries:

- `current_window`: per-strategy realized-edge snapshot (mean of
  `outcome - p_posterior` over the window, plus n_trades, n_wins, win_rate,
  sample_quality, window bounds) — output of
  `src.state.edge_observation.compute_realized_edge_per_strategy`.
- `decay_verdicts`: per-strategy `DriftVerdict` from
  `src.state.edge_observation.detect_alpha_decay` — kind ∈
  {alpha_decay_detected, within_normal_range, insufficient_data}, optional
  severity ∈ {warn, critical}, plus an evidence dict carrying the inputs
  to the decision.
- Generation metadata: report_kind, report_version, generated_at,
  end_date, window_days, n_windows_for_decay, db_path.

## Authority class

**Derived context — NOT authority.** These reports are evidence the
operator can use to spot alpha decay; they do not gate trading or risk
decisions on their own. Per K1 contract documented at
`src/state/edge_observation.py` module docstring.

## How to regenerate

Manual run (BATCH 3 packet does not wire automation):

```
python3 scripts/edge_observation_weekly.py
python3 scripts/edge_observation_weekly.py --end-date 2026-04-28
python3 scripts/edge_observation_weekly.py --window-days 14 --n-windows 6
python3 scripts/edge_observation_weekly.py --db-path state/zeus-shared.db
python3 scripts/edge_observation_weekly.py --report-out /tmp/edge.json
```

Exit code: 0 if no alpha_decay_detected for any of the 4 strategies; 1 if
at least one strategy has alpha_decay_detected (useful for cron monitoring).

## Retention

This directory is operator-managed evidence. There is no auto-purge; old
reports remain until operator decides to archive or delete. A history of
weekly reports IS itself useful (lets the operator see edge trajectory
over many weeks at the JSON level).

## Out-of-scope

- Cron / launchd wiring (operator decides; not in scope of this packet).
- Real-time edge alerting (separate ATTRIBUTION_DRIFT packet per
  ULTIMATE_PLAN.md L305-307).
- Calibration re-fit decisions (separate CALIBRATION_HARDENING packet per
  ULTIMATE_PLAN.md L302-304).

## See also

- `src/state/edge_observation.py` — the K1-compliant projection module.
- `tests/test_edge_observation.py` — relationship tests for both the
  realized-edge math and the alpha-decay detector.
- `tests/test_edge_observation_weekly.py` — end-to-end runner tests.
- `architecture/source_rationale.yaml` — registry entry for
  `src/state/edge_observation.py`.
- `architecture/script_manifest.yaml` — registry entry for
  `scripts/edge_observation_weekly.py`.
