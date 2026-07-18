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

Post-deploy proof then exposed an identity/redecision defect: only newly drained
seeds materialized the corrected formula, while otherwise-current active
families remained covered by pre-change posterior certificates. A complete
global auction therefore mixed probability semantics and selected a São Paulo
candidate from an old carrier. The repair must bind a shared current-evidence
semantics revision into the shape and posterior identity, make the existing
coverage/seed loop naturally re-materialize mismatched active families, and
refuse only a shaped certificate whose revision is not current. This is
probability identity, not deployment freshness and not a new runtime gate.

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
  entry/exit, single/batch unit price must be inside `[0.05, 1)`.
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
- Re-materialize every current active family when the current-evidence
  semantics revision changes; entry and monitor readers must not consume an
  older shaped certificate during that convergence window.
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

The 2026-07-18 forward auction falsified the symmetric 0.95 ceiling: a selected
exact-payoff NO at a sub-unit venue price had positive current robust EV and
delta log wealth but was rejected solely by the envelope ceiling. INV-43 now
keeps the 0.05 cheap-tail floor that prevents Paris-shaped quantity explosions
and uses the binary payout boundary 1 as an exclusive upper domain. High-price
entry admission remains conditional on the existing current-probability, EV,
delta-log-wealth, Fractional Kelly, JIT-book, and submit-certificate proofs.
Focused entry/exit/single/batch antibodies passed 37/37 and the complete W3
auction seam passed 202/202. Independent K0 verification found no submit bypass:
prices below 0.05, at or above 1, and non-finite values still fail before SDK
contact; 0.999 is admitted only inside the existing positive global certificate.

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

- 2026-07-17: the first post-fill Manila auction exposed a deterministic
  projection-lag window: the authenticated fill was already canonical and the
  wallet snapshot already bounded its cash effect, but `chain_shares` remained
  zero until the next reconciliation pass.  Current wealth now uses an exact
  CTF balance when the current chain snapshot carries the token; otherwise it
  admits only canonical `venue_confirmed_*` shares as a non-spendable maximum
  claim.  Unverified submitted shares remain invalid, so the repair removes a
  throughput gap without turning uncertain inventory into cash.

- 2026-07-17: the Milan authenticated fill had no `venue_order_facts` row, so
  terminal reservation settlement released the whole PUSD reservation with
  `converted_amount=0` even though canonical trade facts proved the fill.
  Reservation conversion now takes the larger of cumulative order matched size
  and the sum of exactly-once economic trade fills.  The shared alias reducer
  excludes EDLI/tx-hash duplicates before summing; tests prove both no-order-fact
  conversion and partial-fill alias deduplication.

- 2026-07-17: the first post-deploy authenticated Tokyo fill exposed a
  command-recovery gap.  The command remained `REVIEW_REQUIRED` after its
  point-order read failed, while canonical `venue_trade_facts` proved the full
  31.6-share fill and the EDLI projection wrote a terminal `execution_fact`
  with no `filled_at`.  RiskGuard correctly rejected that malformed exposure
  authority, then became stale and forced global reduce-only behavior.  The
  repair admits authenticated trade facts as fill-time authority when an order
  fact is absent, collapses the EDLI derived alias onto its cited source trade
  fact, and writes one canonical per-command execution fact at the real venue
  timestamp.  It does not edit canonical rows manually, invent wall-clock fill
  time, weaken strict exposure validation, or count the same economic fill
  twice.

- 2026-07-17: the same Tokyo fill also left its venue command in
  `REVIEW_REQUIRED`, so the canonical PUSD reservation stayed open and every
  subsequent global auction failed closed with
  `CURRENT_WEALTH_INFLIGHT_BUY_AMBIGUOUS`.  Command recovery now lets one
  authenticated `WS_USER` `CONFIRMED` full-fill fact plus a matching active,
  synced projection cross the existing review boundary through a formally
  validated `FILL_CONFIRMED` proof.  The proof deduplicates the EDLI alias,
  binds the exact command/order/size and venue time, then uses the canonical
  terminal-event seam to convert the reservation; it does not mutate the live
  DB out of band or infer a fill from portfolio state alone.

