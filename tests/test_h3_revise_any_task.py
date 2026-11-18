"""I3 (issue #79): H3 "revise" works on a backend agent branch of any task, not only T1.

The setup is like the real pilot before the second T2 attempt: T1 is already
merged into pilot/main (with its own test file), and agent/T2-attempt-1 waits
for H3. The real Backend Developer Agent runs with a fake Gemini client that
also reports token counts, so the saved llm_usage can be checked.

T1's test on pilot/main FAILS on purpose: if a T2 revision ran T1's tests
instead of (or together with) T2's own tests, the result would be "failed".
"""

import json
import shutil
from types import SimpleNamespace

import pytest

import main as workflow_main
from agents.backend_developer import BackendDeveloperAgent
from agents.pilot_git_agent import PilotGitAgent
from test_backend_any_task import CODE, TEST, TEST_TEXT, ReadyEnvironment, answers, make_backend_repo, model_answer
from test_frontend_agent import GateOrchestrator, approve_h2  # noqa: F401 (approve_h2 is a fixture)
from test_pilot_git_agent import git

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

BRANCH = "agent/T2-attempt-1"
T1_TEST = "tests/test_backend_database_schema.py"
T1_CODE = "pilot/app/backend/database.py"

# The first patch and the automatic fix are both wrong (the tests fail), so the
# branch waits for H3 with a failed result. The H3 revision makes them pass.
BUGGY = "def extract_text(data):\n    return 'wrong'\n"
STILL_BUGGY = "def extract_text(data):\n    return data\n"
FIXED = (
    "def extract_text(data):\n"
    "    if not data:\n"
    "        raise ValueError('empty document')\n"
    "    return data.decode('utf-8')\n"
)
# A test change, used to check the "may tests change?" rule.
CHANGED_TEST = TEST_TEXT + "\n\ndef test_spaces_are_kept():\n    assert extract_text(b' a ') == ' a '\n"

T1_ISSUE, T2_ISSUE = 47, 48


class UsageClient:
    """Fake Gemini: one prepared answer per request, each with token counts."""

    def __init__(self, *answers_):
        self.answers = list(answers_)
        self.requests = []
        self.models = SimpleNamespace(generate_content=self.generate_content)

    def generate_content(self, **kwargs):
        self.requests.append(kwargs)
        usage = SimpleNamespace(prompt_token_count=1000, candidates_token_count=200, total_token_count=1200)
        return SimpleNamespace(text=self.answers.pop(0), candidates=None, usage_metadata=usage)


class FakeGitHub:
    """Two pilot issues: T1 (#47) and T2 (#48). Records comments (board updates only warn)."""

    def __init__(self):
        self.comments = []

    def list_all_issues(self):
        return [
            {"number": number, "state": "open", "labels": [{"name": "world: pilot"}],
             "body": f"<!-- ai-sdlc:pilot-task-id={task_id} -->"}
            for task_id, number in (("T1", T1_ISSUE), ("T2", T2_ISSUE))
        ]

    def comment_on_issue(self, number, comment):
        self.comments.append((number, comment))


