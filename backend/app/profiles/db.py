"""Persistence for mutable data: profiles (versioned), plans, recommendation runs.

Academic reference data lives in the immutable published snapshot, not in the database.
DATABASE_URL selects the backend (default: SQLite file; PostgreSQL via docker-compose).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

DEFAULT_DB = Path(__file__).resolve().parents[3] / "data" / "app.db"
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DEFAULT_DB}")


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ProfileRow(Base):
    __tablename__ = "profiles"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PlanRow(Base):
    __tablename__ = "plans"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="Semester plan")
    profile_version: Mapped[int] = mapped_column(Integer)
    dataset_version: Mapped[str] = mapped_column(String(64))
    rules_version: Mapped[str] = mapped_column(String(64))
    offering_ids: Mapped[list] = mapped_column(JSON)
    selection: Mapped[dict] = mapped_column(JSON, default=dict)  # locked sections + prefs
    validation: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RunRow(Base):
    __tablename__ = "recommendation_runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(40), index=True)
    profile_version: Mapped[int] = mapped_column(Integer)
    dataset_version: Mapped[str] = mapped_column(String(64))
    rules_version: Mapped[str] = mapped_column(String(64))
    query: Mapped[str] = mapped_column(Text)
    intent: Mapped[dict] = mapped_column(JSON)
    parser: Mapped[str] = mapped_column(String(20))
    summary: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


_engine = None
_Session = None


def init_db(url: str | None = None):
    global _engine, _Session
    url = url or DATABASE_URL
    if url.startswith("sqlite:///"):
        Path(url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(url, future=True,
                            connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    Base.metadata.create_all(_engine)
    _Session = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def session() -> Session:
    if _Session is None:
        init_db()
    return _Session()


def hash_token(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def new_token() -> str:
    return secrets.token_urlsafe(24)


def dumps(obj) -> dict:
    return json.loads(json.dumps(obj, default=str))


__all__ = ["ProfileRow", "PlanRow", "RunRow", "init_db", "session", "hash_token", "new_id", "new_token", "select",
           "dumps", "now"]
