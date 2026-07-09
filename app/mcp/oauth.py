"""
MekongAI Nexus - MCP OAuth 2.1 with PKCE.

Cho phép MekongAI Nexus authenticate với MCP servers yêu cầu OAuth.

Implements:
- PKCE (Proof Key for Code Exchange) — RFC 7636
- OAuth 2.1 authorization code flow
- Token storage (in-memory + file-based)
- Auto-refresh expired tokens

Classes:
- PKCEChallenge: code_verifier + code_challenge pair
- OAuthToken: access/refresh token with expiry tracking
- OAuthClientConfig: OAuth endpoint configuration
- MCPOAuthClient: Full OAuth 2.1 PKCE flow
- MCPTokenStore: In-memory + file-based token persistence
"""

import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx
import structlog

logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------
# PKCE Challenge
# ------------------------------------------------------------------


@dataclass
class PKCEChallenge:
    """
    PKCE code_verifier + code_challenge pair.

    RFC 7636: code_verifier is 43-128 URL-safe chars.
    code_challenge is SHA256(code_verifier), base64url-encoded.
    """

    code_verifier: str
    code_challenge: str
    method: str = "S256"


# ------------------------------------------------------------------
# OAuth Token
# ------------------------------------------------------------------


@dataclass
class OAuthToken:
    """
    OAuth 2.1 token with expiry tracking.

    Properties:
    - is_expired: True nếu token đã hết hạn (với buffer 60s).
    - expires_at: Unix timestamp khi token hết hạn.
    """

    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 3600
    refresh_token: Optional[str] = None
    scope: str = ""
    issued_at: float = field(default_factory=time.time)

    @property
    def is_expired(self) -> bool:
        """Check if token is expired (with 60s buffer)."""
        return time.time() >= (self.issued_at + self.expires_in - 60)

    @property
    def expires_at(self) -> float:
        """Unix timestamp when token expires."""
        return self.issued_at + self.expires_in

    def to_dict(self) -> dict[str, Any]:
        """Serialize token to dict."""
        return {
            "access_token": self.access_token,
            "token_type": self.token_type,
            "expires_in": self.expires_in,
            "refresh_token": self.refresh_token,
            "scope": self.scope,
            "issued_at": self.issued_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OAuthToken":
        """Deserialize token from dict."""
        return cls(
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            expires_in=data.get("expires_in", 3600),
            refresh_token=data.get("refresh_token"),
            scope=data.get("scope", ""),
            issued_at=data.get("issued_at", time.time()),
        )


# ------------------------------------------------------------------
# OAuth Client Config
# ------------------------------------------------------------------


@dataclass
class OAuthClientConfig:
    """OAuth 2.1 client configuration for an MCP server."""

    client_id: str
    authorization_endpoint: str
    token_endpoint: str
    redirect_uri: str = "http://localhost:8000/api/v1/mcp/oauth/callback"
    scope: str = "mcp"


# ------------------------------------------------------------------
# MCP OAuth Client
# ------------------------------------------------------------------


class MCPOAuthClient:
    """
    OAuth 2.1 PKCE client cho MCP server authentication.

    Usage:
        config = OAuthClientConfig(
            client_id="my-app",
            authorization_endpoint="https://auth.example.com/authorize",
            token_endpoint="https://auth.example.com/token",
        )
        client = MCPOAuthClient(config)

        # Step 1: Build authorization URL
        auth_url, pkce = client.build_authorization_url()

        # Step 2: User visits auth_url, gets redirected with ?code=...

        # Step 3: Exchange code for token
        token = await client.exchange_code(code="...", pkce=pkce)

        # Step 4: Auto-refresh when expired
        token = await client.ensure_valid_token(token)
    """

    def __init__(self, config: OAuthClientConfig) -> None:
        self.config = config

    @staticmethod
    def generate_pkce() -> PKCEChallenge:
        """
        Generate PKCE code_verifier and code_challenge.

        code_verifier: 64 URL-safe random chars (secrets.token_urlsafe(48))
        code_challenge: SHA256(code_verifier), base64url-encoded without padding
        """
        # Generate code_verifier (64 chars, URL-safe)
        code_verifier = secrets.token_urlsafe(48)

        # Generate code_challenge = BASE64URL(SHA256(code_verifier))
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

        return PKCEChallenge(
            code_verifier=code_verifier,
            code_challenge=code_challenge,
            method="S256",
        )

    def build_authorization_url(
        self, state: Optional[str] = None
    ) -> tuple[str, PKCEChallenge]:
        """
        Build OAuth authorization URL with PKCE challenge.

        Args:
            state: Optional CSRF state parameter. Auto-generated if None.

        Returns:
            Tuple of (authorization_url, PKCEChallenge).
        """
        pkce = self.generate_pkce()

        if state is None:
            state = secrets.token_urlsafe(32)

        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "scope": self.config.scope,
            "code_challenge": pkce.code_challenge,
            "code_challenge_method": pkce.method,
            "state": state,
        }

        # Build query string
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{self.config.authorization_endpoint}?{query}"

        logger.info(
            "oauth_authorization_url_built",
            client_id=self.config.client_id,
            endpoint=self.config.authorization_endpoint,
        )

        return url, pkce

    async def exchange_code(
        self, code: str, pkce: PKCEChallenge
    ) -> OAuthToken:
        """
        Exchange authorization code for tokens (OAuth 2.1 + PKCE).

        POST to token_endpoint with:
        - grant_type=authorization_code
        - code
        - redirect_uri
        - client_id
        - code_verifier (PKCE proof)

        Args:
            code: Authorization code from callback.
            pkce: PKCEChallenge used during authorization.

        Returns:
            OAuthToken with access_token and optional refresh_token.

        Raises:
            httpx.HTTPStatusError: If token endpoint returns error.
            ValueError: If response is missing required fields.
        """
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.config.redirect_uri,
            "client_id": self.config.client_id,
            "code_verifier": pkce.code_verifier,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self.config.token_endpoint,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            data = response.json()

        if "access_token" not in data:
            raise ValueError(
                f"Token endpoint response missing access_token: {data}"
            )

        token = OAuthToken(
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            expires_in=data.get("expires_in", 3600),
            refresh_token=data.get("refresh_token"),
            scope=data.get("scope", ""),
            issued_at=time.time(),
        )

        logger.info(
            "oauth_code_exchanged",
            client_id=self.config.client_id,
            expires_in=token.expires_in,
            has_refresh=token.refresh_token is not None,
        )

        return token

    async def refresh_token(self, token: OAuthToken) -> OAuthToken:
        """
        Refresh an expired token using refresh_token grant.

        POST to token_endpoint with:
        - grant_type=refresh_token
        - refresh_token
        - client_id

        Args:
            token: Existing OAuthToken with refresh_token.

        Returns:
            New OAuthToken.

        Raises:
            ValueError: If token has no refresh_token.
            httpx.HTTPStatusError: If refresh fails.
        """
        if not token.refresh_token:
            raise ValueError("Cannot refresh: token has no refresh_token")

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
            "client_id": self.config.client_id,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self.config.token_endpoint,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            data = response.json()

        if "access_token" not in data:
            raise ValueError(
                f"Token refresh response missing access_token: {data}"
            )

        new_token = OAuthToken(
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            expires_in=data.get("expires_in", 3600),
            refresh_token=data.get("refresh_token", token.refresh_token),
            scope=data.get("scope", token.scope),
            issued_at=time.time(),
        )

        logger.info(
            "oauth_token_refreshed",
            client_id=self.config.client_id,
            expires_in=new_token.expires_in,
        )

        return new_token

    async def ensure_valid_token(self, token: OAuthToken) -> OAuthToken:
        """
        Ensure token is valid. Auto-refresh if expired.

        Args:
            token: OAuthToken to validate.

        Returns:
            Valid OAuthToken (may be refreshed).

        Raises:
            ValueError: If expired and no refresh_token available.
        """
        if not token.is_expired:
            return token

        logger.info("oauth_token_expired_refreshing", client_id=self.config.client_id)

        if not token.refresh_token:
            raise ValueError(
                "OAuth token expired and no refresh_token available. "
                "Re-authorization required."
            )

        return await self.refresh_token(token)


