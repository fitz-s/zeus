# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3, §6 (P5.0a)
#                  SAFETY_CONTRACT.md §(a) "READ is not exempt", line 100 MKDIR explicit
"""
Tests for maintenance_worker.types.operations.Operation enum.

SCAFFOLD §6 verdict: PASS if Operation.READ is a member with correct
forbidden-path coverage verifiable by automated test.
"""
from maintenance_worker.types.operations import Operation


class TestOperationMembers:
    def test_all_eight_members_present(self) -> None:
        """SCAFFOLD §3: exactly 8 operation kinds."""
        expected = {
            "READ", "WRITE", "MKDIR", "MOVE", "DELETE",
            "GIT_EXEC", "GH_EXEC", "SUBPROCESS_EXEC",
        }
        actual = {op.value for op in Operation}
        assert actual == expected

    def test_read_is_member(self) -> None:
        """SAFETY_CONTRACT §(a): READ not exempt — must exist in enum."""
        assert Operation.READ in Operation

    def test_mkdir_is_distinct_from_write(self) -> None:
        """SAFETY_CONTRACT line 100: MKDIR is explicit, not subsumed under WRITE."""
        assert Operation.MKDIR is not Operation.WRITE
        assert Operation.MKDIR.value == "MKDIR"

    def test_string_values_match_names(self) -> None:
        """String-valued enum: value equals name for logging transparency."""
        for op in Operation:
            assert op.value == op.name

    def test_operation_is_string_subclass(self) -> None:
        """str Enum: usable directly in log strings without .value."""
        assert isinstance(Operation.READ, str)
        assert Operation.GIT_EXEC == "GIT_EXEC"

    def test_git_exec_and_gh_exec_distinct(self) -> None:
        """Git and GH operations have separate enum values for guard routing."""
        assert Operation.GIT_EXEC is not Operation.GH_EXEC
        assert Operation.GIT_EXEC is not Operation.SUBPROCESS_EXEC

    def test_delete_distinct_from_move(self) -> None:
        """DELETE and MOVE must be distinct; agent uses different code paths."""
        assert Operation.DELETE is not Operation.MOVE
