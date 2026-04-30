# Source-Contract Auto-Conversion Runtime Plan

Status: merged packet evidence on `plan-pre5`; not the active live-control
pointer while `docs/operations/current_state.md` routes the branch to R3 G1
Created: 2026-04-30
Branch: source-auto-conversion-2026-04-30

## Objective

Make Polymarket settlement-source drift handling cron-safe enough that a
standard worker model can execute the deterministic workflow without inventing
date ranges or source-transition logic.

The default runtime is still quarantine/plan/dry-run. Phase B adds an explicit
`--execute-apply` lane that may update `config/cities.json`, run scoped
backfill/rebuild/refit commands, and release source quarantine only after every
required evidence ref is present. Production DB mutation is permitted only when
the operator passes the apply flag, the cron lock is held, the transition is the
auto-confirmed same-provider WU branch, and the command uses scoped
city/date/metric filters plus backup/DB-path evidence.

## Runtime Inventory

- `scripts/venus_sensing_report.py` is the existing Venus/cron sensing surface.
  It already invokes `scripts/watch_source_contract.py` and can persist city
  source quarantine while the live daemon is paused.
- `scripts/watch_source_contract.py` is the source-contract detector,
  quarantine writer, release-history reporter, and release evidence validator.
  It does not perform config updates, backfills, settlement rebuilds, or
  calibration rebuilds.
- `scripts/backfill_wu_daily_all.py` is the existing WU daily observation
  backfill. Phase B must remove its stale static station map by deriving WU
  station/country/unit from `config/cities.json`, and must support explicit
  `--start-date/--end-date` so a source transition does not fetch unrelated
  dates.
- `scripts/rebuild_settlements.py` is dry-run by default and supports `--city`
  plus `--apply`. Phase B must add explicit target-date and temperature-metric
  filters and support both high and low settlement rows.
- `scripts/rebuild_calibration_pairs_v2.py` is dry-run by default and supports
  `--city`, `--n-mc`, `--no-dry-run`, and `--force`. It rebuilds metric specs
  from eligible snapshots. Phase B must expose metric/date filters so the source
  transition can rebuild only the affected city/track/window.
- `scripts/refit_platt_v2.py` is dry-run by default and supports `--no-dry-run`,
  `--force`, and `--db`. Phase B must expose metric and bucket filters. The
  controller may refit only buckets affected by the changed city/metric/window.
- `src/riskguard/discord_alerts.py` sends Discord embeds from
  `ZEUS_DISCORD_WEBHOOK` or macOS Keychain id `zeus_discord_webhook`; alerts
  are skipped when `ZEUS_DISABLE_DISCORD_ALERTS=1` or no webhook is available.

## Deterministic Date Scope

The controller must derive dates only from checked Gamma events and current
UTC/local runtime facts:

- `affected_market_start` = minimum non-empty alert `target_date`.
- `affected_market_end` = maximum non-empty alert `target_date`.
- `desired_backfill_end` = max(`affected_market_end`, current UTC date).
- `executable_wu_fetch_end` = current UTC date minus 2 days, because
  `backfill_wu_daily_all.py` currently derives its own WU history end date as
  `date.today() - 2`.
- `backfill_days` = enough days to cover the configured history window ending
  at `executable_wu_fetch_end`, widened only if the affected market start is
  older than that window.
- Target dates newer than `executable_wu_fetch_end` are recorded as
  `future_or_recent_dates_not_fetchable_by_wu_history`; they cannot be
  backfilled until WU history is available. Their presence blocks
  `--execute-apply` completion and source-quarantine release; the city remains
  new-entry quarantined until every affected target date is backfillable and
  release evidence is complete.
- Default same-provider WU station-change historical window: 1095 days. This
  is intentionally explicit policy, not model judgment.

For the Paris test case currently recorded in `current_source_validity.md`, the
active mismatching market dates are 2026-04-29 through 2026-05-01 and the
observed station is `LFPB` while config still contains `LFPG`.

## Auto-Confirm Threshold

The only automatically promotable branch in this packet is:

- `same_provider_station_change`
- observed source family exactly `wu_icao`
- configured source family exactly `wu_icao`
- exactly one observed station id
- exactly one configured station id
- observed station differs from configured station
- at least 2 alert markets
- at least 1 distinct affected target date
- every alert for the city points to the same target source contract

