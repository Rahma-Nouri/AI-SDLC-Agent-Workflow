"""P2.5b-1: the legacy run is blocked, and the Backend Agent works on any task (--backend-patch)."""

import json
import shutil
import sys

import pytest

import main as workflow_main
from agents.backend_developer import BackendDeveloperAgent
from agents.pilot_environment import EnvironmentResult
from agents.pilot_git_agent import PilotGitAgent
from test_frontend_agent import FakeClient, GateOrchestrator, approve_h2  # noqa: F401 (approve_h2 is a fixture)
from test_pilot_git_agent import git, make_pilot_repo

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

ARCHITECTURE = "# Architecture\n\n| **T2** | Document Parsing | Document Ingestion Module | Parse text. |\n"
CODE = "pilot/app/backend/parser.py"
TEST = "tests/test_backend_parser.py"
CODE_TEXT = (
    "def extract_text(data):\n"
    "    if not data:\n"
    "        raise ValueError('empty document')\n"
    "    return data.decode('utf-8')\n"
)
TEST_TEXT = (
    "import pytest\n\n"
    "from pilot.app.backend.parser import extract_text\n\n\n"
    "def test_extract_text():\n"
    "    assert extract_text(b'Question 1') == 'Question 1'\n\n\n"
    "def test_empty_document_is_refused():\n"
    "    with pytest.raises(ValueError):\n"
    "        extract_text(b'')\n"
)


def task(task_id, dependencies=()):
    return {
        "task_id": task_id,
        "title": f"Task {task_id}",
        "description": "The task description.",
        "acceptance_criteria": ["Text is extracted."],
        "dependencies": list(dependencies),
    }


def model_answer(files=None, task_id="T2"):
    files = files if files is not None else {CODE: CODE_TEXT, TEST: TEST_TEXT}
    return json.dumps({
        "task_id": task_id,
        "summary": "Add the text extraction. More details follow",
        "proposed_files": [{"path": path, "purpose": "parsing", "content": content} for path, content in files.items()],
        "acceptance_criteria": [{"criterion": "Text is extracted.", "status": "addressed", "evidence": "parser.py"}],
        "tests_added": [TEST],
        "assumptions": [],
        "unresolved_issues": [],
    })


class ReadyEnvironment:
    """Stands in for the pilot's .venv: the tests run with this Python."""

    def prepare(self):
        return EnvironmentResult(status="ready", python=sys.executable, packages=["pytest"], installed_now=False, detail="test")


def answers(*items):
    replies = list(items)
    return lambda _prompt: replies.pop(0)


def make_backend_repo(tmp_path, t1_approved=True):
    """A pilot workspace on pilot/main with an approved snapshot (T1, T2, T7)."""
    repo, snapshot_path = make_pilot_repo(tmp_path)
    snapshot = {
        "schema_version": 1,
        "human_approvals": {"H1": "approved", "H2": "approved"},
        "plan": "The approved plan",
        "tasks": [task("T1"), task("T2", ["T1"]), task("T7", ["T1"])],
        "architecture": ARCHITECTURE,
    }
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    (repo / "pilot").mkdir()
    (repo / "pilot" / "architecture_design.md").write_text(ARCHITECTURE, encoding="utf-8", newline="\n")
    (repo / ".gitignore").write_text("__pycache__/\n.venv/\n.pytest_cache/\n", encoding="utf-8", newline="\n")
    if t1_approved:
        approvals = repo / "outputs" / "approvals"
        approvals.mkdir()
        (approvals / "T1_H3_approve_20261107_112144_586388.json").write_text('{"decision": "approve"}', encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "approved snapshot")
    return repo


def run_backend(repo, task_id="T2", client=None, ask=None):
    client = client or FakeClient(model_answer())
    agent = BackendDeveloperAgent(client=client, model="test-model")
    outcome = workflow_main.run_backend_patch(
        repo, GateOrchestrator(), task_id, agent=agent, git_agent=PilotGitAgent(repo),
        environment=ReadyEnvironment(), ask=ask or answers("yes"),
    )
    return outcome, client


# --- D1: the legacy run -------------------------------------------------------

class CountingOrchestrator:
    """Records whether the legacy run reached the first Gemini call."""

    calls = []

    def __init__(self):
        self.workflow_state = {"backlog_approved": False, "architecture_approved": False}

    def analyze_project(self, _specification):
        CountingOrchestrator.calls.append("analyze_project")
        return "plan"


def legacy_pilot(tmp_path, monkeypatch, with_snapshot):
    (tmp_path / "pilot").mkdir()
    (tmp_path / "pilot" / "pilot_specification.md").write_text("Spec.", encoding="utf-8")
    if with_snapshot:
        snapshot = tmp_path / "outputs" / "approved_context" / "current_approved_context.json"
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text('{"schema_version": 1}', encoding="utf-8")
    CountingOrchestrator.calls = []
    monkeypatch.setattr(workflow_main, "get_pilot_root", lambda _root: tmp_path)
    monkeypatch.setattr(workflow_main, "OrchestratorAgent", CountingOrchestrator)
    monkeypatch.setattr(workflow_main, "TicketQualityChecker", lambda: object())


