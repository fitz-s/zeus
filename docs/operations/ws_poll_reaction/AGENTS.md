# docs/operations/ws_poll_reaction/

Created: 2026-04-29
Authority basis: round3_verdict.md §1 #2 + ULTIMATE_PLAN.md L312-314 +
WS_OR_POLL_TIGHTENING packet boot §6 (PATH A latency-only) + critic 24th
cycle LOW-DESIGN-WP-2-2 (per-strategy threshold dict).

## What lives here

JSON reports emitted by `scripts/ws_poll_reaction_weekly.py` — one file per
weekly reaction-gap run, named `weekly_<YYYY-MM-DD>.json` where the date is
the inclusive window-end day.

Each report carries:

- `current_window`: per-`strategy_key` reaction-latency snapshot from
  `src.state.ws_poll_reaction.compute_reaction_latency_per_strategy` —
  `latency_p50_ms`, `latency_p95_ms`, `n_signals` (price ticks with valid
  latency in window), `n_with_action` (subset where Zeus emitted a
  position_events row within `ACTION_WINDOW_SECONDS=30s`), `sample_quality`,
  `window_start`, `window_end`.
- `gap_verdicts`: per-strategy `ReactionGapVerdict` from
  `src.state.ws_poll_reaction.detect_reaction_gap` over the trailing
  `n_windows_for_gap` weekly-history entries — `kind` ∈
  {`gap_detected`, `within_normal`, `insufficient_data`}, optional
  `severity` ∈ {`warn`, `critical`}, plus `evidence` dict carrying the
  inputs to the decision (current_p95_ms, trailing_mean_p95_ms, ratio,
  thresholds, window counts).
- `per_strategy_thresholds`: gap-multiplier threshold dict actually used
  this run (defaults + any operator overrides applied — see below).
- `negative_latency_count`: integer count of price-tick rows in the
  current window whose Zeus persist timestamp is BEFORE the venue source
  timestamp. The latency math silently clips these to 0 ms (clock-skew
  defense), but the runner SURFACES the count so operators have visibility
  on upstream clock-skew events instead of silent swallowing. (Cycle-22
  LOW caveat carry-forward.)
- Generation metadata: `report_kind`, `report_version`, `generated_at`,
  `end_date`, `window_days`, `n_windows_for_gap`,
  `critical_ratio_cutoff`, `db_path`.

## Authority class

**Derived context — NOT authority.** These reports are evidence the
operator can use to spot reaction-time degradation; they do not gate
trading or risk decisions on their own. Per K1 contract documented at
`src/state/ws_poll_reaction.py` module docstring.

## Detector limitations (operator-relevant)

Per `src/state/ws_poll_reaction.py` module docstring §"KNOWN LIMITATIONS":

- **PATH A latency-only.** The detector measures end-to-end Zeus persist
  latency (Zeus timestamp minus venue source timestamp) but CANNOT
  attribute individual ticks to WebSocket vs REST poll because
  `token_price_log` lacks an `update_source` column. `ws_share` and
  `poll_share` are intentionally NOT in the report shape.
- **PATH B (heuristic WS-vs-poll inference) was REJECTED** per
  methodology §5.Z2 default-deny on heuristic-without-grounding.
- **PATH C (extending the `token_price_log` writer to tag
  `update_source`)** is deferred to a future
  `WS_PROVENANCE_INSTRUMENTATION` packet that operator will separately
  authorize. Once that lands, this report can be extended with
  `ws_share`/`poll_share` fields.
- **Negative latencies clipped to 0.** Clock-skew defense; the count is
  surfaced via `negative_latency_count` so operators can spot
  misconfigured upstream rather than have it disappear silently.
- **Rows with NULL or unparsable timestamps are excluded** (cannot
  contribute a valid latency).

## Per-strategy threshold rationale

`gap_threshold_multiplier` is the ratio at which `current_p95 /
trailing_mean_p95` is flagged `gap_detected`. Strict greater-than
semantics: `ratio == multiplier` is `within_normal`. Defaults differ
per strategy because reaction-time pressure differs:

| strategy_key       | default | rationale                                                                                              |
|--------------------|---------|--------------------------------------------------------------------------------------------------------|
| opening_inertia    | **1.2** | Alpha decays fastest here (bot scanning per `AGENTS.md::Strategy families` table); needs the tightest gap discipline. |
| shoulder_sell      | 1.4     | Moderate pressure — competition narrows but reaction window is still seconds-scale.                    |
| center_buy         | 1.5     | Standard ratio multiplier.                                                                             |
| settlement_capture | 1.5     | Default — settlement timing is structurally outcome-determined, not WS-reaction-bound.                 |

Operator can override per-strategy via repeated `--override-strategy
KEY=VALUE` flags, e.g.:

```
python3 scripts/ws_poll_reaction_weekly.py \
    --override-strategy opening_inertia=1.1 \
    --override-strategy shoulder_sell=1.3
```

Severity tier: `critical` when ratio >= `--critical-ratio-cutoff`
(default 2.0); `warn` otherwise.

## How to regenerate

Manual run (BATCH 3 packet does not wire automation):

```
python3 scripts/ws_poll_reaction_weekly.py
python3 scripts/ws_poll_reaction_weekly.py --end-date 2026-04-28
python3 scripts/ws_poll_reaction_weekly.py --window-days 7 --n-windows 6
python3 scripts/ws_poll_reaction_weekly.py --critical-ratio-cutoff 2.5
python3 scripts/ws_poll_reaction_weekly.py --override-strategy opening_inertia=1.1
python3 scripts/ws_poll_reaction_weekly.py --report-out /tmp/wp.json --stdout
python3 scripts/ws_poll_reaction_weekly.py --db-path state/zeus-shared.db
```

Exit code: 0 if no strategy is `gap_detected`; 1 if at least one strategy
has `gap_detected` (cron-friendly).

## Retention

Operator-managed evidence. There is no auto-purge; old reports remain
until operator decides to archive or delete. A history of weekly reports
IS itself useful (lets the operator see reaction-time trajectory over
many weeks).

## Out-of-scope

- Cron / launchd wiring (operator decides; not in scope of this packet).
- Modifying actual WS subscription / poll dispatch logic in
  `src/venue/` (measurement only; execution tightening is operator-
  decision per dispatch NOT-IN-SCOPE).
- Extending `token_price_log` writer to tag `update_source` (PATH C;
  deferred to `WS_PROVENANCE_INSTRUMENTATION` packet).
- LEARNING_LOOP integration (separate packet; deferred per ULTIMATE_PLAN.md).

## See also

- `src/state/ws_poll_reaction.py` — the K1-compliant projection module
  (BATCH 1: `compute_reaction_latency_per_strategy`; BATCH 2:
  `detect_reaction_gap` + `ReactionGapVerdict`).
- `tests/test_ws_poll_reaction.py` — relationship tests for the
  per-tick latency math + ratio-test detector.
- `tests/test_ws_poll_reaction_weekly.py` — end-to-end runner tests.
- `docs/operations/edge_observation/` — sibling packet (same operator
  pattern: weekly JSON + per-strategy summary + cron-friendly exit).
- `docs/operations/attribution_drift/` — sibling packet (same
  operator pattern; per-position evidence in addition to per-strategy).
- `architecture/source_rationale.yaml` — registry entry for
  `src/state/ws_poll_reaction.py`.
- `architecture/script_manifest.yaml` — registry entry for
  `scripts/ws_poll_reaction_weekly.py`.
- `architecture/test_topology.yaml` — registry entries for the two test
  files.
