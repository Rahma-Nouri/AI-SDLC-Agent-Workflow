"""Tests for the Documentation Agent (P2.3): offline, with a fake Gemini and real Git."""

import json
import shutil

import pytest

import main as workflow_main
from agents.agent_contracts import BackendDeveloperResult, DocumentationResult
from agents.documentation_agent import DocumentationAgent, docs_architecture_context
from agents.output_validation import validate_files
from agents.pilot_git_agent import PilotGitAgent, branch_role, branch_task_id
from test_frontend_agent import FakeClient, GateOrchestrator, approve_h2  # noqa: F401 (fixture)
from test_pilot_git_agent import git, make_pilot_repo

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

DOC = "pilot/docs/T1_architecture.md"
CODE = "pilot/app/backend/database.py"

# The 7 sections the T1 document needs, plus one it must NOT get (4.2 endpoints).
ARCHITECTURE = """# Architecture

## 2. Architecture Style
A modular monolith.

## 3. System Components
### 3.1. User Interface (UI) Component
The UI.

## 4. Component Interfaces and APIs
### 4.1. Communication Style
REST over HTTP.

### 4.2. API Specifications
SECRET ENDPOINT DETAILS

## 5. Data Design
### 5.1. Entity-Relationship Model (Logical Overview)
Users have documents.

### 5.2. Relational Schema Definitions
TABLE DETAILS

## 6. Technology Recommendations
FastAPI and SQLite.

## 7. Mapping to Approved Tasks
| **T1** | System Architecture and Interface Design | All Modules | Foundation. |

## 8. Risks and Open Questions
Which LLM?

## 9. Human Review Decisions
1. **Grading**: advisory.
"""

GOOD_DOC = f"""# T1 Architecture

## Architecture Decision Records
ADR-001: use `get_engine()` from `{CODE}`.

```mermaid
flowchart LR
  UI --> API
```
"""

T1 = {
    "task_id": "T1",
    "title": "System Architecture and Interface Design",
    "description": "Design the architecture.",
    "acceptance_criteria": ["ADRs are documented and approved.", "Diagrams are completed."],
    "dependencies": [],
}


def model_answer(content=GOOD_DOC, path=DOC, **changes):
    return json.dumps({
        "task_id": "T1",
        "summary": "Add the T1 ADRs and diagrams. More details follow",
        "proposed_files": [{"path": path, "purpose": "T1 design document", "content": content}],
        "acceptance_criteria": [
            {"criterion": "ADRs are documented and approved.", "status": "addressed", "evidence": "Section 2"},
        ],
        "tests_added": [],
        "assumptions": ["The architecture is final."],
        "unresolved_issues": ["The OpenAPI spec covers only some endpoints."],
        **changes,
    })


def docs_agent(answer=None):
    return DocumentationAgent(client=FakeClient(answer or model_answer()), model="test-model")


def make_docs_repo(tmp_path):
    """A pilot workspace after T1: approved snapshot, T1 code, and the Backend Agent's report."""
    repo, snapshot_path = make_pilot_repo(tmp_path)
    snapshot_path.write_text(json.dumps({
        "schema_version": 1,
        "human_approvals": {"H1": "approved", "H2": "approved"},
        "plan": "The approved plan",
        "tasks": [T1, {**T1, "task_id": "T7", "title": "UI"}],
        "architecture": ARCHITECTURE,
    }), encoding="utf-8")
    (repo / "pilot" / "app" / "backend").mkdir(parents=True)
    (repo / "pilot" / "architecture_design.md").write_text(ARCHITECTURE, encoding="utf-8")
    (repo / "pilot" / "pilot_specification.md").write_text("PILOT SPEC TEXT", encoding="utf-8")
    (repo / CODE).write_text("def get_engine():\n    return None\n", encoding="utf-8", newline="\n")

    proposals = repo / "outputs" / "proposed_changes"
    proposals.mkdir(parents=True)
    for stamp, issue in (("20261102_100000_000000", "OLD ISSUE"), ("20261107_110000_000000", "NEWEST ISSUE")):
        (proposals / f"T1_backend_{stamp}_report.json").write_text(
            json.dumps({"task_id": "T1", "summary": "code", "unresolved_issues": [issue], "patch": "PATCH TEXT"}),
            encoding="utf-8",
        )
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "pilot after T1")
    return repo


def run_docs(repo, agent=None, task_id="T1"):
    agent = agent or docs_agent()
    outcome = workflow_main.run_docs_patch(repo, GateOrchestrator(), task_id, agent=agent, git_agent=PilotGitAgent(repo))
    return outcome, agent


def h3(repo, answers, agent=None):
    return workflow_main.run_h3_review_command(
        repo, GateOrchestrator(), git_agent=PilotGitAgent(repo), backend_agent=agent,
        github_client=None, ask=lambda _prompt: answers.pop(0),
    )


# --- the patch step -------------------------------------------------------------

