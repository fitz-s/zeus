# Red-test baseline — base c4d4d45e3 (2026-07-08)

The blueprint claims "867 red tests vs a pre_existing_failure_registry of only
3 entries." This document is the honest measurement against that claim, run
on the R8 worktree base (`c4d4d45e3`), config at `config/settings.json`,
`.venv/bin/python -m pytest <domain> -q -p no:cacheprovider --no-header
-o addopts=""` per top-level `tests/` domain, run separately so one broken
domain can't abort the others.

## Status: complete for 32 named domains; flat top-level bucket partial (71%)

The flat top-level bucket (~1,000 loose `tests/test_*.py` files not under any
named subdirectory) **hung the entire pytest process twice** on unmocked live
network calls before I found and worked around the cause (§3). A third,
CPU-bound hang (§3.3) also had to be excluded. After both workarounds the run
reached 71% coverage (9,490 individual test results) before hitting a further
network-dependent hang; I am still working to close the remaining ~29%. This
document reports confirmed numbers now rather than wait — see §4 for the
running total and what's still open.

## 1. Per-domain breakdown (complete, all 32 named domains)

| domain | result |
|---|---|
| tests/state | **39 failed**, 287 passed, 7 skipped |
| tests/events | 0 failed — 806 passed, 2 skipped, 2 xfailed |
| tests/execution | **28 failed**, 236 passed |
| tests/money_path | 0 failed — 320 passed |
| tests/probability | 0 failed — 30 passed |
| tests/strategy | **20 failed**, 310 passed |
| tests/reconcile | 0 failed — 29 passed |
| tests/venue | 0 failed — 61 passed |
| tests/engine | **1 collection error** (ImportError — see §2.3) |
| tests/integration | **3 failed**, 99 passed, 1 skipped |
| tests/backtest | no tests (dir has only `__init__.py`; not a gap) |
| tests/calibration | **5 failed**, 172 passed |
| tests/ci | **1 failed**, 195 passed |
| tests/contracts | **1 failed**, 186 passed |
| tests/data | **1 failed**, 150 passed |
| tests/decision | **5 failed**, 151 passed |
| tests/decision_kernel | **9 failed**, 278 passed |
| tests/forecast | 0 failed — 63 passed |
| tests/hooks | 0 failed — 27 passed |
| tests/maintenance_worker | **1 failed + 1 error**, 878 passed, 9 skipped |
| tests/observability | **1 failed**, 5 passed |
| tests/riskguard | 0 failed — 2 passed |
| tests/runtime | **1 failed**, 14 passed |
| tests/scripts | **10 failed**, 155 passed |
| tests/signal | 0 failed — 7 passed |
| tests/sizing | 0 failed — 10 passed |
| tests/solve | 0 failed — 86 passed |
| tests/static | 0 failed — 10 xfailed, 6 xpassed (test-hygiene note, not a failure) |
| tests/topology | 0 failed — 107 passed |
| tests/types | 0 failed — 20 passed |
| tests/analysis | 0 failed — 157 passed |
| tests/architecture | 0 failed — 33 passed |

**Named-domain subtotal: 125 failed + 2 collection errors = 127 real red.**

## 2. Named-domain triage (top clusters)

### 2.1 `tests/state` (39F) + `tests/execution` (28F) — real regression, one root cause each, high fan-out

- `tests/state/test_position_open_idempotency.py` (18 of the 39) and
  `tests/state/test_boot_migration_v28_antibody.py` (7 of the 39) fail on
  `NoTradeReason['PHYSICAL_ENVELOPE_UNWIRED']` `KeyError` — the enum member
  the migration test expects doesn't exist in
  `src/state/schema/no_trade_regret_events_schema.py` on this base. Test
  written ahead of the enum, or enum reverted after the test landed — either
  way it's a real drift between test and implementation, not dead-test noise.
- `tests/execution` (28F, all of `test_abandoned_unsubmitted_ghost_reconcile.py`,
  `test_batch_order_submission.py`, `test_edli_absence_resolver_boot.py`,
  `test_venue_sync_contract.py`) all trace through
  `src/events/live_order_aggregate.py::_validate_qkernel_submit_probability`
  → `_positive_number` → `LiveOrderAggregateError` — one stricter validation
  law rejecting a fixture-built payload that predates it. One root cause,
  28 failures.

