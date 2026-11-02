"""
Nexus - AWS Credential Refresher.

Refreshes AWS session tokens via capitol-ai-grafana IMDS bridge script.
Strategy: Run `node capitol_creds_refresh.js` → read output JSON → update env/settings.
Fallback: Read existing output file if script fails but file is still valid.
"""

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)


def should_run_aws_refresh(settings) -> bool:
    """Decide whether the AWS session-token refresher should run.

    Centralizes the enablement rule (used by main.py at startup and by the
    auth-failure path) so the two never disagree:

    - ``aws_token_refresh_enabled is True``  → always on (operator opt-in).
    - ``aws_token_refresh_enabled is False`` → always off (static/long-lived key,
      or bedrock no longer the LLM — rotating a session token is then pointless).
    - ``aws_token_refresh_enabled is None``  → AUTO: on only when a bedrock
      provider is in use AND the credentials carry a rotating STS session token.
      A static access+secret IAM key has nothing to rotate, so AUTO leaves the
      loop off.

    Priority of the credentials themselves (a key sourced from refresh-aws.sh /
    the environment outranks .env) is unaffected — this only gates the rotation
    loop, never which key wins.
    """
    flag = getattr(settings, "aws_token_refresh_enabled", None)
    if flag is True:
        return True
    if flag is False:
        return False
    # AUTO (None): require a bedrock provider AND a rotating session token.
    uses_bedrock = (
        getattr(settings, "core_llm_provider", "") == "bedrock"
        or getattr(settings, "agent_llm_provider", "") == "bedrock"
    )
    has_session_token = bool((getattr(settings, "aws_session_token", "") or "").strip())
    return uses_bedrock and has_session_token


# Path to the capitol refresh script and its output
_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "open-source" / "capitol-ai-grafana"
_REFRESH_SCRIPT = _SCRIPT_DIR / "scripts" / "capitol_creds_refresh.js"
_CREDS_FILE = _SCRIPT_DIR / "output" / "capitol_creds_refresh.json"


