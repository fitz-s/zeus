"""Clock certificate helpers."""

from __future__ import annotations

from datetime import datetime

from src.decision_kernel import claims
from src.decision_kernel.certificate import DecisionCertificate, build_certificate
from src.decision_kernel.modes import CertificateMode


def build_clock_mode_certificate(
    *,
    mode: CertificateMode,
    decision_time: datetime,
    agent_runtime_id: str,
    clock_source: str = "reactor_decision_time",
    replay_run_id: str | None = None,
) -> DecisionCertificate:
    return build_certificate(
        certificate_type=claims.CLOCK_MODE,
        semantic_key=f"clock:{mode}:{decision_time.isoformat()}:{agent_runtime_id}",
        claim_type="clock_mode",
        mode=mode,
        decision_time=decision_time,
        source_available_at=decision_time,
        agent_received_at=decision_time,
        persisted_at=decision_time,
        payload={
            "mode": mode,
            "decision_time": decision_time,
            "clock_source": clock_source,
            "agent_runtime_id": agent_runtime_id,
            "replay_run_id": replay_run_id,
            "live_persist_required": mode in {"LIVE", "NO_SUBMIT"},
        },
        authority_id="zeus.reactor.clock",
        authority_version="v1",
        algorithm_id="decision_kernel.clock_mode",
        algorithm_version="v1",
    )
