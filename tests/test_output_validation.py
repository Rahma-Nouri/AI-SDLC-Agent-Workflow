import ast
import textwrap

from agents.output_validation import (
    check_imports_vs_requirements,
    check_unclosed_resources,
    validate_files,
)


def tree(code):
    return ast.parse(textwrap.dedent(code))


def imports(code, required=(), local=("pilot", "tests")):
    return [f.message for f in check_imports_vs_requirements("f.py", tree(code), set(required), set(local))]


def unclosed(code):
    return [(f.line, f.message) for f in check_unclosed_resources("f.py", tree(code))]


# --- A. imports vs requirements (failure cases #5, #15) ---------------------

def test_a_missing_package_is_reported():
    assert imports("from sqlalchemy import create_engine\n") == [
        "imports 'sqlalchemy', but 'sqlalchemy' is not in requirements.txt"
    ]


def test_a_declared_stdlib_and_local_imports_are_fine():
    code = "import os, sqlite3\nfrom pathlib import Path\nfrom sqlalchemy import inspect\nfrom pilot.app.backend import db\n"
    assert imports(code, required={"sqlalchemy"}) == []


def test_a_import_names_that_differ_from_the_package_name():
    assert imports("import yaml\nfrom dotenv import load_dotenv\n", required={"pyyaml", "python-dotenv"}) == []


# --- B. unclosed resources (failure cases #12, #18) -------------------------

def test_b_engine_never_disposed_is_reported():  # case #18
    findings = unclosed("""
        def test_tables():
            engine = get_engine(url)
            assert engine
    """)
    assert len(findings) == 1 and "never closed" in findings[0][1]


def test_b_engine_disposed_in_teardown_is_fine():  # the H3 revision of #18
    assert unclosed("""
        class T:
            def tearDown(self):
                self.engine.dispose()
            def test_tables(self):
                self.engine = get_engine(url)
    """) == []


def test_b_with_sqlite_connection_only_commits():  # case #12
    findings = unclosed("""
        import sqlite3
        def get_connection(path):
            conn = sqlite3.connect(path)
            return conn
        def init_db(path):
            with get_connection(path) as conn:
                conn.executescript("CREATE TABLE t (id INTEGER)")
    """)
    assert len(findings) == 1 and "only commits" in findings[0][1]


def test_b_closing_in_finally_and_returning_from_a_factory_are_fine():  # the manual fix of #12
    assert unclosed("""
        import sqlite3
        def get_connection(path):
            conn = sqlite3.connect(path)
            return conn
        def init_db(path):
            conn = get_connection(path)
            try:
                with conn:
                    conn.execute("SELECT 1")
            finally:
                conn.close()
    """) == []


def test_b_sqlalchemy_connection_in_with_is_fine():
    # SQLAlchemy's engine.connect() context manager DOES close the connection.
    assert unclosed("""
        def count(engine):
            with engine.connect() as connection:
                return connection.execute("SELECT 1")
    """) == []


# --- running on files ---------------------------------------------------------

def test_validate_files_uses_the_pilot_requirements_and_skips_other_files(tmp_path):
    (tmp_path / "requirements.txt").write_text("SQLAlchemy>=2.0.0\n", encoding="utf-8")
    (tmp_path / "pilot").mkdir()
    (tmp_path / "pilot" / "db.py").write_text("import fastapi\nfrom sqlalchemy import create_engine\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("import nothing", encoding="utf-8")

    findings = validate_files(tmp_path, ["pilot/db.py", "notes.md"])

    assert [(f.check, f.file, f.line) for f in findings] == [("imports_vs_requirements", "pilot/db.py", 1)]
    assert "fastapi" in findings[0].message


# --- the warnings reach the report the human sees at H3 ---------------------

from test_correction_loop import FIXED, make_agent_branch, result, run_loop, FakeBackendAgent, CODE  # noqa: E402


def test_loop_report_contains_the_warnings(tmp_path):
    leaky = FIXED + "\nimport sqlite3\nconn = sqlite3.connect('pilot.db')\n"
    repo = make_agent_branch(tmp_path, code=leaky)

    report = run_loop(repo, result("unused", [CODE]), FakeBackendAgent())

    assert report.decision == "ready_for_review"  # tests pass: warnings never block
    assert [(f.check, f.file) for f in report.validation] == [("unclosed_resources", CODE)]
