---
name: provenance-audit
description: Use before reusing, extending, or trusting ANY existing Zeus code on money-path, ingest, calibration, settlement, deploy, scripts, or tests surfaces. Existing code is LEGACY until audited against current law — code that runs is not code that is safe to reuse. Also use when a helper "looks fine" but predates a law change.
---

# Provenance audit

Zeus law changes faster than its code. A helper that was correct months ago may now silently violate a new invariant, settlement rule, or data contract. Code correctness does NOT imply assumption currency.

## Checklist (all steps, in order)

1. **Provenance**: Read the file end to end. `git log --follow -5 -- <file>` for last-modified date and commit context. Identify which law regime it was written under (compare its date against the relevant `docs/authority/**` doc dates and `architecture/invariants.yaml` entries).
2. **Currency**: Check its assumptions against current law — invariant IDs (INV-##), type/enum contracts, `data_version` rules, DB table ownership (`architecture/db_table_ownership.yaml`), settlement semantics (`SettlementSemantics`), lifecycle enum, HIGH/LOW track separation.
3. **Verdict** (exactly one):
   - `CURRENT_REUSABLE` — assumptions verified current; safe to reuse as-is.
   - `STALE_REWRITE` — core logic sound, assumptions drifted; rewrite before reuse.
   - `DEAD_DELETE` — no current caller or purpose; delete via registry route.
   - `QUARANTINED` — violates current law but load-bearing; isolate, do not extend.
4. **Log the verdict** in-repo (evidence doc, commit message, or inline comment) so the next session skips re-auditing.

## File-header contract

Every new or substantially-touched script (`scripts/*.py`, top-level utilities) and every test file (`tests/test_*.py`) carries:

```python
# Created: YYYY-MM-DD
# Last audited: YYYY-MM-DD
# Authority basis: <law doc / Phase tag / spec ref>
```

Update `Last audited` when reusing or re-auditing. A file without date + authority basis is unaudited by definition.

## Why this exists

Running code proves only that it ran, not that its assumptions match current law — Zeus law changes faster than its code, so a passing test today can rest on an invariant that no longer holds. The audit is near-zero-cost now and expensive to skip later. When in doubt: audit.
