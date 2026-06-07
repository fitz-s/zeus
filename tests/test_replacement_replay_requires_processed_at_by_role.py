from dataclasses import replace

import pytest

from tests.test_replacement_replay_veto_only_not_initiation import _row


def test_replay_input_requires_processed_at_by_role() -> None:
    with pytest.raises(ValueError, match="processed_at_by_role"):
        replace(_row(), processed_at_by_role={})
