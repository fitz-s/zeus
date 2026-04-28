# Region-Down R2L2 — Converged

Created: 2026-04-26
HEAD anchor: 874e00cc0244135f49708682cab434b4d151d25d (main)
Region: Down (V2 SDK migration + collateral semantics + heartbeat)
Layer: 2 (cross-module data-provenance / relationship invariants)
Status: CONVERGED — pending judge accept

Signed-by:
- proponent-down @ 2026-04-26
- opponent-down @ 2026-04-26

Turns: 2 each direction.

---

## Consensus

6 L2 OPEN questions all closed at HEAD-grep-verified. Q-NEW-1 RE-VERIFIED FRESH on-chain in this run (proponent-down dispatched live polygon.drpc.org eth_call). Disk-state lies fixed in-flight (down-01 yaml seam citation rot + down-07 yaml grep-evidenced absence proofs).

## L2 attack disposition (6 attacks)

| # | Attack | Disposition | Lock |
|---|---|---|---|
| A1 | Q-FX-1 ship-block gate (no ship-on-promise) | DUAL-GATE LOCKED | down-07 critic_gate adds: (i) file-existence check `evidence/q-fx-1_classification_decision_2026-04-26.md` MUST exist with operator-signoff specifying `{fx_line_item, trading_pnl_inflow, carry_cost}`; (ii) runtime env-flag `ZEUS_PUSD_REDEMPTION_ENABLED=false` default; fail-closed FXClassificationPending raise. Antibody: `tests/test_pusd_collateral_boundary.py::test_pusd_redemption_blocked_until_fx_classification` |
| A2 | down-01 seam citation rot (`:160-161` → actual `:195-196`) | FIXED IN-FLIGHT | proponent-down edits down-01.yaml during R2L2 turn-2 swapping all 8 occurrences of `polymarket_client.py:160-161` → `:195-196`. Same disk-edit pattern as Mid R3L3 yaml fixes |
| A3 | pUSD propagation 7-row matrix | LOCKED w/ GREP-EVIDENCED ABSENCE | 4 ACTIVE modify-sites + 3 ABSENCE-PROVEN negative-tests (riskguard.py + chain_reconciliation.py + calibration/ all grep-empty for USDC/pUSD/currency) |
| A4 | Heartbeat single-tombstone-per-file | LOCKED | ONE physical tombstone file (auto_pause_failclosed.tombstone), TWO logical reasons keyed by `reason` field prefix: `daemon_heartbeat_failure_*` (existing) vs `clob_v2_heartbeat_failure_*` (V2 fail-soft). V2-heartbeat NEVER touches daemon-heartbeat.json. Negative + positive antibody pair |
| A5 | B2/B4/B5 routing (NOT in Up cards) | DEFER TO WAVE-2 PACKET | Explicit "out-of-scope for ultimate_plan; routes to separate Wave-2 follow-up packet `docs/operations/task_2026-XX-XX_wave_2_data_readiness/` (packet-id TBD)". NO new Up cards minted; B2/B4/B5 are DATA-authority files distinct from execution-state-truth scope |
| A6 | Q-NEW-1 re-verification | FRESH RE-VERIFIED IN R2L2 RUN | proponent-down dispatched live curl to https://polygon.drpc.org during turn-2: symbol() → "pUSD"; name() → "Polymarket USD". Q-NEW-1 STABLE. No proxy upgrade between 2026-04-26 11:12 (original probe) and R2L2 lock time |

## R1L1-OPEN closures

| Open item | R2L2 resolution |
|---|---|
| Q-FX-1 (operator decision required) | Dual-gate (env-flag + evidence-file) blocks down-07 ship until operator picks classification rule; fail-closed default |
| Q1-zeus-egress | Operator-side probe gates down-01 ship; status unchanged from R1L1 (still OPERATOR-WAIT) |
| Q-HB (heartbeat cadence) | Activation gate for down-06; status unchanged from R1L1 (still OPERATOR-WAIT); single-tombstone invariant added per A4 |

## pUSD propagation matrix (R2L2-final, 8 rows = 4 ACTIVE + 4 ABSENCE-PROVEN)

