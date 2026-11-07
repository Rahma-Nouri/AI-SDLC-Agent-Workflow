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

Docs mode (P2.3): a Documentation Agent branch has no tests. The screen shows
the document's automatic reference checks instead of test evidence, "approve"
needs no second confirmation, and "revise" sends the feedback to the
Documentation Agent, which rewrites the document on the same branch.
"""

import json
import re
from datetime import datetime
from pathlib import Path, PurePosixPath

from agents.correction_loop import BACKEND_TESTS, CorrectionLoopReport, run_revision
from agents.issue_sync_agent import PILOT_LABEL, label_names, read_marker
from agents.output_validation import validate_files
from agents.pilot_git_agent import PilotGitError, branch_role

TIMESTAMP = re.compile(r"_(\d{8}_\d{6}_\d{6})\.json$")

# <task>_<role>_<timestamp>_report.json is the first patch of an attempt (role =
# backend, frontend or docs); a label such as fix1_ or revise1_ before the timestamp
# marks a later change.
AGENT_REPORT = re.compile(
    r"_(?P<role>backend|frontend|docs)_(?P<label>[a-z]+\d+_)?(?P<timestamp>\d{8}_\d{6}_\d{6})_report\.json$"
)


def latest_test_report(pilot_root, branch_files=None):
    """The newest test report (loop, re-test or revision) on the branch.

    branch_files: the report files the branch itself added. Reports of tasks
    merged earlier stay in the folder, so without this list a branch with no
    tests of its own would show another task's result as its evidence.
    """
    folder = Path(pilot_root) / "outputs" / "test_results"
    reports = [path for path in folder.glob("*.json") if TIMESTAMP.search(path.name)] if folder.is_dir() else []
    if branch_files is not None:
        own_names = {PurePosixPath(path).name for path in branch_files}
        reports = [path for path in reports if path.name in own_names]
    if not reports:
        return None
    newest = max(reports, key=lambda path: TIMESTAMP.search(path.name).group(1))
    return CorrectionLoopReport.model_validate_json(newest.read_text(encoding="utf-8"))


def agent_reports_on_branch(git_agent, pilot_root, task_id, role="backend"):
    """The reports of this role's agent for this task and attempt, oldest first."""
    found = []
    for path in git_agent.files_added_on_branch("outputs/proposed_changes/"):
        name = PurePosixPath(path).name
        match = AGENT_REPORT.search(name)
        if match and name.startswith(f"{task_id}_{role}_") and (Path(pilot_root) / path).is_file():
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


def write_decision_record(pilot_root, task_id, payload, gate="H3"):
    folder = Path(pilot_root) / "outputs" / "approvals"
    folder.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = folder / f"{task_id}_{gate}_{payload['decision']}_{timestamp}.json"
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


DOCS_PREFIX = "pilot/docs/"


def document_checks(git_agent, pilot_root):
    """The automatic reference checks on the documents the branch added or changed."""
    documents = git_agent.files_added_on_branch(DOCS_PREFIX)
    return documents, validate_files(pilot_root, documents)


def show_document_review(task, summary, documents, findings, agent_reports=()):
    print("\n" + "=" * 70)
    print("H3 - HUMAN REVIEW: DOCUMENT")
    print("=" * 70)
    print(f"Task:   {task['task_id']} - {task.get('title', '')}")
    print(f"Branch: {summary['branch']}")
    print("\nAgent commits:")
    for line in summary["commits"]:
        print(f"  {line}")
    print("\nChanged files (without run records):")
    print(summary["changed_files"] or "  none")
    print("\nPlease READ the document before you decide (a document has no tests):")
    for path in documents or ["  no document found on this branch"]:
        print(f"  {path}")
    if findings:
        print("\nAutomatic checks (warnings - they can be wrong, you decide):")
        for finding in findings:
            print(f"  ! {finding.file}:{finding.line} - {finding.message}")
    else:
        print("\nAutomatic checks: no warnings (every path and code name in backticks exists).")
    show_agent_reports(list(agent_reports))
    print("=" * 70)


