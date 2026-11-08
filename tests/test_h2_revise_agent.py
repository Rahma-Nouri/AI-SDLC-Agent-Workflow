"""P2.5b-2: at H2, "revise" sends the human's notes to the Architect Agent,
which changes the architecture on its own branch (agent/architecture-revision-N)."""

import json
import shutil
import subprocess

import pytest

import main as workflow_main
from agents.orchestrator import OrchestratorAgent
from agents.pilot_git_agent import PilotGitAgent
from agents.software_architect import SoftwareArchitectAgent
from test_frontend_tests_env import SequenceClient
from test_h2b_ui_stack import proposal_data
from test_pilot_git_agent import git, make_pilot_repo

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

ARCH = "pilot/architecture_design.md"
TASKS = [
    {"task_id": "T1", "title": "Database", "description": "d", "acceptance_criteria": ["a"], "dependencies": []},
    {"task_id": "T2", "title": "Parsing", "description": "d", "acceptance_criteria": ["a"], "dependencies": ["T1"]},
]
OLD = "# Architecture\n\n## 7. Mapping to Approved Tasks\n\n| **T1** | Persistence Layer |\n| **T2** | Document Ingestion |\n"
REVISED = OLD + "\n## 8. Caching\n\nUse a small in-memory cache.\n"
REVISED_AGAIN = REVISED + "\nThe cache is cleared after every exam.\n"
WITHOUT_T2 = "# Architecture\n\n## 7. Mapping to Approved Tasks\n\n| **T1** | Persistence Layer |\n"


def make_h2_setup(tmp_path, ui_stack=False):
    """A pilot workspace on pilot/main with an approved snapshot and a 'GitHub' remote."""
    repo, snapshot_path = make_pilot_repo(tmp_path)
    snapshot = {"schema_version": 1, "plan": "The plan", "tasks": TASKS, "architecture": OLD}
    if ui_stack:
        snapshot["ui_stack"] = proposal_data()
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    (repo / "pilot").mkdir()
    (repo / ARCH).write_text(OLD, encoding="utf-8", newline="\n")
    (repo / "pilot" / "pilot_specification.md").write_text("The pilot specification.", encoding="utf-8", newline="\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "approved snapshot")

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "origin", "pilot/main")
    return repo, remote, snapshot_path


def make_architect(*answers):
    """The real Architect Agent with a fake Gemini client (one answer per request)."""
    architect = SoftwareArchitectAgent.__new__(SoftwareArchitectAgent)
    architect.client = SequenceClient(*answers)
    architect.model = "test-model"
    return architect


def make_orchestrator():
    """Real approval bookkeeping, without Gemini."""
    orchestrator = OrchestratorAgent.__new__(OrchestratorAgent)
    orchestrator.workflow_state = {
        "backlog_approved": True, "architecture_approved": False, "tasks": {}, "approval_history": [],
    }
    return orchestrator


def script(*replies):
    """The human's answers. A callable reply runs first (to look at the state) and returns the answer."""
    queue = list(replies)
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        reply = queue.pop(0)
        return reply(prompt) if callable(reply) else reply

    ask.prompts = prompts
    return ask


def review(repo, ask, architect=None, orchestrator=None):
    return workflow_main.review_and_approve_architecture(
        orchestrator=orchestrator or make_orchestrator(),
        architect=architect,
        architecture_path=repo / ARCH,
        tasks=TASKS,
        git_agent=PilotGitAgent(repo),
        ask=ask,
    )


def agent_branches(repo):
    return git(repo, "branch", "--list", "agent/*")


def test_revise_commits_only_the_architecture_and_the_notes_as_the_architect(tmp_path):
    repo, _, _ = make_h2_setup(tmp_path)
    architect = make_architect(REVISED)
    seen = {}

    def look(_prompt):
        seen["branch"] = git(repo, "branch", "--show-current")
        seen["authors"] = git(repo, "log", "pilot/main..HEAD", "--format=%an").splitlines()
        seen["files"] = git(repo, "diff", "--name-only", "pilot/main...HEAD").splitlines()
        return "reject"

    result = review(repo, script("revise", "Add a cache section", look, "Not needed", "reject"), architect)

    assert seen["branch"] == "agent/architecture-revision-1"
    assert seen["authors"] == ["AI-SDLC Architect Agent"]  # exactly one commit, by the Architect
    record, architecture = seen["files"]  # exactly two files (git sorts outputs/ first)
    assert architecture == ARCH and record.startswith("outputs/approvals/architecture_H2_revise_")
    # The Architect received the current document, the notes, and the frozen task IDs.
    prompt = architect.client.requests[0]["contents"]
    assert "Add a cache section" in prompt and "| **T2** | Document Ingestion |" in prompt and "T2: Parsing" in prompt
    assert result is False


