import pytest

from main import DEFAULT_PILOT_WORKSPACE_NAME, get_pilot_root


def test_default_pilot_workspace_is_next_to_the_workflow_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("PILOT_WORKSPACE", raising=False)
    workflow_root = tmp_path / "AI-SDLC-Agent-Workflow"
    workflow_root.mkdir()
    pilot_root = tmp_path / DEFAULT_PILOT_WORKSPACE_NAME
    pilot_root.mkdir()

    assert get_pilot_root(workflow_root) == pilot_root.resolve()


def test_pilot_workspace_can_be_set_with_an_environment_variable(tmp_path, monkeypatch):
    custom_pilot = tmp_path / "somewhere" / "pilot"
    custom_pilot.mkdir(parents=True)
    monkeypatch.setenv("PILOT_WORKSPACE", str(custom_pilot))

    assert get_pilot_root(tmp_path / "workflow") == custom_pilot.resolve()


def test_missing_pilot_workspace_gives_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("PILOT_WORKSPACE", raising=False)
    workflow_root = tmp_path / "AI-SDLC-Agent-Workflow"
    workflow_root.mkdir()

    with pytest.raises(FileNotFoundError, match="git worktree add"):
        get_pilot_root(workflow_root)


def test_pilot_workspace_cannot_be_the_workflow_folder(tmp_path, monkeypatch):
    workflow_root = tmp_path / "AI-SDLC-Agent-Workflow"
    workflow_root.mkdir()
    monkeypatch.setenv("PILOT_WORKSPACE", str(workflow_root))

    with pytest.raises(ValueError, match="must not be the workflow folder"):
        get_pilot_root(workflow_root)
