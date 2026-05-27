"""
LLM Client - Wrapper around local OpenAI-compatible LLM
========================================================
Handles all communication with the local LLM server.
Supports tool/function calling, streaming, and error retry.
"""

import json
import time
import logging
from typing import Optional
from openai import OpenAI

logger = logging.getLogger("nhcx_agent.llm_client")


class LocalLLMClient:
    """
    Wrapper around a local LLM server exposing an OpenAI-compatible API.
    
    Responsibilities:
    - Manages the OpenAI client connection
    - Sends messages with tool definitions
    - Parses tool calls from responses
    - Handles retries on transient failures
    - Tracks token usage per call
    """

    def __init__(self, base_url: str, model: str, api_key: str = "not-needed",
                 temperature: float = 0.7, max_tokens: int = 4096,
                 request_timeout: int = 300):
        """
        Initialize the LLM client.
        
        Args:
            base_url: URL of the local LLM server (e.g., http://localhost:8090/v1)
            model: Model name to use
            api_key: API key (usually not needed for local models)
            temperature: Sampling temperature (0.0 = deterministic, 1.0 = creative)
            max_tokens: Maximum tokens in the response
            request_timeout: Request timeout in seconds
        """
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.request_timeout = request_timeout

        self.client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=request_timeout,
        )

        # Usage tracking
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_calls = 0

        logger.info(f"LLM Client initialized: {base_url} | model={model}")

    def chat(self, messages: list, tools: Optional[list] = None,
             tool_choice: str = "auto", retry_count: int = 3,
             retry_delay: float = 2.0) -> dict:
        """
        Send a chat completion request to the LLM.
        
        Args:
            messages: List of message dicts (role, content)
            tools: Optional list of tool definitions (OpenAI function calling format)
            tool_choice: How the model should use tools ("auto", "none", "required")
            retry_count: Number of retries on failure
            retry_delay: Delay between retries in seconds
            
        Returns:
            dict with keys:
                - content: The text response (may be None if tool call)
                - tool_calls: List of tool call dicts (name, arguments)
                - usage: Token usage dict (prompt_tokens, completion_tokens, total_tokens)
                - finish_reason: Why the model stopped ("stop", "tool_calls", "length")
                - raw_response: The full response object
        """
        last_error = None

        for attempt in range(retry_count):
            try:
                # Build request kwargs
                kwargs = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                }

                if tools and len(tools) > 0:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = tool_choice

                logger.debug(f"LLM call attempt {attempt + 1}/{retry_count} "
                           f"| messages={len(messages)} | tools={len(tools) if tools else 0}")

                response = self.client.chat.completions.create(**kwargs)

                # Extract response data
                choice = response.choices[0]
                message = choice.message

                # Parse tool calls
                tool_calls = []
                if message.tool_calls:
                    for tc in message.tool_calls:
                        try:
                            args = json.loads(tc.function.arguments)
                        except json.JSONDecodeError:
                            # Try to fix common JSON issues
                            args_str = tc.function.arguments
                            logger.warning(f"Failed to parse tool args, raw: {args_str[:200]}")
                            args = {"_raw_args": args_str}

                        tool_calls.append({
                            "id": tc.id,
                            "name": tc.function.name,
                            "arguments": args,
                        })

                # Track usage
                usage = {}
                if response.usage:
                    usage = {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens,
                    }
                    self.total_prompt_tokens += response.usage.prompt_tokens
                    self.total_completion_tokens += response.usage.completion_tokens

                self.total_calls += 1

                # Sanitize content to strip model-internal special tokens
                # The llama.cpp model sometimes leaks <|end|><|start|>assistant<|channel|>...
                # tokens into its output. If these get stored in context and sent back,
                # the server throws a 500 parse error.
                sanitized_content = self._sanitize_content(message.content)

                result = {
                    "content": sanitized_content,
                    "tool_calls": tool_calls,
                    "usage": usage,
                    "finish_reason": choice.finish_reason,
                    "raw_response": response,
                }

                logger.info(f"LLM response: finish_reason={choice.finish_reason} "
                          f"| tool_calls={len(tool_calls)} "
                          f"| usage={usage}")

                return result

            except Exception as e:
                last_error = e
                err_str = str(e)
                is_loading = "503" in err_str or "Loading model" in err_str or "unavailable" in err_str.lower()
                logger.warning(f"LLM call failed (attempt {attempt + 1}/{retry_count}): {e}")
                if attempt < retry_count - 1:
                    # Use longer delay for model-loading errors
                    delay = (retry_delay * (attempt + 1) * 5) if is_loading else (retry_delay * (attempt + 1))
                    if is_loading:
                        logger.info(f"Model loading — waiting {delay:.0f}s before retry")
                    time.sleep(delay)

        # All retries failed
        logger.error(f"LLM call failed after {retry_count} attempts: {last_error}")
        raise RuntimeError(f"LLM call failed after {retry_count} attempts: {last_error}")

    def simple_complete(self, prompt: str, system_prompt: str = None) -> str:
        """
        Simple text completion without tool calling.
        Useful for compression/summarization tasks.
        
        Args:
            prompt: The user prompt
            system_prompt: Optional system message
            
        Returns:
            The LLM's text response
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        result = self.chat(messages, tools=None)
        return result["content"] or ""

    def get_usage_stats(self) -> dict:
        """Get cumulative token usage statistics."""
        return {
            "total_calls": self.total_calls,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
        }

    def test_connection(self, max_wait: int = 120) -> bool:
        """Test if the LLM server is reachable. Waits up to max_wait seconds for model loading."""
        import time as _time
        start = _time.time()
        attempt = 0
        while _time.time() - start < max_wait:
            attempt += 1
            try:
                result = self.simple_complete("Say 'OK' if you can hear me.")
                logger.info(f"LLM connection test: OK on attempt {attempt} (response: {result[:50]})")
                return True
            except Exception as e:
                err_str = str(e)
                if "503" in err_str or "Loading model" in err_str or "unavailable" in err_str.lower():
                    elapsed = _time.time() - start
                    remaining = max_wait - elapsed
                    if remaining > 0:
                        wait = min(10, remaining)
                        logger.info(f"Model still loading (attempt {attempt}, {elapsed:.0f}s elapsed). "
                                    f"Retrying in {wait:.0f}s...")
                        print(f"  Model loading... retrying in {wait:.0f}s ({elapsed:.0f}s/{max_wait}s)")
                        _time.sleep(wait)
                        continue
                logger.error(f"LLM connection test FAILED: {e}")
                return False
        logger.error(f"LLM connection test timed out after {max_wait}s")
        return False

    def _sanitize_content(self, content: str) -> str:
        """
        Strip out model-internal special tokens that leak into the generation.
        If these tokens are sent back to the model in the message history, 
        llama-server triggers a 500 parse error.
        """
        if not content:
            return content
            
        # Common tokens that leak from Llama 3 / Qwen / Mistral / Gemma instruction formats
        bad_tokens = [
            "<|end|>",
            "<|start|>",
            "<|channel|>",
            "<|constrain|>",
            "<|message|>",
            "<|im_end|>",
            "<|im_start|>",
            "<|endoftext|>",
        ]
        
        sanitized = content
        for token in bad_tokens:
            sanitized = sanitized.replace(token, "")
            
        return sanitized
