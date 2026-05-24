# Live Inference

EDLI redemption live-inference code is pure math/proof-kernel code. It must not
submit orders, import venue adapters, or read hindsight settlement outcomes.

Rules:
- Orderbook events may affect executable cost evidence, not `q_live`.
- Forecast events must be COMPLETE for live eligibility.
- Day0 hard facts must pass source authority before absorbing-boundary masks.
- Kelly receives typed `ExecutionPrice`, never a bare float.
