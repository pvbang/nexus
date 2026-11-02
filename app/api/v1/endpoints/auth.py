"""
Nexus - Authentication Endpoints.

Simple credential-based auth. Supports:
- Username/password login (for admin access)
- Token refresh (exchange refresh token for new token pair)
- Auth status check

In dev mode (ADMIN_PASSWORD is empty), auth is disabled and tokens are
generated freely without validation.
"""

import secrets
from typing import Optional

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.config import get_settings
from app.core.auth import (
    AUTH_ENABLED,
    create_access_token,
    create_refresh_token,
    verify_refresh_token,
)

logger = structlog.get_logger(__name__)
settings = get_settings()

router = APIRouter()


class LoginRequest(BaseModel):
    """Request body for login."""

    username: str = Field(..., min_length=1, max_length=255, description="Username")
    password: Optional[str] = Field(None, description="Password")


class RefreshRequest(BaseModel):
    """Request body for token refresh."""

    refresh_token: str = Field(..., description="Refresh token to exchange")


class TokenResponse(BaseModel):
    """JWT token response."""

    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: int
    user_id: str
    auth_enabled: bool


# Legacy schema support (programmatic access)
class TokenRequest(BaseModel):
    """Legacy request body for token generation (programmatic/API access)."""

    user_id: str = Field(..., min_length=1, max_length=255, description="User identifier")
    api_key: Optional[str] = Field(None, description="API key for authentication")


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest) -> TokenResponse:
    """Login with username and password.

    In dev mode (``AUTH_ENABLED=False``), any username works without password.
    In production mode, validates against ADMIN_USERNAME/ADMIN_PASSWORD from .env.

    Returns access_token + refresh_token for token renewal.
    """
    if AUTH_ENABLED:
        # Production: validate credentials in constant time. Prefer a bcrypt
        # hash (ADMIN_PASSWORD_HASH) over legacy plaintext (ADMIN_PASSWORD).
        from app.core.password import verify_admin_password

        username_ok = secrets.compare_digest(
            request.username.encode("utf-8"),
            settings.admin_username.encode("utf-8"),
        )
        password_ok = verify_admin_password(
            request.password or "",
            hashed=settings.admin_password_hash,
            plaintext=settings.admin_password,
        )
        if not (username_ok and password_ok):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password.",
            )

    user_id = request.username
    token = create_access_token(user_id=user_id)
    refresh = create_refresh_token(user_id=user_id)

    logger.info(
        "login_success",
        user_id=user_id,
        auth_enabled=AUTH_ENABLED,
    )

    return TokenResponse(
        access_token=token,
        refresh_token=refresh,
        token_type="bearer",
        expires_in=settings.jwt_expire_minutes * 60,
        user_id=user_id,
        auth_enabled=AUTH_ENABLED,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: RefreshRequest) -> TokenResponse:
    """Exchange a refresh token for a new access + refresh token pair.

    The old refresh token is consumed; a new one is issued.
    """
    user_id = verify_refresh_token(body.refresh_token)
    new_access = create_access_token(user_id=user_id)
    new_refresh = create_refresh_token(user_id=user_id)

    logger.info("token_refreshed", user_id=user_id)

    return TokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        token_type="bearer",
        expires_in=settings.jwt_expire_minutes * 60,
        user_id=user_id,
        auth_enabled=AUTH_ENABLED,
    )


@router.post("/token", response_model=TokenResponse)
async def create_token(request: TokenRequest) -> TokenResponse:
    """Generate a JWT access token (legacy/programmatic endpoint).

    In dev mode (``AUTH_ENABLED=False``), tokens are generated freely.
    In production mode, requires a valid ``api_key`` (= JWT_SECRET_KEY).
    """
    if AUTH_ENABLED:
        # Production: validate API key against the configured secret
        if request.api_key != settings.jwt_secret_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key.",
            )

    token = create_access_token(user_id=request.user_id)

    logger.info(
        "token_created",
        user_id=request.user_id,
        auth_enabled=AUTH_ENABLED,
    )

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.jwt_expire_minutes * 60,
        user_id=request.user_id,
        auth_enabled=AUTH_ENABLED,
    )


@router.get("/status")
async def auth_status() -> dict:
    """Check whether authentication is enabled."""
    return {
        "auth_enabled": AUTH_ENABLED,
        "message": (
            "Authentication is enabled. Use POST /api/v1/auth/login with username and password."
            if AUTH_ENABLED
            else "Authentication is disabled (dev mode). Set ADMIN_PASSWORD in .env to enable."
        ),
    }
