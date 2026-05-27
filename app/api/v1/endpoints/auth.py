"""Auth endpoints — login, refresh, logout, me."""
from typing import Any, Dict

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_user
from app.schemas import (
    ApiResponse,
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    UserPublic,
)
from app.services import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=ApiResponse[LoginResponse])
async def login(payload: LoginRequest) -> ApiResponse[LoginResponse]:
    result = AuthService.login(payload.email, payload.password)
    return ApiResponse(data=LoginResponse(**result))


@router.post("/refresh", response_model=ApiResponse[Dict[str, str]])
async def refresh(payload: RefreshRequest) -> ApiResponse[Dict[str, str]]:
    return ApiResponse(data=AuthService.refresh(payload.refresh_token))


@router.post("/logout", response_model=ApiResponse[Dict[str, str]])
async def logout(_: Dict[str, Any] = Depends(get_current_user)) -> ApiResponse[Dict[str, str]]:
    # Stateless JWT — client just discards. For full revocation we'd add a deny-list in Redis.
    return ApiResponse(data={"status": "logged_out"}, message="Successfully logged out")


@router.get("/me", response_model=ApiResponse[UserPublic])
async def me(user: Dict[str, Any] = Depends(get_current_user)) -> ApiResponse[UserPublic]:
    return ApiResponse(data=UserPublic(**user))
