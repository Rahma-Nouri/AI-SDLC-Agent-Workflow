"""I1 (#77): every workflow run leaves complete evidence for the evaluation.

1. the run log (console output + the human's answers, no secrets),
2. the Gemini usage in the agents' reports,
3. a record for every H2 approve,
4. a required reason at H3 approve.

Offline only: fake Gemini clients, temporary Git repositories.
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import main as workflow_main
from agents.agent_contracts import LlmUsage, llm_usage_from_response, sum_llm_usage
from agents.backend_developer import BackendDeveloperAgent
from agents.documentation_agent import DocumentationAgent
from agents.frontend_developer import FrontendDeveloperAgent
from agents.pilot_git_agent import PilotGitAgent
from agents.run_log import RunLog, command_name, run_log_folder
from test_backend_developer_agent import valid_response, valid_work_package
from test_correction_loop import BUGGY, CODE, FIXED, FakeBackendAgent, diff, make_agent_branch, result, run_loop
from test_documentation_agent import make_docs_repo
from test_documentation_agent import model_answer as docs_answer
from test_frontend_agent import approve_h2, make_frontend_repo  # noqa: F401 (fixture)
from test_frontend_agent import model_answer as frontend_answer
from test_frontend_agent import APP, PACKAGE, GateOrchestrator
from test_h2_revise_agent import ARCH, REVISED, TASKS, make_architect, make_h2_setup, make_orchestrator, review, script
from test_h3_review import FakeGitHub, make_review_setup
from test_h3_review import review as h3_review

WORKFLOW_ROOT = Path(workflow_main.__file__).resolve().parent
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

USAGE = SimpleNamespace(prompt_token_count=1200, candidates_token_count=300, total_token_count=1750)


class UsageClient:
    """A fake Gemini that also reports token usage, like the real response's usage_metadata."""

    def __init__(self, answer, usage=USAGE):
        self.answer = answer
        self.usage = usage
        self.requests = []
        self.models = SimpleNamespace(generate_content=self.generate_content)

    def generate_content(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(text=self.answer, candidates=None, usage_metadata=self.usage)


# --- 1. the run log ---------------------------------------------------------------

def gate_like_command():
    """What a main.py command does: print, warn on stderr, and ask at a gate."""
    print("STEP 1 - working")
    print("a warning", file=sys.stderr)
    answer = input("Enter 'approve' or 'reject': ")
    print(f"You chose {answer}.")
    print("Key in use: secret-gemini-key-123 and token gh-token-456")


def run_with_fake_console(monkeypatch, typed, logged=None):
    """Run gate_like_command with a fake console; returns (stdout, stderr) as the human sees them."""
    stdout, stderr = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(sys, "stdin", io.StringIO(typed))
    if logged is None:
        gate_like_command()
    else:
        with logged:
            gate_like_command()
    return stdout.getvalue(), stderr.getvalue()


def test_run_log_keeps_console_output_and_typed_answers_without_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "secret-gemini-key-123")
    monkeypatch.setenv("GITHUB_TOKEN", "gh-token-456")
    no_env_file = tmp_path / "missing.env"

    plain = run_with_fake_console(monkeypatch, "approve\n")
    run_log = RunLog("backend-patch", tmp_path / "logs", argv=["--backend-patch", "T2"], env_file=no_env_file)
    logged = run_with_fake_console(monkeypatch, "approve\n", run_log)

    # The console looks exactly the same with and without the log.
    assert logged == plain
    assert "secret-gemini-key-123" in logged[0]  # the console itself is not changed

    assert re.fullmatch(r"backend-patch_\d{8}_\d{6}\.log", run_log.path.name)
    log = run_log.path.read_text(encoding="utf-8")
    assert "python main.py --backend-patch T2" in log
    assert "STEP 1 - working" in log and "a warning" in log  # stdout and stderr
    assert "Enter 'approve' or 'reject': approve\n" in log  # the prompt AND the human's answer
    assert "You chose approve." in log
    assert "secret-gemini-key-123" not in log and "gh-token-456" not in log
    assert log.count("[REDACTED]") == 2
    # The real console streams are back after the run.
    assert not type(sys.stdout).__name__.startswith("_")


