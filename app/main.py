"""
Nexus - FastAPI Application Entry Point.

Khởi tạo ứng dụng, router, middleware và lifecycle events.
"""


# Load .env into os.environ FIRST - many tools read env vars directly
from dotenv import load_dotenv
load_dotenv(override=False)  # Don't override already-set env vars

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

# Suppress noisy LiteLLM debug logs before any litellm import
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("LiteLLM Proxy").setLevel(logging.WARNING)
logging.getLogger("litellm").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Suppress noisy HuggingFace/filelock debug logs (model download/cache lock spam)
logging.getLogger("filelock").setLevel(logging.WARNING)
logging.getLogger("transformers").setLevel(logging.WARNING)
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)

# Suppress noisy ddgs/primp/Rust library debug logs (search engine internals)
logging.getLogger("ddgs").setLevel(logging.WARNING)
logging.getLogger("ddgs.ddgs").setLevel(logging.WARNING)
logging.getLogger("primp").setLevel(logging.WARNING)
logging.getLogger("cookie_store").setLevel(logging.WARNING)
logging.getLogger("h2").setLevel(logging.WARNING)
logging.getLogger("hyper_util").setLevel(logging.WARNING)
logging.getLogger("rustls").setLevel(logging.WARNING)
logging.getLogger("reqwest").setLevel(logging.WARNING)

# Force litellm verbose off at module level (before litellm is imported by tools)
try:
    import litellm
    litellm.set_verbose = False
    litellm.suppress_debug_info = True
except ImportError:
    pass

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __app_name__, __version__
from app.config import get_settings
from app.core.logging import setup_logging
from app.core.dependencies import get_redis, close_redis, init_db_engine, close_db_engine
from app.api.v1.router import api_router
from app.api.v1.endpoints.openai_compat import router as openai_compat_router
from app.api.ws import ws_router
from app.channels.manager import get_channel_manager

# Import tool modules to trigger registry registration at startup
import app.mcp.tools  # noqa: F401

# Register Hermes Agent tools (if enabled)
try:
    from app.hermes.tools import register_hermes_tools
    register_hermes_tools()
except Exception as _hermes_import_err:
    structlog.get_logger("hermes").warning(
        "hermes_tools_import_failed", error=str(_hermes_import_err)
    )

