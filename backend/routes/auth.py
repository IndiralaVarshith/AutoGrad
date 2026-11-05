"""Teacher login with the shared password."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Cookie, HTTPException, Response
from pydantic import BaseModel

from services import teacher_auth

router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    password: str


@router.post("/login")
async def login(request: LoginRequest, response: Response):
    if not teacher_auth.is_configured():
        raise HTTPException(503, "TEACHER_PASSWORD is not set on the server, so nobody can sign in.")
    if not teacher_auth.password_matches(request.password):
        await asyncio.sleep(1)  # Slows down password guessing.
        raise HTTPException(401, "That password is not right.")
    response.set_cookie(
        teacher_auth.COOKIE_NAME,
        teacher_auth.new_session(),
        max_age=teacher_auth.SESSION_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return {"signed_in": True}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(teacher_auth.COOKIE_NAME)
    return {"signed_in": False}


@router.get("/status")
async def status(autograde_session: str | None = Cookie(default=None)):
    return {
        "signed_in": teacher_auth.session_is_valid(autograde_session),
        "password_configured": teacher_auth.is_configured(),
    }