### ACTIVE modify-sites (4 rows — code change required)

| Row | File:line | Change |
|---|---|---|
| 1 | `src/data/polymarket_client.py:344 get_balance()` | balanceOf() resolves to pUSD address `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`; SDK's BalanceAllowanceParams structure unchanged |
| 2 | `src/main.py:379` | startup wallet check log copy: "USDC" → "pUSD" |
| 3 | `src/execution/harvester.py:1244-1264` | T2-G redemption path comment + branch on collateral_token CHECK |
| 4 | `src/observability/status_summary.py:308 cycle_summary.get("wallet_balance_usd")` | currently surfaces USD-denominated balance; pUSD-aware framing required (rename surface or document USD as union of pUSD+USDC.e legacy) |

### ABSENCE-PROVEN negative-test rows (4 rows — grep-evidenced clean at HEAD 874e00c)

| Row | Module | Grep evidence | Invariant |
|---|---|---|---|
| 5 | `src/state/db.py` positions table currency tag | `grep -in "currency\|USDC" src/state/db.py` = 0 hits | db.py positions table MUST stay collateral-agnostic; antibody asserts grep returns empty |
| 6 | `src/riskguard/riskguard.py` (sizing) | `grep -in "USDC\|currency\|pUSD\|collateral\|balance" src/riskguard/riskguard.py` = 0 hits | riskguard MUST stay collateral-agnostic ($-units only); antibody asserts grep returns empty |
| 7 | `src/state/chain_reconciliation.py` | `grep -n "USDC\|currency\|pUSD" src/state/chain_reconciliation.py` = 0 hits | chain_reconciliation MUST stay collateral-agnostic; antibody asserts grep returns empty |
| 8 | `src/calibration/` (training authority filter) | `grep -irn "USDC\|currency" src/calibration/` = 0 hits | calibration MUST NOT reference USDC currency unit; antibody asserts grep returns empty |

NOTE: matrix expanded from anchor-to-up-06 7-row shape to actual 4+4 split per R2L2 turn-3 grep evidence. tests/test_pusd_collateral_boundary.py (NEW) is implementation-side, not a propagation row; folded into down-07 antibody bank.

NEW negative-test antibody: `tests/test_pusd_collateral_boundary.py::test_modules_collateral_agnostic`
```python
import subprocess
for module_path in ['src/riskguard/', 'src/calibration/', 'src/state/db.py', 'src/state/chain_reconciliation.py', 'src/observability/']:
    result = subprocess.run(['git','grep','-l','-iE','USDC|pUSD|currency','--',module_path], capture_output=True, text=True)
    assert result.stdout.strip() == '', f"collateral-leakage detected in {module_path}: {result.stdout}"
```

## Q-FX-1 dual-gate body (R2L2-locked)

Engineering owns the gate that BLOCKS pUSD redemption ship until operator decides PnL classification.

```python
# src/execution/harvester.py (or src/data/polymarket_client.py)
def redeem_pusd_collateral(condition_id: str) -> dict:
    import os
    from pathlib import Path
    EVIDENCE_PATH = Path("docs/operations/task_2026-04-26_polymarket_clob_v2_migration/evidence/q-fx-1_classification_decision_2026-04-26.md")
    if not EVIDENCE_PATH.exists():
        raise FXClassificationPending(
            f"Q-FX-1 evidence file missing: {EVIDENCE_PATH}. "
            f"Operator MUST commit classification decision (one of "
            f"{{fx_line_item, trading_pnl_inflow, carry_cost}}) before pUSD redemption can ship."
        )
    if os.environ.get("ZEUS_PUSD_REDEMPTION_ENABLED", "false").lower() != "true":
        raise FXClassificationPending(
            "ZEUS_PUSD_REDEMPTION_ENABLED env-flag is false; operator MUST flip explicitly "
            "after committing classification decision."
        )
    # ... actual redemption call ...
```

