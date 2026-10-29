import pytest

from agents.orchestrator import OrchestratorAgent


def make_orchestrator_without_initialization():
    # This method does not need Gemini or a live workflow.
    return OrchestratorAgent.__new__(OrchestratorAgent)


def test_passing_checks_make_result_ready_for_review_not_h3_approved():
    agent = make_orchestrator_without_initialization()
    approval_calls = []
    agent.record_approval = lambda *args: approval_calls.append(args)

    task = {"task_id": "T1"}
    result = {"summary": "Backend slice is implemented."}
    checks = [
        {
            "check_name": "unit tests",
            "command": "python -m pytest",
            "passed": True,
            "exit_code": 0,
            "output": "3 passed",
        }
    ]

    decision = agent.evaluate_agent_result(task, result, checks)

    assert decision["task_id"] == "T1"
    assert decision["result"] == result
    assert decision["checks_passed"] == checks
    assert decision["failed_checks"] == []
    assert decision["decision"] == "ready_for_review"
    assert approval_calls == []


def test_failed_check_requires_revision():
    agent = make_orchestrator_without_initialization()
    failed_check = {
        "check_name": "unit tests",
        "command": "python -m pytest",
        "passed": False,
        "exit_code": 1,
        "output": "1 failed",
    }

    decision = agent.evaluate_agent_result(
        {"task_id": "T1"},
        {"summary": "Backend slice needs work."},
        [failed_check],
    )

    assert decision["failed_checks"] == [failed_check]
    assert decision["decision"] == "needs_revision"


def test_malformed_check_is_treated_as_failed():
    agent = make_orchestrator_without_initialization()
    checks = [
        None,
        {"check_name": "unit tests", "passed": "true"},
    ]

    decision = agent.evaluate_agent_result(
        {"task_id": "T1"},
        {"summary": "Result with malformed checks."},
        checks,
    )

    assert decision["failed_checks"] == checks
    assert decision["decision"] == "needs_revision"


def test_checks_passed_must_be_a_list():
    agent = make_orchestrator_without_initialization()

    with pytest.raises(ValueError, match="checks_passed must be a list"):
        agent.evaluate_agent_result(
            {"task_id": "T1"},
            {"summary": "Result"},
            None,
        )