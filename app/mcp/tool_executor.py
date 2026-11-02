"""
Nexus - Tool Executor (IronClaw-inspired).

"Đôi tay" thực thi của Agent: dispatch tool calls to the registry.

All tools are now registered via the modular tool_registry pattern.
Legacy hardcoded executor_map has been removed — all dispatch goes
through ToolRegistry.dispatch() for a single source of truth.

Security hardening:
- CommandGuard: dangerous command detection before execution
- SSRF protection: check_url_safety() before fetch
- Path traversal protection: check_path_safety() before file ops
- Credential sanitization: sanitize_credentials() on all tool outputs
"""

import asyncio
import json
from typing import Any, Optional

import structlog

from app.config import get_settings
from app.core.exceptions import ToolExecutionError
from app.core.security_audit import sanitize_credentials

logger = structlog.get_logger(__name__)


class ToolExecutor:
    """
    Thực thi tools cho agents - biến suy nghĩ thành hành động.

    All tools are dispatched via ToolRegistry. No legacy fallback.
    """

    # Module-level cache for tool schemas
    _tool_schemas_cache: list[dict[str, Any]] | None = None
    _tool_schemas_cache_size: int = 0

    def __init__(self) -> None:
        pass

    @staticmethod
    def invalidate_schema_cache() -> None:
        """Force-refresh tool schemas on the next get_tool_schemas() call.

        Needed for dynamic tool (re)loading where registry tool count might not change
        (e.g., overwriting an existing tool with an updated schema).
        """
        ToolExecutor._tool_schemas_cache = None
        ToolExecutor._tool_schemas_cache_size = 0

    async def execute(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Dispatch tool execution via ToolRegistry.

        Enhanced with:
        - Tool argument coercion (auto-fix LLM type errors)
        - Credential sanitization on tool outputs
        - Consistent error handling

        Returns: {"success": bool, "output": str, "data": Any, "artifacts": list}
        """
        from app.mcp.tool_registry import registry

        # Ensure tool modules are imported (triggers registration)
        try:
            import app.mcp.tools  # noqa: F401
        except ImportError:
            pass

        # --- Coerce tool arguments (hermes-agent pattern) ---
        entry = registry.get_entry(tool_name)
        if entry is not None:
            try:
                from app.core.tool_coercion import coerce_tool_args
                schema = entry.schema.get("parameters", {}) if entry.schema else {}
                if schema:
                    params = coerce_tool_args(params, schema)
            except Exception:
                pass  # Coercion is best-effort

        # --- Runtime offensive-target re-gate (audit CC-11) ---
        # The pre-flight authorization gate classifies the user's MESSAGE once. An
        # agent can fabricate a target afterwards and probe it (e5029e0d scanned a
        # government domain the user never named). Re-classify the ACTUAL host an
        # offensive-network tool is about to hit; hard-stop (fail-closed) unless the
        # target is authorized — either covered by the declared pentest scope
        # (per-target, multi-tenant) or the deployment-wide ALLOW_LIVE_OFFENSIVE flag.
        try:
            from app.security.authorization_gate import (
                evaluate_target_authorization,
                extract_tool_target,
                is_offensive_network_tool,
                is_target_authorized,
            )
            if is_offensive_network_tool(tool_name):
                _tgt = extract_tool_target(params)
                if _tgt:
                    _decision = evaluate_target_authorization(
                        _tgt, authorized=is_target_authorized(_tgt)
                    )
                    if _decision.blocked:
                        logger.warning(
                            "offensive_target_regate_blocked",
                            tool=tool_name,
                            target=_decision.target,
                            target_class=_decision.target_class.value,
                        )
                        return {
                            "success": False,
                            "output": f"⛔ {_decision.reason}",
                            "data": None,
                            "artifacts": [],
                            "blocked": True,
                            "blocked_reason": _decision.reason,
                        }
        except Exception as _regate_err:
            # Fail-closed for the offensive tool set: if the re-gate itself errors,
            # do not silently allow an active probe through.
            from app.security.authorization_gate import is_offensive_network_tool as _ion
            if _ion(tool_name):
                logger.error("offensive_target_regate_error", tool=tool_name, error=str(_regate_err))
                return {
                    "success": False,
                    "output": "🚫 Offensive-target authorization re-check failed (fail-closed).",
                    "data": None,
                    "artifacts": [],
                    "blocked": True,
                    "blocked_reason": "target re-gate error (fail-closed)",
                }

        # --- Tool Policy + Security Guard (OpenClaw-inspired) ---
        settings = get_settings()
        if settings.tool_policy_enabled:
            try:
                from app.core.tool_guard import guard_tool_execution, redact_tool_output
                allowed, reason, decision = await guard_tool_execution(
                    tool_name=tool_name,
                    tool_args=params,
                    session_id=params.get("_session_id", ""),
                    user_id=params.get("_user_id", ""),
                )
                if not allowed:
                    logger.warning("tool_blocked", tool=tool_name, reason=reason)
                    return {
                        "success": False,
                        "output": f"🚫 Tool blocked: {reason}",
                        "data": None,
                        "artifacts": [],
                        "blocked": True,
                        "blocked_reason": reason,
                    }
            except Exception as guard_err:
                _dangerous_tools = {
                    "execute_command", "ssh_exec", "docker_build", "docker_deploy",
                    "database_query", "port_scan", "tcp_connect", "send_email",
                    "send_external_message",
                    "expose_port",
                    "install_packages", "delete_file",
                    "scaffold_custom_tool_module", "scaffold_skill",
                    "open_firewall_port", "setup_public_inbox",
                }
                if tool_name in _dangerous_tools:
                    logger.error("tool_guard_check_failed_dangerous", tool=tool_name, error=str(guard_err))
                    return {
                        "success": False,
                        "output": f"🚫 Security guard error for dangerous tool '{tool_name}': {str(guard_err)[:100]}",
                        "data": None,
                        "artifacts": [],
                        "blocked": True,
                        "blocked_reason": "Security guard check failed (fail-closed for dangerous tools)",
                    }
                else:
                    logger.debug("tool_guard_check_failed_safe", tool=tool_name, error=str(guard_err))
                    # Fail-open for safe tools only (read_file, web_search, etc.)

        # --- Dispatch via registry ---
        if entry is not None:
            try:
                if entry.is_async:
                    result = await entry.handler(params)
                else:
                    # BUG-14 FIX: Wrap sync handlers in asyncio.to_thread()
                    # to prevent blocking the event loop during execution.
                    result = await asyncio.to_thread(entry.handler, params)

                if asyncio.iscoroutine(result):
                    result = await result

                # Normalize result: registry handlers may return JSON string
                if isinstance(result, str):
                    try:
                        result = json.loads(result)
                    except (ValueError, TypeError):
                        result = {"success": True, "output": result, "data": None, "artifacts": []}

                # Ensure standard shape
                if not isinstance(result, dict):
                    result = {"success": True, "output": str(result), "data": None, "artifacts": []}

                # Sanitize credentials from tool output (legacy check)
                output = result.get("output", "")
                if isinstance(output, str) and len(output) > 10:
                    sanitized, cred_count = sanitize_credentials(output)
                    if cred_count > 0:
                        result["output"] = sanitized
                        logger.warning("tool_output_credentials_sanitized",
                                       tool=tool_name, count=cred_count)

                # Enhanced secret scanning (OpenClaw-inspired)
                if settings.credential_scan_tool_outputs:
                    try:
                        from app.core.tool_guard import redact_tool_output
                        output_text = result.get("output", "")
                        if isinstance(output_text, str) and len(output_text) > 20:
                            result["output"] = redact_tool_output(output_text)
                    except Exception:
                        pass  # Secret scan is best-effort

                logger.info("tool_executed", tool=tool_name, success=result.get("success"))
                return result
            except Exception as e:
                logger.error("tool_execution_error", tool=tool_name, error=str(e))
                return {
                    "success": False,
                    "output": f"Tool error: {e}",
                    "data": None,
                    "artifacts": [],
                }

        # --- Tool not found ---
        return {
            "success": False,
            "output": f"Unknown tool: {tool_name}. Use todo_list or list available tools.",
            "data": None,
            "artifacts": [],
        }

    async def execute_batch(
        self,
        tool_calls: list[dict[str, Any]],
        max_concurrent: int | None = None,
        timeout: int | None = None,
    ) -> list[dict[str, Any]]:
        """Execute multiple tool calls concurrently via asyncio.gather().

        This is the lower-level parallel primitive used by
        :class:`~app.core.parallel_tools.ParallelToolExecutor`.

        Each *tool_call* dict must contain:
          - ``name``  (str) — tool name
          - ``params`` (dict) — tool parameters

        Returns a list of result dicts in the **same order** as input,
        each containing: ``name``, ``result``, ``success``, ``execution_time_ms``.
        """
        if not tool_calls:
            return []

        _settings = get_settings()
        _max_concurrent = max_concurrent or _settings.tool_max_concurrent
        _timeout = timeout or _settings.tool_timeout
        semaphore = asyncio.Semaphore(_max_concurrent)

        async def _run_one(tc: dict[str, Any]) -> dict[str, Any]:
            name = tc.get("name", "unknown")
            params = tc.get("params", {})
            import time
            start = time.perf_counter()

            async with semaphore:
                try:
                    result = await asyncio.wait_for(
                        self.execute(name, params),
                        timeout=_timeout,
                    )
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    return {
                        "name": name,
                        "result": result,
                        "success": result.get("success", False),
                        "execution_time_ms": round(elapsed_ms, 1),
                    }
                except asyncio.TimeoutError:
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    return {
                        "name": name,
                        "result": {
                            "success": False,
                            "output": f"Tool '{name}' timed out after {_timeout}s",
                            "data": None,
                            "artifacts": [],
                        },
                        "success": False,
                        "execution_time_ms": round(elapsed_ms, 1),
                    }
                except Exception as exc:
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    return {
                        "name": name,
                        "result": {
                            "success": False,
                            "output": f"Tool error: {exc}",
                            "data": None,
                            "artifacts": [],
                        },
                        "success": False,
                        "execution_time_ms": round(elapsed_ms, 1),
                    }

        n = len(tool_calls)
        if n == 1:
            logger.debug("execute_batch_single", tool=tool_calls[0].get("name"))
            return [await _run_one(tool_calls[0])]

        logger.info(
            "execute_batch_parallel",
            count=n,
            tools=[tc.get("name") for tc in tool_calls],
            max_concurrent=_max_concurrent,
        )

        results = await asyncio.gather(
            *[_run_one(tc) for tc in tool_calls],
            return_exceptions=True,
        )

        # Convert any unexpected exceptions to error dicts
        final: list[dict[str, Any]] = []
        for i, r in enumerate(results):
            if isinstance(r, BaseException):
                name = tool_calls[i].get("name", "unknown")
                logger.error("execute_batch_unexpected_error", tool=name, error=str(r))
                final.append({
                    "name": name,
                    "result": {
                        "success": False,
                        "output": f"Unexpected error: {r}",
                        "data": None,
                        "artifacts": [],
                    },
                    "success": False,
                    "execution_time_ms": 0.0,
                })
            else:
                final.append(r)

        return final

    # ============================================================
    # TOOL SCHEMA (OpenAI function calling format)
    # ============================================================

    @staticmethod
    def get_tool_schemas(agent_type: str = "core") -> list[dict[str, Any]]:
        """Trả về schemas cho available tools từ registry.

        If agent_type is provided and not "core", filters schemas
        based on AgentToolPolicy for that agent type.
        Cached: only refreshes when registry size changes (core only).
        """
        if agent_type != "core":
            try:
                from app.core.tool_policy import get_agent_tool_policy
                policy = get_agent_tool_policy()
                return policy.get_tool_schemas(agent_type)
            except Exception:
                pass  # Fallback to all schemas

        from app.mcp.tool_registry import registry

        # Ensure tool modules are imported (triggers registration)
        try:
            import app.mcp.tools  # noqa: F401
        except ImportError:
            pass

        # Check if cache is still valid (same number of tools)
        current_size = len(registry.get_all_tool_names())
        if (ToolExecutor._tool_schemas_cache is not None
                and ToolExecutor._tool_schemas_cache_size == current_size):
            return ToolExecutor._tool_schemas_cache

        definitions = registry.get_definitions()
        if definitions:
            ToolExecutor._tool_schemas_cache = definitions
            ToolExecutor._tool_schemas_cache_size = current_size
            return definitions

        # Fallback: if registry is empty (should not happen with modular tools)
        logger.warning("tool_registry_empty_fallback")
        return [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search the web for information",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query"},
                            "max_results": {"type": "integer", "default": 5},
                        },
                        "required": ["query"],
                    },
                },
            },
        ]


class ScopedToolExecutor:
    """Tool executor with whitelist filtering per agent role.

    Wraps a base ToolExecutor and restricts which tools an agent can call.
    Used in multi-agent debate to enforce role-specific tool access.

    Args:
        base_executor: The underlying ToolExecutor to delegate to.
        allowed_tools: List of tool names this agent may use.
            If None, all tools are allowed (no restriction).
    """

    def __init__(
        self,
        base_executor: ToolExecutor,
        allowed_tools: list[str] | None = None,
    ) -> None:
        self._base = base_executor
        self._allowed = set(allowed_tools) if allowed_tools else None

    async def execute(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute tool if allowed by policy, otherwise return error."""
        if self._allowed and tool_name not in self._allowed:
            logger.debug(
                "tool_blocked_by_policy",
                tool=tool_name,
                allowed=list(self._allowed)[:5],
            )
            return {
                "success": False,
                "output": (
                    f"Tool '{tool_name}' is not available for your role. "
                    f"Available tools: {', '.join(sorted(self._allowed))}"
                ),
                "data": None,
                "artifacts": [],
            }
        return await self._base.execute(tool_name, params)

    @staticmethod
    def get_tool_schemas(
        agent_type: str = "general",
        allowed_tools: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get tool schemas filtered by allowed tools."""
        all_schemas = ToolExecutor.get_tool_schemas(agent_type=agent_type)
        if not allowed_tools:
            return all_schemas
        allowed_set = set(allowed_tools)
        return [
            s for s in all_schemas
            if s.get("function", {}).get("name", "") in allowed_set
        ]