Antibody:
```python
# tests/test_pusd_collateral_boundary.py::test_pusd_redemption_blocked_until_fx_classification
def test_blocked_when_evidence_file_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEUS_PUSD_REDEMPTION_ENABLED", "true")  # env says yes
    # but evidence file does not exist at default path
    with pytest.raises(FXClassificationPending, match="Q-FX-1 evidence file missing"):
        redeem_pusd_collateral("0xCONDITION_ID")

def test_blocked_when_env_flag_false(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEUS_PUSD_REDEMPTION_ENABLED", "false")
    # Even if evidence file exists
    EVIDENCE_PATH.write_text("classification: trading_pnl_inflow\nsignoff: operator-Fitz 2026-04-26")
    with pytest.raises(FXClassificationPending, match="env-flag is false"):
        redeem_pusd_collateral("0xCONDITION_ID")
```

## down-06 single-tombstone invariant (R2L2-locked)

```yaml
# down-06 yaml addition
single_tombstone_per_file_invariant:
  description: |
    auto_pause_failclosed.tombstone is the ONE physical kill-switch file. It carries TWO logical
    reasons keyed by `reason` field prefix:
      - `daemon_heartbeat_failure_*` (existing _write_heartbeat 3-strike fallback at main.py:355-369)
      - `clob_v2_heartbeat_failure_*` (NEW V2-heartbeat fail-soft per down-06 activation)
    V2-heartbeat NEVER writes daemon-heartbeat.json (existing _write_heartbeat owns that file).
    V2-heartbeat ONLY writes auto_pause_failclosed.tombstone with the V2-prefixed reason.
  positive_antibody: |
    test_v2_heartbeat_writes_distinct_reason_prefix:
      mock clob.post_heartbeat to raise; assert auto_pause_failclosed.tombstone written
      with reason starting "clob_v2_heartbeat_failure_" (NOT "daemon_heartbeat_failure_").
  negative_antibody: |
    test_v2_heartbeat_never_touches_daemon_heartbeat_json:
      mock clob.post_heartbeat to raise; capture stat() of state/daemon-heartbeat.json
      before+after V2-heartbeat tick; assert mtime unchanged.
  cite_correction:
    r1l1_off_by_2: "main.py:367 was the R1L1 cite; actual os._exit(1) is at :369. R2L2 corrects."
```

## Down-01 seam citation FIX (in-flight disk edit)

down-01.yaml fixed during R2L2 turn-2 (proponent-down):
- All 8 occurrences of `polymarket_client.py:160-161` swapped to `:195-196`
- Comment trail added: "R2L2 A2 fix — seam citation aligned with Mid R2L2/R3L3 lock at :195-196"

Verification post-edit (opponent-down): `grep -n "160-161" down-01.yaml` returns 0 hits; `grep -n "195-196" down-01.yaml` returns ≥8 hits.

## Q-NEW-1 fresh on-chain re-verification (R2L2 run)

Proponent-down dispatched live curl to https://polygon.drpc.org during turn-2:

```
POST https://polygon.drpc.org
Content-Type: application/json

{"jsonrpc":"2.0","method":"eth_call","params":[{"to":"0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB","data":"0x95d89b41"},"latest"],"id":1}
→ "0x...0470555344..." → ABI-decoded "pUSD" (4 chars: 0x70='p', 0x55='U', 0x53='S', 0x44='D')

{"jsonrpc":"2.0","method":"eth_call","params":[{"to":"0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB","data":"0x06fdde03"},"latest"],"id":1}
→ "0x...0e506f6c796d61726b657420555344..." → ABI-decoded "Polymarket USD" (14 chars)
```

Result: Q-NEW-1 STABLE. pUSD distinct ERC-20 confirmed; no proxy upgrade between 2026-04-26 11:12 (original probe) and R2L2 lock time.

Memory `feedback_on_chain_eth_call_for_token_identity` honored.

## B2/B4/B5 routing decision (R2L2 final)

`grep "B2|B4|B5|settlement backfill|obs_v2|DST flag|observation_instants_v2_writer|source_rationale" docs/operations/task_2026-04-26_ultimate_plan/slice_cards/up-*.yaml` = 0 hits.