If the threshold is not met, the controller must keep the city quarantined,
write a blocked receipt, and alert the operator.

## Hidden Branches That Must Block

The controller must not auto-convert:

- provider-family change, such as WU to NOAA/HKO/CWA/unknown
- unsupported URL/domain or provider text with no station proof
- ambiguous multiple providers or multiple stations
- mixed observed station ids for the same city
- one-market-only evidence below threshold
- missing target dates
- missing WU credentials for a WU backfill path
- required downstream command lacks needed scope for safe apply
- dry-run command exits non-zero
- Discord unavailable while reporting a blocked or failed run
- release evidence is incomplete or missing evidence refs

## Discord / Receipt Contract

Every cron run writes durable JSON under
`state/source_contract_auto_convert/` and updates `latest.json`.

Discord alert behavior:

- no candidate: optional info summary, exit 0
- quarantine or insufficient threshold: warning, exit 1
- unsupported/ambiguous/provider-family/manual branch: blocked warning, exit 1
- command dry-run failure: critical failure, exit 2
- fully verified `--execute-apply --force` release: success, exit 0

Discord message fields must include city, branch, old source, new source,
affected target-date range, alert event ids, receipt path, and next manual
action.

## Implementation Scope

Phase A in this packet:

- add a deterministic `scripts/source_contract_auto_convert.py` controller
- keep it dry-run/plan-first for downstream data mutation
- reuse `watch_source_contract.py` analysis and quarantine helpers
- compute date scopes and ordered command plan
- persist receipt JSON
- report blocked/failed/success/noop status through Discord when configured
- emit a `mini_llm_execution` contract and optional mini report so a smaller
  model can follow exact allowed commands, forbidden actions, evidence paths,
  and stop conditions without inventing transition logic
- include `workspace_locator` and `safe_execution_contract` so a smaller model
  can find the relevant source files, understand current-phase write scopes,
  and refuse destructive or out-of-scope commands
- acquire a cron lock around source-watch/quarantine/receipt generation so
  overlapping cron runs cannot race on quarantine or receipt files
- test same-provider Paris-style conversion, threshold blocking, ambiguous
  station blocking, provider-family blocking, and receipt shape

Phase B implementation scope:

- deterministic config writer for auto-confirmed same-provider WU station
  changes. It must update `wu_station`, `settlement_source`, station-aligned
  `lat`/`lon`, and `airport_name`; if exact station metadata is unavailable it
  must block.
- backfill command scope: `--start-date`, `--end-date`, and config-derived WU
  station identity.
- settlement rebuild scope: `--start-date`, `--end-date`, and
  `--temperature-metric high|low|all`, writing only VERIFIED observation rows
  through `SettlementSemantics`.
- calibration rebuild scope: city/date/metric filters for
  `calibration_pairs_v2`; live writes still require `--no-dry-run --force`.
- Platt refit scope: metric plus explicit bucket selectors derived from changed
  calibration pairs; live writes still require `--no-dry-run --force`.
- controller apply mode: run dry-runs first, then apply steps, write evidence
  artifacts, verify the post-conversion source watch, release quarantine only
  with complete evidence refs, and leave a transition-history record.
- mini-model contract: a standard model must be able to read the receipt, run
  exact allowed commands, refuse hidden branches, and produce a final report
  without inventing source or date logic.
- mini/Venus handoff: each planned receipt must include an
  `execute_apply_controller` step with the exact current invocation paths
  (`--fixture` for canaries, `--quarantine-path`, `--db`, `--config-path`,
  `--source-validity-path`, `--evidence-root-base`). The model should prefer
  that one command; per-script commands are retained as audit/evidence steps.

## Required Verification

- `python scripts/topology_doctor.py --planning-lock --changed-files <files> --plan-evidence docs/operations/task_2026-04-30_source_auto_conversion/plan.md`
- `python scripts/topology_doctor.py --scripts --json`
- `python scripts/topology_doctor.py --freshness-metadata --changed-files <files>`
- focused pytest covering source-contract auto conversion and existing source
  quarantine behavior
