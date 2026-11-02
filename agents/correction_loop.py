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
    runs: list[TestRunSummary]
    fix_attempts: list[FixAttempt]
    final_result: Literal["passed", "failed", "no_tests"]
    decision: Literal["ready_for_review", "needs_revision"]


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
):
    """Run the tests, allow one fix, run them again, and return the report.

    build_project_context(pilot_root) -> dict: current files on the branch.
    save_proposal(result, label) -> (patch_path, report_path): saves a fix proposal.
    """

    task_id = task["task_id"]
    tests_generated = list(first_result.tests_added)
    tests_run = [path for path in tests_generated if (pilot_root / path).is_file()]

    def report(runs, fixes, final_result):
        return CorrectionLoopReport(
            task_id=task_id,
            branch=branch,
            tests_generated=tests_generated,
            tests_run=tests_run,
            runs=runs,
            fix_attempts=fixes,
            final_result=final_result,
            decision="ready_for_review" if final_result == "passed" else "needs_revision",
        )

    # Without tests there is no evidence; the human sees that at H3.
    if not tests_run:
        return report([], [], "no_tests")

    runs = [summarize_run(run_tests(orchestrator, task, pilot_root, tests_run, architecture=architecture), 0)]
    fixes = []

    for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
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
        runs.append(summarize_run(run_tests(orchestrator, task, pilot_root, tests_run, architecture=architecture), attempt))

    return report(runs, fixes, "passed" if runs[-1].passed else "failed")