R1L1's "Wave 2 B2/B4/B5 → Up region" routing is NOT reflected in current Up cards. R2L2 decision: B2/B4/B5 are out-of-scope for ultimate_plan (DATA-authority files, distinct from authority-architecture scope). Route to separate Wave-2 follow-up operational packet:

- `docs/operations/task_2026-XX-XX_wave_2_data_readiness/` (packet-id TBD)
- B2: settlement backfill scripts (touches `scripts/onboard_cities.py` + settlements table)
- B4: obs_v2 physical-bounds (touches `src/state/observation_instants_v2_writer.py`)
- B5: DST flag writer (touches `architecture/source_rationale.yaml` + DST handling)

NO new Up cards minted. Up's up-04 notes existing Wave-2 reference stays as the cross-pointer. Document explicitly to prevent orphan-routing claim.

## File:line locks (all grep-verified at HEAD 874e00c)

| Slice | File:line | Verified |
|---|---|---|
| down-01 | `src/data/polymarket_client.py:195-196` (FIXED from R1L1 :160-161) | ✓ `signed = self._clob_client.create_order(order_args); result = self._clob_client.post_order(signed)` |
| down-01 | `requirements.txt:14 py-clob-client>=0.25` (V1 dual pin target) | ✓ |
| down-02 | `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/open_questions.md` (text-edit) | ✓ |
| down-03 | `tests/test_neg_risk_passthrough.py:66-83` (V1 antibody pattern model) | ✓ |
| down-04 | `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/plan.md §3.2 slice 0.A` (text-edit) | ✓ |
| down-05 | `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/v2_system_impact_report.md` (REWRITE-PENDING) | ✓ |
| down-06 | `src/main.py:336-369` (existing _write_heartbeat block; V2-heartbeat extends; os._exit at :369 correction) | ✓ |
| down-06 | `src/main.py:579 scheduler.add_job` (sibling-position for V2-heartbeat) | ✓ |
| down-07 | `src/data/polymarket_client.py:266-275` (USDC balance + redemption helpers; pUSD rewire) | ✓ |
| down-07 | `src/main.py:379` (startup wallet check log) | ✓ |
| down-07 | `src/execution/harvester.py:1244-1264` (T2-G redemption path) | ✓ |
| down-07 | pUSD address `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` | ✓ Q-NEW-1 RE-VERIFIED FRESH 2026-04-26 R2L2 turn-2 |

## Cross-region asks (no new for R2L2)

R1L1 cross-region asks (X-UD-1 → X4 fold, X-MD-1 → X1 fold, X-UM-1 → X3 fold, F-003→Mid, F-009→Down, F-011→Up, UNKNOWN_STATUS→Mid) all remain in their R1L1 routings; no new asks emerge from L2.

## L3 deferred (cleanly scoped)

- down-01.yaml seam citation in-flight edit: proponent-down commits during turn-2 (matches Mid R3L3 protocol)
- Q-NEW-1 re-verification cadence: fresh re-verify each region-resolution; subsequent re-verifications can be operator-on-demand or proxy-upgrade-detection-monitored
- Q-FX-1 evidence file path: locked at `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/evidence/q-fx-1_classification_decision_2026-04-26.md`; operator owns content; engineering owns gate
- B2/B4/B5 packet creation: separate operational packet, not ultimate_plan scope; cross-pointer at up-04 notes
- down-06 activation gating: stays D2-gated until Q-HB cadence cite landed

## Closure conditions met

- 2 turns each direction (≥2 required) ✓
- All 6 L2 OPEN closed ✓
- Q-NEW-1 RE-VERIFIED FRESH in R2L2 run ✓
- A2 disk-state-lie fixed in-flight (down-01 yaml seam citation) ✓
- A3 grep-evidenced absence proofs for 3 negative-test rows ✓
- A4 single-tombstone-per-file invariant + positive+negative antibody pair ✓
- A5 B2/B4/B5 explicit "Wave-2 follow-up packet" routing ✓
- Joint convergence text ≤200 chars to be sent to judge ✓

L2 closed pending judge accept. Standing by for L3 dispatch.
