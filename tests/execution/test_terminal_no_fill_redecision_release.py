from src.execution import executor
from src.state import venue_command_repo


def test_submit_terminal_no_fill_can_release_entry_duplicate_lock() -> None:
    assert venue_command_repo._TRANSITIONS[("SUBMITTING", "EXPIRED")] == "EXPIRED"
    assert "EXPIRED" not in executor._ENTRY_DUPLICATE_OPEN_COMMAND_STATES


def test_review_required_still_blocks_ambiguous_entry_duplicate_lock() -> None:
    assert venue_command_repo._TRANSITIONS[("SUBMITTING", "REVIEW_REQUIRED")] == "REVIEW_REQUIRED"
    assert "REVIEW_REQUIRED" in executor._ENTRY_DUPLICATE_OPEN_COMMAND_STATES