def run_document_revision(*, orchestrator, task, architecture, pilot_root, branch, feedback,
                          docs_agent, git_agent, build_project_context, save_proposal):
    """One revision of a document after an H3 "revise": the agent rewrites it on the same branch.

    Returns a small dict for the screen and the caller: outcome, detail, findings.
    """

    task_id = task["task_id"]
    work_package = orchestrator.delegate_task(
        task, architecture=architecture, project_context=build_project_context(pilot_root)
    )
    work_package["instructions"] = (
        f"{work_package['instructions']}\n\n"
        "REVISION REQUEST\n"
        f"Your document is committed on branch {branch}. The human reviewer asked for changes:\n"
        f"{feedback}\n"
        "Return the complete updated document. Keep everything the feedback does not ask to change."
    )

    try:
        revision = docs_agent.create_patch(work_package)
    except Exception as error:
        return {"outcome": "failed", "detail": f"No revision proposal: {error}", "findings": []}

    patch_path, report_path = save_proposal(revision, "revise1_")
    declared = [item.path for item in revision.changed_files]
    try:
        commit = git_agent.commit_fix_on_current_branch(
            task_id, patch_path, declared, "H3 revision: " + revision.summary.split(". ")[0][:50],
            record_paths=[patch_path, report_path], frozen_prefixes=(),
            reason="Proposed by the Documentation Agent after the human's H3 feedback. "
                   "Not approved yet: waiting for the H3 decision.",
        )
    except PilotGitError as error:
        git_agent.commit_records(task_id, [patch_path, report_path], "Record a revision proposal that could not be applied")
        return {"outcome": "failed", "detail": str(error), "findings": []}

    _documents, findings = document_checks(git_agent, pilot_root)
    return {"outcome": "committed", "detail": revision.summary, "code_commit": commit.code_commit, "findings": findings}


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
    revision_options=None,
):
    """Show the evidence, take the human's decision, and let the agents act on it.

    revision_options: extra settings for a revision (frontend: run_tests, frozen).
    """

    revision_options = revision_options or {}
    frozen = revision_options.get("frozen", BACKEND_TESTS)
    task_id = task["task_id"]
    summary = git_agent.branch_summary()
    branch = summary["branch"]
    role = branch_role(branch)
    is_document = role == "docs"
    agent_reports = agent_reports_on_branch(git_agent, pilot_root, task_id, role)

    if is_document:
        report = None
        documents, findings = document_checks(git_agent, pilot_root)
        show_document_review(task, summary, documents, findings, agent_reports)
    else:
        report = latest_test_report(pilot_root, git_agent.files_added_on_branch("outputs/test_results/"))
        show_review(task, summary, report, agent_reports)

    decision = ask_choice(ask, "Enter 'approve', 'revise', or 'reject': ", ("approve", "revise", "reject"))
    tests_passed = report is not None and report.final_result == "passed"

    # A document has no tests, so "no test report" is not a reason to ask twice.
    if decision == "approve" and not tests_passed and not is_document:
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
    if is_document:
        record["latest_tests"] = "not_applicable (document)"
        record["documents"] = documents
        record["document_checks"] = [f"{item.file}:{item.line} - {item.message}" for item in findings]

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
        feedback = ask(f"Your feedback for the {'Documentation' if is_document else role.title()} Agent: ").strip()

    if is_document:
        # No tests to freeze or re-run: the agent rewrites the document, then the
        # human reviews it again with a new --h3-review.
        record["notes"] = feedback
        orchestrator.record_approval("H3", "revise", feedback)
        record_path = write_decision_record(pilot_root, task_id, record)
        git_agent.commit_records(task_id, [record_path], "Record the H3 revision request")
        print(f"\nREVISION on {branch}")
        revision = run_document_revision(
            orchestrator=orchestrator, task=task, architecture=architecture, pilot_root=Path(pilot_root),
            branch=branch, feedback=feedback, docs_agent=backend_agent_factory(), git_agent=git_agent,
            build_project_context=build_project_context, save_proposal=save_proposal,
        )
        print(f"Revision: {revision['outcome']} - {revision['detail']}")
        for finding in revision["findings"]:
            print(f"  ! {finding.file}:{finding.line} - {finding.message}")
        print("Review the document again with: python main.py --h3-review")
        return {"decision": "revise", "branch": branch, "document_revision": revision}

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
        tests_generated=report.tests_run if report else frozen.on_branch(git_agent),
        last_run=report.runs[-1] if report and report.runs else None,
        backend_agent=backend_agent_factory(),
        git_agent=git_agent,
        build_project_context=build_project_context,
        save_proposal=save_proposal,
        prepare_environment=environment.prepare,
        **revision_options,
    )
    return {"decision": "revise", "branch": branch, "revision_report": revision_report}
