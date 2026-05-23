# WAVE-3 Batch A — Critic Verdict

**Critic**: opus, fresh context (a5c74157cf60f26ce)
**Branch**: `fix/wave3-batch-a-pr-i5b-adapter-ctor-2026-05-18` @ HEAD `3686da1272`
**Diff**: 4 files, +220/−2 LOC

## VERDICT: APPROVE-FOR-PR-OPEN

## Severity histogram (10 probes)
- PASS: 9 (probes 1, 2, 3, 4, 5, 7-corrected, 8, 9-acceptable, 10)
- MINOR: 1 (probe 6)
- CRITICAL: 0
- FAIL: 0

## MINOR (probe 6) — antibody coverage gap, mitigated upstream
Antibody doesn't exercise partial-creds dict (signer_key present, funder_address missing). However, `_resolve_credentials` raises at the source if either field is missing — partial-creds cannot reach the cycle in the first place. Acceptable; antibody covers the call-site contract, source-side helper covers the field-completeness contract.

## Independent verifications performed
- Diff (4 files, +220/−2 LOC) read end-to-end
- Adapter-construction byte-parity with `_ensure_v2_adapter` (line 311 ↔ line 260): identical 8 kwargs
- Fail-closed order-of-ops: `get_mode()` check → `resolve_polymarket_credentials()` → raise, BEFORE `acquire_lock` / `get_trade_connection` / adapter ctor
- Stub at `polymarket_v2_adapter.py:714` still `REDEEM_DEFERRED_TO_R1` (unchanged)
- Critic-side sed-break: removed live-mode guard → antibody-3 fails with AssertionError at line 144 (confirmed catches regression); main.py restored
- Pre-existing failures: 2 failed / 18 passed IDENTICAL on origin/main and HEAD — not regressions
- Implementer's "citation rot" disclosure (claim that PR_I5 doc cited a non-existent _settlement_submitter_cycle) is FALSE: doc cites _redeem_submitter_cycle correctly throughout
- Zero remaining PolymarketV2Adapter() no-arg sites in src/ or scripts/

## Karachi safety
Stub unchanged. Operator CLI path remains only completion route. No blast.
