import os
import sqlite3
from pathlib import Path

try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import pilot.app.backend.models as models
    HAVE_SQLALCHEMY = models.HAVE_SQLALCHEMY
except ImportError:
    HAVE_SQLALCHEMY = False

DEFAULT_DB_FILE = Path(os.environ.get("PILOT_DATA_DIR", ".")) / "pilot_app.db"


def _parse_sqlite_path(url: str) -> Path:
    prefix = "sqlite:///"
    if url.startswith(prefix):
        raw_path = url[len(prefix):]
        return Path(raw_path)
    return Path(url)


def get_engine(database_url: str = None):
    if not database_url:
        url = os.environ.get("DATABASE_URL")
        if not url:
            db_path = DEFAULT_DB_FILE.resolve()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{db_path}"
        database_url = url

    if HAVE_SQLALCHEMY:
        connect_args = {}
        if database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        return create_engine(database_url, connect_args=connect_args)
    else:
        return {"url": database_url, "path": _parse_sqlite_path(database_url)}


def init_db(engine):
    if HAVE_SQLALCHEMY:
        from pilot.app.backend.models import Base
        Base.metadata.create_all(bind=engine)
    else:
        db_path = engine["path"]
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id VARCHAR(36) PRIMARY KEY,
                    email VARCHAR(255) UNIQUE NOT NULL,
                    password_hash VARCHAR(255),
                    created_at TIMESTAMP NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id VARCHAR(36) PRIMARY KEY,
                    user_id VARCHAR(36) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    doc_type VARCHAR(50) NOT NULL,
                    storage_path VARCHAR(512),
                    parsed_text TEXT,
                    status VARCHAR(30) NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS generated_exams (
                    id VARCHAR(36) PRIMARY KEY,
                    user_id VARCHAR(36) NOT NULL,
                    source_blueprint_id VARCHAR(36),
                    title VARCHAR(255) NOT NULL,
                    is_adaptive BOOLEAN NOT NULL,
                    status VARCHAR(30) NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)
        conn.close()


def get_session_factory(engine):
    if HAVE_SQLALCHEMY:
        return sessionmaker(autocommit=False, autoflush=False, bind=engine)
    else:
        raise NotImplementedError("SQLAlchemy is required for ORM session management.")
