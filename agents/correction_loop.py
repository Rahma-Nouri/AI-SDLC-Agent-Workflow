"""Correction loop: Testing Agent -> Backend Agent (one fix) -> Testing Agent.

After the Pilot Git Agent has committed a patch on its agent branch, this loop:

1. runs the tests the Backend Agent declared (its "tests_added"),
2. if they fail, gives the failure report to the Backend Agent for ONE fix,
3. lets the Pilot Git Agent commit the fix on the same branch,
4. runs the tests again.

The tests are frozen during a fix: the fix may change code, not tests.
The loop never approves anything. Its report ends with "ready_for_review" or
"needs_revision", and the human decides at H3.
"""

from typing import Literal

from agents.agent_contracts import ContractModel
from agents.output_validation import ValidationFinding, validate_files
from agents.pilot_environment import EnvironmentResult
from agents.pilot_git_agent import FrozenFilesError, PilotGitError
from agents.testing_workflow import run_task_tests

MAX_FIX_ATTEMPTS = 1
FROZEN_PREFIXES = ("tests/",)

# Only the end of a long pytest output is kept: it holds the failures and summary.
OUTPUT_TAIL_CHARS = 4000


class TestRunSummary(ContractModel):
    __test__ = False  # not a pytest test class

    attempt: int  # 0 = the first run, 1 = after the first fix
    command: str
    exit_code: int | None
    passed: bool
    output_tail: str


class FixAttempt(ContractModel):
    attempt: int
    outcome: Literal["committed", "rejected_frozen_tests", "failed"]
    detail: str
    patch_path: str | None = None
    code_commit: str | None = None


class CorrectionLoopReport(ContractModel):
    task_id: str
    branch: str
    tests_generated: list[str]  # declared by the Backend Agent
    tests_run: list[str]  # the declared tests that really exist and were run
    environment: list[EnvironmentResult] = []  # one check before each test run
    # Automatic checks on the branch's code (P1.8). Warnings only: shown at H3.
    validation: list[ValidationFinding] = []
    runs: list[TestRunSummary]
    fix_attempts: list[FixAttempt]
    final_result: Literal["passed", "failed", "no_tests", "environment_not_ready"]
    decision: Literal["ready_for_review", "needs_revision"]
    # What started this report: the automatic loop, a re-test, or an H3 "revise".
    trigger: Literal["automatic", "retest", "h3_revise"] = "automatic"
    human_feedback: str | None = None
    tests_may_change: bool = False  # only ever True by a human decision at H3


def validate_branch(git_agent, pilot_root):
    """Run the automatic checks on every code file the agent branch changed."""
    try:
        changed = [path for path in git_agent.files_added_on_branch(".") if not path.startswith("outputs/")]
    except Exception:
        return []
    return validate_files(pilot_root, changed)


def summarize_run(evidence, attempt):
    check = evidence["testing_result"]["checks"][0]
    return TestRunSummary(
        attempt=attempt,
        command=check["command"],
        exit_code=check["exit_code"],
        passed=check["passed"],
        output_tail=(check["output"] or "")[-OUTPUT_TAIL_CHARS:],
    )


def fix_instructions(base_instructions, branch, failed_run, frozen_tests):
    """Add the real failure evidence to the normal Orchestrator instructions."""
    return (
        f"{base_instructions}\n\n"
        "CORRECTION REQUEST\n"
        f"Your earlier patch is committed on branch {branch}, and its tests FAILED.\n"
        f"Command: {failed_run.command}\n"
        f"Exit code: {failed_run.exit_code}\n"
        "End of the test output:\n"
        f"{failed_run.output_tail}\n\n"
        "Fix the code so that these tests pass. The existing files in the project "
        "context are the current state of the branch.\n"
        "The tests are FROZEN: do not change any file under tests/, including: "
        + ", ".join(frozen_tests)
        + ".\nIf you think a test itself is wrong, say so in unresolved_issues "
        "instead of changing it."
    )


