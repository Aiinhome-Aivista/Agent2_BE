"""Authentication / user schemas."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field

UserRole = Literal["admin", "engineer", "user"]


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)


class RefreshRequest(BaseModel):
    refresh_token: str


class UserPublic(BaseModel):
    id: str
    email: EmailStr
    full_name: str = Field(..., alias="fullName")
    role: UserRole
    avatar_url: Optional[str] = Field(None, alias="avatarUrl")
    created_at: datetime = Field(..., alias="createdAt")
    last_login_at: Optional[datetime] = Field(None, alias="lastLoginAt")

    model_config = {"populate_by_name": True}


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LoginResponse(BaseModel):
    user: UserPublic
    access_token: str
    refresh_token: str
