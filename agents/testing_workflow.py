"""Connect approved tasks, local test execution, and Orchestrator review."""

from agents.testing_agent import TestingAgent


def run_task_tests(
    orchestrator,
    task,
    project_root,
    test_paths,
    architecture=None,
    project_context=None,
    python_executable=None,
):
    """Run tests for an approved task and return evidence plus review status."""

    # Enforces the existing H1, H2, and task-approval checks.
    orchestrator.delegate_task(
        task,
        architecture=architecture,
        project_context=project_context,
    )

    testing_result = TestingAgent(project_root, python_executable=python_executable).run_pytest(
        task_id=task["task_id"],
        test_paths=test_paths,
    )

    result_data = testing_result.model_dump()
    checks_data = [check.model_dump() for check in testing_result.checks]

    review = orchestrator.evaluate_agent_result(
        task=task,
        result=result_data,
        checks_passed=checks_data,
    )

    return {
        "testing_result": result_data,
        "review": review,
    }


def run_frontend_task_tests(
    orchestrator,
    task,
    project_root,
    test_paths,
    architecture=None,
    project_context=None,
    python_executable=None,
    *,
    frontend_folder,
    runner=None,
):
    """Like run_task_tests, but runs Vitest in the frontend folder.

    Same arguments and result shape, so the correction loop can use either one.
    python_executable is ignored: the frontend tests run with Node, not Python.
    """

    orchestrator.delegate_task(task, architecture=architecture, project_context=project_context)

    agent = TestingAgent(project_root, runner=runner)
    testing_result = agent.run_vitest(task["task_id"], frontend_folder, test_paths)

    result_data = testing_result.model_dump()
    review = orchestrator.evaluate_agent_result(
        task=task,
        result=result_data,
        checks_passed=[check.model_dump() for check in testing_result.checks],
    )
    return {"testing_result": result_data, "review": review}