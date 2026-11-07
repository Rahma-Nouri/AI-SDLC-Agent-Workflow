import json
import shutil
import subprocess
import sys

import pytest

from agents.correction_loop import CorrectionLoopReport, TestRunSummary
from agents.h3_review import run_h3_review, show_agent_reports
from agents.pilot_environment import EnvironmentResult
from agents.pilot_git_agent import AGENT_NAME, PilotGitAgent
from test_correction_loop import (
    BUGGY, CODE, FIXED, TASK, TEST, TEST_TEXT, FakeBackendAgent, diff, make_agent_branch, make_orchestrator, result,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


class ReadyEnvironment:
    def prepare(self):
        return EnvironmentResult(status="ready", python=sys.executable, packages=["pytest"], installed_now=False, detail="test")


class FakeGitHub:
    def __init__(self):
        self.comments = []

    def list_all_issues(self):
        return [{"number": 47, "labels": [{"name": "world: pilot"}], "body": "<!-- ai-sdlc:pilot-task-id=T1 -->"}]

    def comment_on_issue(self, number, comment):
        self.comments.append((number, comment))


def make_review_setup(tmp_path, code, tests_passed):
    """An agent branch waiting for H3, with a latest test report and a 'GitHub' remote."""
    repo = make_agent_branch(tmp_path, code=code)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "origin", "pilot/main")

    report = CorrectionLoopReport(
        task_id="T1", branch="agent/T1-attempt-1", tests_generated=[TEST], tests_run=[TEST],
        runs=[TestRunSummary(attempt=0, command="pytest", exit_code=0 if tests_passed else 1,
                             passed=tests_passed, output_tail="1 passed" if tests_passed else "assert -1 == 5")],
        fix_attempts=[], final_result="passed" if tests_passed else "failed",
        decision="ready_for_review" if tests_passed else "needs_revision",
    )
    folder = repo / "outputs" / "test_results"
    folder.mkdir(parents=True)
    (folder / "T1_retest_20261102_220000_000000.json").write_text(report.model_dump_json(), encoding="utf-8", newline="\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "agent: test report")
    return repo, remote


def review(repo, answers, backend=None, github=None):
    orchestrator = make_orchestrator()
    orchestrator.workflow_state["implementation_accepted"] = False

    def save_proposal(fix, label):
        folder = repo / "outputs" / "proposed_changes"
        folder.mkdir(parents=True, exist_ok=True)
        patch_path = folder / f"T1_backend_{label}test.patch"
        report_path = folder / f"T1_backend_{label}test_report.json"
        patch_path.write_text(fix.patch, encoding="utf-8", newline="\n")
        report_path.write_text(fix.model_dump_json(), encoding="utf-8", newline="\n")
        return patch_path, report_path

    outcome = run_h3_review(
        orchestrator=orchestrator, task=TASK, architecture="# Architecture", pilot_root=repo,
        git_agent=PilotGitAgent(repo), environment=ReadyEnvironment(),
        backend_agent_factory=lambda: backend, github=github,
        build_project_context=lambda _root: {"existing_files": {}},
        save_proposal=save_proposal, ask=lambda _prompt: answers.pop(0),
    )
    return outcome, orchestrator


def add_agent_report(repo, name, **fields):
    """Commit one Backend Agent report on the agent branch."""
    folder = repo / "outputs" / "proposed_changes"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps({"task_id": name.split("_")[0], **fields}), encoding="utf-8", newline="\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", f"agent: {name}")


def test_h3_shows_the_agent_reports_of_this_attempt_only(tmp_path, capsys):
    repo, _ = make_review_setup(tmp_path, FIXED, tests_passed=True)
    add_agent_report(repo, "T1_backend_20261102_100000_000000_report.json", unresolved_issues=["Earlier failed attempt"])
    add_agent_report(
        repo, "T1_backend_20261102_110000_000000_report.json",
        unresolved_issues=["Open problem"], assumptions=["Uses SQLite"],
        acceptance_criteria=[{"criterion": "Adds numbers", "status": "addressed", "evidence": "first"}],
    )
    add_agent_report(
        repo, "T1_backend_fix1_20261102_120000_000000_report.json",
        unresolved_issues=["Open problem", "New problem"],
        acceptance_criteria=[{"criterion": "Adds numbers", "status": "not_addressed", "evidence": "fix"}],
    )
    add_agent_report(repo, "T2_backend_20261102_130000_000000_report.json", unresolved_issues=["Other task"])

    review(repo, ["reject", "test"], github=FakeGitHub())
    screen = capsys.readouterr().out

    assert screen.count("Open problem") == 1  # in two reports, shown once
    assert "New problem" in screen
    assert "Uses SQLite" in screen
    assert "[not_addressed] Adds numbers" in screen  # the newest status counts
    assert "[addressed] Adds numbers" not in screen
    assert "Other task" not in screen
    assert "Earlier failed attempt" not in screen


