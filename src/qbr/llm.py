"""LLM client abstraction — unified interface for Anthropic Claude and Ollama.

Supports:
- Anthropic: Haiku 4.5 for extraction, Sonnet 4.6 for synthesis
- Ollama: local fallback for development/offline use
- Prompt caching (Anthropic)
- Structured output via constrained decoding or JSON mode
- Token usage logging
- Retry with exponential backoff
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from typing import Any

import anthropic
import ollama as ollama_lib
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Default models
HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-6-20250514"
DEFAULT_OLLAMA_MODEL = "llama3.1:8b"


class TokenUsage(BaseModel):
    """Token usage for a single LLM call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    model: str = ""
    duration_ms: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def estimated_cost_usd(self) -> float:
        """Estimate cost based on model pricing (April 2026)."""
        pricing = {
            HAIKU_MODEL: (1.0, 5.0, 0.10, 1.25),  # input, output, cache_read, cache_write per M
            SONNET_MODEL: (3.0, 15.0, 0.30, 3.75),
        }
        rates = pricing.get(self.model, (3.0, 15.0, 0.30, 3.75))
        inp, out, cr, cw = rates
        return (
            (self.input_tokens - self.cache_read_tokens) * inp / 1_000_000
            + self.output_tokens * out / 1_000_000
            + self.cache_read_tokens * cr / 1_000_000
            + self.cache_creation_tokens * cw / 1_000_000
        )


class UsageTracker:
    """Accumulates token usage across multiple LLM calls."""

    def __init__(self) -> None:
        self.calls: list[TokenUsage] = []

    def record(self, usage: TokenUsage) -> None:
        self.calls.append(usage)
        logger.info(
            "LLM call: model=%s input=%d output=%d cache_read=%d cost=$%.4f duration=%dms",
            usage.model,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_read_tokens,
            usage.estimated_cost_usd(),
            usage.duration_ms,
        )

    @property
    def total_input_tokens(self) -> int:
        return sum(u.input_tokens for u in self.calls)

    @property
    def total_output_tokens(self) -> int:
        return sum(u.output_tokens for u in self.calls)

    @property
    def total_cost_usd(self) -> float:
        return sum(u.estimated_cost_usd() for u in self.calls)

    @property
    def total_calls(self) -> int:
        return len(self.calls)

    def summary(self) -> dict[str, Any]:
        return {
            "total_calls": self.total_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": round(self.total_cost_usd, 4),
        }


class LLMClient(ABC):
    """Abstract base for LLM providers."""

    def __init__(self, tracker: UsageTracker | None = None) -> None:
        self.tracker = tracker or UsageTracker()

    @abstractmethod
    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        model: str | None = None,
        response_schema: type[BaseModel] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        cache_system: bool = False,
    ) -> str | dict[str, Any]:
        """Send a completion request and return text or structured JSON."""
        ...

    @abstractmethod
    def provider_name(self) -> str: ...


class AnthropicClient(LLMClient):
    """Anthropic Claude client with prompt caching and structured outputs."""

    def __init__(
        self,
        api_key: str | None = None,
        tracker: UsageTracker | None = None,
    ) -> None:
        super().__init__(tracker)
        self._client = anthropic.Anthropic(api_key=api_key)

    def provider_name(self) -> str:
        return "anthropic"

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        model: str | None = None,
        response_schema: type[BaseModel] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        cache_system: bool = False,
    ) -> str | dict[str, Any]:
        model = model or SONNET_MODEL

        # Build system prompt — optionally with cache control
        if cache_system:
            system_content = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_content = system  # type: ignore[assignment]

        # Build API kwargs
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_content,
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        }

        # Structured output via tool_use
        if response_schema:
            schema = response_schema.model_json_schema()
            kwargs["tools"] = [
                {
                    "name": "structured_output",
                    "description": f"Return a {response_schema.__name__} object",
                    "input_schema": schema,
                }
            ]
            kwargs["tool_choice"] = {"type": "tool", "name": "structured_output"}

        # Retry with exponential backoff (only transient errors)
        for attempt in range(3):
            try:
                start = time.monotonic()
                response = self._client.messages.create(**kwargs)
                duration = int((time.monotonic() - start) * 1000)
                break
            except (
                anthropic.APIConnectionError,
                anthropic.RateLimitError,
                anthropic.InternalServerError,
            ):
                if attempt == 2:
                    raise
                wait = 2**attempt
                logger.warning("Anthropic API error, retrying in %ds (attempt %d)", wait, attempt)
                time.sleep(wait)

        # Record usage
        usage = response.usage
        token_usage = TokenUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            model=model,
            duration_ms=duration,
        )
        self.tracker.record(token_usage)

        # Extract response
        if response_schema:
            for block in response.content:
                if block.type == "tool_use":
                    return block.input  # type: ignore[return-value]
            # Fallback: parse text as JSON
            text = response.content[0].text if response.content else ""
            return json.loads(text)

        return response.content[0].text if response.content else ""


