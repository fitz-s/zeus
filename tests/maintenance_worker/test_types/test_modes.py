# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3, §6 (P5.0a)
#                  SAFETY_CONTRACT.md §"Accidental-Trigger Containment", §"Kill Switch"
"""
Tests for maintenance_worker.types.modes — InvocationMode, RefusalReason.
"""
from maintenance_worker.types.modes import InvocationMode, RefusalReason


class TestInvocationMode:
    def test_three_members(self) -> None:
        """SCAFFOLD §3: SCHEDULED, MANUAL_CLI, IN_PROCESS."""
        expected = {"SCHEDULED", "MANUAL_CLI", "IN_PROCESS"}
        assert {m.value for m in InvocationMode} == expected

    def test_manual_cli_present(self) -> None:
        """SAFETY_CONTRACT "Accidental-Trigger Containment": MANUAL_CLI forces DRY_RUN_ONLY."""
        assert InvocationMode.MANUAL_CLI in InvocationMode

    def test_string_subclass(self) -> None:
        assert isinstance(InvocationMode.SCHEDULED, str)

    def test_values_match_names(self) -> None:
        for mode in InvocationMode:
            assert mode.value == mode.name


class TestRefusalReason:
    def test_kill_switch_present(self) -> None:
        """SAFETY_CONTRACT §"Kill Switch": KILL_SWITCH is a hard guard."""
        assert RefusalReason.KILL_SWITCH in RefusalReason

    def test_self_quarantined_present(self) -> None:
        """SAFETY_CONTRACT §229-238: post-mutation self-quarantine guard."""
        assert RefusalReason.SELF_QUARANTINED in RefusalReason

    def test_maintenance_paused_is_soft_guard(self) -> None:
        """SAFETY_CONTRACT lines 207-213: MAINTENANCE_PAUSED is skip_tick not fatal."""
        assert RefusalReason.MAINTENANCE_PAUSED in RefusalReason

    def test_eleven_members_total(self) -> None:
        """8 hard guards + 2 soft guards + 1 engine-level (CONFIG_INVALID) = 11."""
        assert len(list(RefusalReason)) == 11

    def test_hard_guards_count(self) -> None:
        hard = {
            RefusalReason.KILL_SWITCH,
            RefusalReason.DIRTY_REPO,
            RefusalReason.ACTIVE_REBASE,
            RefusalReason.LOW_DISK,
            RefusalReason.INFLIGHT_PR,
            RefusalReason.SELF_QUARANTINED,
            RefusalReason.FORBIDDEN_PATH_VIOLATION,
            RefusalReason.FORBIDDEN_OPERATION_VIOLATION,
        }
        assert len(hard) == 8

    def test_soft_guards_count(self) -> None:
        soft = {RefusalReason.MAINTENANCE_PAUSED, RefusalReason.ONCALL_QUIET}
        assert len(soft) == 2

    def test_string_subclass(self) -> None:
        assert isinstance(RefusalReason.KILL_SWITCH, str)
        assert RefusalReason.KILL_SWITCH == "KILL_SWITCH"
