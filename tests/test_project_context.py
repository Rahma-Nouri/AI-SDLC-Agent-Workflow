import builtins

import main as workflow_main
from agents.backend_developer import BackendDeveloperAgent


def test_backend_agent_gets_the_whole_spec():
    specification = "x" * 5000 + " END OF THE SPEC"

    prompt = BackendDeveloperAgent._build_prompt(
        task={"task_id": "T1"},
        architecture="# Architecture",
        project_context={"pilot_specification": specification},
        instructions="Implement the task.",
    )

    assert specification in prompt
    assert "truncated" not in prompt


def test_other_context_limits_stay():
    compacted = BackendDeveloperAgent._compact_project_context({
        "requirements": "x" * 8001,
        "another_field": "x" * 2001,
    })

    assert "truncated at 8000" in compacted["requirements"]
    assert "truncated at 2000" in compacted["another_field"]


def test_pilot_context_has_no_readme(tmp_path):
    (tmp_path / "pilot").mkdir()
    (tmp_path / "pilot" / "pilot_specification.md").write_text("Spec.", encoding="utf-8")

    context = workflow_main.build_pilot_project_context(tmp_path)

    assert "readme" not in context
    assert context["pilot_specification"] == "Spec."


def test_full_run_context_has_no_readme(tmp_path, monkeypatch):
    """The third place that builds the context: the end of main()."""
    (tmp_path / "pilot").mkdir()
    (tmp_path / "pilot" / "pilot_specification.md").write_text("Spec.", encoding="utf-8")
    patch_calls = []

    class FakeOrchestrator:
        workflow_state = {"backlog_approved": True, "architecture_approved": True}

        def analyze_project(self, _specification):
            return "test plan"

    class FakeArchitect:
        def design_architecture(self, _specification, _tasks):
            return "Generated test architecture."

    def create_patch(**kwargs):
        patch_calls.append(kwargs)
        return "proposal.patch", "report.json"

    monkeypatch.setattr(workflow_main, "get_pilot_root", lambda _root: tmp_path)
    monkeypatch.setattr(workflow_main, "OrchestratorAgent", FakeOrchestrator)
    monkeypatch.setattr(workflow_main, "TicketQualityChecker", lambda: object())
    monkeypatch.setattr(workflow_main, "prepare_and_approve_backlog", lambda **_kwargs: ("plan", [{"task_id": "T1"}]))
    monkeypatch.setattr(workflow_main, "SoftwareArchitectAgent", FakeArchitect)
    monkeypatch.setattr(workflow_main, "validate_architecture", lambda _architecture, _tasks: True)
    monkeypatch.setattr(workflow_main, "review_and_approve_architecture", lambda **_kwargs: True)
    monkeypatch.setattr(workflow_main, "save_approved_context", lambda **_kwargs: tmp_path / "snapshot.json")
    monkeypatch.setattr(workflow_main, "create_t1_backend_patch", create_patch)
    monkeypatch.setattr(builtins, "input", lambda _prompt: "yes")

    workflow_main.main([])

    assert "readme" not in patch_calls[0]["project_context"]
    assert patch_calls[0]["project_context"]["pilot_specification"] == "Spec."
