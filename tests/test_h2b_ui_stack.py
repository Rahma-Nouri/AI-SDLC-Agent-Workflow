import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import main as workflow_main
from agents.agent_contracts import UiStackProposal
from agents.orchestrator import OrchestratorAgent
from agents.software_architect import SoftwareArchitectAgent
from test_correction_loop import make_orchestrator


def proposal_data(**changes):
    return {
        "framework": "React",
        "language": "TypeScript",
        "build_tool": "Vite",
        "test_runner": "Vitest",
        "package_manager": "npm",
        "frontend_folder": "pilot/app/frontend",
        "reasons": {
            "framework": "Large community.",
            "language": "Catches mistakes early.",
            "build_tool": "Fast and simple.",
            "test_runner": "Runs from the command line.",
            "package_manager": "Comes with Node.",
            "frontend_folder": "Next to the backend folder.",
        },
        "alternatives_considered": ["Vue.js 3"],
        "risks": ["The backend routes do not exist yet."],
        **changes,
    }


class FakeArchitect:
    """Returns the prepared answers in order and remembers every call."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def propose_ui_stack(self, specification, architecture, tasks, previous_proposal=None, feedback=None):
        self.calls.append({
            "specification": specification,
            "architecture": architecture,
            "tasks": tasks,
            "previous_proposal": previous_proposal,
            "feedback": feedback,
        })
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return UiStackProposal(**answer)


class FakeClient:
    """Stands in for the Gemini client of the real Architect."""

    def __init__(self, text):
        self.requests = []
        self.models = SimpleNamespace(generate_content=self.generate_content)
        self.text = text

    def generate_content(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(text=self.text)


def real_architect(answer_text):
    architect = SoftwareArchitectAgent.__new__(SoftwareArchitectAgent)
    architect.client = FakeClient(answer_text)
    architect.model = "test-model"
    return architect


def snapshot_path(pilot):
    return pilot / "outputs" / "approved_context" / "current_approved_context.json"


def make_pilot(tmp_path, **changes):
    """A pilot workspace with a specification and an approved snapshot."""
    (tmp_path / "pilot").mkdir()
    (tmp_path / "pilot" / "pilot_specification.md").write_text("# The spec", encoding="utf-8")
    snapshot = {
        "schema_version": 1,
        "human_approvals": {"H1": "approved", "H2": "approved"},
        "plan": "The approved plan",
        "tasks": [{
            "task_id": "T7",
            "title": "Practice Exam Execution Interface",
            "description": "The student-facing exam interface.",
            "acceptance_criteria": ["The exam is shown."],
            "dependencies": [],
        }],
        "architecture": "# The approved architecture",
        **changes,
    }
    snapshot_path(tmp_path).parent.mkdir(parents=True)
    snapshot_path(tmp_path).write_text(json.dumps(snapshot), encoding="utf-8")
    return tmp_path


def read_snapshot(pilot):
    return json.loads(snapshot_path(pilot).read_text(encoding="utf-8"))


def read_records(pilot):
    folder = pilot / "outputs" / "approvals"
    if not folder.is_dir():
        return []
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("UI-stack_H2b_*.json"))]


def run_gate(pilot, architect, answers):
    orchestrator = make_orchestrator()
    outcome = workflow_main.run_h2b_ui_stack_command(
        pilot, orchestrator, architect=architect, ask=lambda _prompt: answers.pop(0)
    )
    return outcome, orchestrator


def test_approve_stores_the_ui_stack_and_a_record(tmp_path):
    pilot = make_pilot(tmp_path)
    architect = FakeArchitect(proposal_data())

    outcome, orchestrator = run_gate(pilot, architect, ["approve"])

    assert outcome["decision"] == "approve"
    snapshot = read_snapshot(pilot)
    assert snapshot["ui_stack"] == proposal_data()
    assert snapshot["plan"] == "The approved plan"  # the rest stays
    # The Architect works from the approved snapshot (decision C1).
    assert architect.calls[0]["architecture"] == "# The approved architecture"
    assert architect.calls[0]["specification"] == "# The spec"
    assert architect.calls[0]["tasks"][0]["task_id"] == "T7"
    records = read_records(pilot)
    assert [record["decision"] for record in records] == ["approve"]
    assert records[0]["gate"] == "H2b" and records[0]["proposal"] == proposal_data()
    assert orchestrator.workflow_state["ui_stack_approved"] is True


def test_revise_sends_the_feedback_to_the_architect_then_approve(tmp_path, capsys):
    pilot = make_pilot(tmp_path)
    architect = FakeArchitect(proposal_data(), proposal_data(framework="Vue.js 3"))

    outcome, _ = run_gate(pilot, architect, ["revise", "", "Please use Vue", "approve"])

    assert outcome["decision"] == "approve"
    assert len(architect.calls) == 2
    assert architect.calls[0]["feedback"] is None
    assert architect.calls[1]["feedback"] == "Please use Vue"
    assert architect.calls[1]["previous_proposal"].framework == "React"
    assert read_snapshot(pilot)["ui_stack"]["framework"] == "Vue.js 3"
    # The revised proposal was shown to the human before the approval.
    assert "Framework: Vue.js 3" in capsys.readouterr().out
    records = read_records(pilot)
    assert sorted(record["decision"] for record in records) == ["approve", "revise"]
    revise = next(record for record in records if record["decision"] == "revise")
    assert revise["feedback"] == "Please use Vue"
    assert revise["proposal"]["framework"] == "React"


def test_reject_stores_nothing_but_keeps_the_record(tmp_path, capsys):
    pilot = make_pilot(tmp_path)
    before = snapshot_path(pilot).read_bytes()

    outcome, orchestrator = run_gate(pilot, FakeArchitect(proposal_data()), ["reject"])

    assert outcome == {"decision": "reject"}
    assert snapshot_path(pilot).read_bytes() == before
    assert "nothing was stored" in capsys.readouterr().out
    assert [record["decision"] for record in read_records(pilot)] == ["reject"]
    assert orchestrator.workflow_state["ui_stack_approved"] is False


@pytest.mark.parametrize(
    "folder",
    [
        "src/frontend",
        "pilot/app/frontend-old",
        "pilot/app/frontend/../backend",
        "/pilot/app/frontend",
        "pilot\\app\\frontend",
    ],
)
def test_contract_refuses_a_folder_outside_the_frontend_folder(folder):
    with pytest.raises(ValidationError, match="frontend_folder"):
        UiStackProposal(**proposal_data(frontend_folder=folder))


def test_contract_accepts_a_subfolder():
    assert UiStackProposal(**proposal_data(frontend_folder="pilot/app/frontend/web"))


@pytest.mark.parametrize(
    "changes",
    [
        {"language": "Python"},
        {"framework": "   "},
        {"risks": []},
        {"alternatives_considered": []},
        {"reasons": {"framework": "Only one reason."}},
    ],
)
def test_contract_refuses_incomplete_proposals(changes):
    with pytest.raises(ValidationError):
        UiStackProposal(**proposal_data(**changes))


@pytest.mark.parametrize(
    "answer_text",
    [
        json.dumps(proposal_data(frontend_folder="src/frontend")),
        "This is not JSON.",
        "",
    ],
)
def test_invalid_architect_answer_stops_cleanly(tmp_path, capsys, answer_text):
    pilot = make_pilot(tmp_path)
    before = snapshot_path(pilot).read_bytes()

    outcome, _ = run_gate(pilot, real_architect(answer_text), [])  # the human is never asked

    assert outcome is None
    assert snapshot_path(pilot).read_bytes() == before
    assert read_records(pilot) == []
    assert "Nothing was stored" in capsys.readouterr().out


def test_failed_revision_stops_cleanly_and_keeps_the_revise_record(tmp_path):
    pilot = make_pilot(tmp_path)
    before = snapshot_path(pilot).read_bytes()
    architect = FakeArchitect(proposal_data(), RuntimeError("Gemini is unavailable"))

    outcome, _ = run_gate(pilot, architect, ["revise", "Use Vue"])

    assert outcome is None
    assert snapshot_path(pilot).read_bytes() == before
    assert [record["decision"] for record in read_records(pilot)] == ["revise"]


def test_failed_save_stops_cleanly(tmp_path, monkeypatch, capsys):
    pilot = make_pilot(tmp_path)

    def fail(*_args, **_kwargs):
        raise OSError("disk problem")

    monkeypatch.setattr("agents.h2b_ui_stack.write_decision_record", fail)

    outcome, orchestrator = run_gate(pilot, FakeArchitect(proposal_data()), ["reject"])

    assert outcome is None
    assert "Could not save this decision: disk problem" in capsys.readouterr().out
    assert orchestrator.workflow_state["approval_history"] == []


def test_save_approved_context_keeps_the_ui_stack(tmp_path):
    pilot = make_pilot(tmp_path, ui_stack=proposal_data())
    orchestrator = SimpleNamespace(workflow_state={"backlog_approved": True, "architecture_approved": True})

    workflow_main.save_approved_context(
        orchestrator=orchestrator,
        project_root=pilot,
        plan="A refreshed plan",
        tasks=[{"task_id": "T1"}],
        architecture="# A refreshed architecture",
    )

    snapshot = read_snapshot(pilot)
    assert snapshot["ui_stack"] == proposal_data()
    # The fields the function writes itself are still replaced.
    assert snapshot["plan"] == "A refreshed plan"
    assert snapshot["architecture"] == "# A refreshed architecture"


def test_missing_snapshot_stops_cleanly(tmp_path, capsys):
    architect = FakeArchitect(proposal_data())

    outcome, _ = run_gate(tmp_path, architect, [])

    assert outcome is None
    assert architect.calls == []  # no Gemini request
    assert "H2b needs an approved snapshot" in capsys.readouterr().out


def test_snapshot_without_h2_approval_stops_cleanly(tmp_path, capsys):
    pilot = make_pilot(tmp_path, human_approvals={"H1": "approved"})
    architect = FakeArchitect(proposal_data())

    outcome, _ = run_gate(pilot, architect, [])

    assert outcome is None
    assert architect.calls == []
    assert "approved at H2" in capsys.readouterr().out


def test_rejecting_a_new_proposal_keeps_the_stored_ui_stack(tmp_path):
    pilot = make_pilot(tmp_path, ui_stack=proposal_data())

    run_gate(pilot, FakeArchitect(proposal_data(framework="Vue.js 3")), ["reject"])

    assert read_snapshot(pilot)["ui_stack"]["framework"] == "React"


def test_architect_makes_one_request_with_spec_architecture_and_tasks():
    architect = real_architect(json.dumps(proposal_data()))
    tasks = [{"task_id": "T7", "title": "Practice Exam Execution Interface"}]

    proposal = architect.propose_ui_stack("# The spec", "# The architecture", tasks)

    assert proposal.framework == "React"
    assert len(architect.client.requests) == 1
    prompt = architect.client.requests[0]["contents"]
    assert "# The spec" in prompt and "# The architecture" in prompt
    assert "T7: Practice Exam Execution Interface" in prompt
    assert "HUMAN REVIEWER FEEDBACK" not in prompt


def test_architect_revision_prompt_has_the_feedback_and_the_previous_proposal():
    architect = real_architect(json.dumps(proposal_data()))
    previous = UiStackProposal(**proposal_data(build_tool="Webpack"))

    architect.propose_ui_stack("# Spec", "# Architecture", [], previous_proposal=previous, feedback="Please use Vite")

    prompt = architect.client.requests[0]["contents"]
    assert "Please use Vite" in prompt and "Webpack" in prompt


def test_record_approval_accepts_gate_h2b():
    orchestrator = OrchestratorAgent.__new__(OrchestratorAgent)
    orchestrator.workflow_state = {"ui_stack_approved": False, "approval_history": []}

    orchestrator.record_approval("H2b", "approve")

    assert orchestrator.workflow_state["ui_stack_approved"] is True
    assert orchestrator.workflow_state["approval_history"][0]["gate"] == "H2b"


def test_command_line_flag_runs_the_gate(tmp_path, monkeypatch):
    pilot = make_pilot(tmp_path)
    calls = []

    monkeypatch.setattr(workflow_main, "get_pilot_root", lambda _root: pilot)
    monkeypatch.setattr(workflow_main, "OrchestratorAgent", lambda: "orchestrator")
    monkeypatch.setattr(workflow_main, "run_h2b_ui_stack_command", lambda **kwargs: calls.append(kwargs))

    workflow_main.main(["--h2b-ui-stack"])

    assert calls == [{"project_root": pilot, "orchestrator": "orchestrator"}]
