import json
import shutil
from types import SimpleNamespace

import pytest

import main as workflow_main
from agents.agent_contracts import BackendDeveloperResult, FrontendDeveloperResult
from agents.backend_developer import BackendDeveloperAgent
from agents.correction_loop import CorrectionLoopReport
from agents.frontend_developer import FrontendDeveloperAgent, ui_architecture_context
from agents.orchestrator import OrchestratorAgent
from agents.pilot_git_agent import PilotGitAgent, branch_role, branch_task_id
from test_h2b_ui_stack import proposal_data
from test_pilot_git_agent import git, make_pilot_repo

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

FOLDER = "pilot/app/frontend"
PACKAGE = f"{FOLDER}/package.json"
APP = f"{FOLDER}/src/App.tsx"

ARCHITECTURE = """# Architecture

### 3.1. User Interface (UI) Component
- **Responsibilities**: Provides the student-facing single-page interface.

### 3.2. Document Ingestion Module
- Backend text that the UI agent does not need.

## 4. Component Interfaces and APIs

- `GET /api/v1/exams/{exam_id}`
  - *Response*: the exam
- `POST /api/v1/exams/{exam_id}/sessions`
  - *Response*: `{"session_id": "sess_1"}`
- `PUT /api/v1/exams/sessions/{session_id}/answers`
  - *Response*: `{"status": "SAVED"}`
- `POST /api/v1/exams/sessions/{session_id}/submit`
  - *Response*: `{"job_id": "job_1"}`
- `GET /api/v1/diagnostics/readiness`
  - *Response*: not needed by T7

## 7. Mapping to Approved Tasks

| **T1** | Design | All | Foundation. |
| **T7** | Practice Exam Execution Interface | UI Component | Requires browser `localStorage`. |

## 9. Human Review Decisions

1. **LLM choice**:
   - *Decision*: Which provider?

2. **Grading policy**:
   - *Recommendation*: display an explicit UI disclaimer: *"AI feedback is advisory. Review model answers for authoritative validation."*
"""


def task(task_id):
    return {
        "task_id": task_id,
        "title": f"Task {task_id}",
        "description": "The task description.",
        "acceptance_criteria": ["The exam is shown."],
        "dependencies": [],
    }


class GateOrchestrator(OrchestratorAgent):
    """Real delegation checks, without Gemini. The human approves H1."""

    def __init__(self):
        self.workflow_state = {
            "backlog_approved": False,
            "architecture_approved": False,
            "tasks": {},
            "approval_history": [],
        }

    def request_backlog_approval(self, _plan, tasks):
        self.workflow_state["backlog_approved"] = True
        for item in tasks:
            self.workflow_state["tasks"][item["task_id"]]["status"] = "approved"
        return "approve"


