"""
MekongAI Nexus - Doctor / Health Check System (OpenClaw-inspired).

Self-diagnosis for the entire system: config, DB, LLM, tools, sandbox, security.

Inspired by: OpenClaw `openclaw doctor` — config validation, security audit
"""

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional

import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)


@dataclass
class DiagnosticResult:
    check: str
    status: Literal["ok", "warning", "error"]
    message: str
    fix_hint: str = ""
    duration_ms: float = 0.0


class DoctorService:
    """
    System health check — inspired by OpenClaw doctor.

    Validates config, connectivity, security posture.
    """

    def __init__(self) -> None:
        self.settings = get_settings()

    async def run_all(self) -> list[DiagnosticResult]:
        results: list[DiagnosticResult] = []
        checks = [
            self.check_config,
            self.check_env_secrets,
            self.check_llm_providers,
            self.check_database,
            self.check_redis,
            self.check_workspace,
            self.check_sandbox,
            self.check_plugins,
            self.check_skills,
            self.check_security,
        ]
        for check_fn in checks:
            try:
                results.extend(await check_fn())
            except Exception as e:
                results.append(DiagnosticResult(
                    check=check_fn.__name__,
                    status="error",
                    message=f"Check failed with exception: {e}",
                ))
        return results

    async def check_config(self) -> list[DiagnosticResult]:
        results: list[DiagnosticResult] = []
        s = self.settings

        # App environment
        if s.app_env == "production" and s.app_debug:
            results.append(DiagnosticResult(
                check="config.debug_in_prod",
                status="warning",
                message="Debug mode enabled in production",
                fix_hint="Set APP_DEBUG=false in .env",
            ))
        else:
            results.append(DiagnosticResult(
                check="config.environment",
                status="ok",
                message=f"Environment: {s.app_env}, debug={s.app_debug}",
            ))

        # Secret keys
        if s.app_secret_key == "change-me-in-production":
            results.append(DiagnosticResult(
                check="config.secret_key",
                status="warning" if s.app_env != "production" else "error",
                message="Using default secret key",
                fix_hint="Set a random APP_SECRET_KEY in .env",
            ))

        return results

    async def check_env_secrets(self) -> list[DiagnosticResult]:
        results: list[DiagnosticResult] = []
        s = self.settings

        # Core LLM
        if s.core_llm_provider == "bedrock":
            if s.aws_access_key_id and s.aws_secret_access_key:
                results.append(DiagnosticResult(
                    check="secrets.aws", status="ok",
                    message="AWS credentials configured",
                ))
            else:
                results.append(DiagnosticResult(
                    check="secrets.aws", status="error",
                    message="AWS credentials missing for Bedrock provider",
                    fix_hint="Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY",
                ))
        elif s.core_llm_provider in ("openai", "openrouter"):
            if s.core_llm_api_key or s.openai_api_key or s.openrouter_api_key:
                results.append(DiagnosticResult(
                    check="secrets.llm_key", status="ok",
                    message=f"{s.core_llm_provider} API key configured",
                ))
            else:
                results.append(DiagnosticResult(
                    check="secrets.llm_key", status="error",
                    message=f"API key missing for {s.core_llm_provider}",
                    fix_hint="Set OPENAI_API_KEY (or CORE_LLM_API_KEY) / OPENROUTER_API_KEY as appropriate",
                ))

        return results

    async def check_llm_providers(self) -> list[DiagnosticResult]:
        """Check if LLM providers are reachable with a real API call."""
        results: list[DiagnosticResult] = []
        s = self.settings

        # Core LLM connectivity test
        t0 = time.time()
        try:
            from app.services.llm_client import get_core_llm
            llm = get_core_llm()
            resp = await llm.chat(
                [{"role": "user", "content": "Say 'ok' in one word."}],
                max_tokens=5, temperature=0,
            )
            dt = (time.time() - t0) * 1000
            results.append(DiagnosticResult(
                check="llm.core_connectivity",
                status="ok",
                message=f"Core LLM ({s.core_llm_provider}/{s.core_llm_model}) responded in {dt:.0f}ms",
                duration_ms=dt,
            ))
        except Exception as e:
            dt = (time.time() - t0) * 1000
            results.append(DiagnosticResult(
                check="llm.core_connectivity",
                status="error",
                message=f"Core LLM unreachable: {str(e)[:200]}",
                fix_hint=f"Check {s.core_llm_provider} API key and connectivity",
                duration_ms=dt,
            ))

        # Agent LLM info
        results.append(DiagnosticResult(
            check="llm.agent_provider",
            status="ok",
            message=f"Agent: {s.agent_llm_provider}/{s.agent_llm_model}",
        ))

        if s.local_llm_enabled:
            results.append(DiagnosticResult(
                check="llm.local",
                status="ok",
                message=f"Local LLM: {s.local_llm_base_url} ({s.local_llm_model})",
            ))

        return results

    async def check_database(self) -> list[DiagnosticResult]:
        """Check PostgreSQL database connectivity."""
        results: list[DiagnosticResult] = []
        t0 = time.time()
        try:
            from sqlalchemy import text
            from app.core.dependencies import _engine as async_engine
            if not async_engine:
                results.append(DiagnosticResult(
                    check="db.connectivity", status="error",
                    message="Database engine not initialized",
                    fix_hint="App may still be starting up",
                    duration_ms=(time.time() - t0) * 1000,
                ))
                return results
            async with async_engine.connect() as conn:
                row = await conn.execute(text("SELECT 1"))
                row.fetchone()
            dt = (time.time() - t0) * 1000
            results.append(DiagnosticResult(
                check="db.connectivity", status="ok",
                message=f"PostgreSQL connected in {dt:.0f}ms",
                duration_ms=dt,
            ))
        except Exception as e:
            dt = (time.time() - t0) * 1000
            results.append(DiagnosticResult(
                check="db.connectivity", status="error",
                message=f"Database unreachable: {str(e)[:200]}",
                fix_hint="Check DATABASE_URL in .env and ensure PostgreSQL is running",
                duration_ms=dt,
            ))
        return results

    async def check_redis(self) -> list[DiagnosticResult]:
        """Check Redis connectivity."""
        results: list[DiagnosticResult] = []
        t0 = time.time()
        try:
            import redis.asyncio as aioredis
            s = self.settings
            r = aioredis.from_url(s.redis_url, decode_responses=True)
            pong = await r.ping()
            await r.aclose()
            dt = (time.time() - t0) * 1000
            results.append(DiagnosticResult(
                check="redis.connectivity", status="ok",
                message=f"Redis connected in {dt:.0f}ms (ping={pong})",
                duration_ms=dt,
            ))
        except ImportError:
            results.append(DiagnosticResult(
                check="redis.connectivity", status="warning",
                message="redis package not installed — inter-agent communication disabled",
                fix_hint="pip install redis",
            ))
        except Exception as e:
            dt = (time.time() - t0) * 1000
            results.append(DiagnosticResult(
                check="redis.connectivity", status="error",
                message=f"Redis unreachable: {str(e)[:200]}",
                fix_hint="Check REDIS_URL in .env and ensure Redis is running",
                duration_ms=dt,
            ))
        return results

    async def check_workspace(self) -> list[DiagnosticResult]:
        results: list[DiagnosticResult] = []
        ws = Path("workspace_data")

        if ws.exists():
            results.append(DiagnosticResult(
                check="workspace.exists", status="ok",
                message=f"Workspace: {ws.resolve()}",
            ))
        else:
            results.append(DiagnosticResult(
                check="workspace.exists", status="warning",
                message="workspace_data directory missing",
                fix_hint="Will be auto-created on first use",
            ))

        # Check writable
        try:
            test_file = ws / ".doctor_test"
            test_file.write_text("ok")
            test_file.unlink()
            results.append(DiagnosticResult(
                check="workspace.writable", status="ok",
                message="Workspace is writable",
            ))
        except Exception as e:
            results.append(DiagnosticResult(
                check="workspace.writable", status="error",
                message=f"Workspace not writable: {e}",
            ))

        return results

    async def check_sandbox(self) -> list[DiagnosticResult]:
        results: list[DiagnosticResult] = []
        s = self.settings

        if s.ironclaw_enabled:
            if Path(s.ironclaw_binary_path).exists():
                results.append(DiagnosticResult(
                    check="sandbox.ironclaw", status="ok",
                    message="IronClaw binary found",
                ))
            else:
                results.append(DiagnosticResult(
                    check="sandbox.ironclaw", status="warning",
                    message=f"IronClaw binary not found: {s.ironclaw_binary_path}",
                    fix_hint="Build IronClaw or set IRONCLAW_ENABLED=false",
                ))

        if s.docker_sandbox_enabled:
            results.append(DiagnosticResult(
                check="sandbox.docker", status="ok",
                message=f"Docker sandbox: image={s.docker_sandbox_image}",
            ))

        if not s.ironclaw_enabled and not s.docker_sandbox_enabled:
            results.append(DiagnosticResult(
                check="sandbox.none", status="warning",
                message="No sandbox enabled — code runs as subprocess",
                fix_hint="Enable DOCKER_SANDBOX_ENABLED=true for safer execution",
            ))

        return results

    async def check_plugins(self) -> list[DiagnosticResult]:
        results: list[DiagnosticResult] = []
        plugin_dirs = [Path("plugins"), Path("workspace_data/plugins")]
        total = 0
        for d in plugin_dirs:
            if d.exists():
                count = sum(1 for c in d.iterdir() if c.is_dir() and (c / "plugin.json").exists())
                total += count
        results.append(DiagnosticResult(
            check="plugins.count", status="ok",
            message=f"{total} plugin(s) discovered",
        ))
        return results

    async def check_skills(self) -> list[DiagnosticResult]:
        results: list[DiagnosticResult] = []
        skills_dir = Path("skills")
        if skills_dir.exists():
            count = sum(1 for c in skills_dir.iterdir()
                        if c.is_dir() and (c / "SKILL.md").exists())
            results.append(DiagnosticResult(
                check="skills.count", status="ok",
                message=f"{count} skill(s) discovered",
            ))
        else:
            results.append(DiagnosticResult(
                check="skills.dir", status="warning",
                message="skills/ directory not found",
            ))
        return results

    async def check_security(self) -> list[DiagnosticResult]:
        results: list[DiagnosticResult] = []
        s = self.settings

        if s.jwt_secret_key == "change-me-jwt-secret":
            results.append(DiagnosticResult(
                check="security.jwt", status="warning" if not s.is_production else "error",
                message="Using default JWT secret",
                fix_hint="Set JWT_SECRET_KEY to a random value",
            ))
        else:
            results.append(DiagnosticResult(
                check="security.jwt", status="ok",
                message="JWT secret configured",
            ))

        # .env file permissions (Unix only)
        env_file = Path(".env")
        if env_file.exists() and os.name != "nt":
            mode = oct(env_file.stat().st_mode)[-3:]
            if mode not in ("600", "640"):
                results.append(DiagnosticResult(
                    check="security.env_perms", status="warning",
                    message=f".env file permissions: {mode}",
                    fix_hint="chmod 600 .env",
                ))

        return results