def test_h3_prints_none_reported_for_missing_fields(tmp_path, capsys):
    repo, _ = make_review_setup(tmp_path, FIXED, tests_passed=True)
    add_agent_report(repo, "T1_backend_20261102_110000_000000_report.json")

    review(repo, ["reject", "test"], github=FakeGitHub())
    screen = capsys.readouterr().out

    assert "Unresolved issues: none reported" in screen
    assert "Assumptions: none reported" in screen
    assert "Acceptance criteria (newest report): none reported" in screen


def test_h3_prints_none_reported_without_any_report(capsys):
    show_agent_reports([])

    assert capsys.readouterr().out.count("none reported") == 3


def test_approve_merges_into_pilot_main_and_pushes(tmp_path):
    repo, remote = make_review_setup(tmp_path, FIXED, tests_passed=True)
    github = FakeGitHub()

    outcome, orchestrator = review(repo, ["approve", "Looks good"], github=github)

    assert outcome["decision"] == "approve" and outcome["pushed"]
    assert git(repo, "branch", "--show-current") == "pilot/main"
    assert git(repo, "branch", "--list", "agent/*") == ""
    assert git(repo, "log", "-1", "--format=%an | %s") == f"{AGENT_NAME} | [T1] Merge agent/T1-attempt-1 after the human's H3 approval"
    # GitHub (here: the local remote) now has the merged pilot/main.
    assert git(remote, "rev-parse", "pilot/main") == git(repo, "rev-parse", "pilot/main")
    assert (repo / CODE).read_text(encoding="utf-8") == FIXED
    assert any(name.startswith("outputs/approvals/T1_H3_approve_") for name in git(repo, "ls-files").splitlines())
    assert github.comments and github.comments[0][0] == 47
    assert orchestrator.workflow_state["implementation_accepted"] is True


def test_approving_failing_tests_needs_a_second_confirmation(tmp_path):
    repo, remote = make_review_setup(tmp_path, BUGGY, tests_passed=False)
    before = git(remote, "rev-parse", "pilot/main")

    outcome, _ = review(repo, ["approve", "cancel"], github=FakeGitHub())

    assert outcome["decision"] == "cancelled"
    assert git(repo, "branch", "--show-current") == "agent/T1-attempt-1"
    assert git(remote, "rev-parse", "pilot/main") == before


def test_reject_deletes_the_branch_and_keeps_the_decision(tmp_path):
    repo, remote = make_review_setup(tmp_path, BUGGY, tests_passed=False)
    before = git(repo, "rev-parse", "pilot/main")
    github = FakeGitHub()

    outcome, _ = review(repo, ["reject", "Wrong approach"], github=github)

    assert outcome["decision"] == "reject"
    assert git(repo, "branch", "--show-current") == "pilot/main"
    assert git(repo, "branch", "--list", "agent/*") == ""
    assert git(repo, "rev-parse", "pilot/main") == before  # pilot/main unchanged
    # The decision is kept as a run record; the next agent branch commits it.
    assert "outputs/approvals/" in git(repo, "status", "--porcelain", "--untracked-files=all")
    assert "Wrong approach" in github.comments[0][1]


def test_revise_with_frozen_tests_fixes_the_code_and_retests(tmp_path):
    repo, _ = make_review_setup(tmp_path, BUGGY, tests_passed=False)
    backend = FakeBackendAgent(result(diff(CODE, BUGGY, FIXED), [CODE]))

    outcome, _ = review(repo, ["revise", "add must return a sum", "no"], backend=backend, github=FakeGitHub())

    report = outcome["revision_report"]
    assert report.trigger == "h3_revise" and report.tests_may_change is False
    assert [run.passed for run in report.runs] == [True]
    assert report.decision == "ready_for_review"
    instructions = backend.work_packages[0]["instructions"]
    assert "add must return a sum" in instructions and "FROZEN" in instructions
    assert git(repo, "branch", "--show-current") == "agent/T1-attempt-1"


def test_revise_may_change_tests_only_when_the_human_allows_it(tmp_path):
    repo, _ = make_review_setup(tmp_path, BUGGY, tests_passed=False)
    corrected_test = TEST_TEXT.replace("== 5", "== -1")  # pretend the test itself was wrong
    test_fix = result(diff(TEST, TEST_TEXT, corrected_test), [TEST])

    refused, _ = review(repo, ["revise", "the test is wrong", "no"], backend=FakeBackendAgent(test_fix), github=FakeGitHub())
    assert refused["revision_report"].fix_attempts[0].outcome == "rejected_frozen_tests"

    allowed, _ = review(repo, ["revise", "the test is wrong", "yes"], backend=FakeBackendAgent(test_fix), github=FakeGitHub())
    report = allowed["revision_report"]
    assert report.tests_may_change is True
    assert report.fix_attempts[0].outcome == "committed"
    assert [run.passed for run in report.runs] == [True]
