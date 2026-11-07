"""H3 gate: the human accepts, revises, or rejects the agent's implementation.

The human only decides. The agents do the work:
- approve: the Pilot Git Agent merges the agent branch into pilot/main and
  pushes it; the pilot issue gets a comment.
- revise:  the human's feedback goes to the Backend Agent, which changes the
  code on the same branch; the tests run again. Tests stay frozen unless the
  human explicitly allows test changes for this revision.
- reject:  the agent branch is deleted; pilot/main stays as it was.

Every decision is saved as a record file (outputs/approvals/), so the human's
decisions stay traceable in Git, not only in memory.
"""

import json
import re
from datetime import datetime
from pathlib import Path, PurePosixPath

from agents.correction_loop import CorrectionLoopReport, run_revision
from agents.issue_sync_agent import PILOT_LABEL, label_names, read_marker

TIMESTAMP = re.compile(r"_(\d{8}_\d{6}_\d{6})\.json$")

# <task>_backend_<timestamp>_report.json is the first patch of an attempt;
# a label such as fix1_ or revise1_ before the timestamp marks a later change.
AGENT_REPORT = re.compile(r"_backend_(?P<label>[a-z]+\d+_)?(?P<timestamp>\d{8}_\d{6}_\d{6})_report\.json$")


def latest_test_report(pilot_root):
    """The newest test report (loop, re-test or revision) on the branch."""
    folder = Path(pilot_root) / "outputs" / "test_results"
    reports = [path for path in folder.glob("*.json") if TIMESTAMP.search(path.name)] if folder.is_dir() else []
    if not reports:
        return None
    newest = max(reports, key=lambda path: TIMESTAMP.search(path.name).group(1))
    return CorrectionLoopReport.model_validate_json(newest.read_text(encoding="utf-8"))


def agent_reports_on_branch(git_agent, pilot_root, task_id):
    """The Backend Agent's reports for this task and attempt, oldest first."""
    found = []
    for path in git_agent.files_added_on_branch("outputs/proposed_changes/"):
        name = PurePosixPath(path).name
        match = AGENT_REPORT.search(name)
        if match and name.startswith(f"{task_id}_backend_") and (Path(pilot_root) / path).is_file():
            found.append((match["timestamp"], match["label"], name, path))
    found.sort(key=lambda item: item[0])

    # A branch also keeps the records of earlier failed attempts as evidence.
    # This attempt starts at its newest first patch, so skip what came before it.
    first_patches = [index for index, item in enumerate(found) if item[1] is None]
    if first_patches:
        found = found[first_patches[-1]:]

    reports = []
    for _timestamp, _label, name, path in found:
        data = json.loads((Path(pilot_root) / path).read_text(encoding="utf-8"))
        if data.get("task_id") == task_id:
            reports.append({**data, "file_name": name})
    return reports


def write_decision_record(pilot_root, task_id, payload):
    folder = Path(pilot_root) / "outputs" / "approvals"
    folder.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = folder / f"{task_id}_H3_{payload['decision']}_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8", newline="\n")
    return path


def find_pilot_issue(github, task_id):
    """The official pilot issue of a task (created by the Issue Sync Agent)."""
    for issue in github.list_all_issues():
        if PILOT_LABEL in label_names(issue) and read_marker(issue.get("body")) == task_id:
            return issue["number"]
    return None


def comment_on_pilot_issue(github, task_id, text):
    """Best effort: a GitHub problem must not undo the human's decision."""
    if github is None:
        print("GitHub not available: no issue comment was written.")
        return None
    try:
        number = find_pilot_issue(github, task_id)
        if number is None:
            print(f"No pilot issue found for {task_id}: no comment was written.")
            return None
        github.comment_on_issue(number, text)
        print(f"Commented on pilot issue #{number}.")
        return number
    except Exception as error:
        print(f"Could not comment on the pilot issue: {error}")
        return None


def ask_choice(ask, prompt, choices):
    while True:
        answer = ask(prompt).strip().lower()
        if answer in choices:
            return answer
        print(f"Please enter one of: {', '.join(choices)}")


def print_report_list(title, items):
    if not items:
        print(f"  {title}: none reported")
        return
    print(f"  {title}:")
    for item in items:
        print(f"  - {item}")


def show_agent_reports(reports):
    """What the agent itself said: open problems, assumptions, criteria status."""
    print("\nAgent's own report (its claims, not test evidence):")
    print("  From: " + (", ".join(report["file_name"] for report in reports) or "no report found on this branch"))

    # Problems can be mentioned in any report, so collect them all (without
    # repeats). Criteria status changes over time, so only the newest one counts.
    for title, field in (("Unresolved issues", "unresolved_issues"), ("Assumptions", "assumptions")):
        print_report_list(title, list(dict.fromkeys(item for report in reports for item in report.get(field) or [])))

    criteria = (reports[-1].get("acceptance_criteria") or []) if reports else []
    print_report_list(
        "Acceptance criteria (newest report)",
        [f"[{item.get('status', 'none reported')}] {item.get('criterion', 'none reported')}" for item in criteria],
    )


