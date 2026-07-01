import logging


def test_entries_paused_reason_is_registered_transient(caplog):
    from src.events.reactor import (
        TRANSIENT_MONEY_PATH_REASONS,
        _is_transient_money_path_reason,
    )

    reason = "entries_paused:operator_pause_live_bad_entry_tokyo_005_yes_until_root_fix"
    with caplog.at_level(logging.ERROR, logger="zeus.events.reactor"):
        assert "entries_paused" in TRANSIENT_MONEY_PATH_REASONS
        assert _is_transient_money_path_reason(reason) is True

    assert not any("UNKNOWN money-path reason" in record.message for record in caplog.records)