class OllamaClient(LLMClient):
    """Ollama local model client — fallback for development and offline use."""

    def __init__(
        self,
        host: str = "http://localhost:11434",
        default_model: str = DEFAULT_OLLAMA_MODEL,
        tracker: UsageTracker | None = None,
    ) -> None:
        super().__init__(tracker)
        self._host = host
        self._default_model = default_model

    def provider_name(self) -> str:
        return "ollama"

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        model: str | None = None,
        response_schema: type[BaseModel] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        cache_system: bool = False,
    ) -> str | dict[str, Any]:
        model = model or self._default_model

        ollama_messages = [{"role": "system", "content": system}]
        for m in messages:
            ollama_messages.append({"role": m["role"], "content": m["content"]})

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": ollama_messages,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }

        if response_schema:
            kwargs["format"] = response_schema.model_json_schema()

        start = time.monotonic()
        response = ollama_lib.chat(**kwargs)
        duration = int((time.monotonic() - start) * 1000)

        # Record usage (Ollama provides eval_count / prompt_eval_count)
        token_usage = TokenUsage(
            input_tokens=response.get("prompt_eval_count", 0),
            output_tokens=response.get("eval_count", 0),
            model=model,
            duration_ms=duration,
        )
        self.tracker.record(token_usage)

        text = response["message"]["content"]

        if response_schema:
            return json.loads(text)

        return text


