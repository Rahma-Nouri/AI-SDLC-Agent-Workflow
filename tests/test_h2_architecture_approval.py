import main as workflow_main


class ApprovalRecorder:
    def __init__(self):
        self.decisions = []

    def record_approval(self, gate, decision):
        self.decisions.append((gate, decision))


def test_h2_validates_current_architecture_file(tmp_path, monkeypatch):
    architecture_path = tmp_path / "architecture.md"
    architecture_path.write_text(
        "Architecture edited by the human reviewer.",
        encoding="utf-8",
    )
    tasks = [{"task_id": "T1"}]
    orchestrator = ApprovalRecorder()
    validated_documents = []

    def fake_validate_architecture(document, approved_tasks):
        validated_documents.append((document, approved_tasks))
        return True

    monkeypatch.setattr("builtins.input", lambda _prompt: "approve")
    monkeypatch.setattr(
        workflow_main,
        "validate_architecture",
        fake_validate_architecture,
    )

    result = workflow_main.review_and_approve_architecture(
        orchestrator=orchestrator,
        architect=None,
        architecture_path=architecture_path,
        tasks=tasks,
    )

    assert result is True
    assert validated_documents == [
        ("Architecture edited by the human reviewer.", tasks)
    ]
    assert orchestrator.decisions == [("H2", "approve")]


def test_failed_h2_validation_does_not_record_approval(
    tmp_path,
    monkeypatch,
):
    architecture_path = tmp_path / "architecture.md"
    architecture_path.write_text(
        "Architecture with missing task coverage.",
        encoding="utf-8",
    )
    tasks = [{"task_id": "T1"}]
    orchestrator = ApprovalRecorder()
    decisions = iter(["approve", "reject"])

    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: next(decisions),
    )
    monkeypatch.setattr(
        workflow_main,
        "validate_architecture",
        lambda _document, _tasks: False,
    )

    result = workflow_main.review_and_approve_architecture(
        orchestrator=orchestrator,
        architect=None,
        architecture_path=architecture_path,
        tasks=tasks,
    )

    assert result is False
    assert ("H2", "approve") not in orchestrator.decisions
    assert orchestrator.decisions == [("H2", "reject")]


def test_main_stops_before_patch_generation_when_h2_is_not_approved(
    tmp_path,
    monkeypatch,
):
    pilot_folder = tmp_path / "pilot"
    pilot_folder.mkdir()
    (pilot_folder / "pilot_specification.md").write_text(
        "Test pilot specification.",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        workflow_main,
        "__file__",
        str(tmp_path / "main.py"),
    )

    class FakeOrchestrator:
        def __init__(self):
            self.workflow_state = {
                "backlog_approved": True,
                "architecture_approved": False,
            }

        def analyze_project(self, _specification):
            return "test plan"

    class FakeArchitect:
        def design_architecture(self, _specification, _tasks):
            return "Generated test architecture."

    tasks = [{"task_id": "T1"}]
    patch_calls = []

    monkeypatch.setattr(
        workflow_main,
        "OrchestratorAgent",
        FakeOrchestrator,
    )
    monkeypatch.setattr(
        workflow_main,
        "TicketQualityChecker",
        lambda: object(),
    )
    monkeypatch.setattr(
        workflow_main,
        "prepare_and_approve_backlog",
        lambda **_kwargs: ("approved plan", tasks),
    )
    monkeypatch.setattr(
        workflow_main,
        "SoftwareArchitectAgent",
        FakeArchitect,
    )
    monkeypatch.setattr(
        workflow_main,
        "validate_architecture",
        lambda _architecture, _tasks: True,
    )
    monkeypatch.setattr(
        workflow_main,
        "review_and_approve_architecture",
        lambda **_kwargs: False,
    )

    def record_patch_generation(**_kwargs):
        patch_calls.append("called")
        raise AssertionError(
            "Patch generation must not run before H2 approval."
        )

    monkeypatch.setattr(
        workflow_main,
        "create_t1_backend_patch",
        record_patch_generation,
    )

    workflow_main.main([])

    assert patch_calls == []


def test_invalid_generated_architecture_preserves_existing_file(
    tmp_path,
    monkeypatch,
):
    pilot_folder = tmp_path / "pilot"
    pilot_folder.mkdir()
    (pilot_folder / "pilot_specification.md").write_text(
        "Test pilot specification.",
        encoding="utf-8",
    )
    architecture_path = pilot_folder / "architecture_design.md"
    accepted_architecture = "Previously accepted architecture."
    architecture_path.write_text(
        accepted_architecture,
        encoding="utf-8",
    )

    monkeypatch.setattr(
        workflow_main,
        "__file__",
        str(tmp_path / "main.py"),
    )

    class FakeOrchestrator:
        def __init__(self):
            self.workflow_state = {
                "backlog_approved": True,
                "architecture_approved": False,
            }

        def analyze_project(self, _specification):
            return "test plan"

    class FakeArchitect:
        def design_architecture(self, _specification, _tasks):
            return "Invalid architecture draft."

    tasks = [{"task_id": "T1"}]
    h2_calls = []

    monkeypatch.setattr(
        workflow_main,
        "OrchestratorAgent",
        FakeOrchestrator,
    )
    monkeypatch.setattr(
        workflow_main,
        "TicketQualityChecker",
        lambda: object(),
    )
    monkeypatch.setattr(
        workflow_main,
        "prepare_and_approve_backlog",
        lambda **_kwargs: ("approved plan", tasks),
    )
    monkeypatch.setattr(
        workflow_main,
        "SoftwareArchitectAgent",
        FakeArchitect,
    )
    monkeypatch.setattr(
        workflow_main,
        "validate_architecture",
        lambda _architecture, _tasks: False,
    )
    monkeypatch.setattr(
        workflow_main,
        "review_and_approve_architecture",
        lambda **_kwargs: h2_calls.append("called"),
    )

    workflow_main.main([])

    assert architecture_path.read_text(encoding="utf-8") == accepted_architecture
    assert h2_calls == []