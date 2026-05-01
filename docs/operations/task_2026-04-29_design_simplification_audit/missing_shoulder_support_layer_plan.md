# Missing Shoulder Support Layer Plan

Status: revised implementation plan after critic review
Date: 2026-05-01
Packet: `task_2026-04-29_design_simplification_audit`
Authority status: packet evidence only; not live-deploy authorization, not production DB mutation authorization

## Objective

Repair the weather-bin topology blocker without weakening Zeus's statistical
or execution safety rules.

The current failure mode is not that Polymarket lacks contract shoulder bins.
The current failure mode is that Zeus collapses two different concepts into
one list:

- `contract support`: every Gamma child market that defines the settlement
  partition, including closed/non-accepting/non-orderbook children.
- `executable surface`: the subset of Gamma children that can currently be
  traded and snapshotted into a live CLOB order.

The fix must make that split explicit through the full probability chain:

`contract support bins -> P_raw/P_cal/bootstrap/posterior`

then

`executable mask -> edge scan/FDR/token routing/snapshot/execution`

## Current Evidence

Read-only Gamma probe on 2026-05-01:

- Source-matched active events with parseable bins: `181`.
- Events whose current tradable-only child subset fails strict topology: `32`.
- Failure classes on tradable-only subset:
  - `26` missing low shoulder.
  - `6` missing high shoulder.
- Failure classes on all Gamma child markets for those same events:
  - `32` complete.
  - `0` true missing shoulders.
  - `0` internal gaps.
  - `0` overlaps.
  - `0` mixed units.

Interpretation: the real settlement contract is complete. The tradable subset
is incomplete because some children are closed, non-accepting, or otherwise
not currently executable.

Sample evidence:

- NYC LOW 2026-04-30: all 11 child bins exist; 5 are currently tradable.
- Houston HIGH 2026-04-30: all 11 child bins exist; only 2 are currently
  tradable.
- Tel Aviv HIGH 2026-05-01: all 11 child bins exist; 10 are currently
  tradable, with the left shoulder closed/non-accepting.

Evidence-freeze requirement before M1 implementation:

- Persist the probe command and JSON output in the packet evidence log before
  changing runtime code.
- Active-event definition: Gamma event is source-matched, weather-temperature
  scoped, not event-level closed, and has parseable HIGH/LOW child-market bins.
- Captured child fields must include child market ID, condition ID when
  present, outcome label/question, parsed bin bounds/unit, `closed`/`isClosed`,
  `active`/`isActive`, `acceptingOrders`, `enableOrderBook` or equivalent
  orderbook flag, token IDs, YES/NO prices when executable, and source-match
  provenance.
- The probe must report both views for each event:
  `all_child_support_topology_status` and
  `currently_executable_topology_status`.
- The implementation blocker is confirmed only if all-child support remains
  complete while executable-only subsets remain incomplete. If all-child
  support is truly incomplete, this plan's synthetic-shoulder deferral applies
  and the event must fail closed until a separate evidence ledger exists.

## Non-Goals

- Do not relax `validate_bin_topology()`.
- Do not synthesize fake executable bins.
- Do not assign synthetic prices, token IDs, NO prices, executable snapshot
  IDs, or CLOB facts to non-executable support bins.
- Do not normalize probability over the executable subset.
- Do not use closed/non-accepting child prices as executable quotes.
- Do not authorize live deployment, production DB mutation, source config
  promotion, Paris release, calibration retrain, or live venue side effects.

## Invariants

1. Strict support topology remains mandatory.

   `validate_bin_topology(support_bins)` must pass before probability
   computation. Internal gaps, overlaps, mixed units, malformed F/C widths, and
   open-open universal bins remain fail-closed.

2. Probability mass is computed over settlement support, not over liquidity.

   `p_raw`, `p_cal`, bootstrap recomputation, crosscheck agreement, and
   posterior support all use the complete contract-support bin vector.