class ClaudeCLIClient(LLMClient):
    """Claude via the `claude` CLI (Claude Code) — billed to the user's OAuth
    subscription, no API key required.

    Trade-offs vs. AnthropicClient:
    - No real token counts (estimated from char length, ~4 chars/token).
    - No prompt caching — the CLI does not expose cache_control.
    - Slower (CLI startup overhead) but quality matches Claude Opus/Sonnet.
    - Structured output via schema-in-prompt + JSON parsing (no tool_use).

    Use for demos where API key billing is unwanted, or to tap the
    subscription's Opus quota.
    """

    _FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)

    # Known model aliases accepted by the Claude CLI. Prevents leaking
    # provider-label strings like "claude-cli (opus)" into --model.
    _VALID_ALIASES = {"opus", "sonnet", "haiku", "default"}

    def __init__(
        self,
        model: str = "opus",
        tracker: UsageTracker | None = None,
        binary: str | None = None,
        timeout_s: int = 60,
    ) -> None:
        super().__init__(tracker)
        self._model = model
        self._binary = binary or shutil.which("claude") or "claude"
        self._timeout_s = timeout_s

    def provider_name(self) -> str:
        return "claude-cli"

    def _resolve_model(self, caller_model: str | None) -> str:
        """Pick a valid CLI --model value.

        If caller passes a display-label like 'claude-cli (opus)' (from
        hybrid_clients) we strip it back to the alias inside the parens.
        """
        if not caller_model:
            return self._model
        m = caller_model.strip()
        # Peel off a display wrapper like "claude-cli (opus)"
        paren = re.search(r"\(([^)]+)\)", m)
        if paren:
            m = paren.group(1).strip()
        # Accept aliases, short IDs, or fully-qualified model names
        if m in self._VALID_ALIASES or m.startswith("claude-"):
            return m
        return self._model

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        model: str | None = None,
        response_schema: type[BaseModel] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        cache_system: bool = False,
    ) -> str | dict[str, Any]:
        model_name = self._resolve_model(model)

        # Build prompt. Claude CLI has no separate system/user channels in -p
        # mode, so we fold the system prompt in as a preamble and rely on the
        # model to follow it. Schema goes last with explicit "JSON only" guard.
        parts: list[str] = [system, ""]
        for m in messages:
            role = m.get("role", "user").upper()
            parts.append(f"[{role}]")
            parts.append(m["content"])
            parts.append("")
        if response_schema:
            schema = json.dumps(response_schema.model_json_schema(), indent=2)
            parts.append(
                "Return ONLY a JSON object matching this schema. "
                "No markdown fences, no prose, just the JSON:\n" + schema
            )
        prompt = "\n".join(parts)

        # Note: cannot use --bare because it disables OAuth/keychain and forces
        # ANTHROPIC_API_KEY. The whole point of this client is to use the
        # subscription via OAuth, so we accept the extra CLI overhead.
        cmd = [
            self._binary,
            "-p",
            "--model",
            model_name,
            "--output-format",
            "text",
            "--disable-slash-commands",
            "--disallowedTools",
            "Bash,Edit,Write,WebFetch,WebSearch,Task,Agent",
        ]

        start = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"claude CLI timeout after {self._timeout_s}s (model={model_name})"
            ) from e

        duration = int((time.monotonic() - start) * 1000)

        if result.returncode != 0:
            stderr_snip = (result.stderr or "").strip()[:500]
            stdout_snip = (result.stdout or "").strip()[:200]
            raise RuntimeError(
                f"claude CLI failed (exit {result.returncode}, model={model_name}): "
                f"stderr={stderr_snip!r} stdout={stdout_snip!r}"
            )

        text = result.stdout.strip()
        # Strip optional markdown fences — the model sometimes wraps JSON in ```
        fence_match = self._FENCE_RE.match(text)
        if fence_match:
            text = fence_match.group(1).strip()

        # Estimate usage — CLI does not expose token counts
        token_usage = TokenUsage(
            input_tokens=len(prompt) // 4,
            output_tokens=len(text) // 4,
            model=model_name,
            duration_ms=duration,
        )
        self.tracker.record(token_usage)

        if response_schema:
            return json.loads(text)
        return text


class FallbackClient(LLMClient):
    """Try a primary LLM client; on failure fall back to a secondary.

    Intended for: Claude Opus (via CLI, OAuth) as primary, Ollama local
    gemma4 as fallback. Surfaces the switch in the tracker so progress
    logs can tell the user the call was degraded.
    """

    def __init__(
        self,
        primary: LLMClient,
        secondary: LLMClient,
        secondary_model: str | None = None,
        tracker: UsageTracker | None = None,
    ) -> None:
        # Share the primary's tracker so usage rolls up uniformly
        super().__init__(tracker or primary.tracker)
        self._primary = primary
        self._secondary = secondary
        self._secondary_model = secondary_model

    def provider_name(self) -> str:
        return f"{self._primary.provider_name()}+fallback:{self._secondary.provider_name()}"

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        model: str | None = None,
        response_schema: type[BaseModel] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        cache_system: bool = False,
    ) -> str | dict[str, Any]:
        try:
            return self._primary.complete(
                system=system,
                messages=messages,
                model=model,
                response_schema=response_schema,
                temperature=temperature,
                max_tokens=max_tokens,
                cache_system=cache_system,
            )
        except Exception as e:
            logger.warning(
                "Primary %s failed (%s) — falling back to %s",
                self._primary.provider_name(),
                str(e)[:200],
                self._secondary.provider_name(),
            )
            return self._secondary.complete(
                system=system,
                messages=messages,
                model=self._secondary_model,
                response_schema=response_schema,
                temperature=temperature,
                max_tokens=max_tokens,
                cache_system=cache_system,
            )


