# finite_evidence_probability_symmetry -- Plan

Date: 2026-07-11
Branch: `p2-pending-exit-restart-redecision`
Status: active

## Background

Current live source-clock posteriors display Normal point complements near
`0.999`, while the executable uncertainty carrier conditions on that Normal
family and can grant NO certainty unsupported by the finite current evidence.
The same path still applies a settlement-fitted historical far-tail q_lcb cap to
YES, contrary to the current-evidence-only probability law and the operator's
first-principles constraint.

## Scope

Money path: current source shape -> settlement-preimage point q -> coherent
current-evidence band -> symmetric YES/NO samples -> global lower-CVaR order
selection. Harmonizes executable source, the active replacement authority, and
test topology under INV-06 and INV-41; supersedes no independent authority.

The live proof loop also owns one execution-liveness defect discovered after
deployment: command recovery opened TRADE as MAIN with WORLD attached, while
price-channel opened WORLD as MAIN with TRADE attached. Concurrent
``BEGIN IMMEDIATE`` calls therefore reserved the two WAL writers in opposite
orders. The repair must make command-recovery hold the existing canonical
WORLD+TRADE live flocks for each short apply transaction; increasing timeouts or
editing processing rows is outside scope.

Fresh post-deploy stack evidence exposed the second half of the same inversion:
the held-position monitor could open a TRADE transaction, fetch a quote, then
persist a world-owned Day0 observation fact before releasing TRADE. The repair
must commit earlier monitor writes before probability refresh and delay the
trade-owned microstructure write until the WORLD observation write is complete.

Fresh forward runtime evidence at the London local-day boundary exposed a
probability-authority discontinuity: the monitor switched from a fresh
replacement posterior to mandatory Day0 observation at local midnight, before
EGLC had published the target day's first same-station observation. This made
one held position probability-stale and froze every otherwise-independent
global entry. The repair may use the fresh replacement posterior only inside
the existing local-day-start coverage grace while no Day0 observation is
available. Once an observation exists, after the grace expires, or when the
replacement posterior is stale, the Day0 lane remains fail-closed.

The same live proof window exposed an execution-truth discontinuity after a
reduce-only YES exit: a positive but partial MATCHED order fact was promoted to
`FILL_CONFIRMED`, although its cumulative filled size did not cover the command
size.  The repair must keep a partial exit command and position in their
nonterminal states until cumulative canonical trade facts cover the submitted
shares.  Chain-only dust remains separate reconciliation evidence; it must not
retroactively turn a partial order fact into full-fill proof.

## Deliverables
- Keep Normal `q_json` as an immutable point estimate, never as executable certainty.
- Widen the shared simplex carrier by the exact 51-member zero-hit limit and the
  distribution-free Cantelli limit from current mean/variance.
- Remove historical far-tail floors from the source-clock route; preserve them
  only for explicitly non-source-clock compatibility paths.
- Preserve Day0 absorbing physical facts as dominant.
- Commit, deploy through the official restart path, then prove the result from a
  newly materialized canonical posterior and live auction/order receipts.
- Remove the WORLD/TRADE writer-order inversion that prevents the corrected
  probability carrier from reaching live redecision.
- Make held-position probability refresh order explicit: release TRADE, write
  the current Day0 WORLD fact, then write TRADE quote/monitor evidence.
- Preserve probability continuity before the first target-day observation:
  inside the existing coverage grace, a fresh replacement posterior remains
  monitor authority; this is not permission to use stale forecast belief or to
  ignore any available Day0 observation.
- Require cumulative canonical EXIT fill quantity to cover the command and the
  current position before lifecycle alignment may emit `FILL_CONFIRMED` or
  economic close; cumulative order facts never stack on existing trade facts,
  and a tx-hash aggregate alias never stacks on its exact child trade facts in
  lifecycle finality, locked-token, journal, or capital-balance views.
- Bind the global candidate's canonical YES/NO side into the current-state
  submit certificate. A missing legacy route `side` must not become `UNKNOWN`,
  while any real certificate/candidate side mismatch remains fail-closed.
