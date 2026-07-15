# finite_evidence_probability_symmetry -- Plan

Date: 2026-07-11
Branch: `p2-pending-exit-restart-redecision`
Status: active

## 2026-07-15 current-evidence coherence correction

A current 150-family source-clock census found 72 families where the absolute
ECMWF ENS member mean differed from the served provider center by more than the
entire persisted predictive sigma; 60 differed by at least 1°C. The live Los
Angeles July 16 HIGH carrier had `μ*=29.401°C`, ENS mean `24.965°C`, and claimed
`σ_pred=0.925°C`. The same raw ENS values were treated as absolute forecasts
for settlement-bin hit counts but only their centered population spread entered
`σ_pred`, silently deleting the 4.436°C current-model disagreement.

The first-principles correction retains the provider center, keeps the ENS
within-spread, and adds the absolute current ENS/provider-center displacement
as a third variance component in both predictive and center uncertainty. It
does not add a gate or historical calibration term; aligned evidence preserves
the original within-plus-between numeric decomposition.

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

Fresh 2026-07-14 auction evidence exposed a probability-variable mismatch in
the global preparation seam.  A Day0 family was made conditional on a current
full-day replacement posterior before the Day0 branch rebuilt the probability
of the final daily extreme from the current observed extreme and the forecast
hours that remain.  Those are different random variables.  The repair must let
the Day0 remaining-day authority bind directly to the current canonical
observation, current causal base snapshot, and current remaining-hour vectors;
it must not fabricate missing full-day ENS extrema or weaken replacement
readiness for any non-Day0 family.

Fresh order-outcome evidence on 2026-07-13 refuted three additional assumptions
at the decision/execution boundary:

- a reduce-only exit was refused solely because the running SHA differed from
  HEAD, even though the action could only decrease an already-held exposure;
- `WHALE_TOXICITY` bypassed the held-side probability and executable hold-vs-sell
  comparison, liquidating a Lucknow YES position that later paid $1;
- one confirmed-fill/chain-absence review position with no positive
  `chain_seen_at` made the global wealth witness throw, suppressing every
  unrelated family even though current chain cash remained known.

This slice treats those rows as falsification evidence, not as a historical
calibration corpus.  The first-principles contract is: a reduce-only action
must not be blocked by code-plane freshness; an order-flow observation may
modify evidence but cannot independently decide liquidation; and an uncertain
legacy claim contributes at most a conservative terminal-wealth upper bound,
never spendable cash and never a portfolio-wide entry veto.  INV-01, INV-05,
INV-06, INV-21, and INV-41 remain binding.  This harmonizes
`architecture/capabilities.yaml`, the executable gate, the canonical portfolio
read model, and the current global auction witness; it supersedes no separate
authority surface.

## Deliverables
- Enforce INV-43 at the real venue envelope boundary: every live BUY/SELL,
  entry/exit, single/batch unit price must be inside inclusive `[0.05, 0.95]`.
  No q-kernel, current-state, strategy-tail, risk, or order-role exception may
  waive it; rejection occurs before command persistence where possible and
  always before SDK contact.
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
- At submit, let only the exact sealed global current-state winner bypass legacy
  fixed price/profit/density/win-rate/ROI floors. Bind aggregate admission to
  its `DecisionProofAccepted` audit and executor admission to the durable
  LIVE/VERIFIED actionable certificate; a recomputable identity hash alone is
  never authority.
- Serialize the selected global objective from its actual expected cost,
  robust EV, and robust delta-log-wealth; the receipt must not mark the winner
  unadmitted or synthesize legacy route-optimizer metrics.
- Remove deployment-SHA/worktree freshness from `reduce_only_exit_submit` only;
  new-entry submit remains freshness-gated and the reduce-only lane remains
  constrained by its existing kill-switch, settlement-freeze, and risk policy.
- Make `WHALE_TOXICITY` observational evidence only.  It must not bypass fresh
  probability, executable bid, CI reversal, or hold-vs-sell economics.
