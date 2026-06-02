# POLARITY AUDIT — SETTLE / FILL / PnL / EXIT (NO-side independence)

- Created: 2026-06-01
- Last reused or audited: 2026-06-01
- Authority basis: OPERATOR LAW — "YES and NO are NOT mirror images; NO is
  independently grounded (pays 1 iff the bin does NOT resolve), never 'the other side'."
- Mode: READ-ONLY audit. HEAD 6fcd05a69f. No edits / git / DB writes.

## VERDICT: CONFIRM (no inversion / mirror found)

The settlement/fill/PnL/exit pipeline grounds the NO side independently. A NO
position's payout, PnL sign, settlement leg (CTF indexSet), and exit book are
all derived from the NO native space, not by mirroring/negating the YES PnL.
The one place a "flip" exists (`_held_probability`) is the *correct* native
mapping `held_p = 1 − p_obs_yes`, explicitly guarded by a pre-merge critic
antibody (F-1, 2026-05-27) against the mirror-skip regression.

## OUTCOME → SIDE → PAYOUT TABLE (MECE)

Resolution maps the settled integer to YES on **exactly the hit bin** and NO on
**all other bins**. Source of truth for "which bin is YES":
`_extract_resolved_market_outcomes` (harvester.py:1208) requires the Gamma child
with `outcomePrices == [1,0]` (YES token resolution price 1.0) →
`yes_won=True`; exactly one such child is required (harvester.py:915, else skip).
The winning bin text label is `_canonical_bin_label(lo,hi,unit)` (harvester.py:1330).

Per-position payout in `_settle_positions` (harvester.py:2387-2467):

| pos.direction | won = (pos.bin_label == winning_bin) | exit_price | payout | redeem indexSet |
|---|---|---|---|---|
| buy_yes | True  (its bin hit) | `1.0 if won` → 1.0 | pays 1 | `["2"]` (YES=index1) |
| buy_yes | False (its bin missed) | 0.0 | pays 0 | — (loser) |
| buy_no  | True  (its bin hit) | `1.0 if not won` → 0.0 | pays 0 | — (loser) |
| buy_no  | False (its bin missed) | `1.0 if not won` → 1.0 | pays 1 | `["1"]` (NO=index0) |

`won` (harvester.py:2387) is the YES-truth of the position's *own* bin
(`_parsed_temperature_bins_equivalent(pos.bin_label, winning_label)`), NOT a
copy of the YES position's result. `exit_price` (harvester.py:2403-2406) then
applies the direction-correct payout. PnL is direction-agnostic
`shares × exit_price − cost_basis` (`_compute_realized_pnl`, portfolio.py:2135;
`pnl` fallback harvester.py:2467) — all polarity lives in `exit_price`, so there
is **no double-inversion**.

## DECISIVE EXAMPLE — Singapore 06-01 high settled = 31°C

Market YES-resolved bin = `31°C`. Shadow holds buy_no on `34°C` and `35°C`.
Live code (`_parsed_temperature_bins_equivalent` + exit_price formula) yields:

```
buy_no  on '34°C': won=False  exit_price=1.0  PAYS 1   indexSet=["1"]   ✓ (high=31≠34)
buy_no  on '35°C': won=False  exit_price=1.0  PAYS 1   indexSet=["1"]   ✓ (high=31≠35)
buy_no  on '31°C': won=True   exit_price=0.0  PAYS 0   (loser, no redeem) ✓ (high=31=31)
buy_yes on '31°C': won=True   exit_price=1.0  PAYS 1   indexSet=["2"]   ✓
```

Code yields the operator-required result, **not** the inverse. NO-34 and NO-35
both pay 1; a NO on the hit bin (31) correctly pays 0.

## SITE-BY-SITE FINDINGS (file:line)

CONFIRM (correct independent grounding):
- `harvester.py:2387` `won = bins_equivalent(pos.bin_label, winning_label)` —
  YES-truth of position's OWN bin; not borrowed from YES.
- `harvester.py:2403-2406` exit_price: buy_yes→`1 if won`; buy_no→`1 if not won`.
  Independent NO payout leg. NO MIRROR.
- `harvester.py:2420` redeem token = `token_id if buy_yes else no_token_id` —
  NO redeems its own token, not the YES token.
- `harvester.py:2435-2440` CTF indexSet: buy_yes won→`["2"]`, buy_no won→`["1"]`
  (NO outcome = index 0 → 1<<0 = 1). Correct MECE binary encoding.
- `harvester.py:2467` / `portfolio.py:2132-2135` PnL = `shares·exit_price −
  cost_basis`, direction-agnostic. Polarity solely via exit_price → no re-flip.
- `settlement_semantics.py:155-178` `assert_settlement_value` rounds the
  *observed temperature* to the settled integer; side-agnostic (correctly so —
  it produces the value that selects the YES bin, downstream applies side).
- `exit_triggers.py:6-8,154-278` exit eval is native-space per direction
  (`_evaluate_buy_no_exit` separate from yes); probabilities are P(NO) for
  buy_no, "never flip internally". NO MIRROR.
- `exit_family_optimizer.py:184-200` `_held_probability`: buy_no → `1 − p_obs_yes`.
  This is the CORRECT native flip (NO pays iff YES bin does not settle), and is
  the family-level mirror of the entry-side invariant, F-1 critic-guarded
  against accidental skip. CONFIRM (not a defect).
- `chain_reconciliation.py:1003,1021,1057` per-side token selection
  `token_id if buy_yes else no_token_id` — consistent with harvester. No
  settlement valuation here; phase/share-truth only. NO MIRROR.
- `lifecycle_manager.py` — phase/state machine only; carries NO YES/NO payout
  semantics, so cannot invert. N/A (correctly side-free).

INVERSION / MIRROR SITES: **NONE FOUND.**

## NOTES / RESIDUAL (non-blocking, not polarity inversions)

- V1 redeem indexSet (harvester.py:2421-2440) is binary-only; ranged
  (multi-bin) markets fall to `winning_index_set=None` (WAD per PR-I.5.a). Not
  a polarity inversion — a scope limit on multi-bin redemption.
- `won` requires `pos.bin_label` be parseable vs `winning_label`; non-comparable
  → skip settlement (harvester.py:2388-2395), fail-safe (does not invert).
