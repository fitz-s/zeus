# Lifecycle: created=2026-05-16; last_reviewed=2026-05-16; last_reused=never
# Purpose: scripts.migrations package init — enables importlib-spec loading
#   of digit-prefixed migration scripts (e.g. 202605_add_redeem_operator_required_state.py)
#   by tests/test_migration_redeem_operator_required.py.
# Reuse: Add new migration scripts as siblings (date-prefixed); each carries
#   its own freshness header + tests.
