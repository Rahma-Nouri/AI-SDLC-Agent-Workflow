"""P2.6a: the agents keep the pilot board Status (Todo / In Progress / Done) up to date."""

import json
import shutil

import pytest

import main as workflow_main
from agents.pilot_git_agent import PilotGitAgent
from test_h2_revise_agent import REVISED, make_architect, make_h2_setup
from test_h2_revise_agent import review as h2_review
from test_h2_revise_agent import script
from test_h3_review import FIXED, git, make_review_setup, review
from test_pilot_git_agent import NEW_FILE_PATCH, make_pilot_repo

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def pilot_issue(number, task_id, state="open", label="world: pilot"):
    return {
        "number": number,
        "state": state,
        "labels": [{"name": label}],
        "body": f"<!-- ai-sdlc:pilot-task-id={task_id} -->\n**Task ID:** {task_id}",
    }


class FakeBoardGitHub:
    """Stands in for GitHub: issues, comments, and the board Status of each issue."""

    def __init__(self, issues=None, statuses=None, board_fails=False):
        self.issues = issues if issues is not None else [pilot_issue(47, "T1")]
        self.statuses = dict(statuses or {})
        self.board_fails = board_fails
        self.status_calls = []
        self.comments = []
        self.closed = []

    def list_all_issues(self):
        return self.issues

    def comment_on_issue(self, number, comment):
        self.comments.append((number, comment))

    def close_issue(self, number, comment, state_reason="not_planned"):
        self.closed.append((number, state_reason))
        next(issue for issue in self.issues if issue["number"] == number)["state"] = "closed"

    def issue_status(self, number):
        return self.statuses.get(number)

    def set_issue_status(self, number, status):
        if self.board_fails:
            raise RuntimeError("Resource not accessible by personal access token")
        self.status_calls.append((number, status))
        self.statuses[number] = status


def commit_on_pilot_main(repo, relative_path, text="{}"):
    """Commit one file on pilot/main and come back to the branch that was checked out."""
    current = git(repo, "branch", "--show-current")
    if current != "pilot/main":
        git(repo, "switch", "-q", "pilot/main")
    path = repo / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    git(repo, "add", relative_path)
    git(repo, "commit", "-q", "-m", f"setup: {relative_path}")
    if current != "pilot/main":
        git(repo, "switch", "-q", current)


def committed_approve_record(repo, task_id="T1"):
    names = [name for name in git(repo, "ls-files").splitlines() if name.startswith(f"outputs/approvals/{task_id}_H3_approve_")]
    assert len(names) == 1
    return json.loads((repo / names[0]).read_text(encoding="utf-8"))


# --- D1: agent branches --------------------------------------------------------

def test_creating_a_task_branch_moves_the_card_to_in_progress(tmp_path):
    repo, _snapshot = make_pilot_repo(tmp_path)
    proposals = repo / "outputs" / "proposed_changes"
    proposals.mkdir(parents=True)
    patch_path = proposals / "T1_backend_test.patch"
    report_path = proposals / "T1_backend_test_report.json"
    patch_path.write_text(NEW_FILE_PATCH, encoding="utf-8", newline="\n")
    report_path.write_text(json.dumps({
        "task_id": "T1", "summary": "Add the database module.",
        "changed_files": [{"path": "pilot/app/backend/db.py", "purpose": "db"}],
    }), encoding="utf-8")
    github = FakeBoardGitHub()

    commit = workflow_main.apply_patch_with_pilot_git_agent(
        repo, patch_path, report_path, [patch_path, report_path], git_agent=PilotGitAgent(repo), github=github,
    )

    assert commit.branch == "agent/T1-attempt-1"
    assert github.status_calls == [(47, "In Progress")]


def test_an_architecture_revision_branch_makes_no_board_call(tmp_path, monkeypatch):
    github = FakeBoardGitHub(issues=[pilot_issue(47, "T1"), pilot_issue(48, "T2")])
    monkeypatch.setattr(workflow_main, "GitHubClient", lambda: github)
    monkeypatch.setattr(workflow_main, "optional_github_client", lambda: github)
    repo, _, _ = make_h2_setup(tmp_path)

    result = h2_review(repo, script("revise", "Add a cache section", "approve"), make_architect(REVISED))

    assert result is True
    assert github.status_calls == [] and github.comments == [] and github.closed == []


# --- H3 reject -----------------------------------------------------------------

def test_reject_without_an_earlier_approval_moves_the_card_back_to_todo(tmp_path):
    repo, _ = make_review_setup(tmp_path, FIXED, tests_passed=True)
    github = FakeBoardGitHub(statuses={47: "In Progress"})

    outcome, _ = review(repo, ["reject", "Wrong approach"], github=github)

    assert outcome["decision"] == "reject"
    assert github.status_calls == [(47, "Todo")]


def test_reject_after_an_earlier_approval_keeps_in_progress(tmp_path, capsys):
    repo, _ = make_review_setup(tmp_path, FIXED, tests_passed=True)
    commit_on_pilot_main(repo, "outputs/approvals/T1_H3_approve_20261107_112144_586388.json")
    github = FakeBoardGitHub(statuses={47: "In Progress"})

    review(repo, ["reject", "A later attempt is not better"], github=github)

    assert github.status_calls == [] and github.statuses[47] == "In Progress"
    assert "approved at H3 before" in capsys.readouterr().out


# --- D2: H3 approve ------------------------------------------------------------