### 2.2 `tests/strategy` (20F) — real regression, one root cause

All 20 are in `tests/strategy/live_inference/test_direction_law.py` — a
single test file, so likely one shared fixture/helper drifted against
`src/strategy/*` direction-law code, not 20 independent bugs.

### 2.3 `tests/engine` (1 collection error) — real regression, test references deleted/renamed symbol

```
ImportError: cannot import name '_edli_forecast_only_phase_evidence' from
'src.engine.event_reactor_adapter'
```
`tests/engine/test_edli_forecast_only_phase_exclusion.py` imports a private
symbol that no longer exists in `event_reactor_adapter.py` — dead-check
testing renamed/removed code, not a runtime regression.

### 2.4 `tests/maintenance_worker` (1F + 1E) — real regression + env gap (two different causes)

- `test_untracked_top_level_quarantine.py::test_file_under_task_packet_skipped`
  — real: `enumerate(entry, ctx)` returns `UNTRACKED_QUARANTINE_CANDIDATE`
  where the test expects a SKIP verdict for files under an active task
  packet; the skip-active-packet rule isn't firing.
- `test_zeus_config.py::test_config_task_allowlist_task_ids_exist_in_catalog`
  — **env/fixture gap**: `FileNotFoundError` on
  `docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/TASK_CATALOG.yaml`,
  which doesn't exist in this worktree checkout (task packet likely archived
  or moved on `main` after this base commit).

### 2.5 `tests/state --strict` env gap (found via CLI spot-check, not counted above)

`topology_doctor.py --strict` (used by the doctor's own `run_strict`, tested
separately from the pytest domains) raises `FileNotFoundError` on
`state/assumptions.json`, which also doesn't exist in this fresh worktree —
same class of gap as §2.4: a runtime-state file the checkout never
materialized, not a code regression. Confirmed unrelated to my Task 2 edit
(traceback originates in `check_wmo_gate`, never touches the deleted
`load_schema()`).

### 2.6 Everything else (calibration 5F, ci 1F, contracts 1F, data 1F, decision 5F, decision_kernel 9F, observability 1F, runtime 1F, scripts 10F, integration 3F) — not yet root-caused individually

These are real `FAILED` lines (verbatim names available in
`/private/tmp/claude-501/-Users-leofitz-zeus/ce5821ff-a917-4ece-ab1c-008d235a2639/scratchpad/redlogs/{calibration,ci,contracts,data,decision,decision_kernel,observability,runtime,scripts,integration}.log`
if a follow-up needs the full per-test list) — counted as real red but not
individually triaged into regression/dead-check/env-gap given this packet's
time budget. None showed the "no tests ran" or "collected 0 items" signature
that would flag them as environment gaps at the domain level.

## 3. Flat top-level bucket (~1,000 files) — partial, two classes of hang found

### 3.1 Finding: unmocked live network call hangs the *entire* pytest process

`tests/test_bootstrap_symmetry.py::TestBootstrapCIInRefreshPosition` (all 4
tests in the class) calls `refresh_position()` with no mock on
`get_sibling_outcomes`/`_get_active_events` — unlike the sibling class
`TestBootstrapContextStashing` in the same file, which does patch it. This
makes a real `httpx.get()` call to the live Polymarket Gamma API
(`src/data/market_scanner.py::_gamma_get`). In this sandboxed environment the
socket connects but never receives a response — a true hang, not a slow
test. `pytest-timeout` in `thread` mode cannot recover from it (it dumps the
blocked thread's stack but can't interrupt an in-flight C-level `ssl.recv`),
and the whole pytest process terminates. **This is a real production-code
finding, not a benign gap**: any CI or sandboxed run of the full suite
silently dies here with no summary — that's how the blueprint's registry
comparison went stale in the first place if nobody has run the full flat
bucket clean in a while.

`tests/test_pre_live_integration.py::test_refresh_position_true_metrics` hits
the same `refresh_position` → live-network path and hangs identically —
confirming this is systemic to at least 2 files, not a one-off. A repo-wide
`grep -rln "_get_active_events\|get_sibling_outcomes\|_gamma_get" tests/*.py`
found 11 flat-level files touching this code path; only these two are
confirmed unmocked so far (the rest may or may not properly patch it — not
individually verified given time budget).

