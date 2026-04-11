from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .config import get_settings


settings = get_settings()


def _build_engine():
    db_url = settings.db_url
    connect_args = {}

    if db_url.startswith("sqlite:///"):
        sqlite_path = db_url.replace("sqlite:///", "", 1)
        db_dir = Path(sqlite_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
        connect_args = {"check_same_thread": False}

    return create_engine(db_url, echo=False, future=True, pool_pre_ping=True, connect_args=connect_args)


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

