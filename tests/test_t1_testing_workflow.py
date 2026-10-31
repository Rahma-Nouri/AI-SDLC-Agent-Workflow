import json

import main as workflow_main
from agents.orchestrator import OrchestratorAgent


def approved_snapshot():
    return {
        "schema_version": 1,
        "plan": "Approved plan for the pilot.",
        "tasks": [
            {
                "task_id": "T1",
                "title": "Set up the backend",
                "description": "Create the first backend slice.",
                "acceptance_criteria": ["The backend slice is defined."],
                "dependencies": [],
            }
        ],
        "architecture": "# Architecture for T1",
        "saved_at": "test timestamp",
    }


def prepare_project(tmp_path, monkeypatch, test_body):
    """Create a tiny project folder with an architecture file and a T1 test."""

    snapshot = approved_snapshot()

    architecture_path = tmp_path / "pilot" / "architecture_design.md"
    architecture_path.parent.mkdir(parents=True)
    architecture_path.write_text(snapshot["architecture"], encoding="utf-8")

    test_path = tmp_path / "tests" / "test_backend_database_schema.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(test_body, encoding="utf-8")

    monkeypatch.setattr(
        workflow_main,
        "load_approved_context",
        lambda _path: snapshot,
    )


def make_orchestrator():
    """Use the real gate logic without calling Gemini or needing an API key."""

    orchestrator = OrchestratorAgent.__new__(OrchestratorAgent)
    orchestrator.workflow_state = {
        "backlog_approved": False,
        "architecture_approved": False,
        "tasks": {},
        "approval_history": [],
    }

    # Simulate a human approving H1 at the prompt.
    def approve_h1(_plan, tasks):
        orchestrator.workflow_state["backlog_approved"] = True
        for task in tasks:
            orchestrator.workflow_state["tasks"][task["task_id"]][
                "status"
            ] = "approved"
        return "approve"

    orchestrator.request_backlog_approval = approve_h1
    return orchestrator


def approve_h2(orchestrator):
    def review(**_kwargs):
        orchestrator.record_approval("H2", "approve")
        return True

    return review


def test_passing_t1_tests_are_saved_as_ready_for_review(tmp_path, monkeypatch):
    prepare_project(tmp_path, monkeypatch, "def test_ok():\n    assert True\n")
    orchestrator = make_orchestrator()
    monkeypatch.setattr(
        workflow_main,
        "review_and_approve_architecture",
        approve_h2(orchestrator),
    )

    result_path = workflow_main.run_t1_tests(tmp_path, orchestrator)

    assert result_path.parent == tmp_path / "outputs" / "test_results"
    assert b"\r" not in result_path.read_bytes()

    evidence = json.loads(result_path.read_text(encoding="utf-8"))
    check = evidence["testing_result"]["checks"][0]

    assert evidence["testing_result"]["task_id"] == "T1"
    assert check["exit_code"] == 0
    assert check["passed"] is True
    assert "1 passed" in check["output"]
    assert evidence["review"]["decision"] == "ready_for_review"


def test_failing_t1_tests_are_saved_as_needs_revision(tmp_path, monkeypatch):
    prepare_project(tmp_path, monkeypatch, "def test_bad():\n    assert False\n")
    orchestrator = make_orchestrator()
    monkeypatch.setattr(
        workflow_main,
        "review_and_approve_architecture",
        approve_h2(orchestrator),
    )

    result_path = workflow_main.run_t1_tests(tmp_path, orchestrator)

    evidence = json.loads(result_path.read_text(encoding="utf-8"))
    check = evidence["testing_result"]["checks"][0]

    # A failure is still evidence: it is saved, not hidden.
    assert check["exit_code"] == 1
    assert check["passed"] is False
    assert evidence["review"]["decision"] == "needs_revision"


def test_h2_rejection_stops_before_running_tests(tmp_path, monkeypatch):
    prepare_project(tmp_path, monkeypatch, "def test_ok():\n    assert True\n")
    orchestrator = make_orchestrator()
    monkeypatch.setattr(
        workflow_main,
        "review_and_approve_architecture",
        lambda **_kwargs: False,
    )

    result = workflow_main.run_t1_tests(tmp_path, orchestrator)

    assert result is None
    assert not (tmp_path / "outputs" / "test_results").exists()
