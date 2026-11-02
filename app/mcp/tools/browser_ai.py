"""
Nexus - Browser AI Tools (browser-use Integration).

AI-native browser control using the browser-use library.
These tools allow agents to interact with web pages using natural language
instead of manual CSS selectors.

Includes stealth anti-bot bypass layer:
- Patchright (undetected Chromium) as primary engine
- Camoufox (anti-detect Firefox) as fallback
- Automatic Cloudflare/CAPTCHA bypass
- Human behavior simulation
- Free proxy rotation

Optional dependency: browser-use library.
If not installed, tools simply won't be registered (graceful degradation).
"""

import asyncio
from typing import Any

import structlog

from app.mcp.tool_registry import registry

logger = structlog.get_logger(__name__)

# Check if browser-use is available
_BROWSER_USE_AVAILABLE = False
try:
    from browser_use import Agent as BrowserAgent, Browser
    _BROWSER_USE_AVAILABLE = True
except ImportError:
    logger.info("browser_use_not_installed", message="browser-use tools disabled. Install with: pip install browser-use")

# Check if stealth layer is available
_STEALTH_AVAILABLE = False
try:
    from app.browser.stealth import create_stealth_browser, StealthConfig
    _STEALTH_AVAILABLE = True
except ImportError:
    logger.info("stealth_layer_not_available", message="Stealth browser disabled. Missing deps.")


def _get_browser_llm():
    """Get LLM client for browser-use agent.

    Uses the system's configured LLM (from settings) — same model as core/agent.
    No model substitution — respects exact user configuration.

    browser-use requires its own BaseChatModel (NOT langchain). We use browser-use's
    native LLM classes: ChatAWSBedrock, ChatAnthropic, ChatOpenAI, ChatGoogle, ChatLiteLLM.
    """
    import os

    try:
        from app.config import get_settings
        settings = get_settings()
        provider = settings.core_llm_provider.lower()
        model = settings.core_llm_model

        logger.info("browser_llm_init", provider=provider, model=model)

        if provider == "bedrock":
            try:
                from browser_use.llm.aws.chat_bedrock import ChatAWSBedrock
                region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
                return ChatAWSBedrock(
                    model=model,
                    max_tokens=4096,
                    temperature=0.3,
                    aws_region=region,
                )
            except ImportError:
                logger.warning("browser_llm_bedrock_import_failed", hint="pip install browser-use[aws]")
            except Exception as e:
                logger.warning("browser_llm_bedrock_init_failed", error=str(e)[:200])

        elif provider == "anthropic":
            try:
                from browser_use.llm.anthropic.chat import ChatAnthropic
                api_key = settings.core_llm_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
                if api_key:
                    return ChatAnthropic(model=model, api_key=api_key, temperature=0.3)
            except ImportError:
                logger.warning("browser_llm_anthropic_import_failed")
            except Exception as e:
                logger.warning("browser_llm_anthropic_init_failed", error=str(e)[:200])

        elif provider == "openai":
            try:
                from browser_use.llm.openai.chat import ChatOpenAI
                api_key = (
                    settings.core_llm_api_key
                    or getattr(settings, "openai_api_key", "")
                    or os.environ.get("OPENAI_API_KEY", "")
                )
                if api_key:
                    return ChatOpenAI(model=model, api_key=api_key, temperature=0.3)
            except ImportError:
                logger.warning("browser_llm_openai_import_failed")
            except Exception as e:
                logger.warning("browser_llm_openai_init_failed", error=str(e)[:200])

        elif provider in ("gemini", "google"):
            try:
                from browser_use.llm.google.chat import ChatGoogle
                api_key = settings.core_llm_api_key or os.environ.get("GOOGLE_API_KEY", "")
                if api_key:
                    return ChatGoogle(model=model, api_key=api_key, temperature=0.3)
            except ImportError:
                logger.warning("browser_llm_google_import_failed")
            except Exception as e:
                logger.warning("browser_llm_google_init_failed", error=str(e)[:200])

        elif provider in ("openrouter", "custom"):
            try:
                from browser_use.llm.openrouter.chat import ChatOpenRouter
                api_key = (
                    settings.core_llm_api_key
                    or getattr(settings, "openrouter_api_key", "")
                    or os.environ.get("OPENROUTER_API_KEY", "")
                )
                if api_key:
                    return ChatOpenRouter(model=model, api_key=api_key, temperature=0.3)
            except ImportError:
                logger.warning("browser_llm_openrouter_import_failed")
            except Exception as e:
                logger.warning("browser_llm_openrouter_init_failed", error=str(e)[:200])

        else:
            # Fallback: use ChatLiteLLM which supports any litellm-compatible model
            try:
                from browser_use.llm.litellm.chat import ChatLiteLLM
                return ChatLiteLLM(model=f"{provider}/{model}", temperature=0.3)
            except ImportError:
                logger.warning("browser_llm_litellm_import_failed")
            except Exception as e:
                logger.warning("browser_llm_litellm_init_failed", error=str(e)[:200])

        logger.error(
            "browser_llm_init_failed",
            provider=provider,
            model=model,
            hint="Could not initialize browser LLM from system config",
        )
    except Exception as e:
        logger.error("browser_llm_settings_read_failed", error=str(e)[:200])

    return None