class AWSCredentialRefresher:
    """Background service to refresh AWS session tokens via Grafana IMDS bridge."""

    def __init__(self):
        self._settings = get_settings()
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_refresh_at: float = 0.0
        # Epoch seconds when the current token expires (0.0 = unknown). Drives the
        # expiration-aware loop: refresh before this, not just on the 4h cadence.
        self._token_expires_at: float = 0.0
        self._refresh_lock = asyncio.Lock()

    @staticmethod
    def _parse_expiration_epoch(expiration: str) -> Optional[float]:
        """Parse an ISO8601 expiration into epoch seconds, or None if unparseable."""
        try:
            exp = datetime.fromisoformat(expiration.replace("Z", "+00:00"))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            return exp.timestamp()
        except (ValueError, AttributeError):
            return None

    @classmethod
    def _is_expiration_valid(cls, expiration: str) -> bool:
        """Return True if the ISO8601 expiration is still ahead of now (60s buffer).

        Returns True on unparseable input — never block a refresh due to an
        unexpected timestamp format; only reject when we can prove it's expired.
        """
        epoch = cls._parse_expiration_epoch(expiration)
        if epoch is None:
            return True
        return epoch - datetime.now(timezone.utc).timestamp() > 60

    def _apply_credentials(self, data: dict) -> bool:
        """Apply credentials from parsed JSON to settings and env vars."""
        access_key = data.get("accessKeyId")
        secret_key = data.get("secretAccessKey")
        session_token = data.get("sessionToken")

        if not all([access_key, secret_key, session_token]):
            logger.error("aws_refresh_invalid_payload", keys=list(data.keys()))
            return False

        # Reject expired tokens. Without this, Strategy 2 (cached-file read) would
        # happily "apply" a stale token and log aws_refresh_success, masking the
        # real failure and causing endless bedrock auth-error/retry loops.
        expiration = data.get("expiration")
        if expiration and not self._is_expiration_valid(expiration):
            logger.warning("aws_refresh_rejected_expired", expiration=expiration)
            return False

        self._settings.aws_access_key_id = access_key
        self._settings.aws_secret_access_key = secret_key
        self._settings.aws_session_token = session_token

        os.environ["AWS_ACCESS_KEY_ID"] = access_key
        os.environ["AWS_SECRET_ACCESS_KEY"] = secret_key
        os.environ["AWS_SESSION_TOKEN"] = session_token

        # CC-6: refresh the value-based redaction set so the new STS credentials
        # are scrubbed from durable logs (they rotate; the old literals are stale).
        try:
            from app.core.secret_redaction import refresh_known_secrets
            refresh_known_secrets()
        except Exception:
            pass

        if self._settings.core_llm_provider == "bedrock":
            self._settings.core_llm_api_key = "bedrock"

        try:
            from app.services import llm_client
            if hasattr(llm_client, "_api_key_cache"):
                llm_client._api_key_cache.clear()
        except ImportError:
            pass

        # Reset circuit breaker for bedrock so it can accept calls immediately
        try:
            from app.core.circuit_breaker import get_circuit_breaker
            breaker = get_circuit_breaker()
            breaker.reset("bedrock")
        except Exception:
            pass

        self._last_refresh_at = time.monotonic()
        # Record monotonic deadline for the expiration-aware loop. Convert the
        # absolute expiry epoch into a monotonic offset so it survives clock drift.
        exp_epoch = self._parse_expiration_epoch(expiration) if expiration else None
        if exp_epoch is not None:
            seconds_left = exp_epoch - datetime.now(timezone.utc).timestamp()
            self._token_expires_at = time.monotonic() + max(0.0, seconds_left)
        else:
            self._token_expires_at = 0.0

        logger.info(
            "aws_refresh_success",
            expiration=data.get("expiration"),
            account=data.get("account"),
            role=data.get("role"),
            bedrock_ok=data.get("bedrockOpus46"),
        )
        return True

    async def refresh_now(self) -> bool:
        """Run the capitol refresh script and apply new credentials."""
        # Strategy 1: Run node script to get fresh credentials
        if _REFRESH_SCRIPT.exists():
            logger.info("aws_refresh_via_script", script=str(_REFRESH_SCRIPT))
            try:
                env = os.environ.copy()
                env["CAPITOL_GRAFANA_HOST"] = self._settings.aws_token_refresh_url.replace("https://", "").replace(":443", "").rstrip("/")
                if self._settings.aws_token_refresh_auth_user:
                    env["CAPITOL_GRAFANA_USER"] = self._settings.aws_token_refresh_auth_user
                if self._settings.aws_token_refresh_auth_pass:
                    env["CAPITOL_GRAFANA_PASS"] = self._settings.aws_token_refresh_auth_pass

                proc = await asyncio.create_subprocess_exec(
                    "node", str(_REFRESH_SCRIPT),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(_SCRIPT_DIR),
                    env=env,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

                if proc.returncode == 0:
                    data = json.loads(_CREDS_FILE.read_text(encoding="utf-8"))
                    return self._apply_credentials(data)
                else:
                    logger.warning(
                        "aws_refresh_script_failed",
                        returncode=proc.returncode,
                        stderr=stderr.decode(errors="replace")[:500],
                    )
            except asyncio.TimeoutError:
                logger.error("aws_refresh_script_timeout")
            except Exception as e:
                logger.error("aws_refresh_script_error", error=str(e)[:300])

        # Strategy 2: Read existing output file (may still be valid)
        if _CREDS_FILE.exists():
            try:
                data = json.loads(_CREDS_FILE.read_text(encoding="utf-8"))
                age_info = data.get("fetchedAt", "unknown")
                logger.info("aws_refresh_from_cached_file", fetched_at=age_info)
                return self._apply_credentials(data)
            except Exception as e:
                logger.error("aws_refresh_file_read_failed", error=str(e))

        logger.error(
            "aws_refresh_all_strategies_failed",
            script_exists=_REFRESH_SCRIPT.exists(),
            creds_file_exists=_CREDS_FILE.exists(),
            creds_file_path=str(_CREDS_FILE),
        )
        return False

    async def refresh_on_auth_failure(self) -> bool:
        """Trigger immediate refresh when an auth error is detected.

        Debounced: skips if last successful refresh was < 30s ago (avoids
        hammering the refresh endpoint on repeated fast failures).
        """
        if time.monotonic() - self._last_refresh_at < 30:
            logger.debug("aws_refresh_on_auth_skipped_recent")
            return True

        async with self._refresh_lock:
            # Double-check after acquiring lock
            if time.monotonic() - self._last_refresh_at < 30:
                return True
            logger.info("aws_refresh_triggered_by_auth_failure")
            return await self.refresh_now()

    # Buffer before token expiry to trigger a proactive refresh (5 min).
    _EXPIRY_REFRESH_BUFFER = 300.0
    # Fast retry when a refresh fails, so a transient error doesn't strand the
    # token for the full interval.
    _FAILED_RETRY_INTERVAL = 60.0

    def _next_sleep_seconds(self, last_ok: bool) -> float:
        """Compute how long to sleep before the next refresh attempt.

        Refreshes whenever the token is about to expire OR the 4h interval
        elapses — whichever comes first. On failure, retries quickly instead of
        waiting the full interval.
        """
        interval = float(self._settings.aws_token_refresh_interval)
        if not last_ok:
            return self._FAILED_RETRY_INTERVAL

        sleep = interval
        if self._token_expires_at > 0.0:
            until_expiry = self._token_expires_at - time.monotonic() - self._EXPIRY_REFRESH_BUFFER
            sleep = min(sleep, until_expiry)
        # Never busy-loop; clamp to a sane floor.
        return max(self._FAILED_RETRY_INTERVAL, sleep)

    async def _loop(self):
        """Refresh loop: proactively refreshes before expiry or every 4h, with
        fast retry on failure."""
        # Initial refresh
        last_ok = await self.refresh_now()

        while self._running:
            await asyncio.sleep(self._next_sleep_seconds(last_ok))
            try:
                last_ok = await self.refresh_now()
            except Exception as e:
                last_ok = False
                logger.warning("aws_refresh_loop_error", error=str(e))

    def start(self):
        """Start the background refresh task."""
        if self._task is not None:
            return

        self._running = True
        self._task = asyncio.create_task(self._loop(), name="aws_credential_refresher")
        logger.info(
            "aws_refresher_started",
            interval=self._settings.aws_token_refresh_interval,
            script_exists=_REFRESH_SCRIPT.exists(),
            creds_file_exists=_CREDS_FILE.exists(),
        )

    def stop(self):
        """Stop the background refresh task."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("aws_refresher_stopped")


# Singleton
_refresher: Optional[AWSCredentialRefresher] = None


def get_aws_refresher() -> AWSCredentialRefresher:
    global _refresher
    if _refresher is None:
        _refresher = AWSCredentialRefresher()
    return _refresher
