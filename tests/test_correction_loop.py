import difflib
import shutil
import subprocess

import pytest

from agents.agent_contracts import BackendDeveloperResult
from agents.correction_loop import run_correction_loop
from agents.orchestrator import OrchestratorAgent
from agents.pilot_git_agent import AGENT_NAME, PilotGitAgent

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

CODE = "pilot/app/backend/calc.py"
TEST = "tests/test_backend_calc.py"
BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TEST_TEXT = "from pilot.app.backend.calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
TASK = {"task_id": "T1", "title": "Calculator", "acceptance_criteria": ["add works"], "dependencies": []}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def make_agent_branch(tmp_path, code=BUGGY):
    """A pilot workspace where the agent already committed code + tests on its branch."""
    repo = tmp_path / "pilot-workspace"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Setup")
    git(repo, "config", "user.email", "setup@example.com")
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "switch", "-q", "-c", "pilot/main")
    (repo / "README.md").write_text("pilot\n", encoding="utf-8", newline="\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "setup")
    git(repo, "switch", "-q", "-c", "agent/T1-attempt-1")
    (repo / "pilot" / "app" / "backend").mkdir(parents=True)
    (repo / "pilot" / "app" / "backend" / "__init__.py").write_text("", encoding="utf-8", newline="\n")
    # newline="\n": keep LF on Windows too, like files written by the real agents.
    (repo / CODE).write_text(code, encoding="utf-8", newline="\n")
    (repo / "tests").mkdir()
    (repo / TEST).write_text(TEST_TEXT, encoding="utf-8", newline="\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "agent: first patch")
    return repo


def make_orchestrator():
    """Real Orchestrator gate logic, without Gemini (H1, H2 and T1 approved)."""
    orchestrator = OrchestratorAgent.__new__(OrchestratorAgent)
    orchestrator.workflow_state = {
        "backlog_approved": True,
        "architecture_approved": True,
        "tasks": {"T1": {**TASK, "status": "approved"}},
        "approval_history": [],
    }
    return orchestrator


def diff(path, old, new):
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="\n",
    ))


def result(patch, changed, tests_added=(TEST,)):
    return BackendDeveloperResult(
        task_id="T1",
        summary="Fix the add function. It returned a difference.",
        patch=patch,
        changed_files=[{"path": path, "purpose": "fix"} for path in changed],
        acceptance_criteria=[{"criterion": "add works", "status": "addressed", "evidence": "test"}],
        tests_added=list(tests_added),
    )


class FakeBackendAgent:
    """Returns a prepared fix and remembers what it was asked."""

    def __init__(self, fix=None):
        self.fix = fix
        self.work_packages = []

    def create_patch(self, work_package):
        self.work_packages.append(work_package)
        return self.fix


def run_loop(repo, first, backend):
    def save_proposal(fix, label):
        folder = repo / "outputs" / "proposed_changes"
        folder.mkdir(parents=True, exist_ok=True)
        patch_path = folder / f"T1_backend_{label}test.patch"
        report_path = folder / f"T1_backend_{label}test_report.json"
        patch_path.write_text(fix.patch, encoding="utf-8", newline="\n")
        report_path.write_text(fix.model_dump_json(), encoding="utf-8")
        return patch_path, report_path

    return run_correction_loop(
        orchestrator=make_orchestrator(), task=TASK, architecture="# Architecture",
        pilot_root=repo, first_result=first, branch="agent/T1-attempt-1",
        backend_agent=backend, git_agent=PilotGitAgent(repo),
        build_project_context=lambda _root: {"existing_files": {}},
        save_proposal=save_proposal,
    )


def test_passing_tests_need_no_fix(tmp_path):
    repo = make_agent_branch(tmp_path, code=FIXED)
    backend = FakeBackendAgent()

    report = run_loop(repo, result("unused", [CODE]), backend)

    assert [run.passed for run in report.runs] == [True]
    assert report.fix_attempts == [] and backend.work_packages == []
    assert report.decision == "ready_for_review"


def test_failing_tests_get_one_fix_on_the_same_branch(tmp_path):
    repo = make_agent_branch(tmp_path)
    backend = FakeBackendAgent(result(diff(CODE, BUGGY, FIXED), [CODE]))

    report = run_loop(repo, result("unused", [CODE]), backend)

    assert [run.passed for run in report.runs] == [False, True]
    assert report.fix_attempts[0].outcome == "committed"
    assert report.final_result == "passed" and report.decision == "ready_for_review"
    # The agent received the real failure, and committed the fix itself.
    instructions = backend.work_packages[0]["instructions"]
    assert "CORRECTION REQUEST" in instructions and "FROZEN" in instructions and "assert" in instructions
    assert git(repo, "branch", "--show-current") == "agent/T1-attempt-1"
    assert git(repo, "log", "-1", "--format=%an", report.fix_attempts[0].code_commit) == AGENT_NAME
    assert git(repo, "status", "--porcelain") == ""
    # The report separates generated tests, tests run and results.
    assert report.tests_generated == [TEST] and report.tests_run == [TEST]


def test_a_fix_that_changes_the_tests_is_rejected(tmp_path):
    repo = make_agent_branch(tmp_path)
    weakened = TEST_TEXT.replace("== 5", "== -1")
    backend = FakeBackendAgent(result(diff(TEST, TEST_TEXT, weakened), [TEST]))

    report = run_loop(repo, result("unused", [CODE]), backend)

    assert report.fix_attempts[0].outcome == "rejected_frozen_tests"
    assert report.decision == "needs_revision"
    assert (repo / TEST).read_text(encoding="utf-8") == TEST_TEXT  # tests unchanged
    assert git(repo, "status", "--porcelain") == ""  # rejected proposal kept as a commit


def test_a_fix_that_still_fails_needs_revision(tmp_path):
    repo = make_agent_branch(tmp_path)
    still_wrong = "def add(a, b):\n    return a * b\n"
    backend = FakeBackendAgent(result(diff(CODE, BUGGY, still_wrong), [CODE]))

    report = run_loop(repo, result("unused", [CODE]), backend)

    assert [run.passed for run in report.runs] == [False, False]
    assert len(backend.work_packages) == 1  # only one automatic attempt
    assert report.final_result == "failed" and report.decision == "needs_revision"


def test_no_declared_tests_means_no_evidence(tmp_path):
    repo = make_agent_branch(tmp_path, code=FIXED)

    report = run_loop(repo, result("unused", [CODE], tests_added=()), FakeBackendAgent())

    assert report.runs == []
    assert report.final_result == "no_tests" and report.decision == "needs_revision"
