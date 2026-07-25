# finite_evidence_probability_symmetry -- Plan

Date: 2026-07-11
Branch: `live` (was `p2-pending-exit-restart-redecision`; renamed at main→live cutover)
Status: active

## 2026-07-25 Stale ENS disagreement remains probability uncertainty

The live Dallas Jul-26 102-103°F certificate paired a fresh 06Z carrier center
of 36.934337°C with a 00Z ENS mean of 39.067265°C. Anomaly transport shifted
the 6h-old member sample onto the fresh center and set the operational
2.132928°C disagreement to zero. Predictive sigma collapsed from the
same-evidence absolute-disagreement value 2.251251°C to 0.720244°C:
`q_yes(102-103°F)` moved from 0.120401 to 0.009900 and its upper bound from
0.717837 to 0.155766. The resulting `q_no=0.990100` admitted a live BUY NO
6.5 @ 0.82 that the complete current-evidence ambiguity would reject robustly.

The correction retires synthetic anomaly translation from finite evidence.
Raw absolute members and their ENS/provider-center displacement remain the
independent `delta_ens²` term in predictive and center uncertainty. Bounded
stale-shape certificates advance to
`stale_ensemble_absolute_disagreement_v1`, forcing the existing materialization
loop to replace v3 rows before entry or held-position belief can consume them.
The behavioral antibody reproduces the live 2.132928°C disagreement and
requires raw member semantics, full sigma, and full center uncertainty.

Post-restart evidence exposed a second reader-side bypass. `position_belief`
correctly rejected an Ankara Jul-27 posterior carrying
`ensemble_anomaly_transport_v3`, but the global-auction bundle reader called
that same row live-grade because it checked only non-null bounds and
`FUSED_NORMAL_*`. The held auction therefore kept evaluating Ankara with the
frozen `q_no=0.985060` even after `last_monitor_prob_is_fresh=0`. Live-grade
bundle selection now also requires the shared tradeable q-bound basis and the
current-evidence semantics revision. A focused antibody rejects the old
transport row and accepts the replacement stale-absolute-disagreement row.

## 2026-07-24 BUY-only preflight fallthrough

The current global auction compares new BUYs and held-position SELLs under one
posterior-mean expected-log-growth objective.  A BUY winner can fail submit-time
entry readiness while an independently feasible reduce-only SELL remains in the
same frozen auction epoch.  The live adapter already routes SELL before every
entry-only gate, but preflight classified
`LIVE_ENTRY_BLOCKED:entry_readiness:*` as an unknown batch-wide failure.  That
stopped the complete cut instead of re-auctioning, so unresolved entry outbox
or cap reservations could starve a profitable exit.

The preflight now excludes that exact BUY candidate and re-runs the immutable
current auction.  Other unknown, stale, superseded, or internally inconsistent
authority failures remain fail-closed for the complete cut.  The behavioral
antibody proves that an entry-readiness-blocked BUY falls through to a sibling
SELL without rebuilding probability, book, or wealth truth and with exactly
one venue submission.

Money path: current entry authority + held-position exit authority -> global
capital auction -> submit-time preflight -> venue action.  Probability,
lifecycle, sizing, price-band, and settlement semantics are unchanged.

## 2026-07-24 Post-trade boot identity ordering

The official live restart correctly refused to start the order daemon until
every prerequisite sidecar proved the expected loaded SHA from a fresh
process-owned heartbeat. The post-trade sidecar registered heartbeat,
harvester, and collateral jobs with the same immediate `next_run_time`, then
entered `BlockingScheduler`. Cold network/capital work occupied the scheduler
workers first, so the first new heartbeat arrived several minutes after the
90-second deploy identity window. Recovery had succeeded, but the fail-closed
deployment left live trading stopped.

The sidecar now writes one synchronous boot heartbeat only after sanctioned DB
preflight and the complete cascade-liveness poller contract pass, but before
the scheduler can run any immediate job. The existing periodic heartbeat
remains the ongoing liveness signal. This preserves the proof boundary: the
running sidecar itself publishes its immutable boot SHA; deploy does not
fabricate identity, extend a timeout around unbounded work, or start the order
daemon without prerequisite proof.

Money path: runtime code identity -> restart admission -> continuous
re-decision/exit availability. No probability, entry, sizing, lifecycle, or
capital-job semantics change. Rollback restores the prior safe deployment
block.

## 2026-07-24 Restart fill-identity convergence

The official live restart stopped the complete mesh when restart-preflight
found a confirmed six-share entry command whose original short position id had
no projection. Chain reconciliation had already restored the same six shares
onto an earlier zero-fill position row for the exact token, so naïvely opening
the command's row would create parallel exposure. The command's append-only
trade journal also contained two derived EDLI aliases of one venue trade;
projection recovery aggregated canonical trade ids instead of economic trade
ids and inflated six shares to eighteen.

Recovery now applies the existing economic-fill identity reducer before any
entry aggregation. A command may relink to a different-order chain-observed
row only when one unambiguous same-token, same-condition row has synchronized
quantity and chain cost equal to the complete command-deduped fill, has no
prior command-bound fill event or execution fact, and the command is terminal
FILLED. Any command-bound execution fact still owned by the orphan id is
rehomed in the same repair transaction and keeps one canonical intent identity.
The active-row projection then persists or reuses that command-level execution
provenance before rebuilding the aggregate; the aggregate and synchronized
chain quantity must converge to the same shares, so the fill is attributed
without adding exposure a second time. Restart-preflight runs this narrow link
repair before fill projection. The behavioral antibody reproduces the exact
zero-fill-row -> chain rescue -> later confirmed-fill shape, including an
orphan execution fact and recursive EDLI aliases, and requires one six-share
position, one execution fact, and idempotent replay.

Money path: venue fill truth -> command provenance -> canonical position
identity -> current exposure -> restart admission. No probability, sizing,
entry, or exit threshold changes. Rollback is a single hot-fix revert; because
the preflight remains fail-closed, rollback returns to a safe restart block
rather than fabricating or duplicating exposure.

## 2026-07-24 Atomic chain-reappearance economics

The complete-position pagination repair made a previously omitted Seoul
holding visible again and exposed a second canonical-truth defect. The mirror
reappearance fold restored `chain_shares` and `chain_seen_at`, but discarded
the Data API's `avg_price` / `cost` and left `chain_state` at its stale
pre-observation value. Runtime exposure authority then correctly rejected the
torn state (`chain_shares > 0` with zero chain cost basis), which blocked the
global capital auction including held-position SELLs.

The repair keeps acquisition/fill provenance fields owned by their existing
authority and atomically projects only the complete positive chain
observation: attributed chain shares, chain average price, chain cost basis,
`chain_state=synced`, and observation time in the same append-plus-projection
transaction. A behavioral antibody reproduces the exact
absence-marker -> positive reappearance transition and requires phase and
owned fill economics to remain unchanged. Because an older correction may
already have consumed the absence marker while leaving a torn row, the same
fold is also re-emitted whenever a complete positive observation finds stale
visibility or missing chain economics; recovery does not depend on historical
event ordering. No schema or lifecycle grammar changes. Rollback is a single
hot-fix revert; the next complete reconciliation re-emits the prior event
shape.

## 2026-07-24 Complete venue-position enumeration

