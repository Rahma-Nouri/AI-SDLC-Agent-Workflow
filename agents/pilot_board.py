"""Pilot board: the agents keep the Status of the pilot's GitHub Project up to date.

The board ("Pilot: Exam Preparation System", GitHub Projects v2) shows one card
per official pilot issue, with a Status: Todo, In Progress or Done.

- An agent branch for a task is created      -> In Progress
- H3 reject (the task was never approved)     -> Todo
- H3 approve, the human closes the issue      -> Done
- H3 approve, the issue stays open            -> In Progress

The board is a view, not the source of truth (that is the Git history and
the records in outputs/). So a GitHub or board problem only prints a warning:
it never stops or undoes the Git work. Only issues with the label
"world: pilot" are touched, found the same way as by the Issue Sync Agent.
"""

import re

from agents.issue_sync_agent import PILOT_LABEL, label_names, read_marker

TODO = "Todo"
IN_PROGRESS = "In Progress"
DONE = "Done"

# outputs/approvals/T1_H3_approve_20261107_112144_586388.json -> T1
APPROVE_RECORD = re.compile(r"^outputs/approvals/(?P<task>[A-Za-z0-9-]+)_H3_approve_[^/]+\.json$")

CLOSE_COMMENT = (
    "The human reviewer confirmed at H3 that all acceptance criteria are met. "
    "Closed by the Pilot Git Agent."
)


def official_issues(issues):
    """{task ID: issue} for the official pilot issues (label + hidden marker)."""
    found = {}
    for issue in issues:
        task_id = read_marker(issue.get("body"))
        # The first issue per task wins, like in the Issue Sync Agent.
        if PILOT_LABEL in label_names(issue) and task_id and task_id not in found:
            found[task_id] = issue
    return found


def find_pilot_issue(github, task_id):
    """The number of the official pilot issue of a task (created by the Issue Sync Agent)."""
    issue = official_issues(github.list_all_issues()).get(task_id)
    return issue["number"] if issue else None


def approved_task_ids(git_agent):
    """Tasks with an H3 approve record on pilot/main (that is: merged at least once)."""
    tasks = set()
    for path in git_agent.files_on_base("outputs/approvals/"):
        match = APPROVE_RECORD.match(path)
        if match:
            tasks.add(match["task"])
    return tasks


def find_issue_safely(github, task_id):
    """Like find_pilot_issue, but a problem only prints a warning (returns None)."""
    if github is None:
        return None
    try:
        number = find_pilot_issue(github, task_id)
    except Exception as error:
        print(f"WARNING: could not read the pilot issues: {error}")
        return None
    if number is None:
        print(f"No pilot issue found for {task_id}.")
    return number


def set_task_status(github, task_id, status, number=None):
    """Best effort: set the Status of the task's pilot issue. True when it worked."""
    if github is None:
        print("GitHub not available: the board was not updated.")
        return False
    try:
        number = number or find_pilot_issue(github, task_id)
        if number is None:
            print(f"No pilot issue found for {task_id}: the board was not updated.")
            return False
        github.set_issue_status(number, status)
    except Exception as error:
        print(f"WARNING: could not set the board Status of {task_id} to {status}: {error}")
        return False
    print(f"Board: issue #{number} ({task_id}) -> {status}.")
    return True


def finish_issue_after_approval(github, task_id, number, close):
    """After an H3 approve and merge: close the issue (Done) or keep it open (In Progress)."""
    if github is None or number is None:
        return
    if close:
        try:
            github.close_issue(number, comment=CLOSE_COMMENT, state_reason="completed")
        except Exception as error:
            print(f"WARNING: could not close issue #{number}: {error}")
        set_task_status(github, task_id, DONE, number)
    else:
        set_task_status(github, task_id, IN_PROGRESS, number)


def target_status(issue, task_id, approved):
    """closed -> Done; open and approved at H3 -> In Progress; otherwise Todo."""
    if issue.get("state") == "closed":
        return DONE
    return IN_PROGRESS if task_id in approved else TODO


def task_number(task_id):
    """T10 -> 10, so the plan lists T2 before T10."""
    digits = re.sub(r"\D", "", task_id)
    return int(digits) if digits else 0


def plan_board_sync(issues, approved, current_status):
    """Every official issue with its current and target Status. Changes nothing.

    current_status(number) reads the issue's Status on the board (None if none).
    """
    rows = []
    for task_id, issue in sorted(official_issues(issues).items(), key=lambda item: task_number(item[0])):
        rows.append({
            "task_id": task_id,
            "number": issue["number"],
            "current": current_status(issue["number"]),
            "target": target_status(issue, task_id, approved),
        })
    return rows
