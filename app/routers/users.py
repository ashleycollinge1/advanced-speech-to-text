from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..media import remove_recording_media
from ..models import ROLE_ADMIN, ROLE_USER, Recording, User
from ..security import destroy_user_sessions, generate_temp_password, hash_password, require_admin
from .auth import user_out

router = APIRouter(prefix="/api/users", tags=["users"])

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")


class UserCreateIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    role: str = Field(default=ROLE_USER, pattern=f"^({ROLE_ADMIN}|{ROLE_USER})$")


class UserPatchIn(BaseModel):
    role: str | None = Field(default=None, pattern=f"^({ROLE_ADMIN}|{ROLE_USER})$")
    is_active: bool | None = None


def _get(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found.")
    return user


def _active_admins(db: Session) -> int:
    return db.scalar(select(func.count()).where(User.role == ROLE_ADMIN, User.is_active.is_(True))) or 0


@router.get("")
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    counts = dict(db.execute(select(Recording.owner_id, func.count()).group_by(Recording.owner_id)).all())
    users = db.scalars(select(User).order_by(User.username)).all()
    return {"users": [{**user_out(u), "recordings": counts.get(u.id, 0)} for u in users]}


@router.post("", status_code=201)
def create_user(body: UserCreateIn, _: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    username = body.username.strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Usernames use letters, numbers, dot, dash or underscore (2-64 characters).",
        )
    if db.scalar(select(User).where(func.lower(User.username) == username.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "That username is taken.")
    temp = generate_temp_password()
    user = User(username=username, role=body.role, password_hash=hash_password(temp), must_change_password=True)
    db.add(user)
    db.commit()
    return {"user": {**user_out(user), "recordings": 0}, "temporary_password": temp}


@router.patch("/{user_id}")
def update_user(
    user_id: int, body: UserPatchIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> dict:
    user = _get(db, user_id)
    demoting = body.role == ROLE_USER and user.role == ROLE_ADMIN
    deactivating = body.is_active is False and user.is_active
    if user.id == admin.id and (demoting or deactivating):
        raise HTTPException(status.HTTP_409_CONFLICT, "You can't demote or deactivate yourself.")
    if user.role == ROLE_ADMIN and (demoting or deactivating) and _active_admins(db) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "At least one active administrator is required.")
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
        if not body.is_active:
            destroy_user_sessions(db, user.id)
    db.commit()
    return {"user": user_out(user)}


@router.post("/{user_id}/reset-password")
def reset_password(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    user = _get(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Use Account settings to change your own password.")
    temp = generate_temp_password()
    user.password_hash = hash_password(temp)
    user.must_change_password = True
    destroy_user_sessions(db, user.id)
    db.commit()
    return {"user": user_out(user), "temporary_password": temp}


@router.delete("/{user_id}")
def delete_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    user = _get(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You can't delete your own account.")
    if user.role == ROLE_ADMIN and _active_admins(db) <= 1 and user.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "At least one active administrator is required.")
    rec_ids = db.scalars(select(Recording.id).where(Recording.owner_id == user.id)).all()
    db.delete(user)
    db.commit()
    for rid in rec_ids:
        remove_recording_media(rid)
    return {"ok": True}
