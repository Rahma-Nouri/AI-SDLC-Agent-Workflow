
class TicketQualityChecker:
    """Validate generated development tasks before GitHub synchronization."""

    REQUIRED_FIELDS = {
        "task_id",
        "title",
        "description",
        "acceptance_criteria",
        "dependencies",
    }

    def validate_tasks(self, tasks):
        errors = []
        warnings = []

        # 1. Check the overall task list.
        if not isinstance(tasks, list) or not tasks:
            return {
                "valid": False,
                "errors": ["The task list is empty or invalid."],
                "warnings": [],
            }

        task_ids = []

        # 2. Check each task's structure and content.
        for index, task in enumerate(tasks, start=1):
            if not isinstance(task, dict):
                errors.append(f"Task #{index} is not a valid object.")
                continue

            missing = self.REQUIRED_FIELDS - set(task.keys())
            if missing:
                errors.append(
                    f"Task #{index} is missing fields: "
                    f"{', '.join(sorted(missing))}."
                )
                continue

            task_id = task["task_id"]
            title = task["title"]
            description = task["description"]
            criteria = task["acceptance_criteria"]
            dependencies = task["dependencies"]

            if not isinstance(task_id, str) or not task_id.strip():
                errors.append(f"Task #{index} has an invalid task ID.")
                continue

            task_id = task_id.strip()
            task_ids.append(task_id)

            if not isinstance(title, str) or not title.strip():
                errors.append(f"{task_id}: title is empty or invalid.")
            elif len(title.strip()) > 100:
                warnings.append(
                    f"{task_id}: title is longer than 100 characters."
                )

            if not isinstance(description, str) or not description.strip():
                errors.append(f"{task_id}: description is empty or invalid.")
            elif len(description.strip()) < 20:
                warnings.append(
                    f"{task_id}: description may need more detail."
                )

            if not isinstance(criteria, list) or not criteria:
                errors.append(
                    f"{task_id}: acceptance criteria must be a non-empty list."
                )
            else:
                valid_criteria = [
                    item for item in criteria
                    if isinstance(item, str) and item.strip()
                ]

                if len(valid_criteria) != len(criteria):
                    errors.append(
                        f"{task_id}: acceptance criteria contain "
                        "empty or invalid items."
                    )

                if len(valid_criteria) < 2:
                    warnings.append(
                        f"{task_id}: consider adding more acceptance "
                        "criteria so the task can be tested properly."
                    )

                vague_phrases = (
                    "works well",
                    "user-friendly",
                    "accurate",
                    "as needed",
                    "where possible",
                )

                for criterion in valid_criteria:
                    if any(
                        phrase in criterion.lower()
                        for phrase in vague_phrases
                    ):
                        warnings.append(
                            f"{task_id}: criterion may be difficult "
                            f"to verify objectively: {criterion}"
                        )

            if not isinstance(dependencies, list):
                errors.append(
                    f"{task_id}: dependencies must be a list."
                )
            elif any(
                not isinstance(dep, str) or not dep.strip()
                for dep in dependencies
            ):
                errors.append(
                    f"{task_id}: dependencies contain invalid task IDs."
                )

        # 3. Check duplicate task IDs.
        if len(task_ids) != len(set(task_ids)):
            errors.append("Duplicate task IDs were found.")

        # Stop dependency checks if task IDs are malformed or duplicated.
        if len(task_ids) != len(tasks) or len(task_ids) != len(set(task_ids)):
            return {
                "valid": not errors,
                "errors": errors,
                "warnings": warnings,
            }

        known_ids = set(task_ids)
        task_by_id = {task["task_id"].strip(): task for task in tasks}

        # 4. Check dependency references and self-dependencies.
        graph = {task_id: [] for task_id in task_ids}

        for task_id, task in task_by_id.items():
            dependencies = task["dependencies"]

            if not isinstance(dependencies, list):
                continue

            for dependency in dependencies:
                if not isinstance(dependency, str):
                    continue

                dependency = dependency.strip()

                if dependency == task_id:
                    errors.append(
                        f"{task_id}: a task cannot depend on itself."
                    )
                elif dependency not in known_ids:
                    errors.append(
                        f"{task_id}: unknown dependency '{dependency}'."
                    )
                else:
                    graph[task_id].append(dependency)

        # 5. Detect circular dependencies using depth-first search.
        visiting = set()
        visited = set()

        def has_cycle(task_id):
            if task_id in visiting:
                return True

            if task_id in visited:
                return False

            visiting.add(task_id)

            for dependency in graph[task_id]:
                if has_cycle(dependency):
                    return True

            visiting.remove(task_id)
            visited.add(task_id)
            return False

        for task_id in graph:
            if has_cycle(task_id):
                errors.append(
                    "Circular dependencies detected. "
                    "Review the task dependency graph."
                )
                break

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
        }