The live loss audit found a confirmed 5-share Seoul fill whose exact Polygon
CTF `balanceOf` remained positive while both chain reconciliation and the
global held-position SELL auction treated it as absent. Polymarket's Data API
documents `/positions` with a default `limit=100`; both Zeus enumeration
callers set `sizeThreshold` but omitted `limit` and `offset`. The funded wallet
currently has 117 returned positions, so the default page silently omitted the
held token and prevented an otherwise executable statistical exit.

Both Data API enumeration paths now request the maximum documented page size
and continue by offset until a short page proves completion. Repeated assets
are deduplicated, and reaching the documented offset ceiling without a short
page fails closed instead of treating a prefix as chain absence. This repairs
`Chain/CLOB truth -> reconciliation -> current wealth -> global SELL auction`;
it changes neither probability authority nor exit economics and adds no
per-token RPC fan-out.

## 2026-07-24 Venue-outcome probability feedback

The July 22–24 loss reconstruction found a control-loop split: Gamma had
already resolved the held token and the canonical SETTLED event carried the
frozen entry q, binary payoff, and exact entry q-version, but RiskGuard required
the later physical temperature publication before scoring Brier. The latest
fifty economically resolved positions therefore remained invisible while the
probability system continued to report GREEN.

Venue resolution now makes only the held-token probability outcome
learning-ready. It does not become physical temperature or calibration truth:
`probability_outcome_ready` and `metric_ready` are separate facts. RiskGuard can
localize a degraded recorded probability mechanism from immediate zero-sum
payoff evidence even when each consumer strategy is individually below the
minimum sample floor. The live July 24 sample identifies
`decision_snapshot:metar_fast` rather than pooling it with the independent
forecast qkernel; durable gates therefore stop only the mechanism's actual
strategy consumers while leaving healthy current-evidence forecast alpha and
settlement semantics unchanged.

Strategy gates compose monotonically across independent control and RiskGuard
authorities: if any active row gates a strategy, a separate active enable row
cannot erase that protection by winning lookup order. Operators can still
re-enable a strategy by expiring the gate whose evidence has been reviewed;
an unrelated enable override is not a substitute for retiring current risk
evidence.

## 2026-07-24 Day0 continuous-state residual conditioning

The July 22–24 loss reconstruction falsified the diagnostic-only treatment
below. Across 27 Data-API loss/near-zero positions, no executable SELL command
was emitted before the bid collapsed; for the subset with a still-positive
book and `q < bid`, the decision layer selected HOLD. The common probability
failure was a discontinuity: the current station/model residual was recorded
but discarded, so the engine combined an observed running extreme with
unconditioned future hourly paths and moved toward a point mass. When the next
observation crossed the adjacent bin, q flipped after executable liquidity had
already disappeared.

The replacement law is a causal short-memory state update, shared by new-entry
and held-position probability paths. For each current model path, the latest
elapsed hourly model anchor gives residual `e0 = observed - model`. An unseen
hour at lead `h` is shifted by `e0 * exp(-h / tau)`; elapsed hours stay
unchanged, and the anchor becomes the observation for the terminal sub-hour
fallback. This preserves real future excursions and converges back to the raw
forecast rather than treating one station print as a permanent model
translation.

`tau=4.2h` is runtime config, not a per-order patch. It was fit only on the
available prior seven-day window: training dates 2026-07-21..23 produced
`tau=4.17h` from 1/2/3/4/6h residual persistence. Frozen 4.2h validation on
2026-07-24..25 improved mean absolute error at every tested horizon:
0.584/0.390/0.265/0.181/0.074C respectively. The decision certificate now
persists the conditioning revision, model residuals, and tau so cache/replay
identity cannot silently mix the old and new probability meanings.

Money path: source truth -> forecast signal -> continuous Day0 probability ->
global auction -> Kelly -> entry/held redecision. Re-decision repeats the same
operator on every fresher observation; no market price is used as probability
authority.

The same live audit found a separate constructor-contract regression affecting
12 of 14 open July 25 positions: the Day0 builder still passed retired
`bias_corrected` state into `MarketAnalysis`, so every refresh raised
`TypeError` and the monitor accumulated 12–21 stale-belief cycles under
`EVIDENCE_UNAVAILABLE`. The obsolete argument and its dead local assignments
are removed. A signature relationship antibody now requires every keyword at
that live constructor seam to exist in the current runtime contract. This is a
belief-availability repair, not a relaxation of freshness or source gates.

## 2026-07-24 HKO trajectory truth-domain correction

The July 24 Hong Kong LOW loss exposed a silent same-table-name split across
canonical DBs. The hourly daily-observation job runs with forecasts.db as MAIN
and world.db attached. Its unqualified HKO accumulator and publication-ledger
writes therefore updated forecasts.db, while the source-clock projector and
Day0 current-temperature reader consumed world.db. Forecasts held 16 current
July 24 readings through 12:00Z; world held no July 24 accumulator rows and its
spot ledger stopped at 05:02Z even as official cumulative extrema remained
fresh. No SQL error or source-health failure surfaced because both DBs contained
an HKO accumulator table.

The runtime writer now names the `world` schema explicitly for the accumulator,
spot-print ledger, and next-day realtime finalization read. The forecasts/main
path remains only for isolated legacy/test callers; production routing binds to
the registry-owned world truth domain. An antibody creates same-name tables in
both DBs and proves that a new HKO reading and its trajectory print land only in
world. This repairs source truth -> Day0 trajectory evidence; it does not use
market price as probability authority or change settlement extrema semantics.

The follow-on live audit found one remaining time-domain split after DB routing
was repaired. The Hong Kong July 25 posterior had advanced to the 23:10Z HKO
snapshot while the latest `DAY0_EXTREME_UPDATED` event remained at 22:10Z
because the catch-up scanner deduplicated HKO solely by displayed high/low.
HKO intraday extrema are provisional snapshots, not WU-style monotone bounds:
an equal value at a newer source time shortens the remaining physical window
and must refresh both HIGH and LOW probability families. HKO event watermarks
therefore bind `(value, observation_time)` per metric; an advanced source time
emits once, while rescanning the same version remains silent. The WU monotone
extreme firehose gate is unchanged.

## 2026-07-24 Interim Day0 trajectory correction (superseded)

Current live Ankara 28C evidence falsified one probability assumption before
Kelly or execution. The remaining-path builder transported each model's
instantaneous predawn error unchanged through every future hour, moving the
three current extrema from 28.2/25.6/26.0C to 25.4/24.6/24.9C. No current
temporal covariance witness authorized that permanent translation, yet it
helped produce a 98.67% NO lower bound and a live 16-share maker order.

The clock hypothesis was separately rejected: a later row carrying the same
running extreme proves the no-new-extreme coverage frontier through that later
observation time. Rebinding it to the first occurrence would discard valid
plateau information and contradict continuous-time redecision. That path is
unchanged.

At this interim stage, current temperature cut the remaining window and
recorded model innovation without propagating it into unseen hours. The
seven-day walk-forward witness in the preceding section subsequently supplied
the missing temporal law and superseded this diagnostic-only behavior.

Money path: forecast signal -> Day0 current-evidence probability -> global
auction -> Kelly -> maker lifecycle. Behavioral antibodies cover exclusion of
elapsed model points and the absence of unvalidated permanent state transport.