class FakeClient:
    """Stands in for Gemini: counts the requests and returns the prepared answer."""

    def __init__(self, answer):
        self.requests = []
        self.answer = answer
        self.models = SimpleNamespace(generate_content=self.generate_content)

    def generate_content(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(text=self.answer, candidates=None)


def model_answer(files=None, **changes):
    files = files if files is not None else {PACKAGE: '{"name": "exam-ui"}', APP: "export default function App() {}"}
    return json.dumps({
        "task_id": "T7",
        "summary": "Add the exam screen. More details follow",
        "proposed_files": [{"path": path, "purpose": "UI", "content": content} for path, content in files.items()],
        "acceptance_criteria": [{"criterion": "The exam is shown.", "status": "addressed", "evidence": "App.tsx"}],
        "tests_added": [],
        "assumptions": ["The mock imitates the backend."],
        "unresolved_issues": ["The real submit route does not exist."],
        **changes,
    })


def frontend_agent(answer=None):
    return FrontendDeveloperAgent(client=FakeClient(answer or model_answer()), model="test-model")


@pytest.fixture
def approve_h2(monkeypatch):
    def approve(orchestrator, **_kwargs):
        orchestrator.workflow_state["architecture_approved"] = True
        return True

    monkeypatch.setattr(workflow_main, "review_and_approve_architecture", approve)


def make_frontend_repo(tmp_path, autocrlf="false", ui_stack=True):
    """A pilot workspace with an approved snapshot, like the real one after H2b."""
    repo, snapshot_path = make_pilot_repo(tmp_path, autocrlf)
    snapshot = {
        "schema_version": 1,
        "human_approvals": {"H1": "approved", "H2": "approved"},
        "plan": "The approved plan",
        "tasks": [task("T1"), task("T7")],
        "architecture": ARCHITECTURE,
    }
    if ui_stack:
        snapshot["ui_stack"] = proposal_data()
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    (repo / "pilot").mkdir()
    (repo / "pilot" / "architecture_design.md").write_text(ARCHITECTURE, encoding="utf-8")
    # Like the real pilot: Git stores LF. With autocrlf the working file has CRLF.
    newline = "\r\n" if autocrlf == "true" else "\n"
    (repo / ".gitignore").write_text("__pycache__/\n.venv/\n", encoding="utf-8", newline=newline)

    # The report of a finished task stays on pilot/main after its merge.
    old_report = CorrectionLoopReport(
        task_id="T1", branch="agent/T1-attempt-1", tests_generated=[], tests_run=[], runs=[], fix_attempts=[],
        final_result="passed", decision="ready_for_review",
    )
    results = repo / "outputs" / "test_results"
    results.mkdir()
    (results / "T1_correction_loop_20261102_213447_915595.json").write_text(old_report.model_dump_json(), encoding="utf-8")

    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "approved snapshot")
    return repo


def run_patch(repo, task_id="T7", agent=None):
    agent = agent or frontend_agent()
    outcome = workflow_main.run_frontend_patch(repo, GateOrchestrator(), task_id, agent=agent, git_agent=PilotGitAgent(repo))
    return outcome, agent


def branches(repo):
    return git(repo, "branch", "--list", "agent/*")


@pytest.mark.parametrize("autocrlf", ["false", "true"])
def test_happy_path_commits_only_frontend_files_as_the_frontend_agent(tmp_path, approve_h2, capsys, autocrlf):
    repo = make_frontend_repo(tmp_path, autocrlf)
    gitignore = "__pycache__/\n.venv/\nnode_modules/\ndist/\n"
    answer = model_answer({PACKAGE: "{}", APP: "export {}", ".gitignore": gitignore})

    outcome, agent = run_patch(repo, agent=frontend_agent(answer))

    commit = outcome["agent_commit"]
    assert commit.branch == "agent/T7-frontend-attempt-1"
    assert git(repo, "show", "--name-only", "--format=", commit.code_commit).splitlines() == [".gitignore", PACKAGE, APP]
    author = "AI-SDLC Frontend Agent <frontend-agent@ai-sdlc.local>"
    assert git(repo, "log", "-2", "--format=%an <%ae>") == f"{author}\n{author}"
    assert "Frontend Developer Agent" in git(repo, "log", "-1", "--format=%b", commit.code_commit)
    # Reports are named _frontend_, and nothing is left uncommitted.
    assert "_frontend_" in outcome["report_path"].name and "_backend_" not in outcome["report_path"].name
    assert git(repo, "status", "--porcelain") == ""
    # pilot/main is untouched, and it is one Gemini request.
    assert "pilot/app/frontend" not in git(repo, "ls-tree", "-r", "--name-only", "pilot/main")
    assert len(agent.client.requests) == 1
    # The patch declared no tests, so the loop reports that and the branch waits for H3.
    assert outcome["loop_report"].final_result == "no_tests"
    assert "Waiting for the H3 decision." in capsys.readouterr().out


def test_prompt_has_the_three_context_layers_and_no_backend_text(tmp_path, approve_h2):
    repo = make_frontend_repo(tmp_path)

    _, agent = run_patch(repo)

    prompt = agent.client.requests[0]["contents"]
    # L0: the approved stack, not hard-coded.
    assert "Framework: React" in prompt and "Test runner: Vitest" in prompt
    assert "MOCK API" in prompt and "localStorage" in prompt
    # L1: the task and only the UI-relevant architecture.
    assert "The exam is shown." in prompt
    assert "single-page interface" in prompt
    assert "POST /api/v1/exams/sessions/{session_id}/submit" in prompt
    assert "AI feedback is advisory" in prompt
    assert "Requires browser `localStorage`" in prompt
    assert "Backend text that the UI agent does not need" not in prompt
    assert "not needed by T7" not in prompt
    # L2: the existing .gitignore.
    assert "__pycache__/" in prompt


