"""
Nexus - g4f (GPT4Free) Anonymous Client.

Provides integration with GPT4Free library for anonymous access to various AI models
without API keys. Acts as a fallback when all other providers fail.
"""

import asyncio
import json
import re
from typing import Any, AsyncGenerator, Optional
import structlog

logger = structlog.get_logger(__name__)


class G4FClient:
    """Async wrapper for GPT4Free anonymous API access."""

    def __init__(self, model: str = "gpt-4o"):
        self.model = model
        self.provider = "g4f"
        self._g4f_available = self._check_g4f()

    def _check_g4f(self) -> bool:
        """Check if g4f library is installed."""
        try:
            import g4f  # noqa: F401
            return True
        except ImportError:
            return False

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Send chat completion via g4f anonymous endpoint."""
        if not self._g4f_available:
            raise RuntimeError("g4f library not installed. Run: pip install g4f")

        import g4f

        # Convert model name to g4f format
        g4f_model = self._resolve_g4f_model()

        # g4f works best with sync, run in executor for async
        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(
                None,
                lambda: g4f.ChatCompletion.create(
                    model=g4f_model,
                    messages=messages,
                ),
            )
            return {
                "content": response or "",
                "role": "assistant",
                "finish_reason": "stop",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        except Exception as e:
            logger.error("g4f_chat_failed", model=self.model, error=str(e))
            raise

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream chat completion via g4f streaming endpoint."""
        if not self._g4f_available:
            raise RuntimeError("g4f library not installed. Run: pip install g4f")

        import g4f

        g4f_model = self._resolve_g4f_model()
        loop = asyncio.get_event_loop()

        try:
            # g4f stream=True returns a sync generator; collect in executor
            generator = await loop.run_in_executor(
                None,
                lambda: g4f.ChatCompletion.create(
                    model=g4f_model,
                    messages=messages,
                    stream=True,
                ),
            )
            # Iterate over the sync generator in the executor
            for chunk in generator:
                if chunk:
                    yield chunk
        except Exception as e:
            logger.error("g4f_chat_stream_failed", model=self.model, error=str(e))
            raise

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Get JSON response via g4f anonymous endpoint."""
        if not self._g4f_available:
            raise RuntimeError("g4f library not installed. Run: pip install g4f")

        # Add explicit JSON instruction to the last user message
        json_messages = list(messages)
        json_messages.append({
            "role": "system",
            "content": "You MUST respond with valid JSON only. No markdown, no explanation, just raw JSON.",
        })

        result = await self.chat(json_messages, temperature, max_tokens)
        content = result.get("content", "")

        return self._parse_json_response(content)

    def _parse_json_response(self, content: str) -> dict[str, Any]:
        """Parse JSON from LLM response, handling markdown code blocks."""
        stripped = content.strip()

        # 1. Try direct parse
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

        # 2. Try extracting from markdown code blocks (```json ... ``` or ``` ... ```)
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", stripped, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 3. Fallback: wrap raw text
        return {"response": content, "format": "text_fallback_to_json"}

    def _resolve_g4f_model(self) -> Any:
        """Convert string model name to g4f model enum."""
        import g4f

        model_map = {
            "gpt-4o": g4f.models.gpt_4o,
            "gpt-4": g4f.models.gpt_4,
            "gpt-3.5-turbo": g4f.models.gpt_3_5_turbo,
            "claude": g4f.models.claude_3,
            "claude-3": g4f.models.claude_3,
            "llama-3": g4f.models.llama_3,
        }
        return model_map.get(self.model, g4f.models.gpt_4o)


def get_g4f_client(model: str = "gpt-4o") -> Optional["G4FClient"]:
    """Get g4f client if library is installed."""
    try:
        return G4FClient(model=model)
    except Exception:
        return None