3. Execution is possible only on real executable children.

   Edge scan, full-family FDR, selected edge lookup, token routing, and
   executable snapshot capture operate only where `executable_mask[i] == True`.

4. FDR denominator is executable hypotheses only.

   Non-executable support bins are probability support, not tested live trade
   hypotheses. Including them would overcount the active family; letting them
   select would create false positives.

5. Learning and persistence must preserve topology identity.

   A persisted `p_raw_json` vector is unsafe unless its bin order, labels,
   bounds, units, and executable/support mask are preserved in the same
   snapshot provenance boundary.

6. Market fusion must never impute non-executable quotes.

   `p_raw` and `p_cal` are full-support vectors. Market-prior/fusion inputs are
   executable-only facts aligned back to support indexes. For non-executable
   indexes, fusion is explicitly `disabled_non_executable` or fail-closed if
   the current fusion code cannot represent missing executable quotes without
   treating them as `0`, `1`, or stale closed prices. Non-executable posterior
   values are reporting/replay context only and cannot drive edge, FDR, sizing,
   or exit confidence.

## Design Decision

Build a first-class support topology layer instead of adding ad hoc synthetic
`Bin`s to the existing executable market path.

Proposed contract object shape:

```python
@dataclass(frozen=True)
class MarketSupportTopology:
    support_bins: list[Bin]
    executable_mask: np.ndarray
    token_payload_by_support_index: dict[int, dict]
    support_outcomes: list[dict]
    executable_outcomes: list[dict]
    topology_status: str
    provenance: dict
```

`support_bins` are derived from all parseable Gamma child markets for the
event. `executable_mask` is derived from explicit child facts:

- `closed` / `isClosed`
- `active` / `isActive`
- `acceptingOrders`
- `enableOrderBook` / `orderbookEnabled`
- valid YES/NO token facts

Closed/non-accepting children can define support, but cannot provide
executable prices, CLOB snapshots, or selected trade intents.

Resolved critic defaults:

- `BinEdge` carries `support_index`; evaluator does not recover identity via
  ambiguous label/index lookup after selection.
- FDR denominator is the number of executable live hypotheses actually scanned,
  not `len(support_bins)`.
- Persisted family metadata records non-executable support bins as untested
  context, while selected hypotheses are executable-only.
- `ensemble_snapshots_v2.provenance_json["p_raw_topology"]` is the preferred
  first implementation boundary. If existing provenance cannot store this
  metadata atomically with `p_raw_json`, stop and plan a schema change instead
  of trading partial-executable families.
- Both shoulders being non-executable is not a blanket market-quality blocker
  when support topology is complete. Tradability is per executable hypothesis.
  Add telemetry for this condition, but do not require an executable shoulder
  unless later live evidence shows systematic mispricing or settlement-quality
  risk.

## Implementation Phases

### M0 - Freeze Reproducible Evidence

Likely files:

- `docs/operations/task_2026-04-29_design_simplification_audit/evidence.md`
- optional read-only probe helper if a reusable script is needed

Actions:

- Re-run the Gamma support/executable topology probe and persist the exact
  command, timestamp, city/date/metric samples, aggregate counts, and redacted
  representative child payloads.
- Confirm the probe distinguishes all-child support completeness from current
  executable subset completeness.
- Stop if the current blocker is no longer all-child-complete /
  executable-incomplete.

Acceptance tests/checks:

- Packet evidence includes aggregate counts and at least three sample events
  covering missing-low-executable, missing-high-executable, and near-complete
  executable cases.
- The evidence names the Gamma fields used to decide support membership and
  executability.
- No runtime source files are modified in M0.

### M1 - Parse Contract Support Separately From Executability

Likely files:

- `src/data/market_scanner.py`
- `src/types/market.py` or a small new helper under an existing owned module
- `tests/test_market_scanner_provenance.py`

Actions:

