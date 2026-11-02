import subprocess
import sys

import pytest

import main as workflow_main
from agents.pilot_environment import EnvironmentResult, PilotEnvironment, read_requirements


def make_environment(tmp_path, requirements, answers):
    """A pilot folder plus a PilotEnvironment whose installer is faked (offline)."""
    (tmp_path / "requirements.txt").write_text(requirements, encoding="utf-8")
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return answers.pop(0)

    environment = PilotEnvironment(tmp_path, ask=ask)
    commands = []

    def fake_run(*args):
        commands.append(list(args))
        if "venv" in args:  # "python -m venv .venv" creates the interpreter
            environment.python.parent.mkdir(parents=True, exist_ok=True)
            environment.python.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    environment._run = fake_run
    return environment, asked, commands


def test_requirements_ignore_comments_and_empty_lines(tmp_path):
    (tmp_path / "requirements.txt").write_text("# db\nSQLAlchemy>=2.0\n\nfastapi  # web\n", encoding="utf-8")

    assert read_requirements(tmp_path / "requirements.txt") == ["SQLAlchemy>=2.0", "fastapi"]


def test_new_packages_are_installed_only_after_approval(tmp_path):
    environment, asked, commands = make_environment(tmp_path, "SQLAlchemy>=2.0\n", ["approve"])

    result = environment.prepare()

    assert result.status == "ready" and result.installed_now
    assert len(asked) == 1
    assert "venv" in commands[0]
    install = commands[1]
    assert install[1:4] == ["-m", "pip", "install"] and "SQLAlchemy>=2.0" in install
    assert any(item.startswith("pytest") for item in install)


def test_unchanged_packages_are_not_asked_again(tmp_path):
    environment, asked, _ = make_environment(tmp_path, "SQLAlchemy>=2.0\n", ["approve"])
    environment.prepare()

    second = environment.prepare()

    assert second.status == "ready" and not second.installed_now
    assert len(asked) == 1  # no second question


def test_a_changed_package_list_is_asked_again(tmp_path):
    environment, asked, _ = make_environment(tmp_path, "SQLAlchemy>=2.0\n", ["approve", "approve"])
    environment.prepare()
    (tmp_path / "requirements.txt").write_text("SQLAlchemy>=2.0\nfastapi\n", encoding="utf-8")

    environment.prepare()

    assert len(asked) == 2


def test_rejected_packages_are_not_installed(tmp_path):
    environment, _, commands = make_environment(tmp_path, "SQLAlchemy>=2.0\n", ["reject"])

    result = environment.prepare()

    assert result.status == "rejected" and result.python is None
    assert commands == []


def test_pip_options_are_refused_before_asking(tmp_path):
    environment, asked, commands = make_environment(
        tmp_path, "--index-url https://example.invalid/simple\nSQLAlchemy\n", ["approve"]
    )

    result = environment.prepare()

    assert result.status == "failed" and "not allowed" in result.detail
    assert asked == [] and commands == []


# --- the re-test command, on a real temporary agent branch -----------------

from test_correction_loop import FIXED, TASK, make_agent_branch, make_orchestrator  # noqa: E402


class ReadyEnvironment:
    """Stands in for the pilot .venv: the workflow's own Python is enough here."""

    def prepare(self):
        return EnvironmentResult(status="ready", python=sys.executable, packages=["pytest"], installed_now=False, detail="test")


def prepare_retest(monkeypatch, tmp_path, code):
    repo = make_agent_branch(tmp_path, code=code)
    snapshot = {"plan": "Plan.", "tasks": [TASK], "architecture": "# Architecture"}
    (repo / "pilot" / "architecture_design.md").write_text("# Architecture", encoding="utf-8", newline="\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "architecture"], check=True)
    monkeypatch.setattr(workflow_main, "load_approved_context", lambda _path: snapshot)
    monkeypatch.setattr(workflow_main, "save_approved_context", lambda **_kwargs: None)
    orchestrator = make_orchestrator()
    def approve_h1(_plan, tasks):
        # Like the real H1: approving the backlog approves its tasks.
        for task in tasks:
            orchestrator.workflow_state["tasks"][task["task_id"]]["status"] = "approved"
        return "approve"

    orchestrator.request_backlog_approval = approve_h1
    monkeypatch.setattr(workflow_main, "review_and_approve_architecture", lambda **_kwargs: True)
    return repo, orchestrator


def test_retest_runs_the_branch_tests_without_any_fix(tmp_path, monkeypatch):
    repo, orchestrator = prepare_retest(monkeypatch, tmp_path, FIXED)

    report = workflow_main.run_retest_agent_branch(repo, orchestrator, environment=ReadyEnvironment())

    assert report.tests_run == ["tests/test_backend_calc.py"]
    assert [run.passed for run in report.runs] == [True]
    assert report.fix_attempts == []
    assert report.decision == "ready_for_review"
    log = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%s"], capture_output=True, text=True).stdout
    assert "retest report" in log


def test_retest_stops_when_the_packages_are_rejected(tmp_path, monkeypatch):
    repo, orchestrator = prepare_retest(monkeypatch, tmp_path, FIXED)

    class Rejected:
        def prepare(self):
            return EnvironmentResult(status="rejected", python=None, packages=["x"], installed_now=False, detail="no")

    report = workflow_main.run_retest_agent_branch(repo, orchestrator, environment=Rejected())

    assert report.runs == []
    assert report.final_result == "environment_not_ready" and report.decision == "needs_revision"
