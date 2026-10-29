import pytest
from pydantic import ValidationError

from agents.agent_contracts import (
    BackendDeveloperResult,
    BackendDeveloperModelResponse,
    TestCheckResult,
    TestingAgentResult,
)


def valid_backend_result():
    return {
        "task_id": "T1",
        "summary": "Proposed the FastAPI application entry point.",
        "patch": (
            "--- /dev/null\n"
            "+++ b/pilot/app/backend/main.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+from fastapi import FastAPI\n"
            "+app = FastAPI()\n"
        ),
        "changed_files": [
            {
                "path": "pilot/app/backend/main.py",
                "purpose": "Creates the FastAPI application.",
            }
        ],
        "acceptance_criteria": [
            {
                "criterion": "Backend framework is selected.",
                "status": "addressed",
                "evidence": "The proposed patch uses FastAPI.",
            },
            {
                "criterion": "Frontend framework is selected.",
                "status": "not_addressed",
                "evidence": "Deferred to the frontend implementation slice.",
            },
        ],
        "tests_added": ["tests/test_backend_app.py"],
        "assumptions": [],
        "unresolved_issues": [],
    }


def test_backend_result_accepts_structured_report():
    result = BackendDeveloperResult.model_validate(valid_backend_result())

    assert result.task_id == "T1"
    assert result.patch.startswith("--- /dev/null")
    assert result.changed_files[0].path == "pilot/app/backend/main.py"
    assert result.acceptance_criteria[1].status == "not_addressed"


def test_backend_result_rejects_missing_task_id():
    report = valid_backend_result()
    del report["task_id"]

    with pytest.raises(ValidationError):
        BackendDeveloperResult.model_validate(report)


def test_backend_result_rejects_unknown_criteria_status():
    report = valid_backend_result()
    report["acceptance_criteria"][0]["status"] = "complete"

    with pytest.raises(ValidationError):
        BackendDeveloperResult.model_validate(report)


def test_test_check_accepts_execution_evidence():
    check = TestCheckResult.model_validate(
        {
            "check_name": "Backend unit tests",
            "command": "python -m pytest tests/test_backend_app.py -q",
            "passed": True,
            "exit_code": 0,
            "output": "3 passed",
        }
    )

    assert check.passed is True
    assert check.exit_code == 0


def test_test_check_rejects_string_instead_of_boolean():
    with pytest.raises(ValidationError):
        TestCheckResult.model_validate(
            {
                "check_name": "Backend unit tests",
                "command": "python -m pytest",
                "passed": "true",
                "exit_code": 0,
                "output": "3 passed",
            }
        )

def test_backend_model_response_accepts_proposed_file_contents():
    response = BackendDeveloperModelResponse.model_validate(
        {
            "task_id": "T1",
            "summary": "Proposes the initial FastAPI application.",
            "proposed_files": [
                {
                    "path": "pilot/app/backend/main.py",
                    "purpose": "Creates the FastAPI application.",
                    "content": "from fastapi import FastAPI\napp = FastAPI()\n",
                }
            ],
            "acceptance_criteria": [
                {
                    "criterion": "The backend framework is selected.",
                    "status": "addressed",
                    "evidence": "The proposed file creates a FastAPI application.",
                }
            ],
            "tests_added": [],
            "assumptions": [],
            "unresolved_issues": [],
        }
    )

    assert response.proposed_files[0].path == "pilot/app/backend/main.py"
    assert "FastAPI" in response.proposed_files[0].content


def test_backend_model_response_rejects_missing_file_content():
    with pytest.raises(ValidationError):
        BackendDeveloperModelResponse.model_validate(
            {
                "task_id": "T1",
                "summary": "Proposed backend change.",
                "proposed_files": [
                    {
                        "path": "pilot/app/backend/main.py",
                        "purpose": "Creates the backend application.",
                    }
                ],
                "acceptance_criteria": [
                    {
                        "criterion": "The backend framework is selected.",
                        "status": "addressed",
                        "evidence": "FastAPI is selected.",
                    }
                ],
            }
        )


def test_backend_model_response_rejects_unknown_fields():
    response = {
        "task_id": "T1",
        "summary": "Proposed backend change.",
        "proposed_files": [
            {
                "path": "pilot/app/backend/main.py",
                "purpose": "Creates the backend application.",
                "content": "app = object()\n",
            }
        ],
        "acceptance_criteria": [
            {
                "criterion": "The backend framework is selected.",
                "status": "addressed",
                "evidence": "The proposed file creates an app.",
            }
        ],
        "unapproved_action": "write files directly",
    }

    with pytest.raises(ValidationError):
        BackendDeveloperModelResponse.model_validate(response)


def test_testing_agent_result_accepts_execution_evidence():
    result = TestingAgentResult.model_validate(
        {
            "task_id": "T1",
            "summary": "Ran the selected backend checks.",
            "checks": [
                {
                    "check_name": "Backend unit tests",
                    "command": "python -m pytest tests/test_backend_app.py -q",
                    "passed": True,
                    "exit_code": 0,
                    "output": "3 passed",
                }
            ],
        }
    )

    assert result.task_id == "T1"
    assert result.checks[0].passed is True
    assert result.checks[0].exit_code == 0


def test_testing_agent_result_rejects_empty_check_list():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TestingAgentResult.model_validate(
            {
                "task_id": "T1",
                "summary": "No checks were run.",
                "checks": [],
            }
        )
