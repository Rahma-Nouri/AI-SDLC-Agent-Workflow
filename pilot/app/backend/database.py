import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from pilot.app.backend.models import Base

DEFAULT_DB_FILE = Path(os.environ.get("PILOT_DATA_DIR", ".")) / "pilot_app.db"


def get_engine(database_url: str = None):
    if not database_url:
        url = os.environ.get("DATABASE_URL")
        if not url:
            db_path = DEFAULT_DB_FILE.resolve()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{db_path}"
        database_url = url

    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    return create_engine(database_url, connect_args=connect_args)


def init_db(engine):
    Base.metadata.create_all(bind=engine)


def get_session_factory(engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)
