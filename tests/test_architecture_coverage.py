import pytest

from agents.software_architect import SoftwareArchitectAgent


@pytest.fixture
def tasks():
    return [
        {"task_id": "TASK-01"},
        {"task_id": "TASK-02"},
        {"task_id": "TASK-03"},
    ]


def test_all_expected_task_ids_are_present(tasks):
    architecture = """
    Task mapping:
    TASK-01 handles uploads.
    TASK-02 analyzes content.
    TASK-03 generates practice exams.
    """

    report = SoftwareArchitectAgent.validate_task_coverage(
        architecture, tasks
    )

    assert report["valid"] is True
    assert report["missing_ids"] == []
    assert report["unexpected_ids"] == []


def test_missing_task_id_fails_coverage(tasks):
    architecture = """
    TASK-01 handles uploads.
    TASK-03 generates practice exams.
    """

    report = SoftwareArchitectAgent.validate_task_coverage(
        architecture, tasks
    )

    assert report["valid"] is False
    assert report["missing_ids"] == ["TASK-02"]


def test_unexpected_task_id_fails_coverage(tasks):
    architecture = """
    TASK-01 handles uploads.
    TASK-02 analyzes content.
    TASK-03 generates practice exams.
    TASK-99 is not in the approved backlog.
    """

    report = SoftwareArchitectAgent.validate_task_coverage(
        architecture, tasks
    )

    assert report["valid"] is False
    assert report["unexpected_ids"] == ["TASK-99"]


def test_repeated_references_do_not_invalidate_coverage(tasks):
    architecture = """
    Task mapping:
    TASK-01 handles uploads.
    TASK-02 analyzes content.
    TASK-03 generates practice exams.

    Component overview:
    TASK-01 is also mentioned in the component overview.
    """

    report = SoftwareArchitectAgent.validate_task_coverage(
        architecture, tasks
    )

    assert report["valid"] is True
    assert report["missing_ids"] == []
    assert report["unexpected_ids"] == []
    assert report["duplicated_ids"] == []




def test_duplicate_ids_in_input_tasks_are_rejected():
    tasks = [
        {"task_id": "TASK-01"},
        {"task_id": "TASK-01"},
    ]

    with pytest.raises(ValueError, match="duplicate task IDs"):
        SoftwareArchitectAgent.validate_task_coverage(
            "TASK-01 is covered.", tasks
        )