def run_correction_loop(
    *,
    orchestrator,
    task,
    architecture,
    pilot_root,
    first_result,
    branch,
    backend_agent,
    git_agent,
    build_project_context,
    save_proposal,
    run_tests=run_task_tests,
    prepare_environment=None,
    tests_generated=None,
    max_fix_attempts=MAX_FIX_ATTEMPTS,
):
    """Run the tests, allow one fix, run them again, and return the report.

    build_project_context(pilot_root) -> dict: current files on the branch.
    save_proposal(result, label) -> (patch_path, report_path): saves a fix proposal.
    prepare_environment() -> EnvironmentResult: the pilot's own Python with its
        approved packages; checked before every test run, because a fix may
        change requirements.txt. None = use the workflow's Python (tests only).
    tests_generated: the tests to run; by default the agent's own tests_added.
    max_fix_attempts: 0 = only re-run the tests (no Gemini request).
    """

    task_id = task["task_id"]
    tests_generated = list(first_result.tests_added if tests_generated is None else tests_generated)
    tests_run = [path for path in tests_generated if (pilot_root / path).is_file()]
    environment = []

    def report(runs, fixes, final_result):
        return CorrectionLoopReport(
            task_id=task_id,
            branch=branch,
            tests_generated=tests_generated,
            tests_run=tests_run,
            environment=environment,
            validation=validate_branch(git_agent, pilot_root),
            runs=runs,
            fix_attempts=fixes,
            final_result=final_result,
            decision="ready_for_review" if final_result == "passed" else "needs_revision",
        )

    # Without tests there is no evidence; the human sees that at H3.
    if not tests_run:
        return report([], [], "no_tests")

    def run_once(attempt):
        """Prepare the environment, then run the tests. None = environment not ready."""
        python = None
        if prepare_environment is not None:
            check = prepare_environment()
            environment.append(check)
            if check.status != "ready":
                return None
            python = check.python
        evidence = run_tests(
            orchestrator, task, pilot_root, tests_run,
            architecture=architecture, python_executable=python,
        )
        return summarize_run(evidence, attempt)

    first_run = run_once(0)
    if first_run is None:
        return report([], [], "environment_not_ready")

    runs = [first_run]
    fixes = []

    for attempt in range(1, max_fix_attempts + 1):
        if runs[-1].passed:
            break

        work_package = orchestrator.delegate_task(
            task,
            architecture=architecture,
            project_context=build_project_context(pilot_root),
        )
        work_package["instructions"] = fix_instructions(
            work_package["instructions"], branch, runs[-1], tests_run
        )

        try:
            fix = backend_agent.create_patch(work_package)
        except Exception as error:
            fixes.append(FixAttempt(attempt=attempt, outcome="failed", detail=f"No fix proposal: {error}"))
            break

        patch_path, report_path = save_proposal(fix, f"fix{attempt}_")
        declared = [item.path for item in fix.changed_files]

        try:
            commit = git_agent.commit_fix_on_current_branch(
                task_id, patch_path, declared, fix.summary.split(". ")[0][:60],
                record_paths=[patch_path, report_path], frozen_prefixes=FROZEN_PREFIXES,
            )
        except FrozenFilesError as error:
            # Keep the rejected proposal as evidence (a real failure case).
            git_agent.commit_records(task_id, [patch_path, report_path], "Record a rejected fix proposal")
            fixes.append(FixAttempt(attempt=attempt, outcome="rejected_frozen_tests", detail=str(error), patch_path=str(patch_path)))
            break
        except PilotGitError as error:
            git_agent.commit_records(task_id, [patch_path, report_path], "Record a fix proposal that could not be applied")
            fixes.append(FixAttempt(attempt=attempt, outcome="failed", detail=str(error), patch_path=str(patch_path)))
            break

        fixes.append(FixAttempt(
            attempt=attempt, outcome="committed", detail=fix.summary,
            patch_path=str(patch_path), code_commit=commit.code_commit,
        ))
        next_run = run_once(attempt)
        if next_run is None:
            return report(runs, fixes, "environment_not_ready")
        runs.append(next_run)

    return report(runs, fixes, "passed" if runs[-1].passed else "failed")


