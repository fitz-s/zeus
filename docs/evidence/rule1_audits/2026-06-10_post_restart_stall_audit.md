# RULE 1 audit — post-v13-restart no-trade window (2026-06-10 23:40Z → 00:0xZ)
# Created: 2026-06-10 (late night)
# Authority basis: operator "若10分钟内还没有新交易则按照rule1执行not working排查问题";
# RULE 1 = no-trade is OUR defect until proven honest, layer-by-layer, re-probed reality.

## Verdict: HONEST STALL (two designed causes), zero new artificial blockers found.

## What was checked and excluded (the layers)
1. Daemons: all four alive post-restart (HEAD 2eb3789787), receipts flowing to 23:51+.
2. Funnel since 23:40Z: 40 RETIRED_DAY0_NO_SUBMIT_MARKER, 32 EVENT_BOUND_SELECTED_CANDIDATE_MISSING
   (21 families, ~all 06-12), 7 empty-NO-ask, 4 TRADE_SCORE_NON_POSITIVE, 2 buy_no-evidence.
3. Mainstream gate suspicion (stale L1827 comment "fail the mainstream-agreement gate"):
   EXCLUDED — L7695 + code confirm reference-only, "takes NO part in production selection".
   The 1827 comment is a Mission-2-era fossil; flagged for deletion.
4. K4.0 suspicion: EXCLUDED — the 271-count CANDIDATE_MISSING wave predates K4.0 (v12 era).
   HOLD antibody not engaged (no open family rests; Taipei/Moscow have no orders at all).
5. Coverage shrink (status=UNLICENSED n=78, e.g. 0.971→0.8105): live since 06-09 19:42,
   12,878 emissions, tail bins only — did not flip any sampled bin's verdict tonight
   (shrunk tails were already negative vs their 0.99x books). Honest gate; relicensing
   review queued for when coverage n grows.
6. Hand-math discriminator on two CANDIDATE_MISSING families (raw certified bounds vs
   live books, fee law applied):
   - Taipei 06-12: modal 27°C. Positive-edge bins = 27°C (+8.2¢ taker), 28°C (+2.5¢),
     26°C (maker +1.1¢) — ALL within direction-law tolerance of modal ⇒ buy_no FORBIDDEN
     (the law that killed the Milan/Paris wrong-trade class). Lawful-distance bins
     (25, 29, 30+): every edge negative (−2.8 … −12¢).
   - Moscow 06-12: modal 30°C. Same shape exactly: +12.4¢/+20.4¢ sit on modal/modal±1
     (forbidden); all lawful bins negative (tail NOs priced 0.92-0.999 vs no_lcb ≤0.86).
   ⇒ CANDIDATE_MISSING here = "all positive candidates direction-law-rejected, all lawful
   candidates ΔU≤0" = honest no-trade, structurally identical across the 21 families.

## Why trades will reappear (without touching any gate)
- 00Z cycle (~03-05Z) refreshes posteriors; book/forecast divergence re-opens mispriced
  bins (today's 4 good fills were exactly this class — modal-distant bins the market
  priced at 0.34-0.41 YES).
- day0 (06-11) markets — the most liquid class — are accumulating shadow evidence tonight
  (40 receipts already); promotion is the OPERATOR decision after ~150-270 settled samples.
- K4.0 maker rests now engage automatically when any lawful bin's maker edge ≥ ts.
- Bound tightening (per-city σ-floor cohorts, coverage relicensing at larger n) raises
  no_lcb on lawful bins — the licensed set widens as settlement evidence accrues.

## Defects found on the way (observability, not gates)
1. CANDIDATE_MISSING receipts are starved: regret_bucket=UNKNOWN_REVIEW_REQUIRED, no
   bin/q/score/per-proof missing_reason content. The "all candidates gate-rejected"
   family verdict should carry the per-proof rejection histogram (direction_law=N,
   coverage_tail=M, …). K2.1 registry has the categories; the writer doesn't use them
   here. → next K2.1 increment.
2. The ΔU ranker and selection scoping emit ZERO log lines at INFO — tonight's audit had
   to reconstruct selection from hand-math. One structured line per family decision
   (winner|none + top-3 ΔU + kill histogram) belongs in the K5.2 funnel organ.
3. Stale comment at event_reactor_adapter.py:1827 claims a mainstream gate exists in
   selection — delete to stop the next auditor's false alarm.

## SUPERSEDING CHAPTER (2026-06-11 ~03:25Z) — operator was right; verdict upgraded
The "honest stall" verdict above was INCOMPLETE. The direction-law/bounds analysis holds,
but it answered "why no edge on the data we had" — the real question was "why is our data
this old". Full chain:
1. Live-eligible refresh = MAIN cycles only (00/12Z; operator cycle-physics directive holds
   06/18Z shadow-only). 06Z certified posteriors materialized tonight are lawfully BLOCKED
   (REPLACEMENT_0_1_LIVE_AUTHORITY_INTERMEDIATE_CYCLE_SHADOW_ONLY).
2. 12Z died at the PROVIDER: open-meteo single-runs never published the 12Z run (400
   "model run is not available"; AIFS 12Z itself downloaded fine, 336MB). The 02:10Z cron
   was 12Z's ONLY download attempt — no per-leg retry exists.
3. Therefore next live-eligible refresh = 06-11 00Z at the 14:10Z cron slot — a structural
   ~12h zero-refresh window. Books moved all night against frozen 00Z bounds → honest-looking
   CANDIDATE_MISSING everywhere → "no opportunity" was OUR pipeline's age, not the market.
4. The liveness check said PIPELINE_ALIVE throughout (flat 8h threshold) — false green.
Root-cause class: ONE-SHOT download cadence built on a GUESS lag constant, with no
availability-retry and no registry-derived liveness bounds. Fix items K4.0b(a-e) in the
consolidated overhaul ledger. Stopgap watcher live tonight: re-runs the sanctioned
downloader the moment the 12Z anchor publishes (12Z is live-eligible → starvation ends
hours before 14:10Z if the provider publishes).