## 2026-07-23 Day0 raw-report freshness clock correction

Live Miami evidence separated two clocks that the hourly observation schema
intentionally stores: `utc_timestamp` is the stable UTC-hour bucket identity,
while `hour_*_raw_ts` is the source report time. Day0 readers and the event
catch-up scanner used the bucket identity as the freshness clock. A 01:53Z WU
report possessed at 02:15Z was therefore treated as a 01:00Z observation and
declared stale against a 60-minute budget, suppressing current Day0 authority
while the source stream was healthy.

The hot-fix preserves bucket identity, extrema aggregation, source routing, and
causality gates. Hourly clients now retain the latest raw report in each
bucket; the typed writer rejects possession timestamps earlier than that fact;
and both direct readers and event catch-up use that raw source time, with the
existing max/min raw timestamps as a backward-compatible fallback. HKO's
cumulative snapshot clock remains its canonical `utc_timestamp`. Eligibility
now requires bucket identity, source fact time, and possession time all to be no
later than the decision; gap analysis and latest-context selection use the same
fact clock. This restores causal held-position redecision without letting quote
time or market price enter probability authority.

## 2026-07-23 continuous-time plateau redecision correction

The loss-to-zero reconstruction found that the fast Day0 source clock emitted a
new `DAY0_EXTREME_UPDATED` event only when the rounded running extreme moved.
An unchanged HIGH after the peak, or unchanged LOW after the overnight trough,
was treated as no new information even when the settlement-station observation
version advanced. That froze the conditioned probability surface precisely when
elapsed time and a shrinking remaining window should continuously remove paths
to a different settlement bin. The replacement materializer already treats a
strictly newer observation version on a plateau as new information; the event
producer discarded that version before it could reach the materializer.

The hot-fix extends the existing split event memo with a monotone source-time
version. Rounded extremes remain absorbing and may only move in their physical
direction; observation versions may only move strictly forward. Either change
emits one event, while a repeated source version remains idempotent. This keeps
the source-report cadence (normally 30/60 minutes) and does not restore the
per-scan event firehose that the rounded-extreme gate removed.

Money path: source truth -> Day0 conditioned probability -> held-position
redecision -> exit. Re-decision behavior: plateau evidence now refreshes q(t)
using the shorter remaining window; no market-price anchor or price-only stop is
introduced. The 72-hour causal replay rejected a generic trailing stop because
its apparent gain depended on one large outlier and it sold multiple eventual
winners; this change repairs the missing fact instead of optimizing that
loss-only proxy.

The London 26°C NO incident then exposed a second, independent category error.
At 19:55–22:55 UTC every current remaining-path member was below the already
observed 26°C high, yet live q for the held NO repeatedly rose toward 0.28. The
old calculation evaluated `noise(max(observed_high, future_high))`, so fixed
instrument noise was applied to the already observed settlement boundary and
manufactured a 27°C tail even as current temperature fell to 21°C. Physical law
is `max(observed_high, noisy_future_high)` (and symmetrically `min` for LOW).
The corrected Day0-only operator preserves real future excursions, removes only
the impossible post-boundary noise, and persists its operator identity in the
probability receipt. A same-station fast print may advance the physical clock
and trajectory state, but it cannot replace the settlement-channel value used
for deterministic payoffs. Trajectory state is causal on both source publish
time and local fetch time; Fahrenheit cities require and parse the METAR
tenths-Celsius T-group instead of converting a rounded whole-C ledger value.
Those rules live at the central Day0 fact reduction as well as the trajectory
reader, so global redecision and remaining-path conditioning cannot construct
different information sets from the same observation ledger.

Live resampling exposed a third independent continuity defect: the Day0 hourly
refresh cursor advanced only when an HTTP attempt occurred and took modulo the
already-truncated three-city microbatch. With twelve held cities, throttle made
the cursor repeatedly offer the same first page while later holdings exceeded
the three-hour probability freshness window. The corrected cursor advances by
held slots offered, including throttled/failed slots, and takes modulo the full
held-capital segment. This preserves the bounded HTTP budget while guaranteeing
coverage; it increases fairness, not request volume.

## 2026-07-23 Day0 expected-value and maturity composition

The Mexico City 26C NO receipt isolated a downstream category error after its
fresh held probability fell to 3.73% and its executable bid still paid 20.7c.
Its entry and current confidence intervals were disjoint, yet the exit evaluator
priced HOLD with the current 37.33% upper confidence bound. A confidence bound
is evidence for or against reversal, not the expected terminal payoff. Reusing
it as payoff blocked thirteen of fourteen executable adverse cuts and stranded
the leg below the venue's legal exit band.

The 72-hour Ankara 31C YES winner refutes an unconditional point-q liquidation:
an early-day disjoint reversal at q=10% and bid=42c later settled in the held
side's favor. The missing discriminator is causal time. The Day0 remaining-day
builder already emits whether the observed extreme is mature enough to sponsor
a statistical exit, but the current-global materializer dropped that evidence
before family redecision.

The correction composes the existing authorities instead of adding a price
stop. CI separation remains the confidence gate; current point-q supplies the
Day0 expected HOLD payoff; current-global refresh carries the temporal maturity
reason; and family redecision blocks an otherwise valid statistical exit until
that reason clears. Non-Day0 and near-settlement UCB comparisons are unchanged.
A low-priced claim with high fresh expected value remains held, while a mature
reversal is monetized before its bid becomes unexecutable.

The downstream global auction exposed a second category error: it independently
re-scored every held SELL with lower-CVaR parameter draws and could veto the
local fixed-action expected-value decision. The 72-hour counterfactual set
contains both sides of the discriminator: Mexico City was a mature mean-positive
sale stranded by the tail objective, while Ankara and current Cape Town were
early mean-positive reversals that maturity must exclude. Outcome knowledge is
not an input to either decision.

The global correction therefore makes three different statements in three
different types. Temporal authority determines whether a Day0 statistical SELL
exists in the feasible set and is bound to the exact probability witness.
Action economics determine size and safe FAK prefixes: mature Day0 SELL uses
posterior-mean expected log wealth and EV in `expected_*`, while BUY keeps robust
admission/sizing. Finally, every admitted fixed proposal receives the same
posterior-mean expected-log-growth comparison for cross-action ranking. Mean
numbers are never written into `robust_*`; selection-time maturity is rebuilt at
submit before any venue call. Receipt schema 17 / candidate v11 / holding v2
make those distinctions auditable.

## 2026-07-23 loss-to-zero causality correction

The 72-hour loss census and decision-time reconstruction found three defects that
sit upstream of any exit-price floor: `src.main` remained launchd-running while
its recurring monitor and daemon heartbeat stopped for roughly 9h45m, and Day0
`buy_no` was classified as `settlement_capture` from direction alone even when
the observed extreme had not crossed the selected finite bin. In the same stall,
WU/OGIMET and the HKO fallback fetch path captured possession timestamps before
their network requests; when execution resumed, later source facts were persisted
as if held hours earlier. The first froze q(t) while the book moved; the second
gave forecast-dependent positions the wrong strategy identity, alpha clock,
policy cohort, and attribution; the third violated point-in-time causality.

