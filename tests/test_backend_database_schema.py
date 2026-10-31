import gc
import sqlite3
import warnings
import pytest
from pilot.app.backend.db import init_db, get_connection, get_db_path
from pilot.app.backend.openapi import OPENAPI_SPEC


def test_init_db_creates_required_tables_in_temporary_file(tmp_path):
    test_db = tmp_path / "test_exam.db"
    db_path = str(test_db)

    # Verify tables do not exist before init
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables_before = [row["name"] for row in cursor.fetchall()]
    assert "users" not in tables_before
    assert "documents" not in tables_before
    assert "exam_blueprints" not in tables_before
    assert "generated_exams" not in tables_before
    conn.close()

    # Initialize schema
    init_db(db_path)

    # Verify tables exist after init
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables_after = [row["name"] for row in cursor.fetchall()]

    assert "users" in tables_after
    assert "documents" in tables_after
    assert "exam_blueprints" in tables_after
    assert "generated_exams" in tables_after

    # Test concrete operations to ensure constraints and structure
    cursor.execute(
        "INSERT INTO users (id, email) VALUES (?, ?);",
        ("usr-1", "student@example.com"),
    )
    cursor.execute(
        "INSERT INTO documents (id, user_id, title, doc_type, storage_path, status) VALUES (?, ?, ?, ?, ?, ?);",
        ("doc-1", "usr-1", "Midterm.pdf", "EXAM_SAMPLE", "/storage/doc-1", "PARSED"),
    )
    cursor.execute(
        "INSERT INTO exam_blueprints (id, source_document_id, blueprint_json) VALUES (?, ?, ?);",
        ("bp-1", "doc-1", "{}"),
    )
    cursor.execute(
        "INSERT INTO generated_exams (id, user_id, source_blueprint_id, title, status) VALUES (?, ?, ?, ?, ?);",
        ("exam-1", "usr-1", "bp-1", "Practice Midterm", "READY"),
    )
    conn.commit()

    cursor.execute("SELECT title FROM generated_exams WHERE id = 'exam-1';")
    row = cursor.fetchone()
    assert row["title"] == "Practice Midterm"
    conn.close()


def test_disallows_in_memory_sqlite():
    with pytest.raises(ValueError, match="disallowed"):
        get_db_path(":memory:")


def test_openapi_spec_structure():
    assert "paths" in OPENAPI_SPEC
    assert "/api/v1/documents" in OPENAPI_SPEC["paths"]
    assert "/api/v1/exams/generate" in OPENAPI_SPEC["paths"]


def test_init_db_closes_its_connection(tmp_path):
    db_path = str(tmp_path / "closed_check.db")

    # Python emits a ResourceWarning when a sqlite3 connection is left open.
    # Record warnings while init_db runs, and force cleanup with gc.collect().
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        init_db(db_path)
        gc.collect()

    resource_warnings = [w for w in caught if issubclass(w.category, ResourceWarning)]
    assert resource_warnings == []
