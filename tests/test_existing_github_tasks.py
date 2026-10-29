
from integrations.github_client import GitHubClient
from agents.ticket_quality_checker import TicketQualityChecker


def main():
    print("Reading existing GitHub task issues...")

    github = GitHubClient()
    tasks = github.get_task_issues()

    if not tasks:
        print("No task issues found.")
        return

    print(f"\nFound {len(tasks)} task issues.")

    checker = TicketQualityChecker()
    report = checker.validate_tasks(tasks)

    print("\n" + "=" * 50)
    print("EXISTING GITHUB TASK QUALITY REPORT")
    print("=" * 50)

    for task in tasks:
        print(f"\n#{task['issue_number']} - {task['task_id']}: {task['title']}")
        print(f"URL: {task['issue_url']}")
        print(f"Acceptance criteria: {len(task['acceptance_criteria'])}")
        print(f"Dependencies: {task['dependencies'] or 'None'}")

    print("\n" + "-" * 50)
    print("VALIDATION RESULT:", "PASS" if report["valid"] else "FAIL")

    if report["errors"]:
        print("\nERRORS:")
        for error in report["errors"]:
            print(f"- {error}")

    if report["warnings"]:
        print("\nWARNINGS:")
        for warning in report["warnings"]:
            print(f"- {warning}")

    if report["valid"]:
        print("\nAll blocking checks passed.")
    else:
        print("\nSome tasks need correction before they pass validation.")

    print("\nNo GitHub issues were modified.")


if __name__ == "__main__":
    main()