def test_document_is_committed_on_a_docs_branch_by_the_documentation_agent(tmp_path, approve_h2, capsys):
    repo = make_docs_repo(tmp_path)

    outcome, agent = run_docs(repo)

    assert len(agent.client.requests) == 1
    assert outcome["agent_commit"].branch == "agent/T1-docs-attempt-1"
    assert outcome["agent_commit"].committed_files == [DOC]
    assert git(repo, "log", "-2", "--format=%an <%ae>").splitlines() == [
        "AI-SDLC Documentation Agent <docs-agent@ai-sdlc.local>"
    ] * 2
    # The proposal is saved as a _docs_ record and committed on the branch.
    assert any("T1_docs_" in name for name in git(repo, "ls-files", "outputs/proposed_changes").splitlines())
    assert outcome["findings"] == []
    assert "Automatic checks: no warnings." in capsys.readouterr().out


def test_prompt_has_the_layers_and_only_the_needed_architecture(tmp_path, approve_h2):
    repo = make_docs_repo(tmp_path)

    _outcome, agent = run_docs(repo)

    prompt = agent.client.requests[0]["contents"]
    assert "PILOT SPEC TEXT" in prompt                       # L0
    assert "A modular monolith." in prompt and "| **T1** |" in prompt  # L1
    assert "SECRET ENDPOINT DETAILS" not in prompt and "TABLE DETAILS" not in prompt
    assert "def get_engine()" in prompt                      # L2: the real code
    assert "NEWEST ISSUE" in prompt and "OLD ISSUE" not in prompt
    assert "PATCH TEXT" not in prompt                        # the old patch is not needed
    assert "(this file does not exist)" in prompt            # missing sources are named, not hidden


@pytest.mark.parametrize("path", ["pilot/app/backend/database.py", "pilot/docs/notes.txt", "pilot/docs/../x.md", "README.md"])
def test_paths_outside_pilot_docs_markdown_are_refused_before_a_branch_exists(tmp_path, approve_h2, capsys, path):
    repo = make_docs_repo(tmp_path)

    outcome, _ = run_docs(repo, docs_agent(model_answer(path=path)))

    assert outcome is None
    assert git(repo, "branch", "--list", "agent/*") == ""
    assert "may only write Markdown under pilot/docs/" in capsys.readouterr().out


def test_a_test_from_the_documentation_agent_is_refused(tmp_path, approve_h2, capsys):
    repo = make_docs_repo(tmp_path)

    outcome, _ = run_docs(repo, docs_agent(model_answer(tests_added=["tests/test_doc.py"])))

    assert outcome is None
    assert "must not add tests" in capsys.readouterr().out


def test_task_without_a_document_is_refused_without_a_gemini_request(tmp_path, capsys):
    repo = make_docs_repo(tmp_path)

    outcome, agent = run_docs(repo, task_id="T7")

    assert outcome is None and agent.client.requests == []
    assert "no document defined for T7" in capsys.readouterr().out


def test_workspace_on_another_branch_stops_before_any_gemini_request(tmp_path, capsys):
    repo = make_docs_repo(tmp_path)
    git(repo, "switch", "-q", "-c", "agent/T1-attempt-9")

    outcome, agent = run_docs(repo)

    assert outcome is None and agent.client.requests == []


def test_hallucinated_references_are_reported_as_warnings(tmp_path, approve_h2, capsys):
    repo = make_docs_repo(tmp_path)
    bad_doc = GOOD_DOC + "\nSee `pilot/app/backend/ghost.py` and call `make_magic()` on `ExamPlanner`.\n"

    outcome, _ = run_docs(repo, docs_agent(model_answer(content=bad_doc)))

    # Only warnings: the branch exists, and the human decides at H3.
    assert outcome["agent_commit"].branch == "agent/T1-docs-attempt-1"
    messages = [finding.message for finding in outcome["findings"]]
    assert any("ghost.py" in message for message in messages)
    assert any("make_magic" in message for message in messages)
    assert any("ExamPlanner" in message for message in messages)
    assert {finding.check for finding in outcome["findings"]} == {"docs_paths", "docs_names"}


# --- the reference checks on their own -----------------------------------------

