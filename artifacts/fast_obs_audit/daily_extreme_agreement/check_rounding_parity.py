"""Assert the venv-fallback rounding equals the production WMO half-up function on a dense grid incl. negatives and ties."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from src.contracts.settlement_semantics import SettlementSemantics, round_wmo_half_up_value

grid = [k / 10.0 for k in range(-600, 601)] + [k / 2.0 for k in range(-80, 81)]
fallback = lambda v: float(math.floor(float(v) + 0.5))
bad = [v for v in grid if fallback(v) != round_wmo_half_up_value(v)]
sem = SettlementSemantics(resolution_source="x", measurement_unit="C", precision=1.0, rounding_rule="wmo_half_up", finalization_time="12:00:00Z")
bad2 = [v for v in grid if sem.round_single(v) != round_wmo_half_up_value(v)]
print("grid", len(grid), "fallback mismatches", len(bad), "round_single mismatches", len(bad2))
print("ties:", {v: round_wmo_half_up_value(v) for v in (-2.5, -1.5, -0.5, 0.5, 1.5, 14.5, 15.5)})
assert not bad and not bad2
