"""Issue Sync Agent: keep the pilot's GitHub Issues in line with the approved backlog.

World 2 (the pilot) is managed by agents. After the human approves the backlog
(H1), this agent makes GitHub Issues match the official task list:

- it creates one issue per approved task (label "world: pilot"),
- it closes old task issues from earlier runs as "not planned",
- it never touches World 1 issues (label "world: workflow").

The agent first builds a plan and shows it. Nothing changes on GitHub until the
human confirms the plan.
"""

import re

PILOT_LABEL = "world: pilot"
WORKFLOW_LABEL = "world: workflow"

# Hidden HTML comment in the issue body. GitHub does not display it, but the
# agent uses it to recognise its own issues on the next run (no duplicates).
MARKER_PATTERN = re.compile(r"<!--\s*ai-sdlc:pilot-task-id=([A-Za-z0-9-]+)\s*-->")

# Only issues whose title starts like an old generated task, such as
# "[T1]", "[T01]", "[TASK-03]" or "[TEST]", may be closed automatically.
# Any other unlabeled issue (for example a bug report) is left alone.
OLD_TASK_TITLE_PATTERN = re.compile(r"^\[(TASK-\d+|T\d+|TEST)\]")

CLOSE_COMMENT = (
    "Closed by the Issue Sync Agent after H1 approval: superseded by the "
    "official backlog (approved snapshot, tasks {first}-{last}). "
    "Kept as evidence of earlier workflow runs."
)


def make_marker(task_id):
    return f"<!-- ai-sdlc:pilot-task-id={task_id} -->"


def read_marker(body):
    """Return the task ID stored in an issue body, or None."""
    match = MARKER_PATTERN.search(body or "")
    return match.group(1) if match else None


def label_names(issue):
    return {label.get("name", "") for label in issue.get("labels", [])}


def build_issue_body(task):
    """Write the issue text for one approved task."""

    criteria = task.get("acceptance_criteria") or []
    dependencies = task.get("dependencies") or []

    lines = [
        make_marker(task["task_id"]),
        f"**Task ID:** {task['task_id']}",
        "",
        "## Description",
        task.get("description", "").strip(),
        "",
        "## Acceptance Criteria",
        *[f"- [ ] {item}" for item in criteria],
        "",
        "## Dependencies",
        ", ".join(dependencies) if dependencies else "None",
        "",
        "---",
        "Created by the Issue Sync Agent after H1 approval.",
    ]
    return "\n".join(lines)


def plan_sync(tasks, issues):
    """Decide what to do, without changing anything.

    Returns a dict with:
      create   - approved tasks that have no issue yet
      keep     - existing official issues (task ID -> issue number)
      close    - open old task issues to close as "not planned"
      untouched - open issues the agent does not recognise (left alone)
    """

    official_ids = [task["task_id"] for task in tasks]
    keep = {}
    close = []
    untouched = []

    for issue in issues:
        # The issues API also returns pull requests; never touch them.
        if "pull_request" in issue:
            continue

        labels = label_names(issue)

        # World 1 belongs to Rahma: never touched by any agent.
        if WORKFLOW_LABEL in labels:
            continue

        marked_id = read_marker(issue.get("body"))
        is_open = issue.get("state") == "open"

        if marked_id in official_ids and marked_id not in keep:
            keep[marked_id] = issue["number"]
            continue

        if not is_open:
            continue

        # A marked issue whose task is no longer in the backlog, or a duplicate,
        # or an old generated task issue: close it.
        if marked_id is not None or OLD_TASK_TITLE_PATTERN.match(issue.get("title", "")):
            close.append(issue)
        else:
            untouched.append(issue)

    create = [task for task in tasks if task["task_id"] not in keep]

    return {"create": create, "keep": keep, "close": close, "untouched": untouched}


def describe_plan(plan):
    """Short, human-readable summary for the confirmation prompt."""

    lines = [
        f"Create {len(plan['create'])} official pilot issue(s): "
        + (", ".join(task["task_id"] for task in plan["create"]) or "none"),
        f"Keep {len(plan['keep'])} existing official issue(s)",
        f"Close {len(plan['close'])} old issue(s) as 'not planned': "
        + (", ".join(f"#{issue['number']}" for issue in plan["close"]) or "none"),
    ]
    if plan["untouched"]:
        lines.append(
            "Leave alone (not recognised, no world label): "
            + ", ".join(f"#{issue['number']}" for issue in plan["untouched"])
        )
    return "\n".join(lines)


class IssueSyncAgent:
    """Apply a confirmed sync plan through a GitHub client."""

    def __init__(self, github_client):
        self.github = github_client

    def execute(self, plan, tasks):
        first_id = tasks[0]["task_id"] if tasks else "?"
        last_id = tasks[-1]["task_id"] if tasks else "?"
        created = []
        closed = []

        # Create first, so the official issues exist before old ones close.
        for task in plan["create"]:
            issue = self.github.create_issue(
                title=f"[{task['task_id']}] {task['title']}",
                body=build_issue_body(task),
                labels=[PILOT_LABEL],
            )
            created.append(issue["number"])

        for issue in plan["close"]:
            self.github.close_issue(
                issue["number"],
                comment=CLOSE_COMMENT.format(first=first_id, last=last_id),
                state_reason="not_planned",
            )
            closed.append(issue["number"])

        return {"created": created, "closed": closed}