async def _create_browser(headless: bool = True, stealth: bool = True) -> "Browser":
    """Create browser instance with optional stealth anti-bot protection."""
    if stealth and _STEALTH_AVAILABLE:
        config = StealthConfig(headless=headless)
        manager = await create_stealth_browser(config)
        # Wrap stealth context as a Browser-compatible object for browser-use
        return _StealthBrowserWrapper(manager)

    return Browser(headless=headless)


class _StealthBrowserWrapper:
    """Wraps StealthBrowserManager to be compatible with browser-use's Browser interface."""

    def __init__(self, manager):
        self._manager = manager

    @property
    def config(self):
        return self._manager.config

    async def new_context(self, **kwargs):
        return self._manager._context

    async def close(self):
        await self._manager.close()

    def __getattr__(self, name):
        return getattr(self._manager, name)


async def handle_browser_ai_action(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Execute browser action using natural language instruction."""
    instruction = params.get("instruction", "")
    url = params.get("url", "")
    max_steps = params.get("max_steps", 10)

    if not instruction:
        return {"success": False, "output": "No instruction provided", "data": None, "artifacts": []}

    llm = _get_browser_llm()
    if not llm:
        return {
            "success": False,
            "output": "No LLM API key configured for browser-use. Set OPENAI_API_KEY, ANTHROPIC_API_KEY, or GOOGLE_API_KEY.",
            "data": None,
            "artifacts": [],
        }

    try:
        import os
        headless = os.environ.get("BROWSER_USE_HEADLESS", "true").lower() == "true"
        use_stealth = os.environ.get("BROWSER_USE_STEALTH", "true").lower() == "true"

        browser = await _create_browser(headless=headless, stealth=use_stealth)

        task = instruction
        if url:
            task = f"Go to {url} and then: {instruction}"

        agent = BrowserAgent(
            task=task,
            llm=llm,
            browser=browser,
        )

        history = await agent.run(max_steps=max_steps)

        result_text = history.final_result() or "Action completed"
        is_done = history.is_done()
        urls_visited = history.urls()
        errors = [e for e in history.errors() if e]

        return {
            "success": is_done,
            "output": result_text,
            "data": {
                "urls_visited": urls_visited[:10],
                "steps_taken": history.number_of_steps(),
                "errors": errors[:5],
                "is_successful": history.is_successful(),
            },
            "artifacts": [],
        }
    except Exception as e:
        logger.error("browser_ai_action_failed", error=str(e))
        return {"success": False, "output": f"Browser AI error: {e}", "data": None, "artifacts": []}


async def handle_browser_ai_extract(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Extract structured data from a webpage using AI understanding."""
    url = params.get("url", "")
    instruction = params.get("instruction", "Extract all important information from this page")
    max_steps = params.get("max_steps", 5)

    if not url:
        return {"success": False, "output": "No URL provided", "data": None, "artifacts": []}

    llm = _get_browser_llm()
    if not llm:
        return {
            "success": False,
            "output": "No LLM API key configured for browser-use.",
            "data": None,
            "artifacts": [],
        }

    try:
        import os
        headless = os.environ.get("BROWSER_USE_HEADLESS", "true").lower() == "true"
        use_stealth = os.environ.get("BROWSER_USE_STEALTH", "true").lower() == "true"
        browser = await _create_browser(headless=headless, stealth=use_stealth)

        agent = BrowserAgent(
            task=f"Go to {url} and {instruction}. Return the extracted data as structured text.",
            llm=llm,
            browser=browser,
        )

        history = await agent.run(max_steps=max_steps)
        result_text = history.final_result() or ""

        # Also get any extracted content from action results
        extracted = history.extracted_content()
        all_content = [result_text] + [e for e in extracted if e]

        return {
            "success": True,
            "output": "\n\n".join(filter(None, all_content)),
            "data": {
                "url": url,
                "extracted_items": len(all_content),
                "steps_taken": history.number_of_steps(),
            },
            "artifacts": [],
        }
    except Exception as e:
        logger.error("browser_ai_extract_failed", error=str(e))
        return {"success": False, "output": f"Browser AI extract error: {e}", "data": None, "artifacts": []}


async def handle_browser_ai_navigate_flow(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Execute a multi-step browser navigation flow described in natural language."""
    steps = params.get("steps", [])
    if not steps:
        return {"success": False, "output": "No steps provided", "data": None, "artifacts": []}

    # Combine steps into a single task
    combined_task = "Complete these steps in order:\n" + "\n".join(
        f"{i+1}. {step}" for i, step in enumerate(steps)
    )

    llm = _get_browser_llm()
    if not llm:
        return {
            "success": False,
            "output": "No LLM API key configured for browser-use.",
            "data": None,
            "artifacts": [],
        }

    try:
        import os
        headless = os.environ.get("BROWSER_USE_HEADLESS", "true").lower() == "true"
        use_stealth = os.environ.get("BROWSER_USE_STEALTH", "true").lower() == "true"
        browser = await _create_browser(headless=headless, stealth=use_stealth)

        agent = BrowserAgent(
            task=combined_task,
            llm=llm,
            browser=browser,
        )

        max_steps = min(len(steps) * 5, 50)  # ~5 actions per step, max 50
        history = await agent.run(max_steps=max_steps)

        result_text = history.final_result() or "Flow completed"

        return {
            "success": history.is_done(),
            "output": result_text,
            "data": {
                "steps_requested": len(steps),
                "actions_taken": history.number_of_steps(),
                "urls_visited": history.urls()[:10],
                "is_successful": history.is_successful(),
            },
            "artifacts": [],
        }
    except Exception as e:
        logger.error("browser_ai_navigate_failed", error=str(e))
        return {"success": False, "output": f"Browser AI flow error: {e}", "data": None, "artifacts": []}


# ------------------------------------------------------------------
# Registration (only if browser-use is installed)
# ------------------------------------------------------------------

if _BROWSER_USE_AVAILABLE:
    _TOOLSET = "browser"

    registry.register(
        name="browser_ai_action",
        toolset=_TOOLSET,
        schema={
            "description": (
                "Execute browser actions using natural language. The AI agent will navigate, "
                "click, type, scroll, and interact with web pages autonomously. "
                "Use for complex web interactions that are hard with CSS selectors."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "instruction": {
                        "type": "string",
                        "description": "Natural language instruction (e.g., 'Click the login button and fill email with test@test.com')",
                    },
                    "url": {
                        "type": "string",
                        "description": "Starting URL (optional if already navigated)",
                    },
                    "max_steps": {
                        "type": "integer",
                        "default": 10,
                        "description": "Maximum browser actions to take",
                    },
                },
                "required": ["instruction"],
            },
        },
        handler=handle_browser_ai_action,
        is_async=True,
        emoji="🤖",
    )

    registry.register(
        name="browser_ai_extract",
        toolset=_TOOLSET,
        schema={
            "description": (
                "Extract structured data from a webpage using AI understanding. "
                "Better than CSS selectors for complex/dynamic pages. "
                "Navigates to URL, understands page content, returns extracted data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL of the page to extract data from",
                    },
                    "instruction": {
                        "type": "string",
                        "description": "What data to extract (e.g., 'Extract all product prices and names')",
                        "default": "Extract all important information from this page",
                    },
                    "max_steps": {
                        "type": "integer",
                        "default": 5,
                        "description": "Maximum browser actions",
                    },
                },
                "required": ["url"],
            },
        },
        handler=handle_browser_ai_extract,
        is_async=True,
        emoji="🔍",
    )

    registry.register(
        name="browser_ai_navigate_flow",
        toolset=_TOOLSET,
        schema={
            "description": (
                "Execute a multi-step browser navigation flow. "
                "Provide a list of steps in natural language and the AI will complete them in order. "
                "Great for workflows: login → navigate → fill form → submit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of steps to complete (e.g., ['Go to github.com', 'Search for browser-use', 'Click first result'])",
                    },
                },
                "required": ["steps"],
            },
        },
        handler=handle_browser_ai_navigate_flow,
        is_async=True,
        emoji="🔄",
    )

    logger.info("browser_ai_tools_registered", tools=["browser_ai_action", "browser_ai_extract", "browser_ai_navigate_flow"])
else:
    logger.debug("browser_ai_tools_skipped", reason="browser-use not installed")
