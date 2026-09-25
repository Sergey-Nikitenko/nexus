"""Integrations — provider-specific adapters.

Each integration speaks a provider's protocol (MCP, OpenAI, Ollama, GitHub, ...)
and translates it into Nexus contracts. This is the ONLY layer where a provider
library may be imported; everything above consumes contracts, never provider
objects.
"""