def files_and_contents(folder):
    return {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}


def test_legacy_run_with_a_snapshot_is_blocked_before_gemini(tmp_path, monkeypatch, capsys):
    legacy_pilot(tmp_path, monkeypatch, with_snapshot=True)
    before = files_and_contents(tmp_path)

    workflow_main.main([])

    assert CountingOrchestrator.calls == []
    assert files_and_contents(tmp_path) == before
    output = capsys.readouterr().out
    assert "frozen after H1" in output
    for command in ("--backend-patch TASK", "--frontend-patch TASK", "--docs-patch TASK", "--h3-review", "--retest-agent-branch"):
        assert command in output


def test_legacy_run_without_a_snapshot_runs_as_before(tmp_path, monkeypatch):
    legacy_pilot(tmp_path, monkeypatch, with_snapshot=False)
    # Stop right after the backlog step: H1 not approved.
    monkeypatch.setattr(workflow_main, "prepare_and_approve_backlog", lambda **_kwargs: (None, None))

    workflow_main.main([])

    assert CountingOrchestrator.calls == ["analyze_project"]


# --- D3: --backend-patch TASK -------------------------------------------------

def test_backend_patch_t2_commits_tests_and_records_on_its_own_branch(tmp_path, approve_h2):
    repo = make_backend_repo(tmp_path)

    outcome, client = run_backend(repo)

    assert outcome["agent_commit"].branch == "agent/T2-attempt-1"
    assert outcome["agent_commit"].committed_files == [CODE, TEST]
    report = outcome["loop_report"]
    assert report.task_id == "T2" and report.tests_run == [TEST]
    assert report.final_result == "passed" and report.decision == "ready_for_review"
    assert len(client.requests) == 1  # no fix needed
    # Every commit on the branch is the Backend Agent's, the loop report included.
    assert git(repo, "branch", "--show-current") == "agent/T2-attempt-1"
    assert set(git(repo, "log", "pilot/main..HEAD", "--format=%an").splitlines()) == {"AI-SDLC Backend Agent"}
    assert any(name.startswith("outputs/test_results/T2_correction_loop_") for name in git(repo, "ls-files").splitlines())
    assert git(repo, "status", "--porcelain") == ""
    # The prompt names T2, not T1, and carries the no-binary-files rule.
    prompt = client.requests[0]["contents"]
    assert "backend slice of T2" in prompt and "slice of T1" not in prompt
    assert "never binary files" in prompt


@pytest.mark.parametrize(("task_id", "message"), [("T99", "not in the approved backlog"), ("T7", "--frontend-patch T7")])
def test_unknown_or_ui_only_task_is_refused_before_gemini(tmp_path, approve_h2, capsys, task_id, message):
    repo = make_backend_repo(tmp_path)

    outcome, client = run_backend(repo, task_id, ask=answers())

    assert outcome is None and client.requests == []
    assert message in capsys.readouterr().out
    assert git(repo, "branch", "--list", "agent/*") == ""


def test_missing_dependency_record_warns_and_no_stops_cleanly(tmp_path, approve_h2, capsys):
    repo = make_backend_repo(tmp_path, t1_approved=False)

    outcome, client = run_backend(repo, ask=answers("no"))

    assert outcome is None and client.requests == []
    assert "depends on T1" in capsys.readouterr().out
    assert git(repo, "branch", "--list", "agent/*") == ""
    assert git(repo, "status", "--porcelain") == ""


def test_missing_dependency_record_yes_continues(tmp_path, approve_h2):
    repo = make_backend_repo(tmp_path, t1_approved=False)

    outcome, _client = run_backend(repo, ask=answers("yes", "yes"))

    assert outcome["agent_commit"].branch == "agent/T2-attempt-1"


def test_h3_on_a_t2_branch_reads_task_t2_and_merges(tmp_path, approve_h2, monkeypatch):
    monkeypatch.setattr(workflow_main, "GitHubClient", lambda: None)
    repo = make_backend_repo(tmp_path)
    run_backend(repo)

    outcome = workflow_main.run_h3_review_command(
        repo, GateOrchestrator(), git_agent=PilotGitAgent(repo), environment=ReadyEnvironment(),
        github_client=None, ask=answers("approve", ""),
    )

    assert outcome["decision"] == "approve" and outcome["branch"] == "agent/T2-attempt-1"
    assert git(repo, "branch", "--show-current") == "pilot/main"
    assert git(repo, "log", "-1", "--format=%s").startswith("[T2] Merge agent/T2-attempt-1")
    assert list((repo / "outputs" / "approvals").glob("T2_H3_approve_*.json"))


# --- the Backend Agent itself -------------------------------------------------

def test_backend_agent_refuses_binary_files():
    agent = BackendDeveloperAgent(client=FakeClient(model_answer({"pilot/app/backend/sample.pdf": "%PDF-1.4"})), model="m")
    work_package = {
        "task": task("T2"),
        "architecture": ARCHITECTURE,
        "project_context": {"existing_files": {}, "existing_file_paths": [], "existing_files_complete": True},
        "instructions": "Implement only the approved task.",
    }

    with pytest.raises(ValueError, match="Binary files may not be proposed"):
        agent.create_patch(work_package)
