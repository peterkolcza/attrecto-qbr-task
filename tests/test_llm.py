"""Tests for the LLM client abstraction — all mocked, no real API calls."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from qbr.llm import (
    AnthropicClient,
    OllamaClient,
    TokenUsage,
    UsageTracker,
    create_client,
)

# --- TokenUsage tests ---


class TestTokenUsage:
    def test_total_tokens(self):
        usage = TokenUsage(input_tokens=100, output_tokens=50)
        assert usage.total_tokens == 150

    def test_cost_haiku(self):
        usage = TokenUsage(
            input_tokens=1000,
            output_tokens=500,
            model="claude-haiku-4-5-20251001",
        )
        cost = usage.estimated_cost_usd()
        # 1000 * 1.0/1M + 500 * 5.0/1M = 0.001 + 0.0025 = 0.0035
        assert abs(cost - 0.0035) < 0.0001

    def test_cost_with_cache(self):
        usage = TokenUsage(
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=800,
            model="claude-haiku-4-5-20251001",
        )
        cost = usage.estimated_cost_usd()
        # (1000-800)*1.0/1M + 500*5.0/1M + 800*0.1/1M = 0.0002 + 0.0025 + 0.00008 = 0.00278
        assert cost < 0.0035  # cheaper than without cache


# --- UsageTracker tests ---


class TestUsageTracker:
    def test_empty(self):
        tracker = UsageTracker()
        assert tracker.total_calls == 0
        assert tracker.total_cost_usd == 0.0

    def test_record_and_summary(self):
        tracker = UsageTracker()
        tracker.record(TokenUsage(input_tokens=100, output_tokens=50, model="test"))
        tracker.record(TokenUsage(input_tokens=200, output_tokens=100, model="test"))
        assert tracker.total_calls == 2
        assert tracker.total_input_tokens == 300
        assert tracker.total_output_tokens == 150
        summary = tracker.summary()
        assert summary["total_calls"] == 2


# --- AnthropicClient tests (mocked) ---


class TestAnthropicClient:
    @patch("qbr.llm.anthropic")
    def test_complete_text(self, mock_anthropic_module):
        # Mock the Anthropic client
        mock_client = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_client

        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Hello world")]
        mock_response.usage = MagicMock(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        mock_client.messages.create.return_value = mock_response

        client = AnthropicClient(api_key="test-key")
        result = client.complete(
            system="You are helpful.",
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result == "Hello world"
        assert client.tracker.total_calls == 1

    @patch("qbr.llm.anthropic")
    def test_complete_structured(self, mock_anthropic_module):
        class TestSchema(BaseModel):
            name: str
            value: int

        mock_client = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_client

        mock_response = MagicMock()
        mock_tool_block = MagicMock(type="tool_use", input={"name": "test", "value": 42})
        mock_response.content = [mock_tool_block]
        mock_response.usage = MagicMock(
            input_tokens=20,
            output_tokens=10,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        mock_client.messages.create.return_value = mock_response

        client = AnthropicClient(api_key="test-key")
        result = client.complete(
            system="Extract data.",
            messages=[{"role": "user", "content": "data here"}],
            response_schema=TestSchema,
        )

        assert result == {"name": "test", "value": 42}

    @patch("qbr.llm.anthropic")
    def test_cache_system_prompt(self, mock_anthropic_module):
        mock_client = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_client

        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="OK")]
        mock_response.usage = MagicMock(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=8,
            cache_creation_input_tokens=0,
        )
        mock_client.messages.create.return_value = mock_response

        client = AnthropicClient(api_key="test-key")
        client.complete(
            system="Cached system prompt",
            messages=[{"role": "user", "content": "test"}],
            cache_system=True,
        )

        # Verify system was sent as list with cache_control
        call_kwargs = mock_client.messages.create.call_args[1]
        assert isinstance(call_kwargs["system"], list)
        assert call_kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}


# --- OllamaClient tests (mocked) ---


class TestOllamaClient:
    @patch("qbr.llm.ollama_lib")
    def test_complete_text(self, mock_ollama):
        mock_ollama.chat.return_value = {
            "message": {"content": "Ollama says hi"},
            "prompt_eval_count": 10,
            "eval_count": 5,
        }

        client = OllamaClient()
        result = client.complete(
            system="Be helpful.",
            messages=[{"role": "user", "content": "Hello"}],
        )

        assert result == "Ollama says hi"
        assert client.tracker.total_calls == 1

    @patch("qbr.llm.ollama_lib")
    def test_complete_structured(self, mock_ollama):
        class TestSchema(BaseModel):
            items: list[str]

        mock_ollama.chat.return_value = {
            "message": {"content": '{"items": ["a", "b"]}'},
            "prompt_eval_count": 20,
            "eval_count": 10,
        }

        client = OllamaClient()
        result = client.complete(
            system="Extract items.",
            messages=[{"role": "user", "content": "data"}],
            response_schema=TestSchema,
        )

        assert result == {"items": ["a", "b"]}


# --- Factory tests ---


class TestCreateClient:
    @patch("qbr.llm.anthropic")
    def test_create_anthropic(self, mock_module):
        client = create_client(provider="anthropic", api_key="test")
        assert client.provider_name() == "anthropic"

    def test_create_ollama(self):
        client = create_client(provider="ollama")
        assert client.provider_name() == "ollama"

    def test_unknown_provider(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            create_client(provider="openai")

    def test_shared_tracker(self):
        tracker = UsageTracker()
        client = create_client(provider="ollama", tracker=tracker)
        assert client.tracker is tracker