# ------------------------------------------------------------------
# Token Store
# ------------------------------------------------------------------


class MCPTokenStore:
    """
    In-memory + file-based token storage for MCP OAuth tokens.

    Tokens are stored in memory for fast access and persisted to
    workspace/.mcp-tokens.json with base64 encoding (not plain text).
    """

    def __init__(self, storage_path: Optional[str] = None) -> None:
        self._tokens: dict[str, OAuthToken] = {}
        self._storage_path = Path(
            storage_path or os.path.join("workspace", ".mcp-tokens.json")
        )
        self._load_from_file()

    def store(self, server_name: str, token: OAuthToken) -> None:
        """Store token for a server."""
        self._tokens[server_name] = token
        self._save_to_file()
        logger.info("oauth_token_stored", server=server_name)

    def get(self, server_name: str) -> Optional[OAuthToken]:
        """Get token for a server. Returns None if not found."""
        return self._tokens.get(server_name)

    def remove(self, server_name: str) -> bool:
        """Remove token for a server. Returns True if existed."""
        if server_name in self._tokens:
            del self._tokens[server_name]
            self._save_to_file()
            logger.info("oauth_token_removed", server=server_name)
            return True
        return False

    def list_servers(self) -> list[str]:
        """List server names with stored tokens."""
        return list(self._tokens.keys())

    def get_status(self) -> list[dict[str, Any]]:
        """Get status of all stored tokens."""
        result: list[dict[str, Any]] = []
        for name, token in self._tokens.items():
            result.append({
                "server_name": name,
                "token_type": token.token_type,
                "scope": token.scope,
                "is_expired": token.is_expired,
                "expires_at": token.expires_at,
                "has_refresh_token": token.refresh_token is not None,
            })
        return result

    def _save_to_file(self) -> None:
        """Persist tokens to file with base64 encoding."""
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            data: dict[str, dict[str, Any]] = {}
            for name, token in self._tokens.items():
                data[name] = token.to_dict()

            # Encode as base64 (obfuscation, not encryption)
            json_bytes = json.dumps(data, indent=2).encode("utf-8")
            encoded = base64.b64encode(json_bytes).decode("ascii")

            self._storage_path.write_text(encoded, encoding="utf-8")
            logger.debug("oauth_tokens_saved", path=str(self._storage_path))
        except Exception as e:
            logger.warning("oauth_tokens_save_failed", error=str(e))

    def _load_from_file(self) -> None:
        """Load tokens from file."""
        if not self._storage_path.exists():
            return

        try:
            encoded = self._storage_path.read_text(encoding="utf-8").strip()
            if not encoded:
                return

            json_bytes = base64.b64decode(encoded)
            data = json.loads(json_bytes.decode("utf-8"))

            for name, token_data in data.items():
                self._tokens[name] = OAuthToken.from_dict(token_data)

            logger.info(
                "oauth_tokens_loaded",
                count=len(self._tokens),
                servers=list(self._tokens.keys()),
            )
        except Exception as e:
            logger.warning("oauth_tokens_load_failed", error=str(e))


# ------------------------------------------------------------------
# Module-level singletons
# ------------------------------------------------------------------

_token_store: Optional[MCPTokenStore] = None


def get_token_store() -> MCPTokenStore:
    """Get or create the global token store."""
    global _token_store
    if _token_store is None:
        _token_store = MCPTokenStore()
    return _token_store