This hot-fix extends the existing venue-heartbeat watchdog, under its existing
deploy restart lock and fresh-sidecar prerequisites, to restart a running but
heartbeat-stale live daemon. It also introduces one selected-payoff truth
contract for HIGH/LOW x YES/NO: only a side physically locked by the monotone
observed extreme is `settlement_capture`; unresolved or unavailable truth is
`day0_nowcast_entry`. Command recovery defaults missing legacy truth to nowcast.

Authority surfaces touched: `architecture/strategy_profile_registry.yaml` and
`architecture/source_rationale.yaml`. This harmonizes the registry's existing
"observation itself" versus "forecast-upside" theses; it supersedes the
direction-only aliases in evaluator/event/command-recovery code. INV-05, INV-06,
INV-41, and INV-43 remain binding: risk policy still actuates, point-in-time
truth is preserved, selected-side evidence remains mandatory, and no live price
band is weakened.

The observation repair records possession only after each fetch returns and adds
a typed-writer boundary that rejects any `imported_at` earlier than the hourly
bucket or its raw extrema print. Historical false timestamps remain evidence to
reconstruct or quarantine through sanctioned learning paths; this hot-fix does
not rewrite canonical history out of band.

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
global entry. Local midnight is not physical evidence: when canonical truth
positively proves that the target day still has zero observations, a held
position keeps the fresh replacement posterior until the first causal Day0
observation arrives. Generic observation faults do not prove an empty prefix;
they retain the bounded grace and then fail closed. Entry authority remains
grace-limited, and a stale replacement posterior is never promoted.

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
- Re-materialize every current active family when the current-evidence
  semantics revision changes; entry and monitor readers must not consume an
  older shaped certificate during that convergence window.
- Remove the WORLD/TRADE writer-order inversion that prevents the corrected
  probability carrier from reaching live redecision.
- Make held-position probability refresh order explicit: release TRADE, write
  the current Day0 WORLD fact, then write TRADE quote/monitor evidence.
- Preserve probability continuity before the first target-day observation:
  a typed canonical zero-observation result keeps a fresh replacement posterior
  as held-position monitor authority even after the coverage grace; this is not
  permission to add risk, use stale forecast belief, treat a generic source
  failure as zero observations, or ignore any available Day0 observation.
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
- A deterministic local-midnight antibody proves fresh replacement belief stays
  continuous for held-position redecision while canonical truth proves zero
  observations; entry remains grace-limited, and stale belief or a generic
  post-grace observation failure is still rejected.
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

## 2026-07-19 typed chain-only automatic resolution

Packet class: schema/truth-contract slice.  This remains inside the active
capital-gains packet because a stale family-scoped `CHAIN_ONLY_UNKNOWN_ASSET`
review debt still blocks an otherwise current held family after fresh chain
reconciliation proves the exact local token and size match.  The money path is
`fresh chain snapshot -> canonical reconciliation -> review/suppression state ->
portfolio and exchange drift consumers -> global redecision`.

Objective: represent the fresh exact-match transition as append-only canonical
truth without granting a permanent token ignore or hiding a future chain/local
drift.  This changes the `token_suppression` reason CHECK vocabulary and its
consumer classifications; it does not add a lifecycle phase, venue command,
probability rule, capital gate, or operator override.

Why not the smaller alternatives:

- query-time hiding cannot prove fresh chain truth and leaves the OPEN review
  item as an independent family block;
- resolving only the review item leaves the latest suppression row classified
  as external and causes exchange reconciliation to swallow future drift;
- `operator_quarantine_clear` would forge human provenance and permanently
  suppress future chain-only rediscovery.

Truth layer: `token_suppression_history` plus typed `ReviewWorkItem` state in
the canonical trade DB.  Control layer: only a complete/fresh `CHAIN_SYNCED`
exact held-token match may atomically append
`chain_only_auto_resolved_match`, resolve only OPEN
`CHAIN_ONLY_UNKNOWN_ASSET` debt for that token. The savepoint mutates canonical
DB state only. The current cycle conservatively retains its old in-memory
`ChainOnlyFact`/ignore projection; only a later canonical reload after the outer
transaction commits may remove it. Evidence layer: schema parity plus counterexamples
where the local position later disappears or drifts and therefore must be
reported again.

Zones and invariants: K0 schema/truth vocabulary and K2 reconciliation;
INV-03 append-first authority, INV-08 one transaction boundary, INV-09 missing
chain truth as a first-class fact, and INV-37 single-DB transaction discipline.
Required reads are root/scoped AGENTS, the K0 zero-context authority spine,
`docs/authority/zeus_current_architecture.md`,
`docs/authority/zeus_current_delivery.md`,
`docs/authority/zeus_change_control_constitution.md`, and the state/contracts/
execution module books.

Allowed implementation surfaces are the exact state, contract, execution,
migration, kernel-schema, registry, packet, and focused test files enumerated in
this packet's `scope.yaml`.  Direct/manual writes, copies, or backups of any
canonical DB are forbidden.  No probability, Kelly, sizing, submit, risk,
control, lifecycle phase, or venue-order surface may change.

Schema contract:

- accepted reason vocabulary adds `chain_only_auto_resolved_match`;
- review-resolved, non-resurrectable-ignore, and external-drift-suppression
  reason sets are distinct; the automatic reason belongs only to the first;
- fresh-kernel, legacy mutable-table, and B071 history/view schemas migrate
  transactionally and idempotently while preserving history ids, operations,
  timestamps, views, triggers, and indexes;
- exact proof requires non-empty and equal chain, local-position, and suppression
  condition identities in addition to exact aggregate shares;
- repeated exact matches do not append duplicate history; any failure or caller
  rollback restores both DB facts, while in-memory state remains conservatively
  unchanged until a committed canonical reload.

Acceptance requires focused migration/reconciliation/exchange tests, full
affected test files, `py_compile`, `git diff --check`, planning-lock evidence,
and independent adversarial review.  Parity is schema-row and consumer-behavior
parity rather than market replay because probability and execution economics do
not change. A deployment is the human-gated migration cutover: only the standard
`scripts/deploy_live.py restart all --allow-unpushed` path may apply it. That
path unloads live trading and every prerequisite before starting any new
live-money process. Its stopped-process restart recovery calls the typed trade
schema helper, which first runs `init_schema_trade_only` and then widens the
backward-compatible CHECK inside a dedicated transaction before any daemon is
restarted. A failed migration aborts recovery and leaves live trading stopped.
Only after that does deploy verify prerequisite code identity and start the trade
daemon, so the new reason cannot be emitted against the old three-value CHECK.
The deploy path must then prove loaded SHA, first queue/monitor progress and only
then restore its own temporary restart guard. No out-of-band migration command
or canonical DB write is allowed. Post-start evidence must include canonical
schema/row, chain, review-gate, auction, monitor, venue, and capital facts.
Rollback is the entire slice together;
if an automatic-match row already exists, rollback must first map it
fail-closed to `chain_only_quarantined` rather than loading it under an older
three-value schema.

