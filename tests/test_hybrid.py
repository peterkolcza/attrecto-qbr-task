"""Tests for hybrid LLM client creation and auto-upgrade logic."""

from __future__ import annotations

from unittest.mock import patch

from qbr.llm import (
    HAIKU_MODEL,
    SONNET_MODEL,
    UsageTracker,
    create_hybrid_clients,
)


class TestHybridClients:
    @patch("qbr.llm.anthropic")
    def test_all_ollama_default(self, _mock):
        """Without API key, both clients should be Ollama."""
        ext_client, ext_model, syn_client, syn_model = create_hybrid_clients(
            extraction_provider="ollama",
            synthesis_provider="ollama",
            ollama_model="gemma4:e2b",
        )
        assert ext_client.provider_name() == "ollama"
        assert syn_client.provider_name() == "ollama"
        assert ext_model == "gemma4:e2b"
        assert syn_model == "gemma4:e2b"

    @patch("qbr.llm.anthropic")
    def test_auto_upgrade_with_api_key(self, _mock):
        """With API key and both providers ollama, synthesis auto-upgrades to anthropic."""
        ext_client, ext_model, syn_client, syn_model = create_hybrid_clients(
            extraction_provider="ollama",
            synthesis_provider="ollama",
            api_key="sk-ant-api03-test",
            ollama_model="gemma4:e2b",
        )
        assert ext_client.provider_name() == "ollama"
        assert syn_client.provider_name() == "anthropic"
        assert ext_model == "gemma4:e2b"
        assert syn_model == SONNET_MODEL

    @patch("qbr.llm.anthropic")
    def test_explicit_providers(self, _mock):
        """Explicit provider overrides should be respected."""
        ext_client, ext_model, syn_client, syn_model = create_hybrid_clients(
            extraction_provider="ollama",
            synthesis_provider="anthropic",
            api_key="sk-ant-api03-test",
            ollama_model="gemma4:e2b",
        )
        assert ext_client.provider_name() == "ollama"
        assert syn_client.provider_name() == "anthropic"
        assert ext_model == "gemma4:e2b"
        assert syn_model == SONNET_MODEL

    @patch("qbr.llm.anthropic")
    def test_all_anthropic(self, _mock):
        """Both providers anthropic → Haiku extraction, Sonnet synthesis."""
        ext_client, ext_model, syn_client, syn_model = create_hybrid_clients(
            extraction_provider="anthropic",
            synthesis_provider="anthropic",
            api_key="sk-ant-api03-test",
        )
        assert ext_client.provider_name() == "anthropic"
        assert syn_client.provider_name() == "anthropic"
        assert ext_model == HAIKU_MODEL
        assert syn_model == SONNET_MODEL

    def test_shared_tracker(self):
        """Both clients should share the same usage tracker."""
        tracker = UsageTracker()
        ext_client, _, syn_client, _ = create_hybrid_clients(
            extraction_provider="ollama",
            synthesis_provider="ollama",
            tracker=tracker,
        )
        assert ext_client.tracker is tracker
        assert syn_client.tracker is tracker