def test_the_stack_comes_from_the_snapshot(tmp_path, approve_h2):
    repo = make_frontend_repo(tmp_path)
    snapshot_path = repo / "outputs" / "approved_context" / "current_approved_context.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["ui_stack"] = proposal_data(framework="Vue.js 3", build_tool="Webpack")
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    _, agent = run_patch(repo)

    prompt = agent.client.requests[0]["contents"]
    assert "Framework: Vue.js 3" in prompt and "Build tool: Webpack" in prompt
    assert "React" not in prompt and "Vite\n" not in prompt


@pytest.mark.parametrize(
    "path",
    [
        "pilot/app/backend/routes.py",
        "tests/test_backend_x.py",
        "requirements.txt",
        "pilot/app/frontend-old/App.tsx",
        "pilot/app/frontend/../backend/x.py",
        "/pilot/app/frontend/App.tsx",
        "pilot\\app\\frontend\\App.tsx",
        "pilot/app/frontend/node_modules/x/index.js",
        "pilot/app/frontend/dist/index.js",
    ],
)
def test_disallowed_path_is_refused_before_a_branch_exists(tmp_path, approve_h2, capsys, path):
    repo = make_frontend_repo(tmp_path)

    outcome, _ = run_patch(repo, agent=frontend_agent(model_answer({path: "code"})))

    assert outcome is None
    assert branches(repo) == ""
    assert git(repo, "branch", "--show-current") == "pilot/main"
    assert "No agent branch was created" in capsys.readouterr().out


def test_test_file_outside_the_frontend_folder_is_refused(tmp_path, approve_h2):
    repo = make_frontend_repo(tmp_path)

    outcome, _ = run_patch(repo, agent=frontend_agent(model_answer(tests_added=["tests/test_ui.py"])))

    assert outcome is None and branches(repo) == ""


@pytest.mark.parametrize("content", ["{not json", "[1, 2]"])
def test_invalid_package_json_is_refused(tmp_path, approve_h2, capsys, content):
    repo = make_frontend_repo(tmp_path)

    outcome, _ = run_patch(repo, agent=frontend_agent(model_answer({PACKAGE: content})))

    assert outcome is None and branches(repo) == ""
    assert "package.json" in capsys.readouterr().out


@pytest.mark.parametrize(
    "content",
    [
        "__pycache__/\nnode_modules/\n",  # removes .venv/
        "__pycache__/\n.venv/\n.env.local\n",  # adds a line that is not allowed
    ],
)
def test_gitignore_may_only_gain_the_allowed_lines(tmp_path, approve_h2, content):
    repo = make_frontend_repo(tmp_path)

    outcome, _ = run_patch(repo, agent=frontend_agent(model_answer({".gitignore": content})))

    assert outcome is None and branches(repo) == ""


@pytest.mark.parametrize("task_id", ["T1", "T14", "T99"])
def test_non_ui_task_is_refused(tmp_path, capsys, task_id):
    repo = make_frontend_repo(tmp_path)

    outcome, agent = run_patch(repo, task_id)

    assert outcome is None
    assert agent.client.requests == []
    assert "has no UI work" in capsys.readouterr().out


def test_missing_ui_stack_stops_cleanly(tmp_path, capsys):
    repo = make_frontend_repo(tmp_path, ui_stack=False)
    orchestrator = GateOrchestrator()
    agent = frontend_agent()

    outcome = workflow_main.run_frontend_patch(repo, orchestrator, "T7", agent=agent, git_agent=PilotGitAgent(repo))

    assert outcome is None
    assert agent.client.requests == []
    assert orchestrator.workflow_state["backlog_approved"] is False  # the human was not even asked
    assert "--h2b-ui-stack" in capsys.readouterr().out


