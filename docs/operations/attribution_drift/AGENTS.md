# docs/operations/attribution_drift/

Created: 2026-04-28
Authority basis: round3_verdict.md §1 #2 + ULTIMATE_PLAN.md L305-308 +
ATTRIBUTION_DRIFT packet boot §6 #3 (operator chose option (b): tracked dir
+ small AGENTS.md, mirroring docs/operations/edge_observation/).

## What lives here

JSON reports emitted by `scripts/attribution_drift_weekly.py` — one file per
weekly drift-rate run, named `weekly_<YYYY-MM-DD>.json` where the date is
the inclusive window-end day.

Each report carries:

- `per_strategy`: per-`strategy_key` aggregation from
  `src.state.attribution_drift.compute_drift_rate_per_strategy` —
  `drift_rate` (n_drift / n_decidable, with `n_insufficient` excluded from
  the denominator), `n_positions`, `n_drift`, `n_matches`, `n_insufficient`,
  `n_decidable`, `sample_quality`, `window_start`, `window_end`.
- `drift_positions`: per-position `AttributionVerdict` evidence for every
  position whose `kind == "drift_detected"`. Operator can audit each
  individual mismatch (label_strategy vs inferred_strategy +
  bin_topology + direction + discovery_mode + bin_label +
  mismatch_summary).
- Generation metadata: `report_kind`, `report_version`, `generated_at`,
  `end_date`, `window_days`, `db_path`.

Note: `insufficient_signal` per-position detail is NOT included in
`drift_positions` (would dominate the report by volume; operators can
re-run the BATCH 1 detector directly via
`src.state.attribution_drift.detect_drifts_in_window` if they need it).

## Authority class

**Derived context — NOT authority.** These reports are evidence the
operator can use to spot silent attribution drift; they do not gate
trading or risk decisions on their own. Per K1 contract documented at
`src/state/attribution_drift.py` module docstring.

## Detector limitations (operator-relevant)

Per `src/state/attribution_drift.py` module docstring §"Known limitations":

- The detector is **precision-favored, recall-limited**. Every
  `drift_detected` verdict is a real label/semantics mismatch on at least
  one of clauses 3-5 of the entry-time `_strategy_key_for` dispatch rule.
  Some real drifts are reported as `insufficient_signal` because:
  - `discovery_mode` is not surfaced by `_normalize_position_settlement_event`
    in the canonical row, so clauses 1-2 (Day0 / Opening) of the dispatch
    rule cannot be applied;
  - `bin.is_shoulder` is inferred heuristically from the persisted
    `bin_label` string (per AGENTS.md::Settlement semantics shoulder-bin note,
    the classifier conservatively returns `unknown` rather than guess for
    non-canonical label formats).
- A high `n_insufficient` in a strategy's report is NOT a defect — it
  means the dispatch rule could not be re-applied for those positions, and
  surfacing the volume tells the operator how many positions are in that
  uncertainty bucket.

## How to regenerate

Manual run (BATCH 3 packet does not wire automation):

```
python3 scripts/attribution_drift_weekly.py
python3 scripts/attribution_drift_weekly.py --end-date 2026-04-28
python3 scripts/attribution_drift_weekly.py --window-days 14
python3 scripts/attribution_drift_weekly.py --drift-rate-threshold 0.10
python3 scripts/attribution_drift_weekly.py --db-path state/zeus-shared.db
python3 scripts/attribution_drift_weekly.py --report-out /tmp/ad.json
```

Exit code: 0 if no strategy's `drift_rate` exceeds `--drift-rate-threshold`
(default 0.05); 1 if at least one strategy exceeds (cron-friendly).

## Retention

Operator-managed evidence. There is no auto-purge; old reports remain
until operator decides to archive or delete. A history of weekly reports
IS itself useful (lets the operator see attribution-drift trajectory over
many weeks).

## Out-of-scope

- Cron / launchd wiring (operator decides; not in scope of this packet).
- Automated revert / re-labeling of drifted positions (this is a
  detector, not a corrector).
- LEARNING_LOOP integration (separate packet; deferred per dispatch).

## See also

- `src/state/attribution_drift.py` — the K1-compliant detector module.
- `tests/test_attribution_drift.py` — relationship tests for the detector
  and aggregator.
- `tests/test_attribution_drift_weekly.py` — end-to-end runner tests.
- `docs/operations/edge_observation/` — sibling packet (same operator
  pattern: weekly JSON reports + per-strategy summary + cron-friendly exit).
- `architecture/source_rationale.yaml` — registry entry for
  `src/state/attribution_drift.py`.
- `architecture/script_manifest.yaml` — registry entry for
  `scripts/attribution_drift_weekly.py`.
