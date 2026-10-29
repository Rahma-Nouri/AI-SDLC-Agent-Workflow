from main import collect_backend_file_context


def test_backend_file_context_collects_only_allowed_file_snapshots(tmp_path):
    backend_folder = tmp_path / "pilot" / "app" / "backend"
    backend_folder.mkdir(parents=True)
    backend_file = backend_folder / "main.py"
    backend_file.write_text("app = object()\n", encoding="utf-8")

    tests_folder = tmp_path / "tests"
    tests_folder.mkdir()
    backend_test = tests_folder / "test_backend_app.py"
    backend_test.write_text("def test_app():\n    pass\n", encoding="utf-8")
    unrelated_test = tests_folder / "test_other.py"
    unrelated_test.write_text("def test_other():\n    pass\n", encoding="utf-8")

    requirements = tmp_path / "requirements.txt"
    requirements.write_text("fastapi==1.0.0\n", encoding="utf-8")

    frontend_folder = tmp_path / "pilot" / "app" / "frontend"
    frontend_folder.mkdir(parents=True)
    frontend_file = frontend_folder / "App.tsx"
    frontend_file.write_text("export default function App() {}\n", encoding="utf-8")

    context = collect_backend_file_context(tmp_path)

    assert context["existing_files_complete"] is True
    assert context["existing_files"] == {
        "pilot/app/backend/main.py": backend_file.read_bytes().decode("utf-8"),
        "requirements.txt": requirements.read_bytes().decode("utf-8"),
        "tests/test_backend_app.py": backend_test.read_bytes().decode("utf-8"),
    }
    assert "test_other.py" not in context["existing_files"]
    assert "pilot/app/frontend/App.tsx" not in context["existing_files"]
