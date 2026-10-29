import builtins

import main as workflow_main


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


def prepare_project(tmp_path, monkeypatch):
    snapshot = approved_snapshot()
    architecture_path = tmp_path / "pilot" / "architecture_design.md"
    architecture_path.parent.mkdir(parents=True)
    architecture_path.write_text(
        snapshot["architecture"],
        encoding="utf-8",
    )

    monkeypatch.setattr(
        workflow_main,
        "load_approved_context",
        lambda _path: snapshot,
    )

    return snapshot


class FakeOrchestrator:
    def __init__(self, h1_decision="approve"):
        self.h1_decision = h1_decision
        self.workflow_state = {
            "backlog_approved": False,
            "architecture_approved": False,
            "tasks": {},
        }

    def request_backlog_approval(self, _plan, tasks):
        if self.h1_decision == "approve":
            self.workflow_state["backlog_approved"] = True
            for task in tasks:
                self.workflow_state["tasks"][task["task_id"]][
                    "status"
                ] = "approved"
        return self.h1_decision


def test_h1_rejection_stops_before_h2_and_patch(tmp_path, monkeypatch):
    prepare_project(tmp_path, monkeypatch)
    orchestrator = FakeOrchestrator(h1_decision="reject")
    h2_calls = []
    patch_calls = []

    monkeypatch.setattr(
        workflow_main,
        "review_and_approve_architecture",
        lambda **kwargs: h2_calls.append(kwargs) or True,
    )
    monkeypatch.setattr(
        workflow_main,
        "create_t1_backend_patch",
        lambda **kwargs: patch_calls.append(kwargs),
    )

    result = workflow_main.run_resume_patch(tmp_path, orchestrator)

    assert result is None
    assert h2_calls == []
    assert patch_calls == []


def test_h2_rejection_stops_before_patch(tmp_path, monkeypatch):
    prepare_project(tmp_path, monkeypatch)
    orchestrator = FakeOrchestrator(h1_decision="approve")
    patch_calls = []

    monkeypatch.setattr(
        workflow_main,
        "review_and_approve_architecture",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(
        workflow_main,
        "create_t1_backend_patch",
        lambda **kwargs: patch_calls.append(kwargs),
    )

    result = workflow_main.run_resume_patch(tmp_path, orchestrator)

    assert result is None
    assert orchestrator.workflow_state["backlog_approved"] is True
    assert patch_calls == []


def test_patch_generation_requires_both_approvals(
    tmp_path,
    monkeypatch,
):
    snapshot = prepare_project(tmp_path, monkeypatch)
    orchestrator = FakeOrchestrator(h1_decision="approve")
    patch_calls = []

    def approve_h2(**_kwargs):
        orchestrator.workflow_state["architecture_approved"] = True
        return True

    def create_patch(**kwargs):
        patch_calls.append(kwargs)
        return tmp_path / "proposal.patch", tmp_path / "report.json"

    monkeypatch.setattr(
        workflow_main,
        "review_and_approve_architecture",
        approve_h2,
    )
    monkeypatch.setattr(
        workflow_main,
        "create_t1_backend_patch",
        create_patch,
    )
    monkeypatch.setattr(
        builtins,
        "input",
        lambda _prompt: "yes",
    )

    result = workflow_main.run_resume_patch(tmp_path, orchestrator)

    assert result == (
        tmp_path / "proposal.patch",
        tmp_path / "report.json",
    )
    assert orchestrator.workflow_state["backlog_approved"] is True
    assert orchestrator.workflow_state["architecture_approved"] is True
    assert patch_calls[0]["tasks"] == snapshot["tasks"]
    assert patch_calls[0]["architecture"] == snapshot["architecture"]