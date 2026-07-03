"""Testing protocol certificate constants."""

from __future__ import annotations

ALLOWED_TESTING_PROTOCOL_MODES = frozenset({
    "FIXED_WINDOW_BH",
    "ALPHA_SPENDING",
    "ALWAYS_VALID_PVALUES",
    "NO_FDR_CLAIM",
})

OPTIONAL_STOPPING_VALID_MODES = frozenset({
    "FIXED_WINDOW_BH",
    "ALPHA_SPENDING",
    "ALWAYS_VALID_PVALUES",
})
