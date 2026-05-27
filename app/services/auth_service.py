"""Auth service — login / refresh / current user."""
from typing import Dict

from app.core.exceptions import UnauthorizedError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.repositories import UserRepository


class AuthService:
    @staticmethod
    def login(email: str, password: str) -> Dict:
        user = UserRepository.find_by_email(email)
        if not user or not verify_password(password, user["password_hash"]):
            raise UnauthorizedError("Invalid email or password")

        UserRepository.update_last_login(user["id"])
        access = create_access_token(user["id"], {"role": user["role"]})
        refresh = create_refresh_token(user["id"])

        # Strip sensitive fields before returning.
        public = {k: v for k, v in user.items() if k != "password_hash"}
        return {"user": public, "access_token": access, "refresh_token": refresh}

    @staticmethod
    def refresh(refresh_token: str) -> Dict:
        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh":
            raise UnauthorizedError("Invalid refresh token")

        user_id = payload.get("sub")
        if not user_id:
            raise UnauthorizedError("Invalid token payload")

        user = UserRepository.find_by_id(user_id)
        if not user:
            raise UnauthorizedError("User not found")

        access = create_access_token(user["id"], {"role": user["role"]})
        return {"access_token": access, "refresh_token": refresh_token}

    @staticmethod
    def current_user(access_token: str) -> Dict:
        payload = decode_token(access_token)
        if payload.get("type") != "access":
            raise UnauthorizedError("Invalid access token")
        user = UserRepository.find_by_id(payload["sub"])
        if not user:
            raise UnauthorizedError("User not found")
        return user
