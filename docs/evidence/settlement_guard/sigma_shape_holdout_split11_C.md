# Sigma-shape kernel refit — TEMPORAL HOLDOUT + ring-loss replay
unit=C  window=settled-2026-06-08..2026-06-12  split=2026-06-11  cost=0.02
train_cells=178  test_cells=126

TRAIN fit — LIVE uniform : k=1.6852 w=0.2760
TRAIN fit — CAND kernel  : k=1.0171 w=0.0000 m=1.0000 floor_steps=2.0220

## Held-out ring ratios (realized / expected) — target 1.0
LIVE uniform form:
  dist=0    mean_q=0.1547 realized=0.2364 ratio=1.527 n=110
  dist=1    mean_q=0.1365 realized=0.2553 ratio=1.87 n=235
  dist=2    mean_q=0.0996 realized=0.0996 ratio=0.999 n=231
  dist=3    mean_q=0.0710 realized=0.0431 ratio=0.607 n=209
  dist=>=4  mean_q=0.0430 realized=0.0115 ratio=0.266 n=349
  dist=tail mean_q=0.0953 realized=0.0159 ratio=0.166 n=252
CANDIDATE kernel form:
  dist=0    mean_q=0.1893 realized=0.2364 ratio=1.249 n=110
  dist=1    mean_q=0.1687 realized=0.2553 ratio=1.514 n=235
  dist=2    mean_q=0.1199 realized=0.0996 ratio=0.831 n=231
  dist=3    mean_q=0.0683 realized=0.0431 ratio=0.631 n=209
  dist=>=4  mean_q=0.0179 realized=0.0115 ratio=0.641 n=349
  dist=tail mean_q=0.0688 realized=0.0159 ratio=0.231 n=252

HELD-OUT dist-1 ratio: LIVE=1.8700427563862811 -> CANDIDATE=1.513578076414708  (target 1.0)
HELD-OUT dist-2 ratio: LIVE=0.9992780863505702 -> CANDIDATE=0.830756395561819  (target 1.0)

## NO-gate replay (held-out, scored vs settlement)
NEAR-ring NO admits (dist<=2)  LIVE: 316 admits, win_rate=0.744, losses=81
NEAR-ring NO admits (dist<=2)  CAND: 251 admits, win_rate=0.749, losses=63
FAR NO admits (dist>=3/tail)   LIVE: 0 admits, win_rate=None
FAR NO admits (dist>=3/tail)   CAND: 0 admits, win_rate=None

## GATE-2 prevention (near-ring NO losses)
LIVE near-ring NO losses: 63
CAND near-ring NO losses: 47
PREVENTED by candidate  : 16
   prevented: Busan 2026-06-12 dist-0
   prevented: Chongqing 2026-06-11 dist-0
   prevented: Guangzhou 2026-06-12 dist-0
   prevented: Istanbul 2026-06-12 dist-0
   prevented: Kuala Lumpur 2026-06-12 dist-0
   prevented: London 2026-06-11 dist-0
   prevented: Manila 2026-06-12 dist-0
   prevented: Milan 2026-06-12 dist-0
   prevented: Moscow 2026-06-12 dist-0
   prevented: Munich 2026-06-11 dist-0
   prevented: Panama City 2026-06-11 dist-0
   prevented: Shanghai 2026-06-12 dist-0
   prevented: Singapore 2026-06-12 dist-0
   prevented: Warsaw 2026-06-12 dist-0
   prevented: Wellington 2026-06-11 dist-0
   prevented: Wellington 2026-06-12 dist-0

## Named ring losses present in held-out window
   Karachi 2026-06-11 winner_dist=3 live_NO_loss=False cand_NO_loss=False
   Kuala Lumpur 2026-06-11 winner_dist=1 live_NO_loss=True cand_NO_loss=True
   Karachi 2026-06-12 winner_dist=1 live_NO_loss=True cand_NO_loss=True
   Kuala Lumpur 2026-06-12 winner_dist=1 live_NO_loss=True cand_NO_loss=True
   Karachi 2026-06-11 winner_dist=4 live_NO_loss=False cand_NO_loss=False
   Kuala Lumpur 2026-06-11 winner_dist=2 live_NO_loss=True cand_NO_loss=True
   Karachi 2026-06-12 winner_dist=1 live_NO_loss=True cand_NO_loss=True
   Kuala Lumpur 2026-06-12 winner_dist=0 live_NO_loss=True cand_NO_loss=True