def test_approve_and_yes_closes_the_issue_and_moves_the_card_to_done(tmp_path):
    repo, _ = make_review_setup(tmp_path, FIXED, tests_passed=True)
    github = FakeBoardGitHub(statuses={47: "In Progress"})

    outcome, _ = review(repo, ["approve", "Looks good", "yes"], github=github)

    assert outcome["decision"] == "approve"
    assert github.closed == [(47, "completed")]
    assert github.status_calls == [(47, "Done")]
    record = committed_approve_record(repo)
    assert record["issue"] == 47 and record["issue_closed"] is True


def test_approve_and_no_keeps_the_issue_open_and_in_progress(tmp_path):
    repo, _ = make_review_setup(tmp_path, FIXED, tests_passed=True)
    github = FakeBoardGitHub()

    review(repo, ["approve", "", "no"], github=github)

    assert github.closed == [] and github.issues[0]["state"] == "open"
    assert github.status_calls == [(47, "In Progress")]
    assert committed_approve_record(repo)["issue_closed"] is False


def test_a_board_error_only_warns_and_the_merge_stays(tmp_path, capsys):
    repo, remote = make_review_setup(tmp_path, FIXED, tests_passed=True)
    github = FakeBoardGitHub(board_fails=True)

    outcome, _ = review(repo, ["approve", "", "yes"], github=github)

    assert outcome["decision"] == "approve" and outcome["merge_commit"]
    assert git(repo, "branch", "--show-current") == "pilot/main"
    assert git(remote, "rev-parse", "pilot/main") == git(repo, "rev-parse", "pilot/main")
    assert github.closed == [(47, "completed")]  # the issue is still closed
    assert "WARNING: could not set the board Status of T1 to Done" in capsys.readouterr().out


# --- GitHubClient: the GraphQL helpers -----------------------------------------

def test_github_client_finds_ids_by_name_and_adds_a_missing_issue_to_the_board(monkeypatch):
    from integrations import github_client as module

    sent = []

    class Response:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": self.data}

    def fake_post(url, headers, json, timeout):
        sent.append(json)
        query = json["query"]
        if "projectV2(number" in query:
            return Response({"user": {"projectV2": {"id": "P1", "field": {
                "id": "F1", "options": [{"id": "o-todo", "name": "Todo"}, {"id": "o-done", "name": "Done"}]}}}})
        if "projectItems" in query:  # the issue is not on the board yet
            return Response({"repository": {"issue": {"id": "I47", "projectItems": {"nodes": []}}}})
        if "addProjectV2ItemById" in query:
            return Response({"addProjectV2ItemById": {"item": {"id": "ITEM1"}}})
        return Response({"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "ITEM1"}}})

    monkeypatch.setattr(module.requests, "post", fake_post)
    client = module.GitHubClient.__new__(module.GitHubClient)
    client.headers, client.repository = {}, "Rahma-Nouri/AI-SDLC-Agent-Workflow"
    client.project_owner, client.project_number, client._project = "Rahma-Nouri", 2, None

    client.set_issue_status(47, "Done")

    assert sent[0]["variables"] == {"owner": "Rahma-Nouri", "number": 2}
    assert sent[2]["variables"] == {"project": "P1", "content": "I47"}  # added to the board
    assert sent[3]["variables"] == {"project": "P1", "item": "ITEM1", "field": "F1", "option": "o-done"}
    with pytest.raises(RuntimeError, match="no Status option"):
        client.set_issue_status(47, "Blocked")


# --- D4: --sync-pilot-board ----------------------------------------------------

def sync_setup(tmp_path):
    """T1 approved on pilot/main; T2 open; T3 closed; T4 already right; plus a World 1 issue."""
    repo, _snapshot = make_pilot_repo(tmp_path)
    commit_on_pilot_main(repo, "outputs/approvals/T1_H3_approve_20261107_112144_586388.json")
    github = FakeBoardGitHub(
        issues=[
            pilot_issue(47, "T1"),
            pilot_issue(48, "T2"),
            pilot_issue(49, "T3", state="closed"),
            pilot_issue(50, "T4"),
            pilot_issue(5, "T2", label="world: workflow"),  # never touched
        ],
        statuses={47: "Todo", 49: "In Progress", 50: "Todo"},
    )
    return repo, github


def sync(repo, github, ask):
    return workflow_main.run_sync_pilot_board(repo, github_client=github, git_agent=PilotGitAgent(repo), ask=ask)


def test_board_sync_shows_the_plan_and_no_changes_nothing(tmp_path, capsys):
    repo, github = sync_setup(tmp_path)

    result = sync(repo, github, lambda _prompt: "no")

    assert result is None and github.status_calls == []
    output = capsys.readouterr().out
    assert "#47 T1: Todo -> In Progress" in output
    assert "#48 T2: no status -> Todo" in output
    assert "#49 T3: In Progress -> Done" in output
    assert "#50 T4: Todo == Todo" in output
    assert "#5 " not in output


def test_board_sync_applies_on_yes_and_a_second_run_changes_nothing(tmp_path):
    repo, github = sync_setup(tmp_path)

    applied = sync(repo, github, lambda _prompt: "yes")

    assert github.status_calls == [(47, "In Progress"), (48, "Todo"), (49, "Done")]
    assert [row["task_id"] for row in applied] == ["T1", "T2", "T3"]

    def never_asked(_prompt):
        raise AssertionError("Nothing to change, so nothing to confirm.")

    assert sync(repo, github, never_asked) == []
    assert len(github.status_calls) == 3
