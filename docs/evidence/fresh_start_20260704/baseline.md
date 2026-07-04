# Fresh-start baseline — 2026-07-04 (E3 deploy a884eea2c + risk_actions fix 0e5d1a588)

Operator directive: the 2026-07-04 restart is a fresh start; pre-restart data is legacy
accounting to be graded and closed, not carried as live state. Capital-gain targets are
measured from THIS baseline forward.

## Financial position at baseline (21:10 UTC)
- Bankroll: $211.37. Realized P&L (legacy, all-time): −$72.08 (portfolio surface).
- Open exposure: $6.11 — ONE live position (Manila 2026-07-04 high 33°C buy_no,
  forecast_qkernel_entry, chain synced, monitored; settles naturally today).
- Open venue commands: 0. Open collateral reservations: 0 (the orphaned $6.56 PUSD_BUY
  from CANCELLED command 7e07c586500d46d7 was released 21:08 UTC with reason=CANCELLED).

## Legacy quarantine book — graded against VERIFIED settlements (read-only forensic, 2026-07-04)
All 40 quarantined positions (cost $293.11, 1504.09 chain shares) graded:
- REDEEMABLE: **$236.53** across 18 won positions (all buy_no NO-tokens paying $1/share;
  cost basis $153.82). Redeem is THIRD-PARTY (Zeus never submits redeem tx — operator law).
  Top: Munich 06-30 $29.14, Shenzhen 06-25 $28.00, Milan 06-25 $27.75, KL 06-25 $23.00.
- WRITE-OFF: $130.35 cost = $32.37 (5 lost with shares, incl. the 1184-share Dallas
  buy_yes bought for $2.37) + $97.98 (14 zero-chain projection-debt rows).
- UNMATCHABLE: 3 ($8.94 cost, 21.32 shares): HK 06-25 low (no settlement row),
  HK 06-26 low (settlement QUARANTINED on 26-vs-27C source disagreement; NO-token likely
  redeems ~$5 once resolved), Helsinki 07-02 high (no settlement row).
- Pattern note: chain-backed buy_no-on-exact-degree won 18/22; every legacy buy_yes lost.

## Runtime state at baseline
- All daemons on a884eea2c+ (loaded_sha verified); scheduler FAILED jobs: none;
  c3_staleness_cancel ok; fusion posteriors fresh; entries UNPAUSED (fresh justification
  recorded 17:45 UTC).
- Entry gate: riskguard ORANGE — opening_inertia trailing-30d Brier 0.322 (n=40, all
  pre-restart settlements) ≥ orange 0.30. This is the LEGACY model report card, not stale
  noise: the fresh start clears the position book, NOT the calibration evidence. Unlock
  path is F1 (settlement-realized recalibration), designed via external consult, not
  threshold fiddling. center_buy 0.132 GREEN (n=8); 2 unclassified rows keep localization
  at global level.

## Capital-gain accounting from this baseline
- Numerator (gains): settled after-cost P&L on positions ENTERED after 2026-07-04T17:45 UTC
  (first unpaused cycle on new code), plus legacy recoveries ($236.53 redeemable + any
  unmatchable resolution) counted as one-off balance-sheet items, NOT alpha.
- The E4 tracked metrics and F1–F5 fix queue live in
  docs/operations/current/plans/order_engine_rebuild_execution_plan_2026-07-02.md §E4.