def make_t2_branch(tmp_path, approve_h2):
    """pilot/main with T1 merged (and its failing test); agent/T2-attempt-1 waiting for H3."""
    repo = make_backend_repo(tmp_path)
    (repo / "pilot" / "app" / "backend").mkdir(parents=True)
    (repo / T1_CODE).write_text("TABLES = []\n", encoding="utf-8", newline="\n")
    (repo / "tests").mkdir()
    (repo / T1_TEST).write_text("def test_t1_schema():\n    assert False, 'T1 test was run'\n", encoding="utf-8", newline="\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "T1 merged earlier")

    client = UsageClient(model_answer({CODE: BUGGY, TEST: TEST_TEXT}), model_answer({CODE: STILL_BUGGY}))
    workflow_main.run_backend_patch(
        repo, GateOrchestrator(), "T2", agent=BackendDeveloperAgent(client=client, model="test-model"),
        git_agent=PilotGitAgent(repo), environment=ReadyEnvironment(), ask=answers("yes"),
    )
    assert git(repo, "branch", "--show-current") == BRANCH  # waiting for H3
    return repo


def h3(repo, *replies, revision_answer=None, github=None):
    """One --h3-review run. revision_answer: what the fake Gemini returns for a revise."""
    client = UsageClient(*([revision_answer] if revision_answer else []))
    outcome = workflow_main.run_h3_review_command(
        repo, GateOrchestrator(), git_agent=PilotGitAgent(repo), environment=ReadyEnvironment(),
        backend_agent=BackendDeveloperAgent(client=client, model="test-model"),
        github_client=github or FakeGitHub(), ask=answers(*replies),
    )
    return outcome, client


def committed_on_branch(repo, prefix):
    return [name for name in git(repo, "diff", "--name-only", f"pilot/main...{BRANCH}").splitlines() if name.startswith(prefix)]


def test_revise_on_t2_uses_t2_everywhere_and_runs_only_t2_tests(tmp_path, approve_h2):
    repo = make_t2_branch(tmp_path, approve_h2)
    head_before = git(repo, "rev-parse", "HEAD")

    outcome, client = h3(repo, "revise", "Refuse empty documents", "no", revision_answer=model_answer({CODE: FIXED}))

    report = outcome["revision_report"]
    assert outcome["decision"] == "revise" and outcome["branch"] == BRANCH
    assert report.task_id == "T2" and report.branch == BRANCH and report.trigger == "h3_revise"
    # The tests run are T2's own tests, never T1's (T1's test would fail).
    assert report.tests_generated == [TEST] and report.tests_run == [TEST]
    assert [run.passed for run in report.runs] == [True] and report.final_result == "passed"
    assert T1_TEST not in report.runs[0].command

    # The prompt is about T2, carries the feedback, and freezes T2's tests.
    prompt = client.requests[0]["contents"]
    assert "backend slice of T2" in prompt and "slice of T1" not in prompt
    assert "Refuse empty documents" in prompt and f"branch {BRANCH}" in prompt
    assert f"including: {TEST}." in prompt

    # The revision commit lands on the T2 branch, by the Backend Agent, named T2.
    assert git(repo, "branch", "--show-current") == BRANCH
    log = git(repo, "log", f"{head_before}..HEAD", "--format=%an | %s").splitlines()
    assert "AI-SDLC Backend Agent | [T2] Fix: H3 revision: Add the text extraction" in log
    assert all(line.startswith("AI-SDLC Backend Agent | [T2] ") for line in log)
    assert (repo / CODE).read_text(encoding="utf-8") == FIXED

    # Every record is named after T2 and committed on the branch.
    assert committed_on_branch(repo, "outputs/proposed_changes/T2_backend_revise1_")
    assert committed_on_branch(repo, "outputs/approvals/T2_H3_revise_")
    (saved,) = committed_on_branch(repo, "outputs/test_results/T2_h3_revise_")
    assert not committed_on_branch(repo, "outputs/test_results/T1_")
    assert git(repo, "status", "--porcelain") == ""

    # The revision's Gemini usage is saved in the committed report.
    usage = json.loads((repo / saved).read_text(encoding="utf-8"))["llm_usage"]
    assert usage == {"requests": 1, "prompt_tokens": 1000, "output_tokens": 200, "total_tokens": 1200}


def test_frozen_tests_reject_a_revision_that_changes_a_t2_test(tmp_path, approve_h2):
    repo = make_t2_branch(tmp_path, approve_h2)
    head_before = git(repo, "rev-parse", "HEAD")

    outcome, _ = h3(repo, "revise", "Keep spaces", "no", revision_answer=model_answer({CODE: FIXED, TEST: CHANGED_TEST}))

    report = outcome["revision_report"]
    assert [fix.outcome for fix in report.fix_attempts] == ["rejected_frozen_tests"]
    assert TEST in report.fix_attempts[0].detail
    assert report.final_result == "failed" and report.runs == []
    # Neither the code nor the test changed; only records were committed.
    assert (repo / TEST).read_text(encoding="utf-8") == TEST_TEXT
    assert (repo / CODE).read_text(encoding="utf-8") == STILL_BUGGY
    changed = git(repo, "diff", "--name-only", head_before, "HEAD").splitlines()
    assert changed and all(name.startswith("outputs/") for name in changed)
    assert git(repo, "status", "--porcelain") == ""


def test_allowed_test_changes_are_committed_and_run(tmp_path, approve_h2):
    repo = make_t2_branch(tmp_path, approve_h2)

    outcome, client = h3(repo, "revise", "Keep spaces", "yes", revision_answer=model_answer({CODE: FIXED, TEST: CHANGED_TEST}))

    report = outcome["revision_report"]
    assert report.tests_may_change is True
    assert [fix.outcome for fix in report.fix_attempts] == ["committed"]
    assert (repo / TEST).read_text(encoding="utf-8") == CHANGED_TEST
    assert report.tests_run == [TEST] and report.final_result == "passed"
    assert "ALLOWED changes to test files" in client.requests[0]["contents"]
    assert git(repo, "branch", "--show-current") == BRANCH


def test_a_second_h3_shows_the_revision_result_and_comments_on_the_t2_issue(tmp_path, approve_h2, capsys):
    repo = make_t2_branch(tmp_path, approve_h2)
    h3(repo, "revise", "Refuse empty documents", "no", revision_answer=model_answer({CODE: FIXED}))
    capsys.readouterr()  # forget the first review's screen

    github = FakeGitHub()
    # Tests pass now, so approve needs no second confirmation.
    outcome, client = h3(repo, "approve", "T2 revision fixed it", "no", github=github)

    screen = capsys.readouterr().out
    assert "Task:   T2" in screen and f"Branch: {BRANCH}" in screen
    assert "h3_revise: passed -> ready_for_review" in screen
    assert "Run 1: exit code 0, passed True" in screen
    assert client.requests == []  # approve makes no Gemini request

    assert outcome["decision"] == "approve"
    assert git(repo, "log", "pilot/main", "-1", "--format=%s").startswith(f"[T2] Merge {BRANCH}")
    assert [number for number, _ in github.comments] == [T2_ISSUE]
    record = json.loads(next((repo / "outputs" / "approvals").glob("T2_H3_approve_*.json")).read_text(encoding="utf-8"))
    assert record["task_id"] == "T2" and record["latest_tests"] == "passed" and record["issue"] == T2_ISSUE
