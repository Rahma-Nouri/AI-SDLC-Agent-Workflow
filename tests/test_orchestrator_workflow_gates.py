import pytest

from agents.orchestrator import OrchestratorAgent


def make_agent_without_h2_approval():
    # Skip __init__ so this test does not call Gemini or need an API key.
    agent = OrchestratorAgent.__new__(OrchestratorAgent)
    agent.workflow_state = {
        "backlog_approved": True,
        "architecture_approved": False,
        "tasks": {
            "T1": {
                "task_id": "T1",
                "dependencies": [],
                "status": "approved",
            }
        },
    }
    return agent


def test_get_ready_tasks_requires_h2_approval():
    agent = make_agent_without_h2_approval()

    with pytest.raises(RuntimeError, match="H2"):
        agent.get_ready_tasks({})


def test_delegate_task_requires_h2_approval():
    agent = make_agent_without_h2_approval()

    with pytest.raises(RuntimeError, match="H2"):
        agent.delegate_task(agent.workflow_state["tasks"]["T1"])

def make_agent_with_h2_approval():
    agent = make_agent_without_h2_approval()
    agent.workflow_state["approval_history"] = []
    agent.record_approval("H2", "approve")
    return agent


def test_get_ready_tasks_allows_dispatch_after_h2_approval():
    agent = make_agent_with_h2_approval()
    task = agent.workflow_state["tasks"]["T1"]

    assert agent.get_ready_tasks({}) == [task]


def test_delegate_task_allows_dispatch_after_h2_approval():
    agent = make_agent_with_h2_approval()
    task = agent.workflow_state["tasks"]["T1"]

    work_package = agent.delegate_task(task)

    assert work_package["task"] == task