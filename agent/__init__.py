"""
NHCX Bundle Generator Agent Framework
=====================================
A local agentic framework implementing the ReAct pattern (Reason + Act)
to generate zero-error NHCX FHIR Insurance Plan Bundles from insurance policy PDFs.

Components:
- llm_client: Wrapper around local LLM (OpenAI-compatible API)
- tools: Complete tool library (15+ tools) for file I/O, terminal, search, context
- context_manager: Dynamic token tracking, compression, history management
- core: Main ReAct loop orchestrator
- checkpoint: Save/restore agent state
"""

__version__ = "1.0.0"