def revision_instructions(base_instructions, branch, feedback, last_run, tests_may_change, test_files):
    """The human's H3 feedback, plus the latest test evidence, for the Backend Agent."""
    rule = (
        "The human ALLOWED changes to test files for this revision."
        if tests_may_change
        else "The tests are FROZEN: do not change any file under tests/, including: " + ", ".join(test_files) + "."
    )
    evidence = (
        f"Latest test command: {last_run.command}\nExit code: {last_run.exit_code}\n"
        f"End of the test output:\n{last_run.output_tail}"
        if last_run else "No test run is available."
    )
    return (
        f"{base_instructions}\n\n"
        "REVISION REQUEST FROM THE HUMAN REVIEWER (H3)\n"
        f"Your work is committed on branch {branch}. The human chose 'revise' with this feedback:\n"
        f"{feedback}\n\n{evidence}\n\n{rule}\n"
        "Change only what the feedback asks for. The existing files in the project context "
        "are the current state of the branch."
    )


def run_revision(
    *,
    orchestrator,
    task,
    architecture,
    pilot_root,
    branch,
    feedback,
    tests_may_change,
    tests_generated,
    last_run,
    backend_agent,
    git_agent,
    build_project_context,
    save_proposal,
    prepare_environment,
    run_tests=run_task_tests,
):
    """One revision after an H3 "revise": the agent changes, commits, and re-tests."""

    task_id = task["task_id"]
    tests_run = [path for path in tests_generated if (pilot_root / path).is_file()]
    environment, runs, fixes = [], [], []

    def report(final_result):
        return CorrectionLoopReport(
            task_id=task_id, branch=branch, tests_generated=tests_generated, tests_run=tests_run,
            environment=environment, validation=validate_branch(git_agent, pilot_root),
            runs=runs, fix_attempts=fixes, final_result=final_result,
            decision="ready_for_review" if final_result == "passed" else "needs_revision",
            trigger="h3_revise", human_feedback=feedback, tests_may_change=tests_may_change,
        )

    work_package = orchestrator.delegate_task(
        task, architecture=architecture, project_context=build_project_context(pilot_root)
    )
    work_package["instructions"] = revision_instructions(
        work_package["instructions"], branch, feedback, last_run, tests_may_change, tests_run
    )

    try:
        revision = backend_agent.create_patch(work_package)
    except Exception as error:
        fixes.append(FixAttempt(attempt=1, outcome="failed", detail=f"No revision proposal: {error}"))
        return report("failed")

    patch_path, report_path = save_proposal(revision, "revise1_")
    declared = [item.path for item in revision.changed_files]
    try:
        commit = git_agent.commit_fix_on_current_branch(
            task_id, patch_path, declared, "H3 revision: " + revision.summary.split(". ")[0][:50],
            record_paths=[patch_path, report_path],
            frozen_prefixes=() if tests_may_change else FROZEN_PREFIXES,
        )
    except FrozenFilesError as error:
        git_agent.commit_records(task_id, [patch_path, report_path], "Record a rejected revision proposal")
        fixes.append(FixAttempt(attempt=1, outcome="rejected_frozen_tests", detail=str(error), patch_path=str(patch_path)))
        return report("failed")
    except PilotGitError as error:
        git_agent.commit_records(task_id, [patch_path, report_path], "Record a revision proposal that could not be applied")
        fixes.append(FixAttempt(attempt=1, outcome="failed", detail=str(error), patch_path=str(patch_path)))
        return report("failed")

    fixes.append(FixAttempt(attempt=1, outcome="committed", detail=revision.summary,
                            patch_path=str(patch_path), code_commit=commit.code_commit))

    # The revision may have added or changed tests (if allowed), so look again.
    tests_run[:] = [path for path in git_agent.files_added_on_branch("tests/") if (pilot_root / path).is_file()]
    if not tests_run:
        return report("no_tests")

    check = prepare_environment()
    environment.append(check)
    if check.status != "ready":
        return report("environment_not_ready")

    evidence = run_tests(orchestrator, task, pilot_root, tests_run,
                         architecture=architecture, python_executable=check.python)
    runs.append(summarize_run(evidence, 1))
    return report("passed" if runs[-1].passed else "failed")
