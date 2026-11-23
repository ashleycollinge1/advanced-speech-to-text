from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import AuthSession, User
from ..security import (
    burn_dummy_verify,
    create_session,
    current_session,
    destroy_session,
    destroy_user_sessions,
    hash_password,
    load_session,
    login_limiter,
    needs_rehash,
    validate_new_password,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


def user_out(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "is_active": user.is_active,
        "must_change_password": user.must_change_password,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    ip = _client_ip(request)
    wait = login_limiter.retry_after(ip, body.username)
    if wait:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many failed attempts. Try again in {max(1, wait // 60)} minute(s).",
            headers={"Retry-After": str(wait)},
        )

    user = db.scalar(select(User).where(func.lower(User.username) == body.username.strip().lower()))
    if user is None:
        burn_dummy_verify(body.password)
        ok = False
    else:
        ok = verify_password(user.password_hash, body.password) and user.is_active

    if not ok:
        login_limiter.fail(ip, body.username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect username or password.")

    login_limiter.succeed(body.username)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.last_login_at = datetime.now(UTC)
    # Drop any pre-existing cookie session to prevent fixation.
    destroy_session(db, request, response)
    sess = create_session(db, user, response)
    return {"user": user_out(user), "csrf_token": sess.csrf_token}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    # CSRF-checked when a session exists; logging out without one is a no-op.
    sess = load_session(db, request)
    if sess is not None:
        current_session(request, db)
    destroy_session(db, request, response)
    return {"ok": True}


@router.get("/me")
def me(sess: AuthSession = Depends(current_session)) -> dict:
    return {"user": user_out(sess.user), "csrf_token": sess.csrf_token, "version": settings.app_version}


@router.post("/password")
def change_password(
    body: PasswordChangeIn,
    sess: AuthSession = Depends(current_session),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, sess.user_id)
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")
    if body.new_password == body.current_password:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "New password must differ from the current one.")
    validate_new_password(body.new_password, user.username)
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    # Sign out every other device.
    destroy_user_sessions(db, user.id, keep_token_hash=sess.token_hash)
    db.commit()
    return {"user": user_out(user)}
