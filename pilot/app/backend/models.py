import uuid
from datetime import datetime, timezone

try:
    from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text
    from sqlalchemy.orm import declarative_base, relationship
    HAVE_SQLALCHEMY = True
except ImportError:
    HAVE_SQLALCHEMY = False


def generate_uuid():
    return str(uuid.uuid4())


if HAVE_SQLALCHEMY:
    Base = declarative_base()

    class User(Base):
        __tablename__ = "users"

        id = Column(String(36), primary_key=True, default=generate_uuid)
        email = Column(String(255), unique=True, nullable=False, index=True)
        password_hash = Column(String(255), nullable=True)
        created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

        documents = relationship("Document", back_populates="user", cascade="all, delete-orphan")
        generated_exams = relationship("GeneratedExam", back_populates="user", cascade="all, delete-orphan")

    class Document(Base):
        __tablename__ = "documents"

        id = Column(String(36), primary_key=True, default=generate_uuid)
        user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
        title = Column(String(255), nullable=False)
        doc_type = Column(String(50), nullable=False)
        storage_path = Column(String(512), nullable=True)
        parsed_text = Column(Text, nullable=True)
        status = Column(String(30), default="PENDING", nullable=False)
        created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

        user = relationship("User", back_populates="documents")

    class GeneratedExam(Base):
        __tablename__ = "generated_exams"

        id = Column(String(36), primary_key=True, default=generate_uuid)
        user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
        source_blueprint_id = Column(String(36), nullable=True)
        title = Column(String(255), nullable=False)
        is_adaptive = Column(Boolean, default=False, nullable=False)
        status = Column(String(30), default="GENERATING", nullable=False)
        created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

        user = relationship("User", back_populates="generated_exams")
else:
    Base = None

    class User:
        def __init__(self, email, password_hash=None, id=None, created_at=None):
            self.id = id or generate_uuid()
            self.email = email
            self.password_hash = password_hash
            self.created_at = created_at or datetime.now(timezone.utc)
            self.documents = []
            self.generated_exams = []

    class Document:
        def __init__(self, user_id, title, doc_type, storage_path=None, parsed_text=None, status="PENDING", id=None, created_at=None):
            self.id = id or generate_uuid()
            self.user_id = user_id
            self.title = title
            self.doc_type = doc_type
            self.storage_path = storage_path
            self.parsed_text = parsed_text
            self.status = status
            self.created_at = created_at or datetime.now(timezone.utc)
            self.user = None

    class GeneratedExam:
        def __init__(self, user_id, title, source_blueprint_id=None, is_adaptive=False, status="GENERATING", id=None, created_at=None):
            self.id = id or generate_uuid()
            self.user_id = user_id
            self.source_blueprint_id = source_blueprint_id
            self.title = title
            self.is_adaptive = is_adaptive
            self.status = status
            self.created_at = created_at or datetime.now(timezone.utc)
            self.user = None
