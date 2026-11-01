import json

import pytest

from agents.agent_contracts import BackendDeveloperResult
from main import create_t1_backend_patch


def make_result():
    return BackendDeveloperResult.model_validate(
        {
            "task_id": "T1",
            "summary": "Proposed the initial FastAPI application.",
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
                    "criterion": "The backend framework is selected.",
                    "status": "addressed",
                    "evidence": "The proposed patch uses FastAPI.",
                }
            ],
            "tests_added": [],
            "assumptions": [],
            "unresolved_issues": [],
        }
    )


class FakeOrchestrator:
    def __init__(self, h1=True, h2=True):
        self.workflow_state = {
            "backlog_approved": h1,
            "architecture_approved": h2,
        }
        self.delegated = []

    def delegate_task(self, task, architecture=None, project_context=None):
        self.delegated.append(task["task_id"])
        return {
            "task": task,
            "architecture": architecture,
            "project_context": project_context or {},
            "instructions": "Implement only the approved task.",
        }


class FakeBackendAgent:
    def __init__(self):
        self.work_package = None

    def create_patch(self, work_package):
        self.work_package = work_package
        return make_result()


def test_patch_flow_delegates_t1_and_saves_artifacts(tmp_path):
    orchestrator = FakeOrchestrator()
    agent = FakeBackendAgent()
    tasks = [
        {
            "task_id": "T1",
            "title": "Establish the backend foundation",
            "acceptance_criteria": ["The backend framework is selected."],
        }
    ]

    patch_path, report_path = create_t1_backend_patch(
        orchestrator=orchestrator,
        tasks=tasks,
        architecture="Use FastAPI for the backend.",
        project_context={"pilot_specification": "Pilot context"},
        output_dir=tmp_path / "proposed_changes",
        agent=agent,
    )

    assert orchestrator.delegated == ["T1"]
    assert agent.work_package["task"]["task_id"] == "T1"
    assert patch_path.read_text(encoding="utf-8").startswith("--- /dev/null")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["task_id"] == "T1"
    assert report["patch"].startswith("--- /dev/null")


def test_saved_patch_uses_lf_line_endings(tmp_path):
    patch_path, _ = create_t1_backend_patch(
        orchestrator=FakeOrchestrator(),
        tasks=[{"task_id": "T1"}],
        architecture="Approved architecture",
        project_context={},
        output_dir=tmp_path,
        agent=FakeBackendAgent(),
    )

    # Read raw bytes, not text: read_text() would silently turn "\r\n" back
    # into "\n" and hide the problem this test is meant to catch.
    raw_bytes = patch_path.read_bytes()

    assert b"\r" not in raw_bytes
    assert raw_bytes == make_result().patch.encode("utf-8")


@pytest.mark.parametrize(
    ("h1", "h2", "message"),
    [
        (False, True, "H1"),
        (True, False, "H2"),
    ],
)
def test_patch_flow_stops_when_approval_is_missing(
    tmp_path,
    h1,
    h2,
    message,
):
    orchestrator = FakeOrchestrator(h1=h1, h2=h2)
    agent = FakeBackendAgent()
    tasks = [{"task_id": "T1"}]

    with pytest.raises(RuntimeError, match=message):
        create_t1_backend_patch(
            orchestrator=orchestrator,
            tasks=tasks,
            architecture="Approved architecture",
            project_context={},
            output_dir=tmp_path,
            agent=agent,
        )

    assert orchestrator.delegated == []
    assert agent.work_package is None