Pre-deploy verification: reconciliation/review/exchange suites passed 169
tests; fresh/legacy/B071 schema tests passed 13; the full ops-script smoke file
passed 78; money-path semantic CI passed 10. Mutable-table and B071 alias-view
trade DB fixtures both pass the stopped deploy recovery helper, including
history identity/metadata and view/trigger/index preservation. Schema
fingerprint, source-rationale delta, planning lock, YAML load, compilation, and
diff whitespace checks pass. Independent adversarial re-review is PASS. The
broader architecture/hygiene baseline retains two unrelated failures: a
wall-clock-aged reconciliation fixture and the existing TIGGE AST metric-stamp
fixture; neither intersects this slice.

## 2026-07-19 deterministic Day0 authority continuity

Fresh complete-auction receipts selected a positive Hong Kong July 20 HIGH
29C NO action from a `DeterministicBinPayoffWitness`: the authorized current
observation had already made the exact 29C YES payoff zero, so the selected NO
payoff and both selected probability bounds were one.  No venue command was
created because the certificate bridge relabeled every global Day0 probability
as remaining-window probability and the shared Day0 verifier recognized only
replacement and remaining-window q sources.

This is a typed-authority continuity defect, not permission to weaken live
admission.  The repair must preserve the deterministic witness identity, exact
YES-payoff map, current observation binding, and selected condition/bin/side/q
through receipt projection, calibration certification, actionable validation,
and pre-submit validation.  It may admit only binary exact payoffs whose
selected YES/NO complement equals both `q_live` and `q_lcb`; missing, mixed,
nonbinary, mismatched, or relabeled evidence remains fail-closed.  Ordinary
remaining-window candidates continue to require current remaining models and
their existing transform.  No price threshold, Kelly multiplier, risk level,
operator control, venue command, or canonical DB state changes in this slice.

Allowed additional implementation surfaces are
`src/events/day0_authority.py`, `src/decision_kernel/verifier.py`,
`tests/engine/test_event_reactor_live_qkernel_gate.py`, and
`tests/engine/test_cert_calibration_bridge.py`, plus the existing shared
calibration-authority predicate antibody, as enumerated in the packet
scope. Acceptance requires producer-to-actionable and calibration-certificate
positive tests, missing/mixed/payoff-side/q-drift counterexamples, existing
remaining-window regressions, compilation, diff checks, independent
adversarial verification, one conventional commit, standard live restart, and
fresh natural auction/venue reconciliation.  A model EV is not realized PnL;
post-deploy reporting must keep selected economics, venue command/order/fill,
and settlement evidence separate.

Independent review first refuted the initial bridge: synchronized payoff/side/q
edits could retain an opaque stale witness identity, and a nested observation
could claim a different q source.  The corrected certificate now carries the
complete ordered bin-to-condition-to-YES/NO-token bindings and every input of
the canonical deterministic witness identity.  Pre-submit reconstructs that
witness, recomputes the exact-payoff sample identity, matches the selected
native token to bin and side, and compares every present q-source copy.  The
production-shaped fixture uses Hong Kong's actual `HKO`/`hko` settlement source.

Focused engine, calibration bridge, shared verifier, certificate, solver,
monitor, and symmetry suites pass `436/436`; the full W3 integration file passes
`221` tests and retains two unrelated pre-existing `epoch_superseded()` fixture
failures.  Compilation, YAML parse, changed-test topology filtering, and diff
whitespace checks pass.  The environment has no Ruff executable; Pyflakes still
reports the adapter's pre-existing dynamic/type-only names, with no new finding
in the deterministic authority implementation.  Independent re-review passed
the Day0 slice: both original attacks are rejected, `202` focused authority
tests and `6` key integration tests pass.  Its overall release verdict remains
red only because the same two unrelated W3 `epoch_superseded()` baseline
fixtures fail outside this diff; that residual is reported separately rather
than treated as evidence against this authority chain.

## 2026-07-19 Day0 held-SELL family completeness

Fresh production receipts falsified the monitor-to-auction handoff for three
Day0 positions.  Tokyo 35C NO and Kuala Lumpur 29C YES / 32C NO were refreshed
by the held monitor, yet each appeared zero times in hundreds of applicable
global-auction receipts.  The probability preparer had replaced the complete
conditional family simplex with a partial deterministic witness as soon as any
sibling bin became pathwise dead.  Book capture therefore saw only those dead
siblings and could not materialize the unresolved held legs as SELL candidates.

Whole-family preparation must retain a coherent remaining-day joint witness
whenever unresolved siblings exist.  A partial deterministic witness is valid
only for an explicitly required exact condition, and JIT revalidation must
preserve the selected witness kind rather than silently switching authority.
Every hard-fact payoff carried inside the joint witness must remain exactly
zero or one in both its sample column and point probability; conflict fails
closed.  This changes neither Kelly, price policy, operator control, risk level,
nor venue submission.  Acceptance requires a mixed exact/unresolved Day0 family
test, joint-witness JIT continuity, hard-fact conflict rejection, standard live
restart, and new receipts proving each affected position is represented as a
SELL evaluation or an explicit typed exclusion.  A positive SELL may execute
only through the ordinary robust objective and reduce-only pre-submit path.

## 2026-07-19 Fractional Kelly minimum-lot boundary

The minimum marketable lot does not authorize added risk above the configured
Fractional Kelly terminal-holding target. A positive continuous solution whose
remaining fractional target is smaller than the venue minimum therefore
chooses CASH with
`FRACTIONAL_KELLY_TARGET_BELOW_MINIMUM_LOT`; it must not round the order up to
the venue lot or substitute full Kelly. This is the direct correction for the
live minimum-lot entries that converted a small fractional target into a larger
binary loss budget. A venue minimum is an execution constraint, not an alpha
source or a risk exception.

## 2026-07-19 narrow-wake auction evidence continuity

Targeted producer wakes were replacing the process-global book cache with only
their narrow family scope.  A later disjoint wake therefore had to rebuild the
same broad venue universe and could omit otherwise fresh families from the next
bounded decision window.  The cache now atomically replaces the refreshed
family while retaining other current families, but it never renews their
freshness: the merged epoch expires at the earliest base or delta deadline, and
an already-expired delta is rejected before merge.  Replaced family topology
also removes its old Gamma metadata keys before current keys are installed.

The deterministic Day0 proof bundle now binds candidate evidence to the
selected native proof token rather than an optional family-candidate token.
Unknown proof-builder exceptions remain batch-fatal before any venue side
effect; they are not reclassified as family-local evidence and cannot authorize
a runner-up.  Focused cache, token, neg-risk, and preflight tests pass `44`; the
full W3 file passes `229` and retains only the two unrelated pre-existing
`epoch_superseded()` fixture failures.  Compilation and diff whitespace checks
pass.  Standard deployment and fresh natural auction/venue reconciliation are
still required; this continuity repair neither changes operator entry posture
nor authorizes a forced order.

## 2026-07-19 transient collateral refresh continuity

Canonical trade-ledger evidence showed intermittent `DEGRADED` refresh rows
between successful `CHAIN` snapshots.  A degraded row proves that refresh
failed; it does not prove zero collateral.  Global wealth selection therefore
uses the newest `CHAIN` or `VENUE` snapshot only while the auction's existing
freshness bound still accepts its own capture time.  Current reservations,
in-flight obligations, and portfolio claims are reread inside the same pinned
transaction, so cached cash cannot escape newer commitments.  No trusted
snapshot, a future capture, or an expired trusted snapshot remains fail-closed.

