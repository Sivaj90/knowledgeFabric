"""SQLAlchemy engine/session/declarative base, wired to kb_fabric.config settings."""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from kb_fabric.config import get_settings


class Base(DeclarativeBase):
    pass


def get_engine():
    settings = get_settings()
    return create_engine(settings.sqlalchemy_database_url, future=True)


def get_sessionmaker():
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
