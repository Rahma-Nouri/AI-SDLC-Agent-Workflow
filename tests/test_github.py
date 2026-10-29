
from integrations.github_client import GitHubClient


def main():
    github = GitHubClient()

    github.test_connection()

    github.create_issue(
        title="[TEST] Verify Python-to-GitHub integration",
        body=(
            "This is a test issue created by the AI-SDLC-Agent-Workflow "
            "Python integration.\n\n"
            "Purpose: verify that the orchestrator can create GitHub Issues "
            "programmatically.\n\n"
            "This issue can be closed after verification."
        ),
        labels=[]
    )


if __name__ == "__main__":
    main()
