from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

ROLE_ADMIN = "admin"
ROLE_USER = "user"

STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_DONE = "done"
STATUS_ERROR = "error"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_USER)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_users_username_lower", func.lower(username), unique=True),)

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


class AuthSession(Base):
    __tablename__ = "sessions"

    # SHA-256 of the cookie token; the raw token is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped[User] = relationship(lazy="joined")


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    original_ext: Mapped[str] = mapped_column(String(16), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="upload")  # upload | browser

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_QUEUED, index=True)
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    duration: Mapped[float | None] = mapped_column(Float)
    model: Mapped[str | None] = mapped_column(String(64))
    # Normalised hotwords ("Term A, Term B") passed to the transcriber.
    vocabulary: Mapped[str | None] = mapped_column(Text)
    has_playback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    owner: Mapped[User] = relationship(lazy="joined")


class Segment(Base):
    __tablename__ = "segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recording_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"), nullable=False)
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    start: Mapped[float] = mapped_column(Float, nullable=False)
    end: Mapped[float] = mapped_column(Float, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # [[start, end, "word"], ...]: compact and returned to the client as-is.
    words: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tsv = mapped_column(TSVECTOR, Computed("to_tsvector('english', text)", persisted=True))

    __table_args__ = (
        Index("ix_segments_recording_idx", "recording_id", "idx", unique=True),
        Index("ix_segments_tsv", "tsv", postgresql_using="gin"),
    )


class Highlight(Base):
    __tablename__ = "highlights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recording_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    start: Mapped[float] = mapped_column(Float, nullable=False)
    end: Mapped[float] = mapped_column(Float, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
