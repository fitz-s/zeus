# tests/contracts AGENTS

Spec-owned validation manifests for contract enforcement testing.

## File registry

| File | Purpose |
|------|---------|
| `__init__.py` | Package marker |
| `spec_validation_manifest.py` | Validation manifest — defines which spec claims are testable and their test mappings |

## Rules

- These manifests are owned by the spec, not by individual tests
- Changes here should trace to a specific invariant or architecture contract
- The manifest defines what is tested — individual test files implement the tests
