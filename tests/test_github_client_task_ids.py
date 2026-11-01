import integrations.github_client as github_module
from integrations.github_client import GitHubClient


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def make_client(monkeypatch, issues):
    # Fake settings and a fake GitHub: these tests never use the network.
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(github_module.requests, "get", lambda *a, **k: FakeResponse(issues))
    return GitHubClient()


def test_get_task_issues_reads_t1_style_ids(monkeypatch):
    client = make_client(
        monkeypatch,
        [
            {"number": 24, "title": "[T1] Architecture", "html_url": "u24",
             "body": "**Task ID:** T1\n\n## Description\nDesign.\n\n## Acceptance Criteria\n- [ ] ADRs\n\n## Dependencies\nNone"},
            {"number": 2, "title": "[TASK-01] Upload", "html_url": "u2",
             "body": "**Task ID:** TASK-01\n\n## Description\nUpload.\n\n## Acceptance Criteria\n- [ ] Works\n\n## Dependencies\nNone"},
            {"number": 1, "title": "[TEST] Integration", "html_url": "u1", "body": "No task here."},
        ],
    )

    assert [task["task_id"] for task in client.get_task_issues()] == ["T1", "TASK-01"]


def test_duplicate_check_matches_the_exact_task_id(monkeypatch):
    client = make_client(
        monkeypatch,
        [{"number": 33, "title": "[T10] Ingestion", "body": "**Task ID:** T10\n"}],
    )
    created = []
    monkeypatch.setattr(client, "create_issue", lambda **kwargs: created.append(kwargs) or {"number": 99})

    # "T1" must not be mistaken for the existing "T10" issue.
    client.create_task_issue({"task_id": "T1", "title": "Architecture", "description": "Design."})
    assert len(created) == 1

    # The existing "T10" issue must be recognised, so no duplicate is created.
    client.create_task_issue({"task_id": "T10", "title": "Ingestion", "description": "Ingest."})
    assert len(created) == 1
