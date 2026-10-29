
from agents.ticket_quality_checker import TicketQualityChecker


def main():
    checker = TicketQualityChecker()

    # Test 1: A valid task
    valid_tasks = [
        {
            "task_id": "TASK-01",
            "title": "Create document upload endpoint",
            "description": (
                "Implement an endpoint that accepts supported exam "
                "documents and returns extracted text."
            ),
            "acceptance_criteria": [
                "A valid PDF upload returns extracted text.",
                "An unsupported file type returns a clear error.",
            ],
            "dependencies": [],
        },
        {
            "task_id": "TASK-02",
            "title": "Add document analysis",
            "description": (
                "Analyze the extracted text to identify topics "
                "and question types."
            ),
            "acceptance_criteria": [
                "The response contains a list of identified topics.",
                "The response contains question type information.",
            ],
            "dependencies": ["TASK-01"],
        },
    ]

    result = checker.validate_tasks(valid_tasks)
    print("\nTEST 1 — Valid tasks")
    print("Expected: PASS")
    print("Actual:", "PASS" if result["valid"] else "FAIL")
    print("Errors:", result["errors"])

    # Test 2: A dependency that does not exist
    invalid_tasks = [
        {
            "task_id": "TASK-01",
            "title": "Create document upload endpoint",
            "description": "Implement the document upload endpoint.",
            "acceptance_criteria": [
                "A valid upload returns extracted text.",
                "Unsupported file types are rejected.",
            ],
            "dependencies": ["TASK-99"],
        },
    ]

    result = checker.validate_tasks(invalid_tasks)
    print("\nTEST 2 — Unknown dependency")
    print("Expected: FAIL validation")
    print("Actual:", "FAIL validation" if not result["valid"] else "PASS")
    print("Errors:", result["errors"])

    # Test 3: A circular dependency
    circular_tasks = [
        {
            "task_id": "TASK-01",
            "title": "Implement upload",
            "description": "Implement document upload functionality.",
            "acceptance_criteria": [
                "Valid documents are accepted.",
                "Invalid documents return an error.",
            ],
            "dependencies": ["TASK-02"],
        },
        {
            "task_id": "TASK-02",
            "title": "Implement analysis",
            "description": "Analyze uploaded document contents.",
            "acceptance_criteria": [
                "Topics are extracted.",
                "Question types are identified.",
            ],
            "dependencies": ["TASK-01"],
        },
    ]

    result = checker.validate_tasks(circular_tasks)
    print("\nTEST 3 — Circular dependency")
    print("Expected: FAIL validation")
    print("Actual:", "FAIL validation" if not result["valid"] else "PASS")
    print("Errors:", result["errors"])


if __name__ == "__main__":
    main()