Live evidence at review time contained `94 CHAIN / 6 DEGRADED` rows in the most
recent 100 collateral refreshes.  Collateral-ledger suites pass `76`, focused
wealth tests pass `20`, executor collateral tests pass `2`, and the full W3 file
passes `229` with only its two known `epoch_superseded()` baseline failures.
Deployment and natural receipt evidence remain required.

## 2026-07-19 Day0 coverage truth and close-economics integrity

Current Day0 evidence can be numerous yet discontinuous. A gap overlapping the
metric's likely extreme window therefore cannot be called complete merely from
row count: entry must fail closed for the affected metric, while monitoring may
retain the one-sided physical bound only as non-actionable evidence until the
missing interval is bounded. An observed HIGH never moves down and an observed
LOW never moves up. HIGH and LOW attribution remains separate, HKO cumulative
observations use their own trailing-coverage semantics, and the canonical DB,
WU HTTP, and same-station fast-tail paths all derive continuity from exact
sample instants rather than first-sample time plus count. This changes neither
the settlement source nor the executable objective.

The same release set includes a truth-preservation correction at settlement.
When a real exit fill has already made a position `economically_closed`, a
later chain-mirror settlement may advance lifecycle but must preserve the
booked fill price and realized PnL. If either booked field is missing, the row
fails closed and remains economically closed for explicit recovery; the writer
must not manufacture zero PnL or a binary exit price.

Acceptance requires metric-specific coverage and monitor-bound tests, complete
and incomplete HKO cases, fresh/stale bound monotonicity, booked-close
preservation plus missing-field counterexamples, test-topology registration,
the full W3 seam, independent review, standard deployment, and fresh natural
auction/venue evidence. No forced order is authorized.

## 2026-07-19 typed HTTP retry and persistent negative cache

Current ingest evidence showed deterministic Open-Meteo HTTP 400 responses
being flattened into retryable transport failures, leaving the source cursor
deferred and repeating the same physical request on every scheduler poll. The
client now classifies the actual HTTP response before retry: an explicit
`run_not_published`/availability 400 remains conditional, ordinary 400 and
deterministic client statuses are terminal for the exact request identity,
408/425/5xx remain bounded retries, and 429 follows its `Retry-After` embargo.
Only redacted status, retry class, reason, body hash, and retry time persist in
the existing shared quota state; URL/query/body content is never stored.

BPF carries that typed result through its fail-soft report and the source-clock
cursor consumes the type instead of reparsing a generic transport string. An
exact terminal request is therefore suppressed across scheduler polls, while a
new source-run identity remains independently eligible. Acceptance requires
generic/conditional 400, 429, 5xx, cross-poll suppression, quota budget, source
health, compilation, lint, planning-lock, standard deployment, and new live
request receipts showing the repeated-400 amplification is gone. The direct
Open-Meteo metadata probe remains a separately named unification gap; this
slice does not invent a new canonical DB schema or switch provider semantics.

## 2026-07-19 external heartbeat truth continuity

The live venue keeper can be current and `HEALTHY` while an independently
constructed process reader still has a cold in-process singleton. Treating
that wiring state as `LOST` invents venue failure and sends the allocator into
false reduce-only. In external mode the first runtime read therefore binds an
`ExternalHeartbeatSupervisor` to the current keeper status atomically. Status
now distinguishes `UNCONFIGURED` from genuine `LOST` and carries its source,
reason, write time, and observed age so an operator projection cannot erase
the evidence boundary.

A fresh external snapshot permits ordinary decision processing. Missing,
unreadable, expired, internal-unconfigured, and still-starting states continue
to fail closed for new risk. Those states retain only immediate FOK/FAK order
types so a separately authorized held-position reduction is not disabled by
entry liveness. Acceptance requires cold-singleton/fresh-external,
expired-external, internal-unconfigured, entry-denial, and held-exit antibody
tests, compilation, lint, diff checks, independent live-money review, standard
deployment, and post-restart comparison of keeper truth with allocator and
execution-capability projections.

## 2026-07-19 live-order capital and projection correction

Six natural post-restart entry commands falsified the assumption that a
positive robust objective licenses a venue-minimum lot above the configured
Fractional Kelly target. Five of the six fills ended above the target; the
sharpest counterexample had seven held shares, a `7.015625` target, and still
bought five more. Fractional Kelly is therefore a hard terminal-holding budget:
when no legal venue lot fits below the remaining target, BUY is infeasible and
the auction chooses HOLD/CASH symmetrically for YES and NO. Positive local EV
does not authorize an exception to the portfolio budget.

The same window exposed a separate truth gap: an incremental entry command had
eleven authenticated shares filled while its remainder stayed open, but the
active canonical position still showed only the prior fill. Every positive
partial fill is current exposure immediately. The command remains partial and
its remainder obligation stays open, while canonical trade facts idempotently
update the command execution fact, position event, lot, and position aggregate
at their actual weighted fill economics. Restart recovery must repair the same
shape without requiring venue replay.

Finally, a fixed 250ms retry for one contended Day0 source-clock commit amplified
WORLD writer contention into a hot loop and starved reactor Window B plus
command recovery. Retries must coalesce per pending commit, back off to a bounded
five-second cadence, reset on success, and never drop the pending physical fact
or create an entry gate. Acceptance requires focused Kelly symmetry, partial
fill immediate/restart idempotency, actual-fill cost, retry coalescing/reset,
compilation, diff checks, independent review, standard deployment, and fresh
runtime proof that minimum-lot repair count is zero, the existing partial fill
reconciles, and reactor/recovery throughput advances.

## 2026-07-20 global winner claim transaction boundary

Fresh production logs showed `GLOBAL_WINNER_CLAIM_WORLD_TXN_OPEN` only when the
auction found a positive unpaged winner. The global selector had correctly
included the canonical WORLD connection in its immutable read cut, but then
called the reactor's WORLD write/claim callback before releasing that cut. A
no-trade epoch therefore looked healthy while every actionable winner failed
closed before preflight.

The selected q/book/wealth values and identities are already immutable Python
evidence at that boundary. The read snapshot now releases immediately after a
winner and actuation are selected, before any durable winner materialization;
JIT probability, book, risk, capital, and venue checks remain current and
unchanged. A production-shaped antibody uses the same SQLite connection for
selection and claim and requires `in_transaction=False` at the callback. The
focused claim/snapshot sets pass `62` and the wider global batch/winner set
passes `57`; standard deployment and a natural positive-winner receipt remain
the runtime acceptance evidence.

## 2026-07-20 held Day0 probability producer priority

Canonical loss attribution for Tokyo July 20 showed a fresh remaining-day
probability expiring during an executable exit window. The last complete hourly
bundle aged past its three-hour read limit at 04:36Z and the next bundle arrived
about 40 minutes later. Live scheduler evidence reproduced the mechanism: the
45-second producer skipped every tick while the reactor/redecision lane was
active even though successful fetches reported no quota denial or exhausted
budget. A probability consumer being busy cannot make held-capital truth
optional.