def test_run_log_hides_secrets_from_the_env_file_and_records_a_crash(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("GITHUB_TOKEN=file-token-789\n", encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    run_log = RunLog("h3-review", tmp_path, argv=[], env_file=env_file)
    with pytest.raises(RuntimeError):
        with run_log:
            print("token file-token-789")
            raise RuntimeError("boom file-token-789")

    log = run_log.path.read_text(encoding="utf-8")
    assert "file-token-789" not in log
    assert "token [REDACTED]" in log
    assert "RuntimeError: boom [REDACTED]" in log  # the traceback is evidence too


def test_two_runs_in_the_same_second_get_two_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    paths = []
    for _ in range(2):
        with RunLog("run", tmp_path, argv=[], env_file=tmp_path / "missing.env") as run_log:
            print("hello")
        paths.append(run_log.path)
    assert paths[0] != paths[1] and all(path.is_file() for path in paths)


def test_log_folder_and_command_name(tmp_path, monkeypatch):
    monkeypatch.delenv("RUN_LOG_DIR", raising=False)
    # Default: next to the workflow folder, so outside both repositories.
    assert run_log_folder(WORKFLOW_ROOT) == WORKFLOW_ROOT.parent / "AI-SDLC-run-logs"
    monkeypatch.setenv("RUN_LOG_DIR", str(tmp_path))
    assert run_log_folder(WORKFLOW_ROOT) == tmp_path

    assert command_name(["--backend-patch", "T2"]) == "backend-patch"
    assert command_name(["--h3-review"]) == "h3-review"
    assert command_name([]) == "run"


def test_main_py_writes_the_run_log_for_a_real_command(tmp_path):
    """The real entry point: `python main.py --help` (offline, no Gemini) leaves a log."""
    completed = subprocess.run(
        [sys.executable, str(WORKFLOW_ROOT / "main.py"), "--help"],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
        env={**os.environ, "RUN_LOG_DIR": str(tmp_path)},
    )

    assert completed.returncode == 0
    logs = list(tmp_path.glob("help_*.log"))
    assert len(logs) == 1
    log = logs[0].read_text(encoding="utf-8")
    assert "--backend-patch TASK" in completed.stdout and "--backend-patch TASK" in log


# --- 2. the Gemini usage ------------------------------------------------------------

def test_backend_report_has_the_gemini_usage(tmp_path):
    agent = BackendDeveloperAgent(client=UsageClient(json.dumps(valid_response())), model="test-model")

    patch_result = agent.create_patch(valid_work_package())
    _patch_path, report_path = workflow_main.save_patch_proposal(patch_result, tmp_path)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["llm_usage"] == {"requests": 1, "prompt_tokens": 1200, "output_tokens": 300, "total_tokens": 1750}


def test_missing_usage_data_is_saved_as_null_not_guessed(tmp_path):
    agent = BackendDeveloperAgent(client=UsageClient(json.dumps(valid_response()), usage=None), model="test-model")

    _patch_path, report_path = workflow_main.save_patch_proposal(agent.create_patch(valid_work_package()), tmp_path)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["llm_usage"] == {"requests": 1, "prompt_tokens": None, "output_tokens": None, "total_tokens": None}


def test_a_refused_answer_still_counts_its_tokens():
    agent = BackendDeveloperAgent(client=UsageClient("not JSON"), model="test-model")

    with pytest.raises(ValueError):
        agent.create_patch(valid_work_package())

    assert agent.last_llm_usage == LlmUsage(requests=1, prompt_tokens=1200, output_tokens=300, total_tokens=1750)


def test_usage_sums_and_unknown_counts():
    known = llm_usage_from_response(SimpleNamespace(usage_metadata=USAGE))
    unknown = llm_usage_from_response(SimpleNamespace())

    assert sum_llm_usage([known, known]) == LlmUsage(requests=2, prompt_tokens=2400, output_tokens=600, total_tokens=3500)
    # One unknown count makes that sum unknown: never a guess.
    assert sum_llm_usage([known, unknown]) == LlmUsage(requests=2, prompt_tokens=None, output_tokens=None, total_tokens=None)
    assert sum_llm_usage([]) == LlmUsage(requests=0, prompt_tokens=0, output_tokens=0, total_tokens=0)


@needs_git
def test_frontend_report_has_the_gemini_usage(tmp_path, approve_h2):
    repo = make_frontend_repo(tmp_path)
    gitignore = "__pycache__/\n.venv/\nnode_modules/\ndist/\n"
    agent = FrontendDeveloperAgent(
        client=UsageClient(frontend_answer({PACKAGE: "{}", APP: "export {}", ".gitignore": gitignore})), model="test-model",
    )

    outcome = workflow_main.run_frontend_patch(
        repo, GateOrchestrator(), "T7", agent=agent,
        git_agent=PilotGitAgent(repo),
    )

    report = json.loads(outcome["report_path"].read_text(encoding="utf-8"))
    assert report["llm_usage"]["requests"] == 1 and report["llm_usage"]["total_tokens"] == 1750


@needs_git
def test_documentation_report_has_the_gemini_usage(tmp_path, approve_h2):
    repo = make_docs_repo(tmp_path)
    agent = DocumentationAgent(client=UsageClient(docs_answer()), model="test-model")

    workflow_main.run_docs_patch(
        repo, GateOrchestrator(), "T1", agent=agent,
        git_agent=PilotGitAgent(repo),
    )

    [report_path] = (repo / "outputs" / "proposed_changes").glob("T1_docs_*_report.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["llm_usage"]["prompt_tokens"] == 1200


@needs_git
def test_loop_report_sums_the_usage_of_its_fix_requests(tmp_path):
    repo = make_agent_branch(tmp_path)
    fix = result(diff(CODE, BUGGY, FIXED), [CODE]).model_copy(
        update={"llm_usage": LlmUsage(requests=1, prompt_tokens=10, output_tokens=5, total_tokens=20)}
    )

    report = run_loop(repo, result("unused", [CODE]), FakeBackendAgent(fix))

    assert report.llm_usage == LlmUsage(requests=1, prompt_tokens=10, output_tokens=5, total_tokens=20)


@needs_git
def test_loop_counts_a_failed_fix_request_and_zero_without_requests(tmp_path):
    class RefusedAgent:
        """Got an answer from Gemini (and spent tokens), but the answer was refused."""

        last_llm_usage = LlmUsage(requests=1, prompt_tokens=7, output_tokens=3, total_tokens=12)

        def create_patch(self, _work_package):
            raise ValueError("invalid answer")

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    failed = run_loop(make_agent_branch(tmp_path / "a"), result("unused", [CODE]), RefusedAgent())
    passed = run_loop(make_agent_branch(tmp_path / "b", code=FIXED), result("unused", [CODE]), FakeBackendAgent())

    assert failed.llm_usage.requests == 1 and failed.llm_usage.total_tokens == 12
    assert passed.llm_usage == LlmUsage(requests=0, prompt_tokens=0, output_tokens=0, total_tokens=0)


@needs_git
def test_h2_revise_record_has_the_architects_usage(tmp_path):
    repo, _, _ = make_h2_setup(tmp_path)

    review(repo, script("revise", "Add a cache section", "approve"), make_architect(REVISED))

    [record_path] = (repo / "outputs" / "approvals").glob("architecture_H2_revise_*.json")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    # The fake Gemini reports no usage: one request, token counts null.
    assert record["llm_usage"] == {"requests": 1, "prompt_tokens": None, "output_tokens": None, "total_tokens": None}


# --- 3. the H2 approve record --------------------------------------------------------

@needs_git
def test_h2_approve_without_revise_saves_a_decision_record(tmp_path):
    repo, _, _ = make_h2_setup(tmp_path)

    approved = workflow_main.review_and_approve_architecture(
        orchestrator=make_orchestrator(), architect=None, architecture_path=repo / ARCH, tasks=TASKS,
        ask=script("approve"),
    )

    assert approved is True
    [record_path] = (repo / "outputs" / "approvals").glob("architecture_H2_approve_*.json")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["gate"] == "H2" and record["decision"] == "approve" and record["branch"] is None
    assert record["decided_at"]


@needs_git
def test_h2_reject_writes_no_approve_record(tmp_path):
    repo, _, _ = make_h2_setup(tmp_path)

    review(repo, script("reject"))

    assert not list((repo / "outputs" / "approvals").glob("architecture_H2_approve_*.json"))


# --- 4. the H3 approve reason ---------------------------------------------------------

@needs_git
def test_h3_approve_asks_again_until_a_reason_is_given(tmp_path, capsys):
    repo, _ = make_review_setup(tmp_path, FIXED, tests_passed=True)

    outcome, orchestrator = h3_review(repo, ["approve", "", "   ", "All tests pass", "no"], github=FakeGitHub())

    assert outcome["decision"] == "approve"
    assert capsys.readouterr().out.count("A reason is required.") == 2
    [record_path] = (repo / "outputs" / "approvals").glob("T1_H3_approve_*.json")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["reason"] == "All tests pass"
    assert orchestrator.workflow_state["approval_history"][-1]["notes"] == "All tests pass"
