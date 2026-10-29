
import pytest
from agents.orchestrator import OrchestratorAgent



def test_valid_tasks():
    tasks = [
        {
            "task_id": "T1",
            "title": "Create data model",
            "description": "Define the initial application data model.",
            "acceptance_criteria": [
                "Required entities are defined.",
                "Relationships are documented.",
            ],
            "dependencies": [],
        },
        {
            "task_id": "T2",
            "title": "Implement data service",
            "description": "Implement the service using the approved data model.",
            "acceptance_criteria": [
                "The service supports the required operations.",
                "Tests cover the required operations.",
            ],
            "dependencies": ["T1"],
        },
    ]

    assert OrchestratorAgent.validate_tasks(tasks) is True
    print("PASS: Valid tasks and dependencies.")


def test_unknown_dependency():
    tasks = [
        {
            "task_id": "T1",
            "title": "Create data model",
            "description": "Define the application data model.",
            "acceptance_criteria": ["The model is defined."],
            "dependencies": ["T99"],
        },
    ]

    try:
        OrchestratorAgent.validate_tasks(tasks)
    except ValueError as error:
        assert "unknown dependencies" in str(error)
        print("PASS: Unknown dependencies are rejected.")
    else:
        raise AssertionError("Unknown dependency was not rejected.")


def test_circular_dependency():
    tasks = [
        {
            "task_id": "T1",
            "title": "Task one",
            "description": "Complete the first task.",
            "acceptance_criteria": ["Task one is complete."],
            "dependencies": ["T2"],
        },
        {
            "task_id": "T2",
            "title": "Task two",
            "description": "Complete the second task.",
            "acceptance_criteria": ["Task two is complete."],
            "dependencies": ["T1"],
        },
    ]

    try:
        OrchestratorAgent.validate_tasks(tasks)
    except ValueError as error:
        assert "circular dependencies" in str(error)
        print("PASS: Circular dependencies are rejected.")
    else:
        raise AssertionError("Circular dependencies were not rejected.")


def test_duplicate_task_ids():
    tasks = [
        {
            "task_id": "T1",
            "title": "First task",
            "description": "First task description.",
            "acceptance_criteria": ["First task is complete."],
            "dependencies": [],
        },
        {
            "task_id": "T1",
            "title": "Second task",
            "description": "Second task description.",
            "acceptance_criteria": ["Second task is complete."],
            "dependencies": [],
        },
    ]

    try:
        OrchestratorAgent.validate_tasks(tasks)
    except ValueError as error:
        assert "Duplicate task ID" in str(error)
        print("PASS: Duplicate task IDs are rejected.")
    else:
        raise AssertionError("Duplicate task IDs were not rejected.")


if __name__ == "__main__":
    test_valid_tasks()
    test_unknown_dependency()
    test_circular_dependency()
    test_duplicate_task_ids()
    print("\nAll Orchestrator validation tests passed.")

def test_generated_task_ids_accept_sequential_t_ids():
    tasks = [
        {"task_id": "T1"},
        {"task_id": "T2"},
        {"task_id": "T3"},
    ]

    assert OrchestratorAgent.validate_generated_task_ids(tasks) is True


def test_generated_task_ids_reject_work_item_ids():
    tasks = [{"task_id": "Work Item 1.1"}]

    with pytest.raises(ValueError, match="expected T1"):
        OrchestratorAgent.validate_generated_task_ids(tasks)


def test_generated_task_ids_reject_gaps():
    tasks = [
        {"task_id": "T1"},
        {"task_id": "T3"},
    ]

    with pytest.raises(ValueError, match="expected T2"):
        OrchestratorAgent.validate_generated_task_ids(tasks)