- Add a support parser that reads all Gamma child markets, not only tradable
  children.
- Preserve existing executable-only behavior for legacy callers or make it a
  named wrapper around the new support parser.
- Return aligned support bins, executable mask, token payload map, and raw
  child facts.
- Run strict `validate_bin_topology()` on support bins.

Acceptance tests:

- All-child complete but executable subset missing a shoulder is accepted as
  support-complete.
- Internal gap in all-child support fails closed.
- Overlap in all-child support fails closed.
- Mixed units fail closed.
- Bad F/C width fails closed.
- Open-open universal bin fails closed.
- Non-executable child has no executable token payload.

### M2 - Add Executable Mask to MarketAnalysis

Likely files:

- `src/strategy/market_analysis.py`
- `src/strategy/market_analysis_family_scan.py`
- `tests/test_market_analysis.py`

Actions:

- Add optional `executable_mask` aligned to `bins`.
- Default remains all `True` for backward compatibility where callers pass
  complete executable binary fixtures.
- `find_edges()` skips non-executable bins.
- `scan_full_hypothesis_family()` skips non-executable bins.
- `_bootstrap_bin()` and `_bootstrap_bin_no()` may still use full support bins
  internally, but should reject direct bootstrap calls on non-executable
  indexes.
- Family-scan output records `executable_hypothesis_count` and the skipped
  non-executable support indexes as context.

Acceptance tests:

- Non-executable support bin with high model probability and missing/zero
  market quote creates no `buy_yes` edge.
- Non-executable support bin creates no `buy_no` edge.
- Non-executable support bin does not appear in full-family hypotheses.
- Full-family FDR denominator equals the executable hypothesis count actually
  scanned, never support-bin count.
- A non-executable support bin cannot be returned as a selected hypothesis
  even when its model probability would otherwise dominate.
- Bootstrap still recomputes probability across all support bins for an
  executable center bin.
- Existing binary market tests still pass.

### M3 - Wire Evaluator to Support Topology

Likely files:

- `src/engine/evaluator.py`
- `tests/test_runtime_guards.py` or a focused evaluator test
- `tests/test_market_scanner_provenance.py`

Actions:

- Build `support_topology` from candidate outcomes before p_raw.
- Use `support_bins` everywhere the probability chain currently uses `bins`:
  ENS/Day0 vector, crosscheck vector, Platt calibration, bootstrap,
  `MarketAnalysis`, and persisted decision vectors.
- Build market quote vectors aligned to `support_bins`; non-executable
  entries must remain explicitly non-executable, not quote-derived.
- Disable market fusion for non-executable indexes or fail closed if the
  current fusion implementation cannot represent missing executable quotes
  safely. Never feed closed-child `0`/`1` prices, nulls coerced to zero, or
  stale Gamma prices into posterior/edge as market facts.
- Build `token_map` only for executable support indexes.
- Replace `bins.index(edge.bin)` lookup with `edge.support_index`, guarded by
  executable mask and token-map presence.

Acceptance tests:

- A current-style market whose executable subset lacks a shoulder no longer
  rejects at `MARKET_FILTER/bin topology`.
- A selected executable bin uses the correct real YES/NO token payload.
- A non-executable shoulder with high model probability cannot be selected.
- Non-executable support quotes/prices cannot change posterior values used for
  edge, FDR, sizing, or execution.
- If FDR selects a non-materialized hypothesis, evaluator fails closed with a
  clear rejection reason.
- If fusion cannot represent executable-only market facts against full support,
  evaluator fails closed rather than imputing missing quotes.

### M4 - Reuse the Same Support Builder in Monitor Refresh

Likely files:

- `src/engine/monitor_refresh.py`
- focused monitor tests

Actions:

- Replace `_build_all_bins()` sibling reconstruction with the same support
  topology builder.