- Preserve global redecision when a canonical confirmed-fill dispute lacks a
  current positive chain timestamp: exclude the disputed claim from spendable
  inventory, retain only its maximum payoff in the conservative wealth ceiling,
  and bind that uncertainty into the wealth identity.
- Keep stale held-position probability fail-closed for economically actionable
  exposure, but do not convert a fully evidenced sub-minimum `pending_exit`
  dust claim into a portfolio-wide entry veto. The health surface remains
  degraded and visible; only entry authority scopes the failure to the dust
  position when every stale sample is exactly covered by the sub-min surface.
- Recover a confirmed logical venue fill exactly once when command redecision
  writes a later `ExecutionCommandCreated`: select the same latest command and
  acknowledgement rows as the ledger, clamp only the EDLI ledger timestamp to
  that causal boundary, preserve the raw venue observation time in payload,
  and collapse MATCHED/MINED/CONFIRMED revisions by `trade_id`.
- Remove full-day replacement readiness from the Day0 remaining-day probability
  branch only.  Bind its witness identity to the current observation, current
  causal base snapshot, remaining-model capture identities, finite-evidence
  carrier, and exact sample matrix; preserve all non-Day0 replacement gates.
- Rebind the sealed global winner's current-state economics atomically at JIT:
  `q_lcb`, edge, false-edge rate, prefilter verdict, and admission reason must
  come from one certificate.  A superseded scalar admission reject must not
  survive beside a positive globally certified edge.

## Work record — INV-43 recovery (2026-07-15)

- Git forensic found no reset/rebase/drop and no commit on any ref containing
  `LIVE_ORDER_UNIT_PRICE_MIN`: the 2026-07-13 implementation remained an
  uncommitted worktree slice and was overwritten before deployment.
- Paris 35C canonical evidence proved the consequence: market `2888967`
  accumulated `5106.247161` NO shares at chain average `$0.0039`; every loaded
  SHA during the submit window lacked INV-43.
- Recovery owns the envelope contract, executor/aggregate/qkernel seams,
  strategy registry, invariant/authority/reference surfaces, and direct
  single/batch/entry/exit antibodies. Canonical DB content is read-only.

## Verification
- INV-43 focused venue/entry/exit/current-state antibodies: `22 passed`;
  architecture contracts: `97 passed`; invariant citations, planning evidence,
  `py_compile`, and `git diff --check`: passed. The broader three-file
  qkernel/aggregate/economics slice produced `249 passed, 1 failed`; the lone
  failure is an existing 0.10 strategy-floor expectation for an in-band 0.0538
  order, not an absolute-band bypass.
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
- A stale/dirty HEAD antibody proves new entry is still refused while the exact
  reduce-only capability is admitted.
- Lucknow-shaped YES and mirrored NO tests prove whale flow alone cannot force
  liquidation, while independent economic reversal and settlement evidence
  still exit.
- A Shanghai-shaped confirmed-fill dispute with missing `chain_seen_at` proves
  current cash remains selectable, the claim is not spendable, and its maximum
  payoff is represented only in the wealth ceiling.
- A Wellington-shaped stale `pending_exit` claim below the current venue minimum
  does not block unrelated entry families; the same stale claim in `active`
  phase, an incomplete sample, or any non-dust stale position still blocks.
- Mirrored YES/NO submit antibodies prove the same sealed-global rule admits
  both sides, while a legacy payload with recomputed current-state markers still
  hits the legacy floor because it does not match durable decision authority.
- A production-shaped REST fill antibody proves two command rows times two
  trade-status rows become one `UserTradeObserved`; a WS confirmed re-report
  antibody proves the same logical-fill identity, and non-fill states remain
  ineligible for recovery.
- A Day0 global-preparation antibody proves that missing full-day replacement
  readiness cannot block a complete current observation + remaining-day
  witness, while the same missing readiness still blocks a forecast event and
  missing Day0 current inputs still fail closed.
