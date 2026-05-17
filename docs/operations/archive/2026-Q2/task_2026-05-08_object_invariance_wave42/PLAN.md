# Wave42 Plan — Harvester Corrected Economics Fail-Closed Guard

Status: APPROVED

## Scope

Repair one D3 downstream boundary:

`position read model -> harvester settlement P&L -> settlement record / strategy tracker`

The scoped invariant is: a position that declares corrected executable
economics must not fall back to legacy `size_usd / entry_price` settlement P&L
when venue-confirmed fill economics are absent.

## Evidence

- `_settlement_economics_for_position()` already rejects explicit non-fill
  authorities such as `submitted_limit`.
- A remaining ambiguity exists when corrected markers are present but the row's
  entry/fill authority fields are missing or `legacy_unknown`.
- Legacy fallback may remain for unclassified legacy rows, but corrected rows
  must fail closed rather than be settled with ambiguous floats.

## Repair

1. Import the corrected executable pricing version marker into harvester.
2. Reject corrected-marked positions without fill authority.
3. Add a relationship test proving corrected-marked legacy/unknown rows cannot
   produce settlement economics, while fill-authoritative rows still pass.

## Verification

- Focused `tests/test_runtime_guards.py` harvester settlement-economics slice.
  `2 passed`.
- `py_compile` for touched source/test files.
  Pass.
- `git diff --check` for touched files.
  Pass.
- Planning-lock/map-maintenance after patch.
  Pass.
- Critic `APPROVE`: corrected-marked/no-fill rows now fail closed before
  settlement close, settlement records, and strategy tracker writes; unclassified
  legacy fallback is preserved.

## Non-Scope

- No schema migration.
- No historical row relabeling.
- No canonical DB mutation.
- No settlement harvest execution.
- No live venue/account side effects.
