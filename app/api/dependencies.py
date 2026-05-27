"""FastAPI dependencies — current user, role guards."""
from typing import Any, Dict, List, Optional

from fastapi import Depends, Header

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.services import AuthService


async def get_current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("Missing or malformed Authorization header")
    token = authorization.split(" ", 1)[1].strip()
    return AuthService.current_user(token)


def require_roles(*allowed_roles: str):
    async def _check(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
        if user.get("role") not in allowed_roles:
            raise ForbiddenError(
                f"Role '{user.get('role')}' lacks permission for this resource",
                {"required": list(allowed_roles)},
            )
        return user

    return _check


def require_admin(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    if user.get("role") != "admin":
        raise ForbiddenError("Admin role required")
    return user


def require_engineer(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    if user.get("role") not in ("engineer", "admin"):
        raise ForbiddenError("Engineer role required")
    return user