**Recommended fix** (not made — outside this packet's edit fence, these
files aren't in scripts/topology_doctor* or tests/test_topology_doctor.py):
add the missing `@patch("src.engine.monitor_refresh.get_sibling_outcomes", ...)`
to `TestBootstrapCIInRefreshPosition` mirroring `TestBootstrapContextStashing`
in the same file, and audit the other 9 files found by the grep above.

### 3.2 Workaround used to make progress

Set `HTTPS_PROXY=http://127.0.0.1:1 HTTP_PROXY=http://127.0.0.1:1` (nothing
listens on that port) so httpx's default `trust_env` proxy lookup makes any
real outbound call fail fast (`ConnectionRefused`) instead of hanging. This
unblocked most of the bucket but not all — at least one code path
(`tests/test_pre_live_integration.py`'s call chain) reaches a raw
`ssl_context.wrap_socket(...)` TLS handshake that isn't going through the
env-proxied client and still hangs even with the bogus proxy set. That is
the current blocker on the remaining ~29%.

### 3.3 Second, unrelated finding: pathologically slow/hanging numerical test

`tests/test_fit_sigma_scale.py` (all 15 tests, but specifically
`test_fit_cities_shrunk_writes_only_positive_capital_with_score` and
`test_city_capital_day_se_nonnegative_and_empty_when_too_few_days`) run a
grid-search fit (`scripts/fit_sigma_scale.py::_fit_grid` →
`_neg_log_likelihood` → `_masses_from_edges`) that did not complete in 180s
standalone — not a network issue, a genuine CPU-bound stall or pathological
grid size. Excluded via `--ignore=tests/test_fit_sigma_scale.py` to make
progress; needs separate investigation (not attempted — outside this
packet's scope and time budget).

### 3.4 Partial results: 71% coverage, confirmed real numbers

With both `test_fit_sigma_scale.py` ignored and the network calls blocked,
the run reached **9,490 individual test results at the 71% mark** before
hitting the `test_pre_live_integration.py` hang (§3.2):

- **382 failed**
- **26 errors** (collection or setup-time)
- 81 skipped, 11 xfail/xpass (test-hygiene, not counted as red)
- 8,860 passed

A representative sample of the failing cluster from an earlier `-v` partial
run (before the network workaround, first ~8%): repeated failures across the
`test_bayes_precision_fusion_*` family (5+ distinct files) — worth a
dedicated look in a follow-up, not triaged here.

**I do not have individual failing-test names for the 382+26 flat-bucket
failures** — the run was in `-q` mode (no `-v`) and died mid-stream before
pytest could print its end-of-run `FAILURES`/`short test summary` section,
so only the aggregate pass/fail/error counts from the progress-dot stream
are available. A re-run with `-v --tb=no` after fixing or excluding the
remaining hang would recover the full list.

## 4. Running total vs. the blueprint's 867

| | count |
|---|---|
| Named domains (32, complete) | **127** |
| Flat bucket (partial, 71% coverage) | **408** (382F + 26E) |
| **Confirmed real red so far** | **535** |
| Flat bucket remaining (~29%, unmeasured) | unknown — blocked by §3.2 |

535 confirmed failures already exceed the blueprint's implicit budget (a
`pre_existing_failure_registry` of only 3 entries), and the true total is
higher still once the remaining ~29% of the flat bucket is measured. If the
failure rate holds roughly steady across the unmeasured remainder
(382+26 in 9,490 tests ≈ 4.3% red rate), the full flat bucket would land
around 570 red, putting the true grand total in the **650–700** range — in
the neighborhood of the blueprint's 867 but not a match, and this is an
extrapolation, not a count. **The blueprint's 867 cannot be confirmed or
refuted precisely without finishing the flat-bucket run**; what's certain is
that the true number is not "3" and a `pre_existing_failure_registry` with 3
entries is not a defensible gate baseline.

## 5. What this means for the `pre_existing_failure_registry`

The registry needs to be rebuilt from a full, clean run of this suite (fix or
mock out §3.1/§3.3 first, or every future run silently dies on the same two
hangs and nobody notices). Until then, any packet gating on "0 NEW failures
vs a known pre-existing set" is gating against a registry that is off by at
least two orders of magnitude (3 vs. 535+), which means the gate currently
passes almost anything.
