import os
import tempfile
import unittest
from pathlib import Path
from sqlalchemy import inspect
from pilot.app.backend.database import get_engine, init_db, get_session_factory
from pilot.app.backend.models import User, Document, GeneratedExam


class TestDatabaseBackend(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_pilot.db"
        self.db_url = f"sqlite:///{self.db_path.resolve()}"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_tables_not_created_on_engine_creation(self):
        engine = get_engine(self.db_url)
        inspector = inspect(engine)
        table_names = inspector.get_table_names()
        self.assertNotIn("users", table_names)
        self.assertNotIn("documents", table_names)
        self.assertNotIn("generated_exams", table_names)

    def test_database_initialization_creates_schema_and_records(self):
        engine = get_engine(self.db_url)
        init_db(engine)

        inspector = inspect(engine)
        tables = inspector.get_table_names()
        self.assertIn("users", tables)
        self.assertIn("documents", tables)
        self.assertIn("generated_exams", tables)

        session_maker = get_session_factory(engine)
        with session_maker() as session:
            user = User(email="student@example.com")
            session.add(user)
            session.flush()

            doc = Document(
                user_id=user.id,
                title="Midterm Exam 2024",
                doc_type="EXAM_SAMPLE",
                status="PENDING"
            )
            session.add(doc)

            exam = GeneratedExam(
                user_id=user.id,
                title="Practice Exam 1",
                is_adaptive=True
            )
            session.add(exam)
            session.commit()

            persisted_user = session.query(User).filter_by(email="student@example.com").one()
            self.assertEqual(len(persisted_user.documents), 1)
            self.assertEqual(persisted_user.documents[0].title, "Midterm Exam 2024")
            self.assertEqual(len(persisted_user.generated_exams), 1)
            self.assertEqual(persisted_user.generated_exams[0].title, "Practice Exam 1")

        # Confirm that the file exists and is not in-memory
        self.assertTrue(self.db_path.exists())
        self.assertGreater(os.path.getsize(self.db_path), 0)


if __name__ == "__main__":
    unittest.main()
