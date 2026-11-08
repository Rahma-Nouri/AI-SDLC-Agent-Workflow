
import os
import re

import requests
from dotenv import load_dotenv


class GitHubClient:
    def __init__(self):
        load_dotenv()

        self.token = os.getenv("GITHUB_TOKEN")
        self.repository = os.getenv("GITHUB_REPOSITORY")

        if not self.token:
            raise ValueError("GITHUB_TOKEN was not found in .env")

        if not self.repository:
            raise ValueError(
                "GITHUB_REPOSITORY was not found in .env"
            )

        self.base_url = (
            f"https://api.github.com/repos/{self.repository}"
        )

        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        # The pilot board (GitHub Projects v2). By default: project 2 of the
        # repository owner ("Pilot: Exam Preparation System").
        self.project_owner = os.getenv("PILOT_PROJECT_OWNER") or self.repository.split("/")[0]
        self.project_number = int(os.getenv("PILOT_PROJECT_NUMBER") or 2)
        self._project = None  # filled on first use by _pilot_project()

    def test_connection(self):
        """Verify repository access without changing anything."""
        response = requests.get(
            self.base_url,
            headers=self.headers,
            timeout=15,
        )

        if response.status_code == 401:
            raise RuntimeError(
                "GitHub authentication failed. Check GITHUB_TOKEN."
            )

        if response.status_code == 403:
            raise RuntimeError(
                "GitHub denied access. Check token permissions "
                "and repository access."
            )

        if response.status_code == 404:
            raise RuntimeError(
                "Repository not found or not accessible. "
                "Check GITHUB_REPOSITORY and token access."
            )

        response.raise_for_status()

        repository_info = response.json()

        print("GitHub connection successful!")
        print(f"Repository: {repository_info['full_name']}")
        print(f"Private repository: {repository_info['private']}")

    def list_issues(self):
        """Read open issues, without changing the repository."""
        response = requests.get(
            f"{self.base_url}/issues",
            headers=self.headers,
            params={"state": "open", "per_page": 10},
            timeout=15,
        )

        response.raise_for_status()

        issues = response.json()

        # Pull requests are also returned by this endpoint.
        issues = [
            issue for issue in issues
            if "pull_request" not in issue
        ]

        if not issues:
            print("No open issues found.")
            return

        print("\nOpen GitHub Issues:")
        for issue in issues:
            print(f"#{issue['number']}: {issue['title']}")

    def create_issue(self, title, body, labels=None):
        url = f"{self.base_url}/issues"

        payload = {
            "title": title,
            "body": body,
            "labels": labels or [],
        }

        response = requests.post(
            url,
            headers=self.headers,
            json=payload,
            timeout=15,
        )

        response.raise_for_status()
        issue = response.json()

        print("GitHub Issue created successfully!")
        print(f"Number: #{issue['number']}")
        print(f"Title: {issue['title']}")
        print(f"URL: {issue['html_url']}")

        return issue

    
    def create_task_issue(self, task):
        task_id = str(task["task_id"]).strip()

        # Check existing issues to avoid creating duplicates.
        response = requests.get(
            f"{self.base_url}/issues",
            headers=self.headers,
            params={"state": "all", "per_page": 100},
            timeout=15,
        )
        response.raise_for_status()

        for issue in response.json():
            if "pull_request" in issue:
                continue

            # Match the exact "**Task ID:** T1" line, so "T1" never matches "T10".
            body_lines = (issue.get("body") or "").splitlines()
            if f"**Task ID:** {task_id}" in (line.strip() for line in body_lines):
                print(
                    f"Skipping {task_id}: "
                    f"already exists as issue #{issue['number']}."
                )
                return issue

        criteria = task.get("acceptance_criteria", [])
        dependencies = task.get("dependencies", [])

        body = (
            f"**Task ID:** {task_id}\n\n"
            f"## Description\n"
            f"{task['description']}\n\n"
            f"## Acceptance Criteria\n"
            + "\n".join(f"- [ ] {item}" for item in criteria)
            + "\n\n## Dependencies\n"
            + (
                ", ".join(dependencies)
                if dependencies
                else "None"
            )
            + "\n\nCreated by the AI-SDLC Orchestrator after human plan approval."
        )

        return self.create_issue(
            title=f"[{task_id}] {task['title']}",
            body=body,
            labels=[],
        )

    
    def get_task_issues(self):
        """Read existing task issues from GitHub without modifying them."""
        response = requests.get(
            f"{self.base_url}/issues",
            headers=self.headers,
            params={"state": "all", "per_page": 100},
            timeout=30,
        )
        response.raise_for_status()

        tasks = []

        def get_section(body, heading, next_heading):
            """Extract text between two Markdown headings."""
            if heading not in body:
                return ""

            content = body.split(heading, 1)[1]

            if next_heading and next_heading in content:
                content = content.split(next_heading, 1)[0]

            return content.strip()

        for issue in response.json():
            # GitHub's issues endpoint also returns pull requests.
            if "pull_request" in issue:
                continue

            body = issue.get("body") or ""
            issue_title = issue.get("title") or ""

            # Get the task ID from the issue body.
            task_id = ""
            for line in body.splitlines():
                if line.strip().startswith("**Task ID:**"):
                    task_id = line.split("**Task ID:**", 1)[1].strip()
                    break

            # Skip non-task issues, such as the integration test issue.
            if not re.fullmatch(r"TASK-\d+|T\d+", task_id):
                continue

            # Extract the description.
            description = get_section(
                body, "## Description", "## Acceptance Criteria"
            )

            # Extract acceptance criteria from Markdown checkboxes.
            criteria_section = get_section(
                body, "## Acceptance Criteria", "## Dependencies"
            )

            acceptance_criteria = []
            for line in criteria_section.splitlines():
                line = line.strip()
                if line.startswith("- [ ] ") or line.startswith("- [x] "):
                    acceptance_criteria.append(line[6:].strip())

            
            # Extract dependencies.
            dependencies_section = get_section(
                body, "## Dependencies", None
            )

            # Remove the orchestrator note and any other trailing sections.
            dependencies_section = dependencies_section.split(
                "\nCreated by the AI-SDLC Orchestrator", 1
            )[0].strip()

            if dependencies_section.lower() in ("", "none", "n/a"):
                dependencies = []
            else:
                dependencies = [
                    item.strip()
                    for item in dependencies_section.replace(";", ",").split(",")
                    if item.strip()
                ]


            # Remove the [TASK-01] prefix from the issue title.
            title_prefix = f"[{task_id}]"
            title = issue_title
            if title.startswith(title_prefix):
                title = title[len(title_prefix):].strip()

            tasks.append(
                {
                    "task_id": task_id,
                    "title": title,
                    "description": description,
                    "acceptance_criteria": acceptance_criteria,
                    "dependencies": dependencies,
                    "issue_number": issue["number"],
                    "issue_url": issue["html_url"],
                }
            )

        return tasks


    def list_all_issues(self):
        """Read every issue (open and closed), page by page. Changes nothing."""
        issues = []
        page = 1

        while True:
            response = requests.get(
                f"{self.base_url}/issues",
                headers=self.headers,
                params={"state": "all", "per_page": 100, "page": page},
                timeout=30,
            )
            response.raise_for_status()
            batch = response.json()
            if not batch:
                break

            # The issues endpoint also returns pull requests; skip them.
            issues.extend(item for item in batch if "pull_request" not in item)

            if len(batch) < 100:
                break
            page += 1

        return issues

    def close_issue(self, number, comment, state_reason="not_planned"):
        """Add a comment, then close the issue with the given reason."""
        comment_response = requests.post(
            f"{self.base_url}/issues/{number}/comments",
            headers=self.headers,
            json={"body": comment},
            timeout=15,
        )
        comment_response.raise_for_status()

        response = requests.patch(
            f"{self.base_url}/issues/{number}",
            headers=self.headers,
            json={"state": "closed", "state_reason": state_reason},
            timeout=15,
        )
        response.raise_for_status()
        print(f"Closed issue #{number} ({state_reason}).")
        return response.json()

    def comment_on_issue(self, number, comment):
        """Add a comment to an issue (for example the agent's H3 outcome)."""
        response = requests.post(
            f"{self.base_url}/issues/{number}/comments",
            headers=self.headers,
            json={"body": comment},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    # --- the pilot board: GitHub Projects v2 (GraphQL API) -------------------

    def _graphql(self, query, variables):
        """Run one GraphQL request; any error from GitHub becomes a RuntimeError."""
        response = requests.post(
            "https://api.github.com/graphql",
            headers=self.headers,
            json={"query": query, "variables": variables},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("errors"):
            messages = "; ".join(error.get("message", "unknown error") for error in data["errors"])
            raise RuntimeError(f"GitHub GraphQL error: {messages}")
        return data["data"]

    def _pilot_project(self):
        """The board's IDs, found by name (never hard-coded): project, Status field, options."""
        if self._project is None:
            data = self._graphql(
                """
                query($owner: String!, $number: Int!) {
                  user(login: $owner) {
                    projectV2(number: $number) {
                      id
                      field(name: "Status") {
                        ... on ProjectV2SingleSelectField { id options { id name } }
                      }
                    }
                  }
                }
                """,
                {"owner": self.project_owner, "number": self.project_number},
            )
            project = (data.get("user") or {}).get("projectV2")
            if not project or not project.get("field"):
                raise RuntimeError(
                    f"Project {self.project_number} of {self.project_owner} or its Status field was not found."
                )
            self._project = {
                "id": project["id"],
                "status_field": project["field"]["id"],
                "options": {option["name"]: option["id"] for option in project["field"]["options"]},
            }
        return self._project

    def _issue_on_board(self, number):
        """(issue node ID, board item ID or None, current Status name or None)."""
        project = self._pilot_project()
        owner, name = self.repository.split("/")
        data = self._graphql(
            """
            query($owner: String!, $name: String!, $number: Int!) {
              repository(owner: $owner, name: $name) {
                issue(number: $number) {
                  id
                  projectItems(first: 20) {
                    nodes {
                      id
                      project { id }
                      fieldValueByName(name: "Status") {
                        ... on ProjectV2ItemFieldSingleSelectValue { name }
                      }
                    }
                  }
                }
              }
            }
            """,
            {"owner": owner, "name": name, "number": number},
        )
        issue = data["repository"]["issue"]
        for item in issue["projectItems"]["nodes"]:
            if item["project"]["id"] == project["id"]:
                status = (item.get("fieldValueByName") or {}).get("name")
                return issue["id"], item["id"], status
        return issue["id"], None, None

    def issue_status(self, number):
        """The issue's Status on the pilot board, or None (not on the board, or no Status)."""
        return self._issue_on_board(number)[2]

    def set_issue_status(self, number, status):
        """Set the issue's Status on the pilot board; adds the issue to the board if missing."""
        project = self._pilot_project()
        if status not in project["options"]:
            raise RuntimeError(f"The board has no Status option {status!r}; it has {sorted(project['options'])}.")

        issue_id, item_id, _current = self._issue_on_board(number)
        if item_id is None:
            data = self._graphql(
                """
                mutation($project: ID!, $content: ID!) {
                  addProjectV2ItemById(input: {projectId: $project, contentId: $content}) { item { id } }
                }
                """,
                {"project": project["id"], "content": issue_id},
            )
            item_id = data["addProjectV2ItemById"]["item"]["id"]

        self._graphql(
            """
            mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
              updateProjectV2ItemFieldValue(input: {
                projectId: $project, itemId: $item, fieldId: $field,
                value: {singleSelectOptionId: $option}
              }) { projectV2Item { id } }
            }
            """,
            {"project": project["id"], "item": item_id, "field": project["status_field"],
             "option": project["options"][status]},
        )
