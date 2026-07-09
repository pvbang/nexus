"""
MekongAI Nexus - JWT Authentication.

Provides JWT token creation, verification, and FastAPI dependencies.
Supports optional auth mode: when JWT_SECRET_KEY is default, auth is disabled.

Uses python-jose for JWT operations (already in project dependencies).
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from fastapi import Depends, HTTPException, Request, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

REFRESH_TOKEN_EXPIRE_DAYS = 7

# Auth is enabled when ADMIN_PASSWORD is set (non-empty).
# Also enabled if JWT_SECRET_KEY is changed from default (backward compat).
AUTH_ENABLED = (
    bool(settings.admin_password)
    or bool(settings.admin_password_hash)
    or settings.jwt_secret_key != "change-me-jwt-secret"
)

_security = HTTPBearer(auto_error=False)


def create_access_token(
    user_id: str,
    extra_claims: Optional[dict] = None,
    expires_minutes: Optional[int] = None,
) -> str:
    """Create a JWT access token.

    Args:
        user_id: Subject identifier for the token.
        extra_claims: Additional claims to include in payload.
        expires_minutes: Override default expiration. Falls back to settings.

    Returns:
        Encoded JWT string.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.jwt_expire_minutes,
    )
    payload: dict = {
        "sub": user_id,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "access",
        "ver": settings.jwt_token_version,
    }
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def verify_token(token: str) -> Optional[dict]:
    """Verify and decode a JWT token.

    Args:
        token: Raw JWT string.

    Returns:
        Decoded payload dict, or None if invalid/expired.
    """
    try:
        payload: dict = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        if payload.get("ver", 0) != settings.jwt_token_version:
            logger.debug("token_version_mismatch", token_ver=payload.get("ver"))
            return None
        return payload
    except JWTError as exc:
        logger.debug("token_verification_failed", error=str(exc))
        return None


def create_refresh_token(user_id: str) -> str:
    """Create a long-lived refresh token.

    Args:
        user_id: Subject identifier for the token.

    Returns:
        Encoded JWT string with type=refresh.
    """
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "refresh",
        "ver": settings.jwt_token_version,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def verify_refresh_token(token: str) -> str:
    """Verify refresh token and return user_id.

    Args:
        token: Raw refresh JWT string.

    Returns:
        user_id from token payload.

    Raises:
        HTTPException: If token is invalid, expired, or wrong type.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        if payload.get("ver", 0) != settings.jwt_token_version:
            raise HTTPException(status_code=401, detail="Refresh token revoked")
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user_id
    except HTTPException:
        raise
    except JWTError as exc:
        if "expired" in str(exc).lower():
            raise HTTPException(status_code=401, detail="Refresh token expired")
        raise HTTPException(status_code=401, detail="Invalid refresh token")


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
) -> str:
    """FastAPI dependency: extract user_id from JWT token.

    When AUTH_ENABLED=False (default dev mode), returns ``"default_user"``.
    When AUTH_ENABLED=True, requires a valid Bearer token and raises 401
    if missing or invalid.
    """
    if not AUTH_ENABLED:
        return "default_user"

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = verify_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload.get("sub", "unknown")


async def get_download_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
) -> str:
    """Auth dependency for download endpoints - supports both Bearer header and ?token= query param.

    Browser-initiated downloads (e.g. <a href>) cannot send Authorization headers,
    so this dependency also accepts a ``?token=`` query parameter as fallback.
    """
    if not AUTH_ENABLED:
        return "default_user"

    # Check Bearer header first
    if credentials:
        payload = verify_token(credentials.credentials)
        if payload:
            return payload.get("sub", "unknown")

    # Fallback: check ?token= query param (for <a href download>)
    token = request.query_params.get("token")
    if token:
        payload = verify_token(token)
        if payload:
            return payload.get("sub", "unknown")

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Provide Bearer token in header or ?token= query parameter.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
) -> str:
    """FastAPI dependency: extract user_id if token present, else ``"default_user"``.

    Never raises — use for endpoints that work both with and without auth.
    """
    if credentials is None:
        return "default_user"

    payload = verify_token(credentials.credentials)
    if payload is None:
        return "default_user"

    return payload.get("sub", "default_user")


def get_ws_user(websocket: WebSocket) -> str:
    """Extract user_id from WebSocket query params.

    WebSocket auth approaches:
    1. Query param: ``ws://host/ws/chat/123?token=xxx``
    2. Protocol header (Sec-WebSocket-Protocol) — not implemented yet.

    Returns:
        user_id from token, or ``"default_user"`` when auth disabled
        or no valid token found.
    """
    if not AUTH_ENABLED:
        return "default_user"

    # Try query param
    token = websocket.query_params.get("token")
    if token:
        payload = verify_token(token)
        if payload:
            return payload.get("sub", "default_user")

    return "default_user"
