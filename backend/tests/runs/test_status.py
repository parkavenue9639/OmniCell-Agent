from __future__ import annotations

import pytest

from omnicell_agent.runs.status import (
    InvalidRunTransitionError,
    RunStatus,
    is_terminal_run_status,
    validate_run_transition,
)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (RunStatus.PENDING, RunStatus.RUNNING),
        (RunStatus.RUNNING, RunStatus.REVIEW_REQUIRED),
        (RunStatus.REVIEW_REQUIRED, RunStatus.RUNNING),
        (RunStatus.RUNNING, RunStatus.COMPLETED),
        (RunStatus.CANCELLING, RunStatus.CANCELLED),
    ],
)
def test_valid_run_transitions(source: RunStatus, target: RunStatus) -> None:
    validate_run_transition(source, target)


def test_terminal_run_transition_is_idempotent_but_irreversible() -> None:
    validate_run_transition(RunStatus.COMPLETED, RunStatus.COMPLETED)
    assert is_terminal_run_status(RunStatus.COMPLETED)
    with pytest.raises(InvalidRunTransitionError):
        validate_run_transition(RunStatus.COMPLETED, RunStatus.RUNNING)


def test_unknown_run_status_fails_closed() -> None:
    with pytest.raises(ValueError):
        validate_run_transition("invented", RunStatus.RUNNING)