- Consume the sealed global current-state certificate consistently through
  opportunity-book admission, receipt validation, and final sizing. These
  surfaces use the global target cost/shares/max-spend envelope rather than
  re-require legacy family-route optimizer fields after global selection.
- Serialize the selected global objective from its actual expected cost,
  robust EV, and robust delta-log-wealth; the receipt must not mark the winner
  unadmitted or synthesize legacy route-optimizer metrics.

## Verification
- Focused first-principles antibody and settlement-preimage regressions pass.
- All carrier rows sum to one; NO lower-CVaR is the pointwise complement and does
  not exceed `1 - q_ucb_required`.
- Pure builder over current canonical Guangzhou 39C inputs changes executable NO
  confidence without changing its Normal point q.
- Existing global capital-optimality evaluator passes; fresh runtime evidence is
  required separately from tests.
- POSIX WAL-byte evidence shows no simultaneous opposite-order WORLD/TRADE
  writer hold after restart; reactor cycles progress beyond claim bounces.
- A deterministic local-midnight antibody proves fresh replacement belief is
  admitted only during the pre-observation coverage grace, with stale belief
  and post-grace absence still rejected.
- A partial MATCHED EXIT antibody proves the command remains PARTIAL and the
  position remains pending exit; the full-size sibling still closes exactly
  once.

## Work record

- 2026-07-11: live rows isolated the source-clock YES historical-floor / NO
  near-one asymmetry.
- 2026-07-11: first implementation's zero-hit-only member bound was rejected:
  current member values now remain in-memory and exact settlement-preimage hit
  counts drive Clopper-Pearson UCBs; provenance persists their hash/count/hits.
- 2026-07-11: current canonical posterior 32089 / snapshot 1203438,
  Guangzhou Jul 12 39C, has 0/51 hits, Normal NO point 0.999915, but current-
  evidence NO LCB and 5% lower-CVaR 0.933965; all 400 carrier rows remain simplex.
- 2026-07-11: focused antibody 3/3, current source-clock contracts 7/7, and
  global capital-optimality evaluator 226/226 passed before final deploy audit.
- 2026-07-11: live WAL byte-range locks isolated an execution deadlock:
  price-channel held WORLD while main held TRADE; command recovery and
  price-channel used opposite MAIN/ATTACH order. No DB rows were edited.
- 2026-07-11: post-deploy SIGUSR1 stack pinned the remaining inversion to
  `exit_monitor -> refresh_position -> write_day0_metric_fact`: TRADE was open
  before the WORLD fact write while price-channel held WORLD and awaited TRADE.
- 2026-07-11: at London 00:00-00:14 local, WU returned HTTP 200 but no EGLC
  sample belonged to the new target date; aviationweather's latest EGLC METAR
  was 22:50 UTC / 23:50 local. The monitor therefore recorded three consecutive
  stale probabilities despite a fresh replacement posterior, and live health
  globally excluded multiple independent positive-EV YES candidates.
- 2026-07-11: Lucknow EXIT command `23c4fa3771644e43` submitted 60 shares,
  while its canonical trade fact proved only 46.59 shares at alignment time;
  command recovery nevertheless emitted `FILL_CONFIRMED`.  Current chain/data
  truth later exposed 0.91 share as quarantined dust, so order-fill proof and
  residual reconciliation must remain separate.
- 2026-07-11: live DB inspection found eight EXIT commands where one real fill
  appeared as both `trade_id=tx_hash` and an exact child trade ID. The old
  canonical sums were exactly 2x command size; the economic identity reducer
  maps all eight back to exactly 1x while retaining distinct child trade IDs.
- 2026-07-11: after monitor freshness recovered, Madrid YES reached actual
  submit quality but was rejected as `side=UNKNOWN:direction=buy_yes`. The
  sealed global certificate carried the global candidate and direction but did
  not copy the candidate's typed YES/NO side; the final check was therefore
  testing an unproduced legacy field rather than the selected order identity.
- 2026-07-11: after side binding deployed, Madrid passed that boundary and then
  failed certificate construction with
  `EDLI_LIVE_OPPORTUNITY_BOOK_SELECTED_MISSING`. The book admission and sizing
  consumers still validated only legacy family-route fields even though T2/T3
  had already sealed a global current-state utility certificate.