def test_reference_checks_ignore_routes_technology_names_and_mermaid_blocks(tmp_path):
    (tmp_path / "pilot" / "app").mkdir(parents=True)
    (tmp_path / "pilot" / "app" / "models.py").write_text(
        "from sqlalchemy.orm import declarative_base\nBase = declarative_base()\n"
        "class User(Base):\n    pass\n\ndef init():\n    Base.metadata.create_all(None)\n",
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("", encoding="utf-8")
    (tmp_path / "doc.md").write_text(
        "Routes: `/api/v1/exams`, `GET /api/v1/jobs/{job_id}`. Tables: `users`. Tech: FastAPI.\n"
        "Code: `User`, `init()`, `Base.metadata.create_all()`, `declarative_base()`, `models.py`, `pilot/app/`.\n"
        "```mermaid\nflowchart LR\n  A[`fake.py`] --> B[`NotReal()`]\n```\n",
        encoding="utf-8",
    )

    assert validate_files(tmp_path, ["doc.md"]) == []


# --- branch names ---------------------------------------------------------------

@pytest.mark.parametrize(
    ("branch", "task_id", "role"),
    [
        ("agent/T1-docs-attempt-1", "T1", "docs"),
        ("agent/T7-frontend-attempt-2", "T7", "frontend"),
        ("agent/T1-attempt-3", "T1", "backend"),
    ],
)
def test_branch_names_tell_the_task_and_the_role(branch, task_id, role):
    assert branch_task_id(branch) == task_id
    assert branch_role(branch) == role


def test_docs_attempts_are_counted_separately(tmp_path):
    repo, _ = make_pilot_repo(tmp_path)
    git(repo, "branch", "agent/T1-attempt-1")

    assert PilotGitAgent(repo).next_branch_name("T1", "docs") == "agent/T1-docs-attempt-1"


# --- H3 in docs mode ------------------------------------------------------------

def test_h3_shows_a_document_review_and_one_approve_is_enough(tmp_path, approve_h2, capsys, monkeypatch):
    monkeypatch.setattr(workflow_main, "GitHubClient", lambda: None)
    repo = make_docs_repo(tmp_path)
    run_docs(repo)
    capsys.readouterr()

    outcome = h3(repo, ["approve", "Read and accepted"])

    screen = capsys.readouterr().out
    assert "H3 - HUMAN REVIEW: DOCUMENT" in screen
    assert DOC in screen
    assert "The OpenAPI spec covers only some endpoints." in screen  # the agent's _docs_ report is shown
    assert "did NOT pass" not in screen
    assert outcome["decision"] == "approve"
    assert git(repo, "branch", "--show-current") == "pilot/main"
    assert (repo / DOC).is_file()
    record_name = next(name for name in git(repo, "ls-files", "outputs/approvals").splitlines() if "_H3_approve_" in name)
    record = json.loads((repo / record_name).read_text(encoding="utf-8"))
    assert record["latest_tests"] == "not_applicable (document)"
    assert record["documents"] == [DOC]


def test_h3_revise_goes_back_to_the_documentation_agent(tmp_path, approve_h2, capsys, monkeypatch):
    monkeypatch.setattr(workflow_main, "GitHubClient", lambda: None)
    repo = make_docs_repo(tmp_path)
    run_docs(repo)
    reviser = docs_agent(model_answer(content=GOOD_DOC + "\n## Data flow\nAdded.\n"))

    outcome = h3(repo, ["revise", "Add a data flow diagram"], agent=reviser)

    revision = outcome["document_revision"]
    assert revision["outcome"] == "committed"
    prompt = reviser.client.requests[0]["contents"]
    assert "Add a data flow diagram" in prompt
    assert "ADR-001" in prompt  # the agent saw its own earlier document
    assert git(repo, "branch", "--show-current") == "agent/T1-docs-attempt-1"
    assert "Added." in (repo / DOC).read_text(encoding="utf-8")
    message = git(repo, "log", "-2", "--format=%an | %B")
    assert "after the human's H3 feedback" in message and "failing tests" not in message
    assert "python main.py --h3-review" in capsys.readouterr().out


def test_h3_reject_deletes_the_docs_branch(tmp_path, approve_h2, monkeypatch):
    monkeypatch.setattr(workflow_main, "GitHubClient", lambda: None)
    repo = make_docs_repo(tmp_path)
    before = git(repo, "rev-parse", "pilot/main")
    run_docs(repo)

    outcome = h3(repo, ["reject", "Not useful"])

    assert outcome["decision"] == "reject"
    assert git(repo, "branch", "--list", "agent/*") == ""
    assert git(repo, "rev-parse", "pilot/main") == before


# --- small contract checks ------------------------------------------------------

def test_documentation_result_is_accepted_where_a_backend_result_is():
    assert issubclass(DocumentationResult, BackendDeveloperResult)


def test_architecture_without_a_needed_section_fails_clearly():
    with pytest.raises(ValueError, match="no section"):
        docs_architecture_context("# Architecture\n## 2. Architecture Style\nx\n", "T1")


def test_command_line_flag_runs_the_docs_patch(tmp_path, monkeypatch):
    (tmp_path / "pilot").mkdir()
    (tmp_path / "pilot" / "pilot_specification.md").write_text("Spec", encoding="utf-8")
    calls = []

    monkeypatch.setattr(workflow_main, "get_pilot_root", lambda _root: tmp_path)
    monkeypatch.setattr(workflow_main, "OrchestratorAgent", lambda: "orchestrator")
    monkeypatch.setattr(workflow_main, "run_docs_patch", lambda **kwargs: calls.append(kwargs))

    workflow_main.main(["--docs-patch", "T1"])

    assert calls == [{"project_root": tmp_path, "orchestrator": "orchestrator", "task_id": "T1"}]