- A production-shaped Day0 global-winner antibody starts with the legacy
  `q_lcb=0` capital-efficiency reject and proves that current-state rebinding
  updates its FDR inputs as one unit before submit preflight.

## Work record

- 2026-07-14: post-deploy global auctions covered all 128 current families and
  repeatedly selected Sao Paulo Jul 14 HIGH NO with positive robust delta-log
  wealth, but preflight inherited `passed_prefilter=false` and the old
  `ADMISSION_CAPITAL_EFFICIENCY_LCB_EV:q_lcb=0` reason after rebinding the
  candidate to `q_lcb=0.575`.  No venue command was created.  The repair makes
  the current-state proof overlay atomic; book, probability, cash, wealth, and
  venue safety revalidation remain fail-closed.
- 2026-07-14: later 129/129 epochs selected the same Sao Paulo family at a
  full-depth NO cost of `0.999`, with Day0 win-probability LCB `1.0`, positive
  robust EV, and positive delta-log wealth.  Targeted refresh then switched the
  legacy local proof to its fixed near-settled-price rejection, so exact
  condition/token/direction binding failed before the current-state certificate
  could be evaluated.  The JIT seam now recovers only scalar gates that the
  sealed global certificate can replace; evidence/structure rejections remain
  non-rebindable.
- 2026-07-14: canonical global-auction receipts retained complete scope scanning
  but excluded Hong Kong Jul 15 LOW and Miami Jul 14 LOW because their full-day
  ENS extrema were temporally boundary-ambiguous.  Both were Day0 families with
  current observations and remaining-hour forecast inputs.  The exclusion was
  therefore traced to an incorrect full-day-posterior dependency, not to an
  absent current Day0 probability variable.  No historical fit or synthesized
  member value is admitted by the repair.
- 2026-07-14: Paris Jul 14 HIGH 35C NO produced a real FOK match: the command
  requested 90 shares at a `0.012` limit, while the venue confirmed
  `99.726666` shares at `0.011` with a Polygon transaction hash.  Wallet and
  chain projections synchronized the exposure, but command recovery kept the
  row in `REVIEW_REQUIRED` because it required fill price equality.  A limit is
  a one-sided economic bound, not an equality: BUY fills may improve below it
  and SELL fills may improve above it.  Recovery and restart priming now share
  that side-aware rule while retaining exact token, side, time, unique trade,
  bound order ID, open-order absence, confirmed status, and residual proofs.

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
- 2026-07-13: Cape Town 20C NO and Guangzhou 39C NO expired worthless after
  near-one source-clock beliefs; Lucknow 35C YES was sold by the unconditional
  whale trigger before paying $1; Wellington 11C NO exited after current belief
  reversal and avoided the terminal loss. These are current falsification cases
  for the decision shape, not inputs to a fitted historical error floor.
- 2026-07-13: Guangzhou emitted repeated EXIT intents that were refused only by
  `reduce_only_exit_deployment_freshness_mismatch`. Current runtime inspection
  reproduced the condition from the loaded-SHA/HEAD difference.
- 2026-07-13: global entry retries isolated one contradictory projection:
  Shanghai 29C NO remained `synced` with positive `chain_shares` but blank
  `chain_seen_at` while emitting `entry_authority_chain_absence_conflict`.
  Current chain cash was fresh; the prior witness converted this one disputed
  claim into a portfolio-wide exception.
- 2026-07-13: implementation now keeps deployment freshness on new-entry
  `live_venue_submit` while removing it from reduce-only submit, demotes whale
  flow to observation, and represents unresolved local claims only in the
  terminal-wealth ceiling. A live canonical read-only replay produced
  floor/spendable `$1146.300538` and ceiling `$1361.238629` across 13 open
  positions without the prior chain-time exception.
- 2026-07-13: independent `gpt-5.6-sol` read-only review found that deleting
  the legacy reduce-only freshness error classifier would leave already-
  persisted retries cooling for up to 15 minutes. The compatibility classifier
  and antibody were restored without restoring the gate. The reviewer then
  re-read the final diff, verified mixed current balances plus uncertain claims,
  and returned PASS with no material finding.
