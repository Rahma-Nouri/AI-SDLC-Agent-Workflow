import builtins

import main as workflow_main
from agents.issue_sync_agent import (
    PILOT_LABEL,
    IssueSyncAgent,
    build_issue_body,
    plan_sync,
    read_marker,
)
from agents.orchestrator import OrchestratorAgent

TASKS = [
    {
        "task_id": "T1",
        "title": "System Architecture",
        "description": "Design the system.",
        "acceptance_criteria": ["ADRs are documented."],
        "dependencies": [],
    },
    {
        "task_id": "T2",
        "title": "Document Parsing",
        "description": "Parse documents.",
        "acceptance_criteria": ["Text is extracted."],
        "dependencies": ["T1"],
    },
]


def issue(number, title, state="open", labels=(), body=""):
    return {
        "number": number,
        "title": title,
        "state": state,
        "labels": [{"name": name} for name in labels],
        "body": body,
    }


class FakeGitHub:
    """Records what the agent would do, instead of calling GitHub."""

    def __init__(self, issues):
        self.issues = list(issues)
        self.created = []
        self.closed = []

    def list_all_issues(self):
        return list(self.issues)

    def create_issue(self, title, body, labels=None):
        number = 100 + len(self.created)
        self.created.append({"title": title, "body": body, "labels": labels})
        self.issues.append(issue(number, title, labels=labels or [], body=body))
        return {"number": number}

    def close_issue(self, number, comment, state_reason="not_planned"):
        self.closed.append((number, state_reason, comment))
        for item in self.issues:
            if item["number"] == number:
                item["state"] = "closed"


def test_plan_creates_missing_tasks_and_closes_old_task_issues():
    issues = [
        issue(1, "[TEST] Verify integration"),
        issue(2, "[TASK-01] Old upload task"),
        issue(24, "[T1] Old architecture task"),
    ]

    plan = plan_sync(TASKS, issues)

    assert [task["task_id"] for task in plan["create"]] == ["T1", "T2"]
    assert [item["number"] for item in plan["close"]] == [1, 2, 24]


def test_world_1_issues_and_pull_requests_are_never_touched():
    workflow_issue = issue(40, "[P1.4] Agent applies its own patch", labels=["world: workflow"])
    pull_request = {**issue(50, "[T1] some PR"), "pull_request": {}}

    plan = plan_sync(TASKS, [workflow_issue, pull_request])

    assert plan["close"] == []
    assert plan["untouched"] == []


def test_unrecognised_issues_are_left_alone():
    bug_report = issue(60, "Login page crashes")

    plan = plan_sync(TASKS, [bug_report])

    assert plan["close"] == []
    assert [item["number"] for item in plan["untouched"]] == [60]


def test_marker_round_trip_and_body_content():
    body = build_issue_body(TASKS[1])

    assert read_marker(body) == "T2"
    assert "**Task ID:** T2" in body
    assert "- [ ] Text is extracted." in body
    assert "Created by the Issue Sync Agent after H1 approval." in body


def test_second_run_changes_nothing():
    github = FakeGitHub([issue(24, "[T1] Old architecture task")])
    agent = IssueSyncAgent(github)

    agent.execute(plan_sync(TASKS, github.list_all_issues()), TASKS)
    second_plan = plan_sync(TASKS, github.list_all_issues())

    assert len(github.created) == 2
    assert all(item["labels"] == [PILOT_LABEL] for item in github.created)
    assert [number for number, *_ in github.closed] == [24]
    assert github.closed[0][1] == "not_planned"
    assert second_plan["create"] == [] and second_plan["close"] == []


def make_orchestrator(h1_decision="approve"):
    orchestrator = OrchestratorAgent.__new__(OrchestratorAgent)
    orchestrator.workflow_state = {
        "backlog_approved": False,
        "architecture_approved": False,
        "tasks": {},
        "approval_history": [],
    }

    def answer_h1(_plan, tasks):
        if h1_decision == "approve":
            orchestrator.workflow_state["backlog_approved"] = True
        return h1_decision

    orchestrator.request_backlog_approval = answer_h1
    return orchestrator


def prepare(monkeypatch):
    snapshot = {"plan": "Approved plan.", "tasks": TASKS, "architecture": "# A"}
    monkeypatch.setattr(workflow_main, "load_approved_context", lambda _path: snapshot)

    # The issue sync must not ask for H2.
    def fail_if_h2_is_asked(**_kwargs):
        raise AssertionError("H2 must not be requested for the issue sync.")

    monkeypatch.setattr(workflow_main, "review_and_approve_architecture", fail_if_h2_is_asked)


def test_sync_runs_after_h1_and_human_confirmation(tmp_path, monkeypatch):
    prepare(monkeypatch)
    monkeypatch.setattr(builtins, "input", lambda _prompt: "yes")
    github = FakeGitHub([issue(24, "[T1] Old architecture task")])

    result = workflow_main.run_sync_pilot_issues(tmp_path, make_orchestrator(), github)

    assert result == {"created": [100, 101], "closed": [24]}


def test_no_changes_without_confirmation(tmp_path, monkeypatch):
    prepare(monkeypatch)
    monkeypatch.setattr(builtins, "input", lambda _prompt: "no")
    github = FakeGitHub([issue(24, "[T1] Old architecture task")])

    result = workflow_main.run_sync_pilot_issues(tmp_path, make_orchestrator(), github)

    assert result is None
    assert github.created == [] and github.closed == []


def test_h1_rejection_stops_before_reading_github(tmp_path, monkeypatch):
    prepare(monkeypatch)

    class NoGitHub:
        def list_all_issues(self):
            raise AssertionError("GitHub must not be read after an H1 rejection.")

    result = workflow_main.run_sync_pilot_issues(
        tmp_path, make_orchestrator(h1_decision="reject"), NoGitHub()
    )

    assert result is None