- 2026-07-17: command terminalization then exposed a second exactly-once gap:
  the native Tokyo fill, its EDLI `source_trade_fact_id` alias, and a later REST
  re-observation projected one 31.6-share economic fill as 63.2 local shares
  while chain truth remained 31.6.  The shared economic reducer now validates
  the EDLI pointer against the append-only source fact, independent of which
  later revision becomes canonical, and entry reconciliation consumes that
  reducer just as exit reconciliation already did.  A production-shaped
  re-observation test proves the canonical projection converges from the
  contaminated 63.2/$37.92 state back to 31.6/$18.96 without an out-of-band DB
  edit.

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

## 2026-07-18 alpha-clock fault containment slice

Current runtime evidence and source tracing show that family probability
preparation is serial inside one global auction.  The adapter currently erases
the distinction between a transient SQLite lock on one family and an unknown
runtime failure; the batch therefore aborts before evaluating siblings
that already have current probability and book authority.

The fault boundary for this slice is one weather family.  A recognized SQLite
`locked`/`busy` failure makes only that family's current authority unavailable
for the epoch, records the exact exclusion in the global receipt, and continues
selection over the remaining complete admissible set.  Family-local missing
current authority uses an explicit `FamilyAuthorityUnavailable` reason allowlist;
generic `ValueError`, unknown `OperationalError`, schema drift, malformed
identity/time/simplex contracts, and every unclassified exception remain
whole-epoch fail-closed.  No stale probability fallback, synthetic q, risk-gate
relaxation, DB mutation, or venue action is permitted.

Files: `src/engine/event_reactor_adapter.py`,
`src/engine/global_batch_runtime.py`,
`tests/integration/test_w3_solve_seam_g3.py`,
`tests/events/test_transient_money_path_requeue.py`, and this plan.  Acceptance
requires an adapter-to-batch two-family counterexample where the unaffected
family still wins, preservation of whole-epoch rejection for contract, schema,
and unknown preparation errors, focused tests, planning-lock, compilation, and
`git diff --check`.  Deployment remains operator-only.

Verification: independent review found and closed the original generic
`ValueError` downgrade gap.  The final full W3 global-auction seam passes
`197/197`; the money-path retry suite passes `43/43`; the focused adapter-to-batch
lock and contract/schema/unknown-error counterexamples pass `12/12`.
Planning-lock, compilation, and `git diff --check` pass.  The existing repo-wide
source registry check still reports unrelated baseline drift.  No canonical DB
was copied or mutated, and no process restart, config change, or venue action was
performed.

## 2026-07-18 Day0 action-lane fault containment

The durable Day0 wake previously made targeted held-position monitor success a
strict prerequisite for processing the same observation event.  A monitor DB,
quote, or lifecycle failure therefore blocked both the dead-position SELL lane
and every sibling BUY/HOLD/CASH redecision even when the reactor still had
current family authority.

The wake now owns two independent completion conditions.  A failed or incomplete
targeted monitor keeps the durable wake pending, but no longer blocks the event
reactor from consuming the committed observation.  Once the event is terminal,
the next poll retries only the targeted monitor and does not repeat the reactor;
the wake is acknowledged only after both lanes complete.  A monitor already in
flight remains a concurrency boundary, and every downstream submit, capital,
risk, freshness, and unknown-side-effect gate remains unchanged.

Acceptance requires the existing monitor-before-reactor success ordering, a
counterexample for both `False` and exception monitor outcomes, proof that the
reactor runs exactly once, proof that the wake remains durable until monitor
recovery, and no regression to future-retry wake retirement.  The complete wake
listener suite passes `79/79`; the focused periodic-monitor preemption antibody
passes `1/1`; compilation and `git diff --check` pass.  No runtime process,
canonical DB, config, or venue state was changed.