def test_missing_snapshot_stops_cleanly(tmp_path, capsys):
    repo, snapshot_path = make_pilot_repo(tmp_path)
    snapshot_path.unlink()

    outcome, agent = run_patch(repo)

    assert outcome is None and agent.client.requests == []
    assert "needs an approved snapshot" in capsys.readouterr().out


def test_workspace_on_another_branch_stops_before_any_gemini_request(tmp_path, capsys):
    repo = make_frontend_repo(tmp_path)
    git(repo, "switch", "-q", "-c", "agent/T1-attempt-1")

    outcome, agent = run_patch(repo)

    assert outcome is None and agent.client.requests == []
    assert "not ready" in capsys.readouterr().out


def test_architecture_without_a_needed_endpoint_stops_cleanly(tmp_path, approve_h2, capsys):
    repo = make_frontend_repo(tmp_path)
    architecture = (repo / "pilot" / "architecture_design.md")
    architecture.write_text(ARCHITECTURE.replace("- `GET /api/v1/exams/{exam_id}`", "- removed"), encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "an architecture without the endpoint")

    outcome, agent = run_patch(repo)

    assert outcome is None and agent.client.requests == []
    assert "does not describe the endpoint" in capsys.readouterr().out


def test_backend_and_frontend_branch_names_do_not_clash(tmp_path):
    repo, _ = make_pilot_repo(tmp_path)
    agent = PilotGitAgent(repo)
    git(repo, "branch", "agent/T7-attempt-1")

    assert agent.next_branch_name("T7", "frontend") == "agent/T7-frontend-attempt-1"
    git(repo, "branch", "agent/T7-frontend-attempt-1")
    assert agent.next_branch_name("T7", "frontend") == "agent/T7-frontend-attempt-2"
    assert agent.next_branch_name("T7") == "agent/T7-attempt-2"  # counts only backend attempts


@pytest.mark.parametrize(
    ("branch", "task_id", "role"),
    [
        ("agent/T1-attempt-1", "T1", "backend"),
        ("agent/T7-frontend-attempt-2", "T7", "frontend"),
        ("agent/T10-attempt-12", "T10", "backend"),
    ],
)
def test_branch_names_tell_the_task_and_the_role(branch, task_id, role):
    assert branch_task_id(branch) == task_id
    assert branch_role(branch) == role


def h3(repo, answers, agent=None):
    return workflow_main.run_h3_review_command(
        repo, GateOrchestrator(), git_agent=PilotGitAgent(repo), backend_agent=agent,
        github_client=None, ask=lambda _prompt: answers.pop(0),
    )


def test_h3_finds_the_frontend_reports_and_not_the_results_of_another_task(tmp_path, approve_h2, capsys, monkeypatch):
    monkeypatch.setattr(workflow_main, "GitHubClient", lambda: None)
    repo = make_frontend_repo(tmp_path)
    run_patch(repo)
    capsys.readouterr()

    outcome = h3(repo, ["reject", "Not now"])

    screen = capsys.readouterr().out
    assert outcome["decision"] == "reject"
    assert "Task:   T7" in screen
    assert "_frontend_" in screen
    assert "The real submit route does not exist." in screen
    assert "[addressed] The exam is shown." in screen
    # T1's "passed" result on pilot/main must not look like evidence for T7:
    # H3 shows the T7 branch's own report.
    assert "automatic: no_tests -> needs_revision" in screen
    assert "passed -> ready_for_review" not in screen


def test_h3_approval_of_a_branch_without_tests_needs_a_second_confirmation(tmp_path, approve_h2, monkeypatch):
    monkeypatch.setattr(workflow_main, "GitHubClient", lambda: None)
    repo = make_frontend_repo(tmp_path)
    run_patch(repo)

    assert h3(repo, ["approve", "cancel"])["decision"] == "cancelled"
    assert h3(repo, ["approve", "approve", "No tests yet"])["decision"] == "approve"

    assert git(repo, "log", "-1", "--format=%an <%ae>") == "AI-SDLC Frontend Agent <frontend-agent@ai-sdlc.local>"
    assert git(repo, "branch", "--show-current") == "pilot/main"


