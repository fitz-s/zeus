# Why every live fill is TAKER (and what it costs) — root cause + measurement
# Created: 2026-06-10
# Authority basis: operator escalation "所有的交易都是taker而不是挂单,部分点差甚至达到5cent";
# mode law = src/strategy/live_inference/mode_consistent_ev.py; Fitz Constraint #4
# (data provenance) applied to the p_fill prior.

## Observed (re-probed, not memory)
All 6 live fills since 2026-06-09 are FOK taker crosses (post_only=False), zero resting
maker entries:

| cmd | market | price | book bid/ask | spread | paid vs mid |
|---|---|---|---|---|---|
| 0b5c305e | Milan 06-11 YES | 0.016 | 0.01/0.02 | 0.7¢ | 0.35¢ |
| 1f4c6d65 | Paris 06-12 YES | 0.12 | 0.11/0.12 | 1¢ | 0.5¢ |
| c8d19068 | HK 06-12 NO | 0.72 | 0.65/0.72 | 7¢ | 3.5¢ |
| c200af0e | Karachi 06-12 NO | 0.66 | 0.58/0.66 | 8¢ | 4.0¢ |
| 087e1159 | KL 06-12 NO | 0.67 | 0.62/0.67 | 5¢ | 2.5¢ |
| 7a5afeb0 | Lucknow 06-12 NO | 0.80 | 0.79/0.80 | 1¢ | 0.5¢ |

Total $31.34 notional, **$1.25 (4.0% of notional) donated to spread** vs mid. Against
certified edges of 3-8¢/share, the cross eats roughly half the edge on the wide-book
fills (HK, Karachi, KL — exactly the 0.2-0.6 class we want).

## Root cause (structural, two layers)

1. **p_fill_maker = 0.10 flat GUESS crushes maker EV ~10×.**
   EV_maker = 0.10 × (q_fill_adj − maker_limit) vs EV_taker = 1.0 × (q_lcb − cost).
   With any healthy certified edge, taker wins by construction; maker can only win when
   the taker lane is forbidden (spread guard / empty book). The TAKER_OVER_MAKER_MARGIN
   hysteresis only handles knife-edge ties — it never rescues a 10×-handicapped lane.

2. **One-shot mutual exclusion is the wrong decision shape.** The code chooses maker XOR
   taker once. The real option structure is REST-THEN-CROSS: post at bid+tick with a
   deadline; if unfilled at deadline and the edge still certifies, cross then. Resting
   first costs (edge-decay risk during the rest) and earns (spread × p_fill). Immediate
   cross is optimal only when the edge is fleeting or event-end is near. The current
   formula cannot represent this policy at all — that is the design failure, not the
   constant's value.

## Measurement (replaces the GUESS — Kaplan-Meier on our own resting facts)
108 historical GTC/post_only orders (May canary era), right-censored at
EXPIRED/CANCELLED time; 28 fill events. Median rest only 24.9 min (old TTLs), p75 54 min.

Cumulative fill probability (KM):
- by 15 min: 0.188
- by 60 min: 0.214
- by 120 min: 0.390
- by 240 min: 0.530
- (tail beyond ~240 min: at-risk set too thin to certify)

By price band (proxy for the bin class):
- deep-cheap [0.01,0.10): 12/50 filled — this band dominated the old "7.7% at bid+tick"
  bucket that justified 0.10
- [0.10,0.40): 7/49 filled
- **[0.40,1.00): 9/9 filled** — the mid-range class we actually trade now

Verdict: 0.10 is below the measured ANY-horizon, ANY-band cumulative rate (15-min
all-band = 0.188) and ~4-5× below the 2-4h horizon for tradeable books. The cited
"17.8%/7.7%" provenance was conditioned on ~25-minute rests of deep-longshot quotes —
wrong population for the question (Fitz #4: the data was real, its semantics didn't
match the use).

Honest caveats: n=9 in the [0.4,1.0) band; those fills may carry adverse selection
(λ haircut exists for this); markets/era differ. The numbers license REST-THEN-CROSS
with a measured deadline; they do not yet license a precise p_fill point for one-shot EV.

## K-decision (proposed; for the consolidated overhaul)
Replace one-shot maker-XOR-taker with a REST-THEN-CROSS policy:
1. Default entry = post_only GTC at min(bid+tick, reservation, 1−yes_bid−tick), with a
   re-quote/escalation deadline D (start: 120 min, from the KM curve; registry-tracked
   constant with basis=MEASURED).
2. At D: if unfilled AND edge re-certifies ≥ ts → taker cross (today's path). If edge
   gone → cancel, receipt the decay (that datum measures rest-cost for free).
3. Taker-immediate ONLY when: time-to-event-end < floor, or edge > fleeting threshold
   (both registry constants), or book one-sided cases already lawful.
4. Antibody: relationship test pinning "no taker cross while an unexpired same-family
   maker rest exists"; funnel receipts carry chosen mode + deadline so the settlement
   loop measures realized maker markout (λ recal) and fill hazard per band.
Expected effect at tonight's mix: ~4% notional spread donation → ~4%×(1−p_fill(D)) with
p_fill(120m)=0.39 ⇒ roughly halves the cost, compounding with every future trade.