- 2026-07-13: two post-deploy reactor cycles proved the wealth exception fixed:
  `CURRENT_WEALTH_POSITION_CHAIN_TIME_INVALID` disappeared. The sole remaining
  global entry blocker was one stale Wellington `pending_exit` dust claim with
  `0.00818` share against a venue minimum of `5`.
- 2026-07-13: the first dust-scoping implementation was rejected by independent
  `gpt-5.6-sol` review: all health queries reported a display-limited sample
  length as count, so an unseen eleventh actionable row could have been hidden;
  zero-count categories also failed to require an explicit empty sample. The
  producer now emits exact counts plus explicit truncation facts while retaining
  bounded display samples. Entry scoping requires every monitor and sub-min set
  to be complete, non-truncated, count-consistent, and ID-covered; missing or
  truncated evidence remains fail-closed. A second independent review then
  rejected stale venue-minimum evidence: sub-min coverage now requires the
  snapshot deadline to remain fresh at the entry decision. A durable canonical
  `MARKET_CLOSED_HOLD_TO_SETTLEMENT` event is the separate absorbing proof that
  a closed market needs settlement, not a fresh probability or a sell attempt.
  The Wellington token returned current CLOB `/book` 404, matching its latest
  canonical closed-hold event; neither fact is relabeled as a fresh book.
  A final non-object JSON antibody closed the last producer exception path;
  malformed JSON now yields a degraded read-unavailable surface. Independent
  `gpt-5.6-sol` follow-up returned PASS with no finding. Focused antibodies
  passed 17/17, the two affected test modules passed 223/224 with one pre-existing Day0 fragile-
  edge expectation failure, and the unchanged capital-optimality evaluator
  passed 258/258.
- 2026-07-13: the first fully healthy post-restart auction reached the NO bound
  certificate and rejected all 21 candidates with
  `parent_probability:side_q_lcb_served`. The global selection binder copied an
  already tightened tail LCB back into the field named `pre_qkernel_q_lcb_5pct`,
  overwriting the immutable replacement-served NO bound while leaving the
  signed certificate unchanged. The current-state tail may tighten executable
  economics, but it cannot rewrite its parent. The binder now preserves the
  existing pre-qkernel value and only synthesizes it for legacy proofs where the
  field is absent.
- 2026-07-13: Helsinki Jul 14 25C NO became an actual 5-share FOK fill at
  `$0.48` (`$2.40` spend), with decision-time `q_live=0.819330` and
  `q_lcb=0.696391`. Command recovery moved the durable command from
  `REVIEW_REQUIRED` to `FILLED` after canonical chain trade confirmation; the
  position then entered normal active monitoring.
- 2026-07-13: a later current global auction selected Jeddah YES (5 shares,
  `$0.44` limit) ahead of every NO alternative, proving YES participation in the
  same executable universe. Submit revalidation then rejected it only because
  the legacy absolute expected-profit floor required `$1` while the sealed
  global certificate remained positive in robust delta-log-wealth and robust
  fee-aware EV. This was a downstream objective mismatch, not a probability or
  side-selection failure.
- 2026-07-13: the first floor-alignment diff was rejected by independent
  `gpt-5.6-sol` review because a caller could add arbitrary current-state markers
  to legacy economics and recompute the public hash. Submit bypass now requires
  exact equality with the decision aggregate's qkernel audit and the durable
  LIVE/VERIFIED actionable payload, plus the full global witness, terminal-payoff,
  utility, and optimum grammar. Follow-up adversarial probes found and closed a
  one-way current-to-legacy intent downgrade and a route-less global payload q
  binding gap; global certificates no longer need legacy optimizer fields in
  either aggregate or executor. Mirrored YES/NO, recomputed-marker, downgrade,
  and route-less q-drift antibodies pass; affected modules pass 155/155; the
  declared capital-optimality evaluator passes 258/258. The actual 82-field
  Jeddah YES certificate validates against the final shared global grammar.