- Compute held-bin probability against full support bins.
- Avoid single-bin fallback for probability refresh. If support cannot be
  reconstructed safely, mark monitor probability stale. A stale refresh must
  not create a new probability, drive exit sizing, or present false confidence.
  Only existing held real tokens may be routed for exit, using the existing
  lifecycle/risk path rather than synthetic sibling support.

Acceptance tests:

- Held position in an executable subset missing one shoulder refreshes against
  full support.
- Held market not found in support fails stale, not single-bin probability,
  and does not update exit sizing from the failed refresh.
- Non-executable sibling support contributes probability mass but cannot
  create an exit order token.

### M5 - Persist Topology Metadata With P_raw

Likely files:

- `src/engine/evaluator.py`
- possibly `src/state/schema/v2_schema.py` only if existing
  `provenance_json` cannot safely carry the metadata
- focused snapshot/provenance tests

Preferred implementation:

- Store topology metadata inside existing `ensemble_snapshots_v2.provenance_json`
  under a `p_raw_topology` key when `_store_snapshot_p_raw()` writes
  `p_raw_json`.
- Mirror enough metadata for replay/audit:
  - bin labels
  - low/high bounds
  - unit
  - support index
  - executable mask
  - Gamma child market IDs / condition IDs when present
  - support/executable topology status
  - market-fusion status per support index
  - executable hypothesis count and skipped non-executable support indexes

Acceptance tests:

- `p_raw_json` and `p_raw_topology` have matching cardinality.
- Non-executable support indexes are marked and cannot be reconstructed as
  executable children.
- Replay can distinguish executable tested hypotheses from non-executable
  untested support context.
- If topology metadata persistence fails for partial-executable families,
  evaluator fails closed.

### M6 - End-to-End Smoke And Guardrail Verification

Read-only smoke:

- Re-run Gamma topology probe.
- Expected result: source-matched events are support-complete; executable
  subset incompleteness is reported as mask metadata, not as bin-topology
  rejection.
- For every smoke-selected event, assert executable FDR denominator equals the
  number of executable live hypotheses and non-executable support indexes are
  not selected.

Focused checks:

- `python3 -m pytest -q -p no:cacheprovider tests/test_market_scanner_provenance.py`
- `python3 -m pytest -q -p no:cacheprovider tests/test_market_analysis.py`
- focused evaluator/runtime guard tests added in M3.
- focused monitor refresh tests added in M4.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <changed files>`
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <changed files>`

## Rollback Plan

- The new support builder should be introduced as a narrow seam.
- If tests reveal unexpected coupling, revert evaluator/monitor callers to the
  old executable-only outcome list and keep the parser behind tests only.
- No production DB mutation is part of this plan.
- No schema migration should be introduced unless `provenance_json` proves
  insufficient for topology metadata.

## Critic Review Resolutions

The 2026-05-01 critic pass returned `REVISE`, not `BLOCK`. The core
support/executable split was accepted; the plan was revised with these binding
defaults:

1. Closed or non-accepting child prices are diagnostic payload only. They are
   excluded from executable market quote vectors and cannot affect posterior,
   edge, FDR, sizing, or exit confidence.
2. `BinEdge` carries `support_index`.
3. `ensemble_snapshots_v2.provenance_json["p_raw_topology"]` is sufficient for
   the first implementation only if metadata is stored atomically with
   `p_raw_json`; otherwise stop for schema planning.
4. Complete support topology plus at least one executable selected hypothesis
   is sufficient. Executable shoulders are not required as a separate quality
   rule.
5. FDR family definition is executable live hypotheses only. Persisted metadata
   also records non-executable support as untested context.

## Ready-To-Implement Criteria

Begin implementation only after critic review confirms:

- support-vs-executable split is the correct first-principles repair;
- no synthetic executable facts are introduced;
- strict topology remains the support invariant;
- FDR denominator is explicitly defined;
- p_raw topology provenance is adequate for replay/learning;
- monitor refresh uses the same support builder or fails stale.