def test_revise_then_approve_merges_pushes_and_updates_the_snapshot(tmp_path, capsys):
    repo, remote, snapshot_path = make_h2_setup(tmp_path)
    orchestrator = make_orchestrator()

    result = review(repo, script("revise", "Add a cache section", "approve"), make_architect(REVISED), orchestrator)

    assert result is True and orchestrator.workflow_state["architecture_approved"] is True
    assert git(repo, "branch", "--show-current") == "pilot/main"
    assert agent_branches(repo) == ""  # the branch was deleted after the merge
    assert git(repo, "log", "-1", "--format=%s") == "[architecture] Merge agent/architecture-revision-1 after the human's H2 approval"
    assert git(remote, "rev-parse", "pilot/main") == git(repo, "rev-parse", "pilot/main")
    assert (repo / ARCH).read_text(encoding="utf-8") == REVISED
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["architecture"] == REVISED and snapshot["tasks"] == TASKS
    files = git(repo, "ls-files").splitlines()
    assert any(name.startswith("outputs/approvals/architecture_H2_approve_") for name in files)
    assert git(repo, "status", "--porcelain") == ""
    # The notes are kept: in the orchestrator's history and in the committed record.
    assert {"gate": "H2", "decision": "revise", "notes": "Add a cache section"} in orchestrator.workflow_state["approval_history"]
    assert "H2b" not in capsys.readouterr().out  # no UI stack in this snapshot


def test_a_second_revise_adds_a_commit_on_the_same_branch(tmp_path):
    repo, _, _ = make_h2_setup(tmp_path)
    seen = {}

    def look(_prompt):
        seen["commits"] = git(repo, "log", "pilot/main..HEAD", "--format=%an").splitlines()
        seen["branches"] = agent_branches(repo)
        return "approve"

    result = review(
        repo, script("revise", "Add a cache section", "revise", "Say when the cache is cleared", look),
        make_architect(REVISED, REVISED_AGAIN),
    )

    assert seen["commits"] == ["AI-SDLC Architect Agent", "AI-SDLC Architect Agent"]
    assert seen["branches"].strip().lstrip("* ") == "agent/architecture-revision-1"
    assert result is True and (repo / ARCH).read_text(encoding="utf-8") == REVISED_AGAIN


def test_revise_then_reject_deletes_the_branch_and_keeps_the_old_architecture(tmp_path):
    repo, remote, _ = make_h2_setup(tmp_path)
    before = git(repo, "rev-parse", "pilot/main")

    # After the rejection the H2 menu comes back for the old architecture: approve it.
    result = review(repo, script("revise", "Add a cache section", "reject", "Not needed", "approve"), make_architect(REVISED))

    assert result is True
    assert git(repo, "branch", "--show-current") == "pilot/main" and agent_branches(repo) == ""
    assert git(repo, "rev-parse", "pilot/main") == before == git(remote, "rev-parse", "pilot/main")
    assert (repo / ARCH).read_text(encoding="utf-8") == OLD
    rejects = list((repo / "outputs" / "approvals").glob("architecture_H2_reject_*.json"))
    assert len(rejects) == 1 and json.loads(rejects[0].read_text(encoding="utf-8"))["notes"] == "Not needed"


def test_a_revision_that_drops_a_task_id_is_refused_and_not_committed(tmp_path, capsys):
    repo, _, _ = make_h2_setup(tmp_path)
    before = git(repo, "rev-parse", "HEAD")
    ask = script("revise", "Shorten the mapping", "approve", "reject")

    result = review(repo, ask, make_architect(WITHOUT_T2))

    assert result is False
    assert "refused" in capsys.readouterr().out
    # After the refusal only revise or reject is offered; "approve" is asked again.
    assert ask.prompts[2].strip() == "Enter 'revise' or 'reject':" == ask.prompts[3].strip()
    assert git(repo, "rev-parse", "HEAD") == before and agent_branches(repo) == ""
    assert git(repo, "status", "--porcelain") == ""
    assert (repo / ARCH).read_text(encoding="utf-8") == OLD


def test_approve_without_revise_makes_no_gemini_call_and_no_branch(tmp_path, monkeypatch):
    repo, _, _ = make_h2_setup(tmp_path)

    class NoArchitect(SoftwareArchitectAgent):
        """Keeps the coverage check; fails if an Architect (Gemini client) is created."""

        def __init__(self):
            raise AssertionError("The Architect must not be created for an approve-only review.")

    monkeypatch.setattr(workflow_main, "SoftwareArchitectAgent", NoArchitect)

    result = workflow_main.review_and_approve_architecture(
        orchestrator=make_orchestrator(), architect=None, architecture_path=repo / ARCH, tasks=TASKS,
        ask=script("approve"),
    )

    assert result is True and agent_branches(repo) == ""
    assert git(repo, "status", "--porcelain") == ""


def test_an_approved_revision_warns_about_the_ui_stack(tmp_path, capsys):
    repo, _, _ = make_h2_setup(tmp_path, ui_stack=True)

    result = review(repo, script("revise", "Add a cache section", "approve"), make_architect(REVISED))

    assert result is True
    output = capsys.readouterr().out
    assert "WARNING" in output and "--h2b-ui-stack" in output
