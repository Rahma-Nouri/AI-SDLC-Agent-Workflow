import json
from types import SimpleNamespace

import pytest

from agents.backend_developer import BackendDeveloperAgent


class FakeModels:
    def __init__(self, response_text):
        self.response_text = response_text
        self.call = None

    def generate_content(self, *, model, contents, config=None):
        self.call = {"model": model, "contents": contents, "config": config}
        return SimpleNamespace(text=self.response_text)


class FakeClient:
    def __init__(self, response_text):
        self.models = FakeModels(response_text)


def valid_response():
    return {
        "task_id": "T1",
        "summary": "Proposes a minimal FastAPI application.",
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
                "evidence": "The proposed patch imports and creates a FastAPI app.",
            }
        ],
        "tests_added": [],
        "assumptions": [],
        "unresolved_issues": [],
    }


def valid_work_package():
    return {
        "task": {
            "task_id": "T1",
            "title": "Establish the backend foundation",
            "description": "Create the initial backend application.",
            "acceptance_criteria": ["The backend framework is selected."],
            "dependencies": [],
        },
        "architecture": "Use FastAPI for the backend and SQLite for the database.",
        "project_context": {
            "specification": "Pilot specification excerpt.",
            "existing_files_complete": True,
            "existing_file_paths": [],
            "existing_files": {},
        },
        "instructions": "Implement only the delegated backend task.",
    }


def test_agent_returns_validated_patch_and_uses_work_package():
    response = json.dumps(valid_response())
    client = FakeClient(response)
    agent = BackendDeveloperAgent(client=client, model="test-model")

    result = agent.create_patch(valid_work_package())

    assert result.task_id == "T1"
    assert result.patch.startswith("--- /dev/null")
    assert result.changed_files[0].path == "pilot/app/backend/main.py"
    assert client.models.call["model"] == "test-model"
    assert "Approved architecture" in client.models.call["contents"]
    assert "T1" in client.models.call["contents"]


def test_agent_rejects_empty_model_response():
    agent = BackendDeveloperAgent(client=FakeClient(""))

    with pytest.raises(RuntimeError, match="empty response"):
        agent.create_patch(valid_work_package())


def test_agent_rejects_invalid_json_response():
    agent = BackendDeveloperAgent(client=FakeClient("not JSON"))

    with pytest.raises(ValueError, match="not valid JSON"):
        agent.create_patch(valid_work_package())


def test_agent_rejects_result_for_different_task():
    response_data = valid_response()
    response_data["task_id"] = "T99"
    agent = BackendDeveloperAgent(client=FakeClient(json.dumps(response_data)))

    with pytest.raises(ValueError, match="does not match"):
        agent.create_patch(valid_work_package())


def test_agent_rejects_changed_file_outside_backend_folder():
    response_data = valid_response()
    response_data["proposed_files"][0]["path"] = "pilot/app/frontend/App.tsx"
    agent = BackendDeveloperAgent(client=FakeClient(json.dumps(response_data)))

    with pytest.raises(ValueError, match="must stay under"):
        agent.create_patch(valid_work_package())


def test_agent_rejects_path_traversal():
    response_data = valid_response()
    response_data["proposed_files"][0]["path"] = (
        "pilot/app/backend/../../outside.py"
    )
    agent = BackendDeveloperAgent(client=FakeClient(json.dumps(response_data)))

    with pytest.raises(ValueError, match="Unsafe changed-file path"):
        agent.create_patch(valid_work_package())

@pytest.mark.parametrize(
    "path",
    [
        "requirements.txt",
        "tests/test_backend_app.py",
    ],
)
def test_agent_allows_dependency_and_backend_test_paths(path):
    response_data = valid_response()
    response_data["proposed_files"][0]["path"] = path

    agent = BackendDeveloperAgent(
        client=FakeClient(json.dumps(response_data))
    )

    result = agent.create_patch(valid_work_package())

    assert result.changed_files[0].path == path


def test_agent_rejects_python_file_with_syntax_error():
    response_data = valid_response()
    response_data["proposed_files"][0]["content"] = (
        "class Broken:\n"
        "pass\n"
    )

    agent = BackendDeveloperAgent(
        client=FakeClient(json.dumps(response_data)),
        model="test-model",
    )

    with pytest.raises(ValueError, match="not syntactically valid"):
        agent.create_patch(valid_work_package())