def show_review(task, summary, report, agent_reports=()):
    print("\n" + "=" * 70)
    print("H3 - HUMAN REVIEW: IMPLEMENTATION AND TEST EVIDENCE")
    print("=" * 70)
    print(f"Task:   {task['task_id']} - {task.get('title', '')}")
    print(f"Branch: {summary['branch']}")
    print("\nAgent commits:")
    for line in summary["commits"]:
        print(f"  {line}")
    print("\nChanged files (without run records):")
    print(summary["changed_files"] or "  none")
    print("\nLatest test evidence:")
    if report is None:
        print("  No test report found on this branch.")
    else:
        print(f"  {report.trigger}: {report.final_result} -> {report.decision}")
        for run in report.runs:
            print(f"  Run {run.attempt}: exit code {run.exit_code}, passed {run.passed}")
        if report.validation:
            print("  Automatic checks (warnings - they can be wrong, you decide):")
            for finding in report.validation:
                print(f"  ! {finding.file}:{finding.line} - {finding.message}")
        else:
            print("  Automatic checks: no warnings.")
        if report.runs and not report.runs[-1].passed:
            print("  End of the last test output:")
            print("  " + report.runs[-1].output_tail[-1200:].replace("\n", "\n  "))
    show_agent_reports(list(agent_reports))
    print("=" * 70)


def run_h3_review(
    *,
    orchestrator,
    task,
    architecture,
    pilot_root,
    git_agent,
    environment,
    backend_agent_factory,
    github,
    build_project_context,
    save_proposal,
    ask=input,
):
    """Show the evidence, take the human's decision, and let the agents act on it."""

    task_id = task["task_id"]
    summary = git_agent.branch_summary()
    branch = summary["branch"]
    report = latest_test_report(pilot_root)
    show_review(task, summary, report, agent_reports_on_branch(git_agent, pilot_root, task_id))

    decision = ask_choice(ask, "Enter 'approve', 'revise', or 'reject': ", ("approve", "revise", "reject"))
    tests_passed = report is not None and report.final_result == "passed"

    if decision == "approve" and not tests_passed:
        print("\nThe latest tests did NOT pass.")
        confirm = ask_choice(ask, "Type 'approve' again to approve anyway, or 'cancel': ", ("approve", "cancel"))
        if confirm == "cancel":
            print("H3 cancelled. The branch keeps waiting.")
            return {"decision": "cancelled", "branch": branch}

    record = {
        "gate": "H3",
        "task_id": task_id,
        "branch": branch,
        "decision": decision,
        "decided_at": datetime.now().isoformat(timespec="seconds"),
        "latest_tests": report.final_result if report else None,
        "agent_commits": summary["commits"],
    }

    if decision == "approve":
        record["notes"] = ask("Optional note for the record (Enter to skip): ").strip()
        orchestrator.record_approval("H3", "approve", record["notes"])
        record_path = write_decision_record(pilot_root, task_id, record)
        git_agent.commit_records(task_id, [record_path], "Record the H3 approval")
        merge_commit = git_agent.merge_into_base(task_id, branch)
        print(f"\nMerged {branch} into pilot/main: {merge_commit}")
        pushed = True
        try:
            git_agent.push_base()
            print("Pushed pilot/main to GitHub.")
        except Exception as error:
            pushed = False
            print(f"Push failed (the merge is kept locally): {error}")
        comment_on_pilot_issue(
            github, task_id,
            f"H3 approved by the human reviewer. The Pilot Git Agent merged `{branch}` into "
            f"`pilot/main` (merge commit `{merge_commit}`). Latest tests: {record['latest_tests']}.",
        )
        return {"decision": "approve", "branch": branch, "merge_commit": merge_commit, "pushed": pushed}

    if decision == "reject":
        record["notes"] = ask("Why do you reject it? (saved in the record): ").strip()
        orchestrator.record_approval("H3", "reject", record["notes"])
        git_agent.delete_agent_branch(branch)
        # Written after the deletion, so the record lands on pilot/main as a run
        # record; the next agent branch commits it, so the decision is not lost.
        write_decision_record(pilot_root, task_id, record)
        print(f"\nDeleted {branch}. pilot/main is unchanged.")
        comment_on_pilot_issue(
            github, task_id,
            f"H3 rejected by the human reviewer: `{branch}` was deleted. Reason: {record['notes'] or 'not given'}.",
        )
        return {"decision": "reject", "branch": branch}

    # revise
    feedback = ""
    while not feedback:
        feedback = ask("Your feedback for the Backend Agent: ").strip()
    tests_may_change = ask_choice(
        ask, "May the agent change test files for this revision? (yes/no): ", ("yes", "no")
    ) == "yes"
    record.update({"notes": feedback, "tests_may_change": tests_may_change})
    orchestrator.record_approval("H3", "revise", feedback)
    record_path = write_decision_record(pilot_root, task_id, record)
    git_agent.commit_records(task_id, [record_path], "Record the H3 revision request")

    print(f"\nREVISION on {branch}")
    revision_report = run_revision(
        orchestrator=orchestrator,
        task=task,
        architecture=architecture,
        pilot_root=Path(pilot_root),
        branch=branch,
        feedback=feedback,
        tests_may_change=tests_may_change,
        tests_generated=report.tests_run if report else git_agent.files_added_on_branch("tests/"),
        last_run=report.runs[-1] if report and report.runs else None,
        backend_agent=backend_agent_factory(),
        git_agent=git_agent,
        build_project_context=build_project_context,
        save_proposal=save_proposal,
        prepare_environment=environment.prepare,
    )
    return {"decision": "revise", "branch": branch, "revision_report": revision_report}