logger = structlog.get_logger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle manager."""
    setup_logging(debug=settings.app_debug)

    # --- SEC-03: Validate secret keys at startup ---
    _DEFAULT_SECRETS = {"change-me-in-production", "change-me-jwt-secret"}
    if settings.is_production:
        if settings.app_secret_key in _DEFAULT_SECRETS:
            logger.critical("INSECURE: app_secret_key is still the default value! Set APP_SECRET_KEY in .env")
            raise RuntimeError("Cannot start in production with default APP_SECRET_KEY")
        if settings.jwt_secret_key in _DEFAULT_SECRETS:
            logger.critical("INSECURE: jwt_secret_key is still the default value! Set JWT_SECRET_KEY in .env")
            raise RuntimeError("Cannot start in production with default JWT_SECRET_KEY")
    else:
        if settings.app_secret_key in _DEFAULT_SECRETS or settings.jwt_secret_key in _DEFAULT_SECRETS:
            logger.warning("⚠️ Using default secret keys — change before deploying to production!")

    # --- SEC-01: Terminal API key. Terminal endpoints run arbitrary commands, so
    # in production an unset key (which makes them unauthenticated) is fatal —
    # same fail-fast policy as the secret keys above. Dev still just warns.
    if not settings.terminal_api_key:
        if settings.is_production:
            logger.critical("INSECURE: TERMINAL_API_KEY unset — terminal endpoints open!")
            raise RuntimeError(
                "Cannot start in production without TERMINAL_API_KEY "
                "(terminal endpoints run shell commands)"
            )
        logger.warning("⚠️ TERMINAL_API_KEY not set — terminal endpoints unprotected (dev only)!")

    logger.info(
        "starting_nexus",
        app_name=settings.app_name,
        version=__version__,
        env=settings.app_env,
    )

    # Initialize database
    await init_db_engine()
    logger.info("database_connected")

    # Cleanup orphaned tasks stuck in executing state from prior crash/restart
    try:
        from app.core.dependencies import _session_factory
        from app.models.database import TaskModel
        from sqlalchemy import update
        if _session_factory:
            async with _session_factory() as _cleanup_db:
                if settings.enable_task_resume:
                    # Mark as "suspended" so auto-resume can pick them up
                    result = await _cleanup_db.execute(
                        update(TaskModel)
                        .where(TaskModel.status.in_(["executing", "planning", "decomposing"]))
                        .values(status="suspended", error="Server restarted — awaiting resume")
                    )
                else:
                    result = await _cleanup_db.execute(
                        update(TaskModel)
                        .where(TaskModel.status.in_(["executing", "planning", "decomposing"]))
                        .values(status="failed", error="Server restarted while task was running")
                    )
                if result.rowcount > 0:
                    await _cleanup_db.commit()
                    logger.info("orphaned_tasks_cleaned", count=result.rowcount,
                                resume_enabled=settings.enable_task_resume)
    except Exception as _cleanup_err:
        logger.warning("orphaned_task_cleanup_failed", error=str(_cleanup_err))

    # Initialize Redis and Message Bus
    redis = await get_redis()
    logger.info("redis_connected")
    
    from app.communication.message_bus import get_message_bus
    bus = await get_message_bus()
    await bus.start_listening()
    logger.info("message_bus_listening_started")

    # Subscribe this worker to the cross-process cancel channel so a cancel
    # request served by another worker still stops a task running here.
    try:
        from app.core.cancel_subscriber import register_cancel_subscriber
        await register_cancel_subscriber(bus)
    except Exception as _cancel_sub_err:
        logger.warning("cancel_subscriber_failed", error=str(_cancel_sub_err))

    # Initialize Task Queue (bounded orchestrator concurrency)
    from app.core.task_queue import get_orchestrator_queue

    _queue_concurrent = settings.effective_queue_concurrent
    _queue_size = settings.effective_queue_size
    queue = get_orchestrator_queue(
        max_concurrent=_queue_concurrent,
        max_queue_size=_queue_size,
    )
    queue.start_workers(num_workers=_queue_concurrent)
    logger.info(
        "task_queue_started",
        max_concurrent=_queue_concurrent,
        max_queue_size=_queue_size,
        auto_scale=settings.auto_scale,
    )

    # Seed default agents — SKIPPED in lifespan (done by entrypoint.sh pre-Uvicorn).
    # spawn_from_template() is too expensive for multi-worker lifespan (PromptBuilder).
    # For non-Docker dev, run: python -m app.cli_seed
    logger.debug("default_agent_seeding_handled_by_entrypoint")

    # Initialize messaging channels
    await _setup_channels()

    # Initialize Memory Layers system (after DB is ready)
    try:
        from app.memory.layers.startup import setup_memory_layers
        from app.services.llm_client import get_core_llm
        from app.core.dependencies import _session_factory as _ml_session_factory
        if _ml_session_factory:
            _ml_bridge = await setup_memory_layers(
                llm_client=get_core_llm(),
                session_factory=_ml_session_factory,
            )
            if _ml_bridge:
                logger.info("memory_layers_initialized")
        else:
            logger.warning("memory_layers_skipped", reason="session_factory not ready")
    except Exception as e:
        logger.warning("memory_layers_init_failed", error=str(e))

    # Log Hermes Agent status (in-process, no subprocess needed)
    try:
        from app.hermes.config import get_hermes_settings
        hs = get_hermes_settings()
        if hs.enabled:
            logger.info("hermes_in_process_ready", hermes_path=hs.hermes_abs_path)
        else:
            logger.info("hermes_disabled_in_config")
    except Exception as e:
        logger.warning("hermes_config_check_failed", error=str(e))

    # Start system metrics emitter (dashboard real-time data)
    from app.core.system_metrics import get_metrics_emitter
    metrics_emitter = get_metrics_emitter()
    await metrics_emitter.start(interval=10)
    logger.info("system_metrics_emitter_started")

    # ── Leader election: Only one worker runs heavy background tasks ──
    # In multi-worker Uvicorn, each worker runs lifespan independently.
    # Use Redis SET NX to elect a leader for singleton operations.
    is_leader = False
    try:
        redis = await get_redis()
        if redis:
            is_leader = await redis.set(
                "nexus:worker_leader", "1", ex=600, nx=True
            )
            if is_leader:
                logger.info("worker_leader_elected")
            else:
                logger.debug("worker_follower_mode")
        else:
            is_leader = True  # No Redis → single-worker assumption
    except Exception as e:
        logger.warning("leader_election_failed", error=str(e))
        is_leader = True  # Fallback: act as leader

    # Auto-resume: Detect and resume tasks interrupted by crash/OOM (leader only)
    if is_leader and settings.enable_task_auto_resume:
        try:
            from app.core.dependencies import _session_factory
            if _session_factory:
                from app.core.checkpoint_persistence import CheckpointPersistence
                from app.core.task_runner import get_task_runner
                _checkpoint_svc = CheckpointPersistence(_session_factory)
                _resumable = await _checkpoint_svc.get_resumable_tasks()
                if _resumable:
                    logger.info(
                        "auto_resuming_tasks",
                        count=len(_resumable),
                        tasks=[
                            {"task_id": t["task_id"][:8], "turn": t["turn_number"]}
                            for t in _resumable[:5]
                        ],
                    )
                    _runner = get_task_runner()
                    for _task_info in _resumable[:3]:
                        try:
                            await _runner.resume_task(
                                session_id=_task_info.get("session_id", _task_info["task_id"]),
                                user_id=_task_info.get("user_id", "default_user"),
                                task_id=_task_info["task_id"],
                                checkpoint={
                                    "turn_number": _task_info["turn_number"],
                                    "state_json": _task_info["state_json"],
                                },
                            )
                            logger.info(
                                "task_auto_resumed",
                                task_id=_task_info["task_id"][:8],
                                from_turn=_task_info["turn_number"],
                            )
                        except Exception as _resume_one_err:
                            logger.warning(
                                "task_auto_resume_failed",
                                task_id=_task_info["task_id"][:8],
                                error=str(_resume_one_err),
                            )
                else:
                    logger.debug("no_resumable_tasks_found")
        except Exception as _resume_err:
            logger.warning("auto_resume_check_failed", error=str(_resume_err))

    # Pre-load embedding model (Qwen3-Embedding) — only leader worker loads model
    # to avoid OOM when multiple workers each load ~1.5GB into RAM.
    # Runs as background task to avoid blocking server startup (model download can be slow).
    if is_leader:
        async def _load_embedding_background():
            """Load embedding model in background - doesn't block startup."""
            try:
                from app.xskill.embedding import init_embedding_model
                ok = await init_embedding_model()
                if ok:
                    logger.info("embedding_model_ready")
                else:
                    logger.warning("embedding_model_not_available", fallback="keyword_search")
            except Exception as e:
                logger.warning("embedding_init_failed_background", error=str(e))

        asyncio.create_task(_load_embedding_background())
        logger.info("embedding_model_loading_background")
    else:
        logger.info("embedding_model_skipped_follower_worker")

    cron_scheduler = None
    if is_leader:
        try:
            from app.cron.scheduler import CronScheduler
            cron_scheduler = CronScheduler()
            started = await cron_scheduler.start()
            if started:
                logger.info("cron_scheduler_auto_started")
            else:
                # Start background loop fallback (no APScheduler)
                await cron_scheduler.start_background_loop(interval=60.0)
                logger.info("cron_scheduler_background_loop_started")
        except Exception as e:
            logger.warning("cron_scheduler_auto_start_failed", error=str(e))

    # Start Bounty PR Monitor (leader-only)
    if is_leader:
        try:
            from app.cron.bounty_pr_monitor import get_bounty_pr_monitor
            bounty_monitor = get_bounty_pr_monitor()
            await bounty_monitor.start()
            logger.info("bounty_pr_monitor_started")
        except Exception as e:
            logger.warning("bounty_pr_monitor_start_failed", error=str(e))

    # Start AWS Credential Refresher (leader-only). Gated by should_run_aws_refresh:
    # explicit aws_token_refresh_enabled flag, else AUTO (bedrock + STS session token).
    if is_leader:
        try:
            from app.core.aws_refresher import get_aws_refresher, should_run_aws_refresh
            if should_run_aws_refresh(settings):
                refresher = get_aws_refresher()
                refresher.start()
                logger.info("aws_credential_refresher_started")
            else:
                logger.info(
                    "aws_credential_refresher_skipped",
                    enabled=settings.aws_token_refresh_enabled,
                    core_provider=settings.core_llm_provider,
                    agent_provider=settings.agent_llm_provider,
                )
        except Exception as e:
            logger.warning("aws_credential_refresher_init_failed", error=str(e))

    try:
        from app.core.resource_monitor import get_resource_monitor
        resource_monitor = get_resource_monitor()
        await resource_monitor.start()
        logger.info("resource_monitor_started")
    except Exception as e:
        logger.warning("resource_monitor_start_failed", error=str(e))

    task_watchdog = None
    if is_leader:
        try:
            from app.core.task_watchdog import get_task_watchdog
            task_watchdog = get_task_watchdog()
            await task_watchdog.start()
            logger.info("task_watchdog_started")
        except Exception as e:
            logger.warning("task_watchdog_start_failed", error=str(e))

    dreaming_task = None
    if is_leader:
        try:
            from app.memory.dreaming import DreamingEngine

            async def _dreaming_periodic():
                """Run dreaming cycle every 6 hours (background)."""
                import asyncio as _aio
                while True:
                    await _aio.sleep(6 * 3600)  # Every 6 hours
                    try:
                        from app.core.dependencies import _session_factory
                        if _session_factory:
                            async with _session_factory() as dream_db:
                                engine = DreamingEngine(dream_db)
                                result = await engine.run_cycle()
                                logger.info("dreaming_cycle_completed", result=str(result)[:200])
                    except Exception as dream_err:
                        logger.warning("dreaming_cycle_failed", error=str(dream_err))

            dreaming_task = asyncio.create_task(_dreaming_periodic())
            logger.info("dreaming_engine_periodic_scheduled", interval_hours=6)
        except Exception as e:
            logger.warning("dreaming_engine_setup_failed", error=str(e))

    # ── OpenClaw Bridge + Security (Phase 15) ──────────────────────
    # Start OpenClaw bridge (connect to Gateway) — with timeout to avoid blocking startup
    openclaw_bridge = None
    try:
        from app.openclaw.bridge import get_openclaw_bridge
        from app.openclaw.config import get_openclaw_config
        oc_config = get_openclaw_config()
        if oc_config.gateway_url and oc_config.gateway_token:
            openclaw_bridge = get_openclaw_bridge()
            try:
                await asyncio.wait_for(openclaw_bridge.start(), timeout=10.0)
                logger.info("openclaw_bridge_started", url=oc_config.gateway_url,
                            healthy=openclaw_bridge.is_healthy)
            except asyncio.TimeoutError:
                logger.warning("openclaw_bridge_start_timeout",
                               url=oc_config.gateway_url,
                               msg="Gateway unreachable, bridge will retry in background")
        else:
            logger.info("openclaw_bridge_skipped", reason="no gateway_url or token configured")
    except Exception as e:
        logger.warning("openclaw_bridge_start_failed", error=str(e))

    # ── MCP servers auto-connect (audit #1: external tools) ─────────
    # The MCP client was fully built but never wired at startup, so configured
    # servers (MCP_SERVERS) sat unused. Connect them now so their tools join the
    # agent toolset. Per-server failures are isolated inside connect_all; the
    # whole block is timeout-guarded so an unreachable server can't block startup.
    try:
        from app.mcp.client import get_mcp_client_manager, parse_mcp_server_configs
        _mcp_configs = parse_mcp_server_configs(settings.mcp_servers)
        if _mcp_configs:
            _mcp_manager = get_mcp_client_manager()
            _mcp_results = await asyncio.wait_for(
                _mcp_manager.connect_all(_mcp_configs), timeout=20.0
            )
            # Bridge connected MCP tools into the registry so agents can use them.
            from app.mcp.external_bridge import register_mcp_tools
            from app.mcp.tool_registry import registry as _tool_registry
            _bridged = register_mcp_tools(_mcp_manager, _tool_registry)
            logger.info(
                "mcp_servers_auto_connected",
                connected=sum(1 for ok in _mcp_results.values() if ok),
                total=len(_mcp_configs),
                tools_bridged=_bridged,
            )
        else:
            logger.info("mcp_servers_skipped", reason="MCP_SERVERS not configured")
    except asyncio.TimeoutError:
        logger.warning("mcp_servers_connect_timeout", msg="continuing without external MCP tools")
    except Exception as e:
        logger.warning("mcp_servers_connect_failed", error=str(e))

    # Start OpenClaw subprocess if enabled (leader-only)
    openclaw_lifecycle = None
    if is_leader:
        try:
            from app.openclaw.lifecycle import get_openclaw_lifecycle
            lifecycle = get_openclaw_lifecycle()
            if lifecycle._config.subprocess_enabled:
                started = await lifecycle.start()
                if started:
                    openclaw_lifecycle = lifecycle
                    logger.info("openclaw_subprocess_started")
        except Exception as e:
            logger.warning("openclaw_subprocess_failed", error=str(e))

    # Configure SSRF Guard from settings
    try:
        from app.security.ssrf_guard import configure_ssrf_guard, SSRFConfig
        configure_ssrf_guard(SSRFConfig(
            allow_localhost=settings.ssrf_allow_localhost,
            allow_private=settings.ssrf_allow_private,
        ))
        logger.info("ssrf_guard_configured",
                    allow_localhost=settings.ssrf_allow_localhost,
                    allow_private=settings.ssrf_allow_private)
    except Exception as e:
        logger.warning("ssrf_guard_config_failed", error=str(e))

    # Run security audit on startup if configured — with timeout
    if settings.security_audit_on_startup:
        try:
            from app.security.audit import get_security_audit_runner
            audit_runner = get_security_audit_runner()
            report = await asyncio.wait_for(audit_runner.run_audit(deep=False), timeout=10.0)
            logger.info("startup_security_audit",
                        passed=report.passed,
                        findings=report.total_findings,
                        critical=report.critical_count)
        except asyncio.TimeoutError:
            logger.warning("startup_audit_timeout", msg="Security audit skipped (took >10s)")
        except Exception as e:
            logger.warning("startup_audit_failed", error=str(e))

    # Register approval notification callback (send to OpenClaw channels)
    try:
        from app.core.approval import get_approval_manager

        async def _notify_approval_via_openclaw(request):
            """Send approval request to user's channel via OpenClaw."""
            if openclaw_bridge and openclaw_bridge.is_healthy:
                await openclaw_bridge.send_chat(
                    session_id=request.session_id,
                    messages=[{"role": "assistant", "content": request.format_for_channel()}],
                )

        approval_mgr = get_approval_manager()
        approval_mgr.register_notify_callback(_notify_approval_via_openclaw)
        logger.debug("approval_notifications_wired_to_openclaw")
    except Exception as e:
        logger.warning("approval_notify_wire_failed", error=str(e))

    # Plugin auto-discovery
    try:
        from app.plugins.registry import get_plugin_registry
        plugin_registry = get_plugin_registry()
        loaded_plugin_ids = await plugin_registry.load_all()
        logger.info("plugins_discovered", count=len(loaded_plugin_ids))
    except Exception as e:
        logger.warning("plugin_discovery_failed", error=str(e))

    # Removes tasks stuck in 'running' but with dead asyncio tasks.
    # Without this, a session can be permanently blocked if the task
    # fails before its asyncio_task is properly set or after a GC edge case.
    _stale_cleanup_task = None
    try:
        from app.core.task_runner import get_task_runner as _get_tr_for_stale

        async def _periodic_stale_task_cleanup():
            """Clean stale tasks every 2 minutes."""
            while True:
                await asyncio.sleep(120)  # Every 2 minutes
                try:
                    _runner = _get_tr_for_stale()
                    await _runner.cleanup_stale_tasks(max_idle_seconds=600)
                except Exception as _stale_err:
                    logger.debug("stale_task_cleanup_error", error=str(_stale_err))

        _stale_cleanup_task = asyncio.create_task(_periodic_stale_task_cleanup())
        logger.info("stale_task_cleanup_started", interval_s=120, max_idle_s=600)
    except Exception as e:
        logger.warning("stale_task_cleanup_setup_failed", error=str(e))

    # Clients that crash (mobile network loss, browser kill) leave orphaned
    # connections in ConnectionManager. This periodic task probes and removes them.
    _ws_cleanup_task = None
    try:
        from app.api.ws import manager as _ws_manager

        async def _periodic_ws_cleanup():
            while True:
                await asyncio.sleep(60)  # Every 60 seconds
                try:
                    removed = await _ws_manager.cleanup_dead_connections()
                    if removed:
                        logger.debug("ws_periodic_cleanup", removed=removed)
                except Exception as _ws_err:
                    logger.debug("ws_cleanup_error", error=str(_ws_err))

        _ws_cleanup_task = asyncio.create_task(_periodic_ws_cleanup())
        logger.info("ws_periodic_cleanup_started", interval_s=60)
    except Exception as e:
        logger.warning("ws_cleanup_setup_failed", error=str(e))

    yield

    # ═══ Graceful Shutdown: Checkpoint all running tasks before cancel ═══
    if settings.enable_task_resume:
        try:
            from app.core.task_runner import get_task_runner as _get_tr_checkpoint
            from app.core.dependencies import _session_factory as _ckpt_sf
            if _ckpt_sf:
                from app.core.checkpoint_persistence import CheckpointPersistence
                _ckpt_svc = CheckpointPersistence(_ckpt_sf)
                _tr_ckpt = _get_tr_checkpoint()
                _running = getattr(_tr_ckpt, "_running_tasks", {})
                _ckpt_count = 0
                # _running_tasks is keyed by session_id; the real task_id lives
                # on the RunningTask (task_checkpoints.task_id FKs tasks.id, so
                # passing session_id here would violate the foreign key).
                for _session_id, _rtask in list(_running.items()):
                    if _rtask.status == "running":
                        try:
                            # Build minimal ExecutionState for checkpoint
                            from app.orchestration.models import ExecutionState
                            _ckpt_state = ExecutionState(
                                task_id=_rtask.task_id,
                                user_message=_rtask.message,
                                status="executing",
                            )
                            _saved = await _ckpt_svc.save_checkpoint(
                                task_id=_rtask.task_id,
                                turn_number=0,  # Best-effort — exact turn unknown at runner level
                                state=_ckpt_state,
                                reason="graceful_shutdown",
                            )
                            if _saved:
                                await _ckpt_svc.mark_task_suspended(_rtask.task_id)
                                _ckpt_count += 1
                            else:
                                # Don't claim a save that didn't happen — the
                                # checkpoint layer already logged the cause.
                                logger.warning(
                                    "shutdown_checkpoint_not_persisted",
                                    task_id=_rtask.task_id[:8],
                                    session_id=_session_id[:8],
                                )
                        except Exception as _ckpt_err:
                            logger.debug("shutdown_checkpoint_failed", task_id=_rtask.task_id[:8], error=str(_ckpt_err))
                if _ckpt_count > 0:
                    logger.info("shutdown_checkpoints_saved", count=_ckpt_count)
        except Exception as _shutdown_ckpt_err:
            logger.warning("graceful_shutdown_checkpoint_failed", error=str(_shutdown_ckpt_err))

    # Fire-and-forget persist tasks may still be in-flight. Wait for them
    # to complete so events are not lost on graceful shutdown.
    try:
        from app.core.task_runner import get_task_runner as _get_tr_drain
        _drain_runner = _get_tr_drain()
        await _drain_runner.drain_pending_persists(timeout=5.0)
    except Exception as _drain_err:
        logger.warning("drain_pending_persists_failed", error=str(_drain_err))

    # Stop stale task cleanup
    if _stale_cleanup_task and not _stale_cleanup_task.done():
        _stale_cleanup_task.cancel()
        try:
            await _stale_cleanup_task
        except (asyncio.CancelledError, Exception):
            pass

    # Stop WS cleanup task
    if _ws_cleanup_task and not _ws_cleanup_task.done():
        _ws_cleanup_task.cancel()
        try:
            await _ws_cleanup_task
        except (asyncio.CancelledError, Exception):
            pass

    # Stop Dreaming Engine background task
    if dreaming_task and not dreaming_task.done():
        dreaming_task.cancel()
        try:
            await dreaming_task
        except (asyncio.CancelledError, Exception):
            pass

    # Stop AWS Credential Refresher
    try:
        from app.core.aws_refresher import get_aws_refresher
        get_aws_refresher().stop()
    except Exception:
        pass

    # Stop Bounty PR Monitor
    try:
        from app.cron.bounty_pr_monitor import get_bounty_pr_monitor
        await get_bounty_pr_monitor().stop()
    except Exception:
        pass

    # Stop Cron Scheduler
    if cron_scheduler:
        try:
            await cron_scheduler.stop()
        except Exception:
            pass

    # Stop OpenClaw bridge + subprocess
    if openclaw_bridge:
        await openclaw_bridge.stop()
    if openclaw_lifecycle:
        await openclaw_lifecycle.stop()

    # Disconnect MCP servers (mirror of startup auto-connect)
    try:
        from app.mcp.client import get_mcp_client_manager
        await get_mcp_client_manager().disconnect_all()
    except Exception as e:
        logger.warning("mcp_servers_disconnect_failed", error=str(e))

    # Stop metrics emitter
    await metrics_emitter.stop()

    # Stop resource monitor + watchdog
    try:
        await resource_monitor.stop()
    except Exception:
        pass
    if task_watchdog:
        try:
            await task_watchdog.stop()
        except Exception:
            pass

    # Stop task queue
    await queue.stop_workers()

    # Disconnect channels
    channel_mgr = get_channel_manager()
    await channel_mgr.disconnect_all()

    # Cleanup
    await close_redis()
    await close_db_engine()
    logger.info("nexus_shutdown_complete")


