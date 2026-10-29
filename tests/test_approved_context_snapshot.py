import json

import pytest

from main import save_approved_context
from main import load_approved_context

class ApprovalState:
    def __init__(self, backlog_approved, architecture_approved):
        self.workflow_state = {
            "backlog_approved": backlog_approved,
            "architecture_approved": architecture_approved,
        }


def test_saves_exact_context_after_h1_and_h2(tmp_path):
    orchestrator = ApprovalState(
        backlog_approved=True,
        architecture_approved=True,
    )
    plan = "Approved project plan"
    tasks = [{"task_id": "T1", "title": "Backend setup"}]
    architecture = "# Approved architecture\nFastAPI and SQLite"

    snapshot_path = save_approved_context(
        orchestrator=orchestrator,
        project_root=tmp_path,
        plan=plan,
        tasks=tasks,
        architecture=architecture,
    )

    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

    assert snapshot["plan"] == plan
    assert snapshot["tasks"] == tasks
    assert snapshot["architecture"] == architecture
    assert snapshot["human_approvals"] == {
        "H1": "approved",
        "H2": "approved",
    }
    assert "fresh H1 and H2 approvals" in snapshot["note"]
    assert not snapshot_path.with_suffix(".json.tmp").exists()


@pytest.mark.parametrize(
    ("backlog_approved", "architecture_approved"),
    [
        (False, True),
        (True, False),
        (False, False),
    ],
)
def test_refuses_to_save_without_both_approvals(
    tmp_path,
    backlog_approved,
    architecture_approved,
):
    orchestrator = ApprovalState(
        backlog_approved=backlog_approved,
        architecture_approved=architecture_approved,
    )

    with pytest.raises(RuntimeError):
        save_approved_context(
            orchestrator=orchestrator,
            project_root=tmp_path,
            plan="Plan",
            tasks=[{"task_id": "T1"}],
            architecture="# Architecture",
        )

    assert not (
        tmp_path
        / "outputs"
        / "approved_context"
        / "current_approved_context.json"
    ).exists()


def test_loads_and_validates_saved_context(tmp_path):
    snapshot = {
        "schema_version": 1,
        "plan": "Approved plan",
        "tasks": [
            {
                "task_id": "T1",
                "title": "Build backend foundation",
                "description": "Set up the approved backend foundation.",
                "acceptance_criteria": ["The backend foundation is defined."],
                "dependencies": [],
            }
        ],
        "architecture": "# Approved architecture",
        "human_approvals": {
            "H1": "approved",
            "H2": "approved",
        },
    }
    snapshot_path = tmp_path / "approved_context.json"
    snapshot_path.write_text(
        json.dumps(snapshot),
        encoding="utf-8",
    )

    loaded = load_approved_context(snapshot_path)

    assert loaded["plan"] == "Approved plan"
    assert loaded["tasks"][0]["task_id"] == "T1"
    assert loaded["architecture"] == "# Approved architecture"


def test_loader_rejects_unsupported_snapshot_version(tmp_path):
    snapshot_path = tmp_path / "approved_context.json"
    snapshot_path.write_text(
        json.dumps({"schema_version": 99}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="version"):
        load_approved_context(snapshot_path)


def test_loader_rejects_unknown_task_dependency(tmp_path):
    snapshot = {
        "schema_version": 1,
        "plan": "Approved plan",
        "tasks": [
            {
                "task_id": "T1",
                "title": "Build backend foundation",
                "description": "Set up the approved backend foundation.",
                "acceptance_criteria": ["The backend foundation is defined."],
                "dependencies": ["T99"],
            }
        ],
        "architecture": "# Approved architecture",
    }
    snapshot_path = tmp_path / "approved_context.json"
    snapshot_path.write_text(
        json.dumps(snapshot),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown dependencies"):
        load_approved_context(snapshot_path)