The same trace found a redundant lock wait at the lane boundary: an urgent Day0
monitor waited up to 30 seconds for the active reactor even though the durable
wake already retries and the active reactor observes the urgent preemption flag.
Urgent handoff acquisition is now non-blocking; periodic monitor handoff retains
the existing 30-second bound.  This prevents the wake listener itself from being
occupied for tens of seconds while preserving mutual exclusion and durable
retry.  The targeted-handoff antibody proves a zero-second timeout.

## 2026-07-18 current-center scenario preservation

Four live losing NO certificates exposed a probability-geometry defect rather
than a YES/NO complement defect. Manila, Panama, and Taipei served target-bin
YES upper bounds almost identical to their point estimates even though the
current provider center sat near the eventual winning exact bin and the ENS
center materially disagreed. The old executable band folded that disagreement
into one wide predictive Normal, then bootstrapped only the provider center;
for an exact bin near that center, extra width lowers its mass and therefore
cannot express the competing current-evidence world. Singapore already carried
a materially wide target YES upper bound and remains an acknowledged stochastic
loss, not a sign/complement error.

The correction preserves the strategy-of-record point q and changes only its
current-evidence ambiguity band. In addition to exact-member CP and Cantelli
floors, every bin now receives the maximum probability licensed by two observed
current scenarios: provider center plus ENS within-spread, and ENS center plus
the same within-spread. The existing coherent-simplex stress carrier transports
those marginal UCB requirements into symmetric YES/NO bounds and lower-CVaR.
No historical fit, price anchor, constant probability floor, or admission gate
is introduced. Both same-cycle and transported-shape revision identities bump
so stale rows must be naturally rematerialized before money-path use.

Acceptance requires a regression proving point q is unchanged while provider-
center exact-bin mass widens q_ucb enough to reject the mirrored NO at its old
cost; coherent sample rows must still sum to one; the four frozen live cases
must be replayed read-only; focused probability, cycle-policy, and global Kelly
endowment tests must pass; then standard live deploy must load the committed SHA
and produce the new semantics revision without forced orders.

Read-only replay against the exact four entry posterior identities proves the
change is selective. The target-bin YES point q remains byte-for-byte unchanged.
Manila q_ucb widens `0.239724 -> 0.558916`, so mirrored NO q_lcb becomes
`0.441084 < 0.568891` executable cost; Panama widens
`0.216604 -> 0.640282`, so NO becomes `0.359718 < 0.631780`; Taipei widens
`0.131232 -> 0.616159`, so NO becomes `0.383841 < 0.602095`. All three old
orders therefore fail the robust probability objective before Kelly sizing.
Singapore remains `q_ucb=0.308017` and `NO q_lcb=0.691983 > 0.552420`; it was
already an explicitly bounded stochastic risk and is not falsely rewritten by
the disagreement correction. Focused probability and revision tests pass
`20/20`; the three open/in-flight entry endowment antibodies pass `3/3`.

## 2026-07-18 selected Day0 deterministic-payoff preflight

The first post-deploy complete auction selected Shenzhen July 18 HIGH 30C NO,
then preflight rejected it as `DAY0_REMAINING_DAY_MEMBERS_UNAVAILABLE`. Current
authorized station evidence had already observed 31C, making the exact 30C bin
pathwise impossible: its YES payoff is zero and NO payoff is one regardless of
any remaining-hour forecast.

The global probability path already builds a `DeterministicBinPayoffWitness`
for that first-principles fact, but selected-bin preflight compared the required
bin-id string with the full `(bin_id, payoff)` tuple collection. That comparison
can never match, so the selected dead bin fell through to the unrelated
remaining-day probability path. The repair compares like types: required bin id
against the set of deterministic bin ids. Still-live bins continue to require a
current remaining-day witness; source, observation, topology, and submit checks
are unchanged. Acceptance requires the selected dead-bin witness to avoid the
remaining-day reader, a still-live sibling to keep using it, symmetric HIGH/LOW
hard-fact tests, the full W3 seam, compilation, and `git diff --check`.