When a trading lane is active, the producer now performs only the bounded
same-local-day held-city prefix. Pending candidates, open rests, and the static
universe still defer; the existing HTTP budget, per-city throttle, critical
quota tranche, and non-blocking forecast-DB persist lock remain unchanged. No
stale vector is accepted and no exit is authorized by this producer. Acceptance
requires a pre-fix failing test that proves an active trading lane still reaches
the held-city refresh without scanning pending families, the focused scheduler
and Day0 suites, standard live deployment, and new logs with `held_only=True`
while trading remains active.

## 2026-07-20 frozen artifact HWM product-cycle scan

Production sampling attributed the auction's 140–193 second
`prepare_families` stage to the frozen raw-artifact HWM query. The query joined
requested family identity through `json_extract(artifact_metadata_json, ...)`
before narrowing the source cycle, so SQLite scanned wide historical artifact
JSON and the auction's otherwise complete q/book evidence expired before
selection. Freshness rejection was correct; the read path feeding it was not
capital-efficient.

The current structured artifact schema already owns a
`(source_id, product_id, source_cycle_time)` index. Frozen selection now walks
those product-cycle partitions newest-first, parses only one exact partition at
a time, validates payload coverage only for still-unresolved requested
families, and stops when the request set is complete. Legacy tables without the
structured product identity retain the generic fail-closed path. Acceptance
requires the pre-fix malformed-old-cycle antibody, focused HWM tests, a
read-only canonical-DB benchmark with an indexed query plan, standard live
deployment, and natural auction evidence below the 180-second evidence horizon.

## 2026-07-20 submit-feasible ranking and scalar HWM

The batch HWM repair restored complete auctions, then natural production exposed
two later-ordering defects. Winner JIT still used the legacy scalar JSON scan
inside the frozen read transaction, blocking one preflight for about 102 seconds.
The same epoch ranked a `0.99904995` all-in BUY first even though the durable live
submission contract rejects every unit price outside inclusive `[0.05, 0.95]`.
Final guards prevented a venue order, but late rejection wasted the evidence
window and hid any legal runner-up.

Structured scalar HWM reads now reuse the indexed newest-first product-cycle
resolver; legacy schemas keep the generic path. Global BUY scoring admits only
exact probe sizes whose all-in average unit cost is in the live band, while SELL
uses its exact submitted limit price. This feasibility constraint is native-side
symmetric and precedes ranking; robust log wealth, EV, correlated endowment, and
Fractional Kelly still decide among the remaining orders. Acceptance requires
pre-fix failing `0.004`/`0.999` YES/NO BUY and SELL antibodies, the malformed-old
cycle scalar antibody, focused and integration regressions, a read-only canonical
scalar benchmark, standard deployment, and natural receipts showing no late
price-band preflight loop or out-of-band venue command.

## 2026-07-20 WU post-day final observation continuity

Live held-position evidence exposed a permanent authority gap after the local
target day ended. HKO markets could promote an explicit verified daily product
to an exact settlement simplex, while WU markets always returned
`POST_LOCAL_DAY_FINAL_OBSERVATION_UNAVAILABLE` even after canonical same-station
hourly history had completed. The global auction therefore retained stale held
probability for WU positions and could not compare HOLD/SELL from current
physical evidence.

WU finality now requires two independent causal facts: exact coverage of every
UTC hour belonging to the contract-local target day (23/24/25 across DST), and
the exact first same-station `wu_icao_history` observation of the following
local day. Every contributing row must be `VERIFIED`, `OK`,
`historical_hourly`, `utc_hour_bucket_extremum`, unit/station correct, and both
observed and imported no later than the decision time. The target extreme is
then settlement-rounded and mapped to the complete exact family simplex.
Missing an hour, the following-day
publication, or current causality remains fail-closed. The reactor checks the
forecast daily-product plane first and the canonical observation connection
second; neither source role is guessed or substituted.

Acceptance requires the pre-fix failing complete-WU antibody, incomplete/future
counterexamples, a cross-connection global-simplex integration test, current
canonical read-only proof, standard deployment, and a natural held-position
receipt showing the stale WU probability is replaced without a forced order.

## 2026-07-20 deploy heartbeat priority inversion

The WU deployment exposed a restart-ordering failure rather than a strategy
gate. `deploy_live` deliberately kept the external venue-heartbeat supervisor
unloaded while it waited up to four minutes for every held position and then up
to four more minutes for reactor progress. Those proofs themselves need current
CLOB and heartbeat authority, so the verifier created `heartbeat LOST`,
`reduce_only`, and request-lease failures and could leave its temporary entry
pause in place.

The heartbeat watchdog already acquires a shared nonblocking lease on the same
restart lock held exclusively by deploy; while deploy owns that lock the
watchdog records `deploy_restart_in_progress` and cannot restart `src.main`.
Deployment must therefore restart venue-heartbeat immediately after the new
process identity is verified, before monitor and queue proofs. A failed monitor
proof is terminal for that deploy attempt, so a second four-minute queue wait
cannot repair it and is skipped. The entry pause still clears only after
runtime, monitor, queue, and sidecar proofs are all green; no money-path safety
condition is waived.

## 2026-07-20 incomplete global-book response retry amplification

Production logs showed one incomplete two-token CLOB batch response being
reclaimed roughly every four seconds. The reactor's unknown-reason fallback
correctly preserved the opportunity, but the reason was absent from the
explicit transient vocabulary, snapshot-refresh classifier, and retry-floor
set. The same event therefore repeated the full book request before the venue
or substrate could recover, multiplying API/quota pressure without adding any
decision evidence.

`GLOBAL_BOOK_RESPONSE_INCOMPLETE` is now an explicit refreshable transient. It
queues the existing same-family snapshot refresh and applies the existing
attempt-scaled, bounded snapshot retry floor; it remains horizon-bounded with
no attempt cap and every retry re-runs the complete auction from fresh evidence.
This adds no admission gate and cannot force an order. Acceptance requires the
pre-fix-shaped incomplete-response antibody, the complete transient-requeue
suite, standard live deployment, and post-restart logs with no immediate
same-event incomplete-response loop.

## 2026-07-21 heartbeat lease scope and failure backoff

The authenticated heartbeat service remained unavailable with repeated HTTP
503 responses after the deployment-order repair. Heartbeat authority owns the
lease for resting GTC/GTD orders; it does not add evidence to an immediate
FOK/FAK fill. The risk allocator already forced non-healthy heartbeat states to
TAKER and allowed only FOK/FAK, and the executor rechecked the concrete order
type before command persistence. A separate portfolio-wide reduce-only latch
made that safe path unreachable, stopping every otherwise valid entry because
an unrelated resting-order lease was unavailable.

Heartbeat health no longer activates portfolio-wide reduce-only mode. LOST,
STARTING, and UNCONFIGURED states permit only immediate FOK/FAK execution;
resting GTC/GTD still fail closed at discovery and the final executor boundary.
Failed heartbeat POSTs use bounded 10/20/30-second retry backoff after lease
loss while the keeper continues writing a fresh typed status at its normal
cadence. Economics, current book/probability, price band, global robust
delta-log-wealth/EV, Fractional Kelly, and submit-time revalidation remain
cumulative requirements. Acceptance requires LOST/stale/unconfigured
antibodies, resting-order rejection, immediate-order permission, bounded POST
rate with fresh status writes, standard deployment, and a natural receipt that
selects CASH or a positive executable order without heartbeat priority
inversion.