async def _setup_channels() -> None:
    """Register and connect messaging channels based on config."""
    channel_mgr = get_channel_manager()

    # Telegram (existing). Background getUpdates polling is gated by
    # TELEGRAM_POLLING_ENABLED (default off) so it does not compete with the
    # agent-facing telegram_get_updates tool (409 Conflict / swallowed replies).
    if settings.telegram_bot_token:
        from app.channels.telegram import TelegramChannel
        channel_mgr.register(
            TelegramChannel(enable_polling=settings.telegram_polling_enabled)
        )
        logger.info(
            "channel_registered",
            type="telegram",
            polling=settings.telegram_polling_enabled,
        )

    # Discord
    if settings.discord_bot_token:
        from app.channels.discord import DiscordChannel
        guild_ids = [
            int(gid.strip())
            for gid in settings.discord_guild_ids.split(",")
            if gid.strip()
        ] if settings.discord_guild_ids else []
        channel_mgr.register(DiscordChannel(
            bot_token=settings.discord_bot_token,
            guild_ids=guild_ids,
            command_prefix=settings.discord_command_prefix,
        ))
        logger.info("channel_registered", type="discord")

    # Slack
    if settings.slack_bot_token:
        from app.channels.slack import SlackChannel
        channel_mgr.register(SlackChannel(
            bot_token=settings.slack_bot_token,
            app_token=settings.slack_app_token,
            signing_secret=settings.slack_signing_secret or "",
        ))
        logger.info("channel_registered", type="slack")

    # WhatsApp
    if settings.whatsapp_enabled and (settings.whatsapp_api_url or settings.whatsapp_api_token):
        try:
            from app.channels.whatsapp import WhatsAppChannel
            channel_mgr.register(WhatsAppChannel(
                bridge_url=settings.whatsapp_api_url or "http://localhost:3001",
                phone_number_id=settings.whatsapp_phone_number_id,
                access_token=settings.whatsapp_api_token,
                mode=settings.whatsapp_mode,
            ))
            logger.info("channel_registered", type="whatsapp")
        except Exception as e:
            logger.warning("channel_registration_failed", type="whatsapp", error=str(e))

    # Email — auto-polling channel (inbound mail → task). Gated by an explicit
    # flag so that merely having IMAP/SMTP creds (needed by the fetch_emails /
    # send_email tools) does NOT silently turn notification mail into tasks.
    if settings.email_channel_enabled and (settings.email_smtp_host or settings.email_imap_host):
        try:
            from app.channels.email import EmailChannel
            channel_mgr.register(EmailChannel(
                imap_host=settings.email_imap_host,
                imap_port=settings.email_imap_port,
                imap_user=settings.email_username,
                imap_password=settings.email_password,
                smtp_host=settings.email_smtp_host,
                smtp_port=settings.email_smtp_port,
                smtp_user=settings.email_username,
                smtp_password=settings.email_password,
                from_address=settings.email_from_address or settings.email_username,
            ))
            logger.info("channel_registered", type="email")
        except Exception as e:
            logger.warning("channel_registration_failed", type="email", error=str(e))

    # SMS (Twilio)
    if settings.sms_account_sid and settings.sms_auth_token:
        try:
            from app.channels.sms import SMSChannel
            channel_mgr.register(SMSChannel(
                account_sid=settings.sms_account_sid,
                auth_token=settings.sms_auth_token,
                phone_number=settings.sms_phone_number,
            ))
            logger.info("channel_registered", type="sms")
        except Exception as e:
            logger.warning("channel_registration_failed", type="sms", error=str(e))

    # Signal
    if settings.signal_api_url:
        try:
            from app.channels.signal_adapter import SignalChannel
            channel_mgr.register(SignalChannel(
                api_url=settings.signal_api_url,
                phone_number=settings.signal_phone_number,
            ))
            logger.info("channel_registered", type="signal")
        except Exception as e:
            logger.warning("channel_registration_failed", type="signal", error=str(e))

    # Matrix
    if settings.matrix_homeserver and settings.matrix_access_token:
        try:
            from app.channels.matrix import MatrixChannel
            channel_mgr.register(MatrixChannel(
                homeserver=settings.matrix_homeserver,
                user_id=settings.matrix_user_id,
                access_token=settings.matrix_access_token,
            ))
            logger.info("channel_registered", type="matrix")
        except Exception as e:
            logger.warning("channel_registration_failed", type="matrix", error=str(e))

    # Webhook
    if settings.webhook_secret:
        try:
            from app.channels.webhook import WebhookChannel
            channel_mgr.register(WebhookChannel(
                secret=settings.webhook_secret,
            ))
            logger.info("channel_registered", type="webhook")
        except Exception as e:
            logger.warning("channel_registration_failed", type="webhook", error=str(e))

    # Connect all registered channels — with timeout to avoid blocking startup
    if channel_mgr.list_channels():
        try:
            connected = await asyncio.wait_for(channel_mgr.connect_all(), timeout=15.0)
            logger.info("channels_connected", channels=connected)
        except asyncio.TimeoutError:
            logger.warning("channels_connect_timeout",
                           msg="Some channels timed out during connect, will retry later")

    # Wire MessageRouter → CoreOrchestrator so inbound messages are processed
    try:
        from app.channels.routing import get_message_router
        from app.channels.base import InboundMessage

        router = get_message_router()

        async def _channel_message_handler(inbound: InboundMessage) -> Optional[str]:
            """Bridge channel messages to CoreOrchestrator."""
            from app.core.dependencies import _session_factory
            from app.services.orchestrator import CoreOrchestrator

            if _session_factory is None:
                logger.error("channel_handler_no_db", msg="DB not initialized")
                return "❌ Database not ready."

            try:
                async with _session_factory() as db:
                    orchestrator = CoreOrchestrator(db)
                    result = await orchestrator.process_user_request(
                        message=inbound.content,
                        user_id=inbound.sender_id or "channel_user",
                        conversation_id=inbound.metadata.get("session_id") if inbound.metadata else None,
                    )
                    if db.is_active:
                        await db.commit()
                    # result is a dict from process_user_request
                    if isinstance(result, dict):
                        return result.get("message", str(result))
                    return str(result) if result else None
            except Exception as e:
                logger.error("channel_handler_error", error=str(e))
                return f"❌ Error processing message: {str(e)[:200]}"

        router.set_handler(_channel_message_handler)
        logger.info("message_router_handler_set")
    except Exception as e:
        logger.warning("message_router_setup_failed", error=str(e))


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title=__app_name__,
        version=__version__,
        description="Core Agent System with Swarm Intelligence & Multi-Agent Orchestration",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    _cors_default = "http://localhost:3000,http://localhost:3099,http://localhost:8899,http://127.0.0.1:3000"
    cors_origins = os.environ.get("CORS_ORIGINS", _cors_default).split(",")
    # Strip whitespace from each origin
    cors_origins = [o.strip() for o in cors_origins if o.strip()]
    # Allow wildcard only if explicitly set (not from default)
    _allow_all = cors_origins == ["*"]
    if _allow_all and settings.is_production:
        # Match the secret-key policy (SEC-03): production must FAIL fast on an
        # insecure config rather than boot with any-origin access. A warning that
        # the process ignores is how wildcard CORS reaches prod unnoticed.
        logger.critical(
            "cors_wildcard_in_production",
            message="CORS_ORIGINS=* is DANGEROUS in production! Any origin can call your APIs.",
            hint="Set CORS_ORIGINS to specific origins (e.g., https://yourdomain.com)",
        )
        raise RuntimeError(
            "Cannot start in production with CORS_ORIGINS=* — set explicit origins."
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=not _allow_all,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Rate Limiting Middleware (auto-scaled)
    from app.core.rate_limiter import RateLimitMiddleware

    app.add_middleware(
        RateLimitMiddleware,
        rpm=settings.effective_rate_limit_rpm,
        burst=settings.effective_rate_limit_burst,
        enabled=settings.rate_limit_enabled,
    )
    logger.info(
        "rate_limiter_configured",
        rpm=settings.effective_rate_limit_rpm,
        burst=settings.effective_rate_limit_burst,
        auto_scale=settings.auto_scale,
    )

    # Auto-learn the public base URL from incoming requests so preview links work
    # without anyone setting EXTERNAL_BASE_URL by hand (the process can't otherwise
    # know the domain it's served under). Explicit config still overrides.
    @app.middleware("http")
    async def _learn_public_base(request: Request, call_next):
        try:
            from app.core.public_base import derive_base_url, remember_base_url
            remember_base_url(derive_base_url(dict(request.headers)))
        except Exception:
            pass
        return await call_next(request)

    # API Routes
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(openai_compat_router, prefix="/v1")
    app.include_router(ws_router)

    # Preview reverse-proxy: lets users open agent-hosted dev-server apps via this
    # domain (https://<domain>/preview/<port>/...). Public + SSRF-guarded to the
    # dev-server port range. Mounted before the SPA catch-all so /preview wins.
    from app.api.preview_router import preview_router
    app.include_router(preview_router)

    # OpenClaw inbound webhook router (receives messages from OpenClaw Gateway)
    from app.openclaw.inbound import router as openclaw_router
    app.include_router(openclaw_router)

    @app.get("/health")
    async def health_check():
        return {
            "status": "healthy",
            "app": __app_name__,
            "version": __version__,
        }

    @app.get("/metrics")
    async def prometheus_metrics():
        """Prometheus scrape endpoint (audit C4).

        Renders the text exposition format from live circuit-breaker health plus
        a few process gauges, with no prometheus_client dependency.
        """
        from fastapi.responses import PlainTextResponse

        from app.core.circuit_breaker import get_circuit_breaker
        from app.core.metrics_exporter import render_prometheus

        extra_gauges: dict[str, float] = {}
        try:
            from app.core.system_metrics import collect_process_gauges
            extra_gauges = collect_process_gauges()
        except Exception:
            # Metrics must never take the endpoint down — emit circuits only.
            pass

        body = render_prometheus(
            circuit_status=get_circuit_breaker().get_status(),
            extra_gauges=extra_gauges,
        )
        # Prometheus text format content type (version 0.0.4).
        return PlainTextResponse(
            body, media_type="text/plain; version=0.0.4; charset=utf-8"
        )

    # ============================================================
    # Serve Frontend Static Files (All-in-One Docker mode)
    # ============================================================
    static_dir = os.environ.get("STATIC_DIR", "static")
    serve_frontend = os.environ.get("NEXUS_SERVE_FRONTEND", "false").lower() in ("true", "1", "yes")

    if serve_frontend and Path(static_dir).is_dir():
        # Mount /assets for Vite-built JS/CSS/images
        assets_dir = Path(static_dir) / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="static-assets")

        # SPA catch-all: any non-API route → index.html
        index_html = Path(static_dir) / "index.html"

        @app.get("/")
        async def serve_root():
            """Serve index.html for root path."""
            if index_html.is_file():
                return FileResponse(str(index_html))
            return {"error": "Frontend not found"}

        @app.get("/{path:path}")
        async def serve_spa(request: Request, path: str):
            # Skip API/WS routes — return a real 404 (not a 200 with an error
            # object) so clients expecting JSON arrays don't silently receive
            # an object and crash on `.map()`.
            if path.startswith(("api/", "v1/", "ws/", "preview/", "health", "metrics", "openapi.json")):
                raise HTTPException(status_code=404, detail="Not found")
            # Try to serve static file first
            file_path = Path(static_dir) / path
            if file_path.is_file():
                return FileResponse(str(file_path))
            # Otherwise serve index.html for SPA routing
            if index_html.is_file():
                return FileResponse(str(index_html))
            return {"error": "Frontend not found"}

        logger.info("frontend_static_serving_enabled", static_dir=static_dir)

    return app


app = create_app()


def main():
    """CLI entry point."""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_debug,
        log_level="debug" if settings.app_debug else "info",
    )


if __name__ == "__main__":
    main()