def test_h3_revise_goes_back_to_the_frontend_agent(tmp_path, approve_h2, monkeypatch):
    monkeypatch.setattr(workflow_main, "GitHubClient", lambda: None)
    repo = make_frontend_repo(tmp_path)
    run_patch(repo)
    reviser = frontend_agent(model_answer({APP: "export default function App() { return null; }"}))

    outcome = h3(repo, ["revise", "Show the disclaimer", "no"], agent=reviser)

    assert outcome["revision_report"].fix_attempts[0].outcome == "committed"
    prompt = reviser.client.requests[0]["contents"]
    assert "Show the disclaimer" in prompt and "Framework: React" in prompt
    # The agent saw its own earlier file, and the record is a _frontend_ one.
    assert "export default function App() {}" in prompt
    assert git(repo, "log", "--format=%an", "-3").splitlines() == ["AI-SDLC Frontend Agent"] * 3
    assert "T7_frontend_revise1_" in git(repo, "ls-files", "outputs/proposed_changes")


def test_large_files_are_limited_in_the_prompt_and_cannot_be_changed_blindly(tmp_path, approve_h2, monkeypatch):
    monkeypatch.setattr(FrontendDeveloperAgent, "MAX_EXISTING_FILE_CHARS", 100)
    repo = make_frontend_repo(tmp_path)
    big_file = repo / FOLDER / "package-lock.json"
    big_file.parent.mkdir(parents=True)
    big_file.write_text("x" * 5000 + "\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "a big file")

    _, agent = run_patch(repo, agent=frontend_agent(model_answer({f"{FOLDER}/package-lock.json": "{}"})))

    prompt = agent.client.requests[0]["contents"]
    assert "xxxxx" not in prompt and "package-lock.json" in prompt  # named, but not shown
    assert branches(repo) == ""  # the blind change was refused


def test_context_skips_generated_folders(tmp_path):
    (tmp_path / FOLDER / "src").mkdir(parents=True)
    (tmp_path / FOLDER / "src" / "App.tsx").write_text("app\n", encoding="utf-8")
    (tmp_path / FOLDER / "logo.png").write_bytes(b"\x89PNG\xff\xfe\x00")
    (tmp_path / FOLDER / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / FOLDER / "node_modules" / "x" / "index.js").write_text("x\n", encoding="utf-8")
    (tmp_path / FOLDER / "dist").mkdir()
    (tmp_path / FOLDER / "dist" / "bundle.js").write_text("x\n", encoding="utf-8")

    context = workflow_main.collect_frontend_file_context(tmp_path, FOLDER)

    assert context["existing_file_paths"] == [APP]


def test_real_architecture_style_sections_are_selected():
    context = ui_architecture_context(ARCHITECTURE, "T7")

    assert "Document Ingestion" not in context
    assert "LLM choice" not in context
    assert "AI feedback is advisory" in context


@pytest.mark.parametrize("answer", ["This is not JSON.", "[1, 2]"])
def test_malformed_answer_errors_name_the_right_agent(answer):
    with pytest.raises(ValueError, match="Frontend Developer Agent"):
        FrontendDeveloperAgent._parse_json_response(answer)

    with pytest.raises(ValueError, match="Backend Developer Agent"):
        BackendDeveloperAgent._parse_json_response(answer)


def test_frontend_result_is_accepted_where_a_backend_result_is():
    assert issubclass(FrontendDeveloperResult, BackendDeveloperResult)


def test_command_line_flag_runs_the_frontend_patch(tmp_path, monkeypatch):
    (tmp_path / "pilot").mkdir()
    (tmp_path / "pilot" / "pilot_specification.md").write_text("Spec", encoding="utf-8")
    calls = []

    monkeypatch.setattr(workflow_main, "get_pilot_root", lambda _root: tmp_path)
    monkeypatch.setattr(workflow_main, "OrchestratorAgent", lambda: "orchestrator")
    monkeypatch.setattr(workflow_main, "run_frontend_patch", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(workflow_main, "optional_github_client", lambda: "github")

    workflow_main.main(["--frontend-patch", "T7"])

    assert calls == [{"project_root": tmp_path, "orchestrator": "orchestrator", "task_id": "T7", "github": "github"}]