## 2026-07-21 live WU source truth versus durable continuity

The 48-hour loss audit separated a market/forecast miss from a source-routing
defect. Same-station METAR prints reached the append-only publication ledger
within seconds, but WU-settled positions intentionally applied a one-unit
divergence margin until the settlement source confirmed the crossing. The
direct WU client could supply that confirmation; however,
`get_current_observation` returned any existing canonical
`observation_instants` context before contacting WU. Consequently the
WU-vs-METAR anomaly guard compared no live WU side at all, and the hard-fact
lane's nominal `wu_api` refresh could actually be an older durable row.

The source contract is now explicit. `get_live_wu_observation` can return only
a current `wu_api` context and fails closed instead of substituting durable or
METAR evidence. The WU anomaly guard and WU hard-fact confirmation use that
narrow contract; HKO and general continuity consumers keep the existing
canonical-capable path. Successful live WU observations remain memoized for
ten minutes per city/date, while failures retry after two minutes, preventing
per-position HTTP amplification without freezing recovery for a full success
interval. Acceptance requires provider-isolation antibodies, failure-backoff
proof, the complete Day0 fast-observation/hard-fact suites, one read-only live
WU probe with station/source identity, standard deployment, and natural monitor
evidence that a WU-confirmed crossing dominates the prior probabilistic belief
before the settlement-channel hourly row arrives.

## 2026-07-24 chain aggregate versus owned open exposure

The 72-hour loss audit found a canonical exposure split after wallet balances
shrunk. Paris Jul 22 NO 26C retained `shares=97.8947` while fresh wallet truth
and `chain_shares` were `45.0747`; Wuhan Jul 23 YES 32C retained the original
fill aggregate in its chain cost after current wallet exposure fell from
`124.8075` to `3.1125`. The chain-mirror writer updated only `chain_shares`,
while fill-authority runtime exposure continued to read the old owned
`shares`. Its next comparison preferred the already-updated `chain_shares`,
making the torn projection appear permanently consistent.

Truth contract: the wallet position surface owns the current token balance;
command-linked fill facts own Zeus acquisition provenance; `position_current`
owns Zeus's currently open attributed slice. A lower wallet balance than the
attributed open slice reduces current sellable exposure immediately and
pro-rates remaining open cost at the existing unit basis, without inventing an
exit fill or lifecycle close. A higher wallet balance never expands one Zeus
position: the positive residual remains unattributed chain inventory for
reconciliation/risk review. The mirror must compare fresh wallet balance
against current owned `shares`, not against its prior `chain_shares` cache.

This is a K2 reconciliation/truth-ownership bugfix under INV-08, INV-18,
INV-27, and INV-37. Allowed files are
`src/state/chain_mirror_reconciler.py`,
`src/state/chain_reconciliation.py`, focused reconciliation tests, and this
plan. No schema, lifecycle grammar, probability, strategy, source, settlement,
or control change is allowed.

Acceptance requires:

- a Paris-shaped antibody proving a wallet reduction updates current
  `shares`, `chain_shares`, and proportional remaining cost in one canonical
  event/projection transaction while preserving phase;
- a second pass proving the mirror does not hide the remaining owned-vs-chain
  comparison behind cached `chain_shares`;
- an excess-wallet antibody proving wallet aggregate inventory cannot increase
  one position's shares or chain-backed attributed slice;
- existing known-empty/unknown-chain, multi-lot, pending-exit, canonical
  append/projection, and live-safety reconciliation suites remain green;
- planning-lock, source compile, and diff checks pass before hot-fix landing.

## 2026-07-24 Day0 target-window coverage at persistence and selection

The loss time-series audit found persisted hourly vectors whose
`target_date=2026-07-23` but whose provider grid began on July 24. The writer
stamped one two-day response under both requested dates without proving that
the response still covered each target's causal remaining window. The reader
then selected the newest row per model before checking coverage, so one newer
wrong-day row hid an older, still-fresh and complete target-day trajectory.
This converts a usable probability refresh into avoidable fail-closed
unavailability exactly when a held position needs continuous re-decision.

The canonical contract is target-window coverage, not response recency alone.
A vector may be persisted under the current local target only when it covers
every hourly grid point from capture through local-day end, and under the next
target only when it covers that complete local day. The live reader selects
the newest eligible row per model, skipping target-incomplete rows before
forming the required model bundle. No target-date rewriting, interpolation,
forecast fallback, or cross-day reuse is allowed.

Acceptance requires a wrong-day persistence antibody, an existing-row
fallback antibody, the full Day0 remaining-day suite, compile/diff checks, and
no weakening of model completeness, capture-skew, freshness, or causal-window
gates.

## 2026-07-24 provisional match versus economic ownership

The loss/lifecycle audit found one Tel Aviv July 25 position whose canonical
projection reported 5.3 shares although the complete authenticated Data API
snapshot had never observed the token, `chain_seen_at` was NULL, the current
order lookup returned no order, and the only durable trade fact was
`MATCHED`. Command recovery had projected `ENTRY_ORDER_FILLED` directly from
that provisional fact. Chain mirror then treated either the event name or any
`MATCHED` fact as confirmed economic ownership, so repeated complete wallet
absence could never retire the phantom exposure. A second continuity defect
made that permanent: ordinary monitor payloads containing non-finite `NaN`
were invalid to SQLite JSON functions and therefore falsely reset the
consecutive-absence proof on every monitor cycle.

The venue state grammar is causal: `MATCHED` is not `MINED` or `CONFIRMED`.
Confirmed ownership for the chain-absence guard now requires a prior positive
wallet observation, a positive chain reconciliation event, a legacy fill event
without an explicit provisional witness, or a positive `MINED`/`CONFIRMED`
trade fact. An explicitly `MATCHED`-only recovery event remains fill-unproven;
after the existing two consecutive complete absence reads and zero open orders,
the existing `CLOSED_EXITED` administrative-void path removes only the local
projection. A position with any historical positive chain observation remains
open for economic-close evidence. Plain monitor events remain continuity noise
even when their numeric payload contains `NaN`; only typed semantic monitor
events remain reset boundaries.

This is a K1/K2 canonical truth-boundary hot-fix under INV-18. Allowed files are
`src/state/chain_mirror_reconciler.py`, its focused reconciliation test, and
this plan. No schema, lifecycle grammar, venue action, probability, strategy,
source, settlement, or entry/exit threshold changes are allowed.

Acceptance requires:

- a Tel Aviv-shaped antibody proving `MATCHED`-only recovery closes only after
  two complete absent reads;
- a `MINED` fill antibody proving confirmed ownership remains open for review;
- a prior-positive-chain antibody proving current absence cannot erase real
  historical ownership;
- a non-finite plain-monitor antibody proving JSON encoding does not mint
  false Chain/CLOB evidence while typed semantic monitor events still reset;
- the complete chain-mirror and affected state/runtime safety suites remain
  green relative to their recorded baseline;
- planning lock, compile, diff, dry-run canonical reconciliation, standard
  hot-fix landing, and post-restart natural lifecycle evidence.

Rollback is the exact hot-fix revert. It restores conservative review of the
phantom row but performs no inverse DB mutation; any already-voided row remains
an append-only canonical fact for operator review.
