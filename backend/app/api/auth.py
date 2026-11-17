"""Login with the school code and session status."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import Services, api_error, services
from app.auth import COOKIE, code_matches, make_token, token_valid

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    code: str = Field(max_length=200)


def client_key(request: Request) -> str:
    return request.client.host if request.client else "desconhecido"


@router.get("/status")
def status(request: Request, svc: Services = Depends(services)) -> dict:
    required = svc.settings.access_required
    return {
        "required": required,
        "authenticated": (not required) or token_valid(svc.settings, request.cookies.get(COOKIE)),
    }


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response, svc: Services = Depends(services)) -> dict:
    settings = svc.settings
    if not settings.access_required:
        return {"authenticated": True}
    key = f"login:{client_key(request)}"
    if svc.limiter.blocked(key, limit=10, window_s=600):
        raise api_error(429, "too_many_attempts", "Muitas tentativas. Espere alguns minutos e tente de novo.")
    if not code_matches(settings, body.code):
        svc.limiter.hit(key)  # only wrong codes count
        raise api_error(401, "wrong_code", "Código incorreto. Confira com a secretaria da escola.")
    response.set_cookie(
        COOKIE, make_token(settings), max_age=settings.session_days * 86400, httponly=True,
        samesite="lax", secure=request.url.scheme == "https", path="/",
    )
    return {"authenticated": True}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE, path="/")
    return {"authenticated": False}