def create_client(
    provider: str = "anthropic",
    api_key: str | None = None,
    ollama_host: str = "http://localhost:11434",
    ollama_model: str = DEFAULT_OLLAMA_MODEL,
    claude_cli_model: str = "opus",
    tracker: UsageTracker | None = None,
) -> LLMClient:
    """Factory function to create an LLM client based on provider name."""
    if provider == "anthropic":
        return AnthropicClient(api_key=api_key, tracker=tracker)
    if provider == "ollama":
        return OllamaClient(host=ollama_host, default_model=ollama_model, tracker=tracker)
    if provider == "claude-cli":
        return ClaudeCLIClient(model=claude_cli_model, tracker=tracker)
    raise ValueError(f"Unknown provider: {provider!r}. Use 'anthropic', 'ollama', or 'claude-cli'.")


def _model_identifier(
    provider: str, ollama_model: str, claude_cli_model: str, anthropic_model: str
) -> str:
    """Model identifier passed back into complete() calls.

    Must be valid for the underlying provider — do NOT wrap in display text.
    For claude-cli this is the bare alias ('opus'); ClaudeCLIClient will log
    the friendlier form.
    """
    if provider == "ollama":
        return ollama_model
    if provider == "claude-cli":
        return claude_cli_model
    return anthropic_model


def create_hybrid_clients(
    extraction_provider: str = "ollama",
    synthesis_provider: str = "ollama",
    api_key: str | None = None,
    ollama_host: str = "http://localhost:11434",
    ollama_model: str = DEFAULT_OLLAMA_MODEL,
    claude_cli_model: str = "opus",
    claude_cli_timeout_s: int = 60,
    claude_cli_fallback: bool = True,
    tracker: UsageTracker | None = None,
) -> tuple[LLMClient, str, LLMClient, str]:
    """Create separate clients for extraction and synthesis stages.

    Returns: (extraction_client, extraction_model, synthesis_client, synthesis_model)

    If ANTHROPIC_API_KEY is set but providers are both "ollama",
    auto-upgrades synthesis to Anthropic Sonnet for better quality.

    When `claude-cli` is the chosen provider and `claude_cli_fallback=True`,
    each client is wrapped in a FallbackClient that falls back to a local
    Ollama call if the CLI fails or times out.
    """
    tracker = tracker or UsageTracker()

    # Auto-upgrade: if API key exists and no explicit synthesis provider override,
    # use Anthropic for synthesis (higher quality)
    if api_key and synthesis_provider == "ollama" and extraction_provider == "ollama":
        synthesis_provider = "anthropic"

    def _build(provider: str) -> LLMClient:
        client = create_client(
            provider=provider,
            api_key=api_key,
            ollama_host=ollama_host,
            ollama_model=ollama_model,
            claude_cli_model=claude_cli_model,
            tracker=tracker,
        )
        # Set per-call timeout on claude-cli
        if isinstance(client, ClaudeCLIClient):
            client._timeout_s = claude_cli_timeout_s  # noqa: SLF001
        # Wrap claude-cli in a fallback to local Ollama
        if provider == "claude-cli" and claude_cli_fallback:
            fallback = OllamaClient(host=ollama_host, default_model=ollama_model, tracker=tracker)
            return FallbackClient(
                primary=client,
                secondary=fallback,
                secondary_model=ollama_model,
                tracker=tracker,
            )
        return client

    extraction_client = _build(extraction_provider)
    extraction_model = _model_identifier(
        extraction_provider, ollama_model, claude_cli_model, HAIKU_MODEL
    )

    synthesis_client = _build(synthesis_provider)
    synthesis_model = _model_identifier(
        synthesis_provider, ollama_model, claude_cli_model, SONNET_MODEL
    )

    return extraction_client, extraction_model, synthesis_client, synthesis_model
