"""
Context Manager - Dynamic Token Tracking and Compression
=========================================================
Manages the conversation history, tracks token usage,
and dynamically compresses context to stay within budget.
"""

import json
import copy
import logging
from typing import Optional

import tiktoken

logger = logging.getLogger("nhcx_agent.context_manager")


class ContextManager:
    """
    Manages the agent's conversation context dynamically.
    
    Responsibilities:
    - Tracks all messages in the conversation
    - Counts tokens accurately using tiktoken
    - Auto-compresses old history when approaching token limits
    - Provides context status for the agent to query
    - Manages compression of long tool outputs
    """

    def __init__(self, context_window_tokens: int = 12000,
                 compression_threshold: float = 0.80,
                 history_summary_max_chars: int = 500,
                 auto_compress_output_threshold: int = 3000,
                 llm_client=None):
        """
        Initialize the context manager.
        
        Args:
            context_window_tokens: Total token budget for context
            compression_threshold: Fraction of budget that triggers compression (0.8 = 80%)
            history_summary_max_chars: Max chars for compressed history summaries
            auto_compress_output_threshold: Char count that triggers auto-compression of tool output
            llm_client: Reference to LLM client (for compress_text calls)
        """
        self.context_window_tokens = context_window_tokens
        self.compression_threshold = compression_threshold
        self.history_summary_max_chars = history_summary_max_chars
        self.auto_compress_output_threshold = auto_compress_output_threshold
        self.llm_client = llm_client

        # Message storage
        self.system_message = None        # The system prompt (always kept)
        self.messages = []                # Full conversation history
        self.compressed_summary = ""      # Summary of compressed-away messages
        self.messages_compressed_count = 0  # How many messages were compressed

        # Token tracking
        try:
            self.encoder = tiktoken.encoding_for_model("gpt-4")
        except Exception:
            self.encoder = tiktoken.get_encoding("cl100k_base")

        # Statistics
        self.total_messages_added = 0
        self.compression_events = 0

        logger.info(f"ContextManager initialized: window={context_window_tokens} tokens, "
                   f"threshold={compression_threshold}")

    def set_system_message(self, content: str):
        """Set the system message (always kept, never compressed)."""
        self.system_message = {"role": "system", "content": content}
        logger.info(f"System message set: {self.count_tokens(content)} tokens")

    def add_user_message(self, content: str):
        """Add a user message to the conversation."""
        msg = {"role": "user", "content": content}
        self.messages.append(msg)
        self.total_messages_added += 1
        self._check_and_compress()

    def add_assistant_message(self, content: str = None, tool_calls: list = None):
        """
        Add an assistant message (may include tool calls).
        
        Args:
            content: Text content of the response
            tool_calls: List of tool call dicts from the LLM response
        """
        msg = {"role": "assistant"}
        if content:
            msg["content"] = content
        if tool_calls:
            # Convert our internal format to OpenAI format for context
            formatted_tool_calls = []
            for tc in tool_calls:
                formatted_tool_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["arguments"]),
                    }
                })
            msg["tool_calls"] = formatted_tool_calls
            if "content" not in msg:
                msg["content"] = None
        self.messages.append(msg)
        self.total_messages_added += 1
        self._check_and_compress()

    def add_tool_result(self, tool_call_id: str, tool_name: str, result: str):
        """
        Add a tool result to the conversation.
        Auto-compresses if the result is too long.
        
        Args:
            tool_call_id: The ID of the tool call this result is for
            tool_name: Name of the tool that was called
            result: The output/result from the tool
        """
        # Auto-compress long outputs
        if len(result) > self.auto_compress_output_threshold:
            original_len = len(result)
            result = self._auto_compress_output(result, tool_name)
            logger.info(f"Auto-compressed tool output: {original_len} → {len(result)} chars")

        msg = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result,
        }
        self.messages.append(msg)
        self.total_messages_added += 1
        self._check_and_compress()

    def get_messages_for_api(self) -> list:
        """
        Get the complete message list ready for the API call.
        Includes system message, compressed summary (if any), and recent messages.
        
        Returns:
            List of message dicts ready for OpenAI API
        """
        api_messages = []

        # Always include system message
        if self.system_message:
            system_content = self.system_message["content"]

            # Prepend compressed summary if we have one
            if self.compressed_summary:
                system_content += (
                    f"\n\n═══ CONVERSATION SUMMARY (iterations 1-{self.messages_compressed_count}) ═══\n"
                    f"{self.compressed_summary}\n"
                    f"═══ END SUMMARY ═══"
                )

            api_messages.append({"role": "system", "content": system_content})

        # Add all current (non-compressed) messages
        for msg in self.messages:
            safe_msg = copy.deepcopy(msg)
            if safe_msg.get("role") == "assistant":
                has_content = bool(safe_msg.get("content"))
                has_tools = bool(safe_msg.get("tool_calls"))
                if not has_content and not has_tools:
                    safe_msg["content"] = "(empty response)"
            api_messages.append(safe_msg)

        return api_messages

    def count_tokens(self, text: str) -> int:
        """Count the number of tokens in a text string."""
        if not text:
            return 0
        try:
            return len(self.encoder.encode(text))
        except Exception:
            # Fallback: rough estimate of 4 chars per token
            return len(text) // 4

    def count_message_tokens(self, messages: list) -> int:
        """Count total tokens across a list of messages."""
        total = 0
        for msg in messages:
            # Each message has ~4 tokens of overhead
            total += 4
            if msg.get("content"):
                total += self.count_tokens(msg["content"])
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    if isinstance(tc, dict) and "function" in tc:
                        total += self.count_tokens(tc["function"].get("name", ""))
                        total += self.count_tokens(tc["function"].get("arguments", ""))
        return total

    def get_current_token_usage(self) -> int:
        """Get the current total token usage of the context."""
        all_messages = self.get_messages_for_api()
        return self.count_message_tokens(all_messages)

    def get_remaining_tokens(self) -> int:
        """Get how many tokens remain in the budget."""
        return self.context_window_tokens - self.get_current_token_usage()

    def get_context_status(self) -> dict:
        """
        Get detailed context status information.
        This is exposed as a tool for the agent to query.
        """
        current_usage = self.get_current_token_usage()
        remaining = self.context_window_tokens - current_usage
        usage_pct = (current_usage / self.context_window_tokens) * 100

        return {
            "context_window_tokens": self.context_window_tokens,
            "current_usage_tokens": current_usage,
            "remaining_tokens": remaining,
            "usage_percentage": round(usage_pct, 1),
            "total_messages": len(self.messages),
            "total_messages_ever": self.total_messages_added,
            "messages_compressed": self.messages_compressed_count,
            "compression_events": self.compression_events,
            "has_compressed_summary": bool(self.compressed_summary),
            "status": "CRITICAL" if usage_pct > 90 else "WARNING" if usage_pct > 75 else "OK",
        }

    def _check_and_compress(self):
        """Check if we're approaching the token limit and compress if needed."""
        current_usage = self.get_current_token_usage()
        threshold = self.context_window_tokens * self.compression_threshold

        if current_usage > threshold:
            logger.warning(f"Context approaching limit: {current_usage}/{self.context_window_tokens} "
                         f"tokens ({(current_usage/self.context_window_tokens)*100:.1f}%). Compressing...")
            self._compress_history()

    def _compress_history(self):
        """Compress older messages into a summary, preserving critical context."""
        if len(self.messages) < 10:
            logger.info("Too few messages to compress, skipping")
            return

        # Keep more recent messages — scale with context window
        # For 262K window: keep ~20 messages; for 131K: keep ~12
        base_keep = max(12, min(24, self.context_window_tokens // 12000))
        keep_count = min(base_keep, len(self.messages) - 4)

        # OpenAI API requires every 'tool' message to be preceded by its 'assistant' message
        while keep_count < len(self.messages) and self.messages[-keep_count].get("role") == "tool":
            keep_count += 1

        messages_to_compress = self.messages[:-keep_count]
        messages_to_keep = self.messages[-keep_count:]

        if not messages_to_compress:
            return

        # Build structured summary that doesn't depend on LLM quality
        # Extract critical facts directly from messages
        files_read = set()
        files_written = set()
        tools_used = []
        validator_results = []
        key_findings = []

        for msg in messages_to_compress:
            role = msg.get("role", "unknown")
            content = msg.get("content", "") or ""

            if role == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    if isinstance(tc, dict) and "function" in tc:
                        func = tc["function"]
                        name = func.get("name", "")
                        args = func.get("arguments", "{}")
                        try:
                            parsed = json.loads(args) if isinstance(args, str) else args
                        except Exception:
                            parsed = {}

                        tools_used.append(name)
                        if name == "read_file" and "path" in parsed:
                            files_read.add(parsed["path"])
                        elif name == "write_file" and "path" in parsed:
                            files_written.add(parsed["path"])

            elif role == "tool":
                if "VALIDATION SUCCESSFUL" in content:
                    validator_results.append("PASSED (0 errors)")
                elif "VALIDATION FAILED" in content:
                    import re
                    m = re.search(r'(\d+)\s+errors', content)
                    count = m.group(1) if m else "?"
                    validator_results.append(f"FAILED ({count} errors)")
                    # Preserve the error types
                    for line in content.split('\n'):
                        if line.strip().startswith('[') and 'x]' in line:
                            key_findings.append(f"Validator: {line.strip()}")

                # Capture extraction notes content
                if "extraction_notes" in content.lower() and len(content) > 200:
                    key_findings.append(f"Extraction notes written ({len(content)} chars)")

            elif role == "assistant" and content:
                # Capture the agent's key decisions and findings
                content_lower = content.lower()
                if any(w in content_lower for w in ["found", "extracted", "coverage", "exclusion",
                                                      "error", "fix", "phase", "plan"]):
                    key_findings.append(f"Agent: {content[:300]}")

        # Build the structured summary
        structured_parts = []
        structured_parts.append("=== COMPRESSION SUMMARY ===")
        structured_parts.append(f"Messages compressed: {len(messages_to_compress)}")

        if files_read:
            structured_parts.append(f"\nFiles read: {', '.join(sorted(files_read))}")
        if files_written:
            structured_parts.append(f"Files written: {', '.join(sorted(files_written))}")
        if validator_results:
            structured_parts.append(f"Validator runs: {' → '.join(validator_results)}")

        # Also build a raw text summary for LLM compression
        raw_parts = []
        for msg in messages_to_compress:
            role = msg.get("role", "unknown")
            content = msg.get("content", "") or ""
            if role == "assistant" and msg.get("tool_calls"):
                tool_names = [tc["function"]["name"] for tc in msg["tool_calls"]
                              if isinstance(tc, dict) and "function" in tc]
                raw_parts.append(f"[Tools: {', '.join(tool_names)}]")
            elif role == "tool":
                preview = content[:800] + "..." if len(content) > 800 else content
                raw_parts.append(f"[Result: {preview}]")
            elif content:
                preview = content[:500] + "..." if len(content) > 500 else content
                raw_parts.append(f"[{role}]: {preview}")

        raw_summary = "\n".join(raw_parts)

        # Use LLM to compress the raw parts, but prepend the structured facts
        llm_summary = ""
        if self.llm_client:
            try:
                # Limit input to avoid overwhelming the summarization call
                input_text = raw_summary[:40000]
                llm_summary = self.llm_client.simple_complete(
                    prompt=(
                        f"Summarize this AI agent's progress in a FHIR bundle generation task. "
                        f"Write a LONG, DETAILED summary (aim for {self.history_summary_max_chars // 2} characters minimum). "
                        f"You MUST preserve ALL specific details: file paths, coverage names, "
                        f"SNOMED codes, error messages, exclusion codes, amounts, identifiers, "
                        f"and what phase the agent is in. DO NOT be brief — detail is critical.\n\n"
                        f"Agent history:\n{input_text}"
                    ),
                    system_prompt=(
                        "You are summarizing an AI agent's work. Write a DETAILED summary. "
                        "Preserve EVERY specific fact: file paths, coverage names, SNOMED codes, "
                        "error types and counts, exclusion names, identifiers. "
                        "A longer, more detailed summary is BETTER than a short one. "
                        "Minimum 2000 characters."
                    )
                )
            except Exception as e:
                logger.warning(f"LLM compression failed: {e}")

        # Combine structured facts + LLM summary
        structured_text = "\n".join(structured_parts)
        if key_findings:
            structured_text += "\n\nKey findings:\n" + "\n".join(key_findings[:20])

        if llm_summary:
            new_summary = f"{structured_text}\n\n=== DETAILED SUMMARY ===\n{llm_summary}"
        else:
            new_summary = f"{structured_text}\n\n=== RAW HISTORY ===\n{raw_summary}"

        new_summary = new_summary[:self.history_summary_max_chars]

        # Update state — preserve previous summaries better
        if self.compressed_summary:
            combined = f"{self.compressed_summary}\n\n{new_summary}"
            self.compressed_summary = combined[:self.history_summary_max_chars]
        else:
            self.compressed_summary = new_summary

        self.messages_compressed_count += len(messages_to_compress)
        self.messages = messages_to_keep
        self.compression_events += 1

        new_usage = self.get_current_token_usage()
        logger.info(f"Compressed {len(messages_to_compress)} messages → kept {keep_count}. "
                   f"Summary: {len(self.compressed_summary)} chars. "
                   f"New usage: {new_usage}/{self.context_window_tokens} tokens")

    def _auto_compress_output(self, output: str, tool_name: str) -> str:
        """Auto-compress a long tool output to stay within budget."""
        if self.llm_client:
            try:
                compressed = self.llm_client.simple_complete(
                    prompt=(
                        f"The following is the output of tool '{tool_name}'. "
                        f"Summarize it concisely, preserving all key information "
                        f"(file names, error messages, line numbers, counts). "
                        f"Keep it under 500 characters:\n\n{output[:5000]}"
                    ),
                    system_prompt="You are a concise summarizer. Output only the summary."
                )
                return f"[AUTO-COMPRESSED from {len(output)} chars]:\n{compressed}"
            except Exception:
                pass

        # Fallback: truncate with head and tail
        head = output[:1500]
        tail = output[-500:]
        return f"[TRUNCATED from {len(output)} chars]\n--- HEAD ---\n{head}\n--- TAIL ---\n{tail}"

    def get_state(self) -> dict:
        """Get serializable state for checkpointing."""
        return {
            "system_message": self.system_message,
            "messages": copy.deepcopy(self.messages),
            "compressed_summary": self.compressed_summary,
            "messages_compressed_count": self.messages_compressed_count,
            "total_messages_added": self.total_messages_added,
            "compression_events": self.compression_events,
        }

    def load_state(self, state: dict):
        """Restore state from a checkpoint."""
        self.system_message = state.get("system_message")
        self.messages = state.get("messages", [])
        self.compressed_summary = state.get("compressed_summary", "")
        self.messages_compressed_count = state.get("messages_compressed_count", 0)
        self.total_messages_added = state.get("total_messages_added", 0)
        self.compression_events = state.get("compression_events", 0)
        logger.info(f"Context state restored: {len(self.messages)} messages, "
                   f"{self.messages_compressed_count} compressed")

    def clear(self):
        """Clear all conversation history (keep system message)."""
        self.messages = []
        self.compressed_summary = ""
        self.messages_compressed_count = 0
        logger.info("Context cleared")
