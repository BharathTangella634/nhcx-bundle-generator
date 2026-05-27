"""
Core ReAct Agent - Main orchestrator loop
==========================================
Implements the ReAct (Reason + Act) pattern:
  Loop: LLM reasons → calls tool → observes result → reasons again → ...
  Until: task complete OR max iterations reached

Key design decisions:
  - LLM is NEVER forced to use tools - it can think/plan freely
  - Errors are caught per-tool, never crash the loop
  - Context is compressed DYNAMICALLY based on actual usage
  - Consecutive failures trigger escalation, not just retry
  - Iteration context is injected so LLM can self-manage
"""

import os
import re
import json
import time
import traceback
import logging
from datetime import datetime

from agent.llm_client import LocalLLMClient
from agent.tools import ToolRegistry
from agent.context_manager import ContextManager
from agent.checkpoint import CheckpointManager

logger = logging.getLogger("nhcx_agent.core")


class ReActAgent:
    """
    The main ReAct loop orchestrator.

    The agent runs autonomously: it decides what to do, when to use tools,
    when to think, and when to declare completion. The loop only intervenes
    for error recovery, context management, and checkpointing.
    """

    def __init__(self, project_root: str, settings: dict):
        self.project_root = os.path.abspath(project_root)
        self.settings = settings
        self.iteration = 0
        self.start_time = None
        self.is_complete = False
        self.completion_message = ""

        # Error tracking for resilience
        self.consecutive_errors = 0
        self.consecutive_empty = 0
        self.consecutive_text_only = 0
        self.total_errors = 0
        self.MAX_CONSECUTIVE_ERRORS = 5
        self.MAX_TEXT_ONLY_BEFORE_NUDGE = 5

        # Validation tracking — prevents false completion claims
        self.last_validator_passed = False
        self.last_validator_result_summary = ""
        self.validator_run_count = 0
        self.consecutive_validator_passes = 0

        # Initialize components
        llm_cfg = settings.get("llm", {})
        agent_cfg = settings.get("agent", {})
        paths_cfg = settings.get("paths", {})

        # 1. LLM Client
        self.llm_client = LocalLLMClient(
            base_url=llm_cfg.get("base_url", "http://localhost:8090/v1"),
            model=llm_cfg.get("model", "gpt-oss-120b"),
            api_key=llm_cfg.get("api_key", "not-needed"),
            temperature=llm_cfg.get("temperature", 0.7),
            max_tokens=llm_cfg.get("max_tokens", 4096),
            request_timeout=llm_cfg.get("request_timeout", 300),
        )

        # 2. Context Manager
        self.context_manager = ContextManager(
            context_window_tokens=agent_cfg.get("context_window_tokens", 12000),
            compression_threshold=agent_cfg.get("compression_threshold", 0.80),
            history_summary_max_chars=agent_cfg.get("history_summary_max_chars", 500),
            auto_compress_output_threshold=agent_cfg.get("auto_compress_tool_output_threshold", 3000),
            llm_client=self.llm_client,
        )

        # 3. Tool Registry
        self.tool_registry = ToolRegistry(
            project_root=self.project_root,
            settings=settings,
            llm_client=self.llm_client,
            context_manager=self.context_manager,
        )

        # 4. Checkpoint Manager
        checkpoints_dir = os.path.join(self.project_root, paths_cfg.get("checkpoints_dir", "workspace/checkpoints"))
        self.checkpoint_manager = CheckpointManager(checkpoints_dir)

        # Wire up checkpoint tools to actual implementations
        self.tool_registry.tool_map["save_checkpoint"] = self._tool_save_checkpoint
        self.tool_registry.tool_map["load_checkpoint"] = self._tool_load_checkpoint

        # Agent config
        self.max_iterations = agent_cfg.get("max_iterations", 50)
        self.checkpoint_interval = agent_cfg.get("checkpoint_interval", 10)
        self.exit_keywords = [kw.lower() for kw in agent_cfg.get("exit_keywords", [
            "task complete", "validation successful", "zero errors", "0 errors"
        ])]

        # Logging setup
        logs_dir = os.path.join(self.project_root, paths_cfg.get("logs_dir", "logs"))
        os.makedirs(logs_dir, exist_ok=True)
        self.trace_file = os.path.join(logs_dir, "agent_trace.txt")
        self.error_file = os.path.join(logs_dir, "errors.txt")

        # Create output directories
        for d in ["generated_dir", "checkpoints_dir"]:
            dir_path = os.path.join(self.project_root, paths_cfg.get(d, f"workspace/{d}"))
            os.makedirs(dir_path, exist_ok=True)

        logger.info("ReActAgent initialized")

    def load_system_prompt(self, prompt_path: str = None) -> str:
        """Load and configure the system prompt."""
        if not prompt_path:
            prompt_path = os.path.join(self.project_root, "config", "system_prompt.txt")

        with open(prompt_path, "r", encoding="utf-8") as f:
            prompt = f.read()

        prompt = prompt.replace("{workspace_root}", self.project_root)
        self.context_manager.set_system_message(prompt)
        return prompt

    def run(self, pdf_path: str = None) -> dict:
        """Execute the main ReAct loop. Returns run results dict."""
        self.start_time = datetime.now()
        self._log_trace(f"═══ AGENT RUN STARTED: {self.start_time.isoformat()} ═══\n")

        # Load system prompt and tool definitions
        self.load_system_prompt()
        tool_defs = self.tool_registry.get_tool_definitions()

        print(f"\n{'═'*60}")
        print(f"  NHCX Bundle Generator Agent")
        print(f"  Model: {self.settings['llm']['model']}")
        print(f"  Max iterations: {self.max_iterations}")
        print(f"  Tools: {len(tool_defs)}")
        print(f"  Context window: {self.settings['agent']['context_window_tokens']} tokens")
        if pdf_path:
            print(f"  Target PDF: {pdf_path}")
        print(f"{'═'*60}\n")

        # Test LLM connection
        print("Testing LLM connection...", end=" ", flush=True)
        if not self.llm_client.test_connection():
            print("FAILED!")
            return {"status": "error", "message": "LLM connection failed"}
        print("OK ✓\n")

        # Kick off the agent with an open-ended initial message
        initial_message = (
            "Begin the NHCX Insurance Plan Bundle generation task. "
            "You have full autonomy. Discover the workspace, understand the requirements, "
        )
        if pdf_path:
            initial_message += f"extract the PDF content from '{pdf_path}', "
        else:
            initial_message += "extract the PDF content, "
            
        initial_message += (
            "study the rulebooks and example bundle, "
            "write Python code to generate the bundle, validate it, and fix any errors. "
            "Take it step by step. Start now."
        )
        
        self.context_manager.add_user_message(initial_message)

        # ═══════════════════════════════════════
        # MAIN REACT LOOP
        # ═══════════════════════════════════════
        while self.iteration < self.max_iterations and not self.is_complete:
            self.iteration += 1
            ctx_status = self.context_manager.get_context_status()
            elapsed = (datetime.now() - self.start_time).total_seconds()

            print(f"\n{'─'*55}")
            print(f"  ITERATION {self.iteration}/{self.max_iterations} "
                  f"| Context: {ctx_status['usage_percentage']}% "
                  f"| Time: {elapsed:.0f}s")
            print(f"{'─'*55}")

            try:
                # Force compression if context is critical
                if ctx_status["status"] == "CRITICAL":
                    print("  ⚠️  Context CRITICAL — forcing compression")
                    self.context_manager._compress_history()
                    self._auto_checkpoint()

                # Build messages and call LLM
                messages = self.context_manager.get_messages_for_api()
                response = self.llm_client.chat(messages, tools=tool_defs)
                self.consecutive_errors = 0  # Reset on successful LLM call

                # Route based on response type
                has_tool_calls = bool(response.get("tool_calls"))
                has_content = bool(response.get("content"))

                if has_tool_calls:
                    # LLM wants to use tools — execute them
                    if has_content:
                        print(f"  💭 Thinking: {response['content'][:120]}...")
                    self._handle_tool_calls(response)
                    self.consecutive_empty = 0
                    self.consecutive_text_only = 0

                elif has_content:
                    # LLM gave a text response — could be thinking, planning, or done
                    self._handle_text_response(response)
                    self.consecutive_empty = 0

                else:
                    # Empty response — nudge gently
                    self.consecutive_empty += 1
                    self.context_manager.add_assistant_message(content="(empty response)")
                    if self.consecutive_empty >= 3:
                        self.context_manager.add_user_message(
                            "You've returned empty responses. Please use a tool to make progress, "
                            "or explain what you need."
                        )
                    else:
                        self.context_manager.add_user_message("Continue.")

                # Auto-checkpoint at intervals
                if self.iteration % self.checkpoint_interval == 0:
                    self._auto_checkpoint()

            except KeyboardInterrupt:
                print("\n  🛑 User interrupted. Saving checkpoint...")
                self._auto_checkpoint()
                break

            except Exception as e:
                self.consecutive_errors += 1
                self.total_errors += 1
                error_msg = f"Iteration {self.iteration}: {type(e).__name__}: {e}"
                logger.error(error_msg)
                self._log_error(error_msg)
                print(f"  ❌ ERROR: {e}")

                if self.consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                    print(f"  🛑 {self.MAX_CONSECUTIVE_ERRORS} consecutive errors. Stopping.")
                    self._auto_checkpoint()
                    break

                # Gentle recovery — don't flood context with error messages
                self.context_manager.add_user_message(
                    f"A system error occurred (attempt {self.consecutive_errors}/"
                    f"{self.MAX_CONSECUTIVE_ERRORS}): {str(e)[:200]}. "
                    f"Please try a different approach or simpler tool call."
                )
                time.sleep(1)  # Brief pause before retry

        # ═══════════════════════════════════════
        # RUN COMPLETE
        # ═══════════════════════════════════════
        end_time = datetime.now()
        duration = (end_time - self.start_time).total_seconds()

        if self.consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
            status = "error_limit"
        elif self.is_complete:
            status = "complete"
        else:
            status = "max_iterations"

        result = {
            "status": status,
            "iterations": self.iteration,
            "duration_seconds": round(duration, 1),
            "completion_message": self.completion_message,
            "total_errors": self.total_errors,
            "llm_usage": self.llm_client.get_usage_stats(),
            "context_status": self.context_manager.get_context_status(),
        }

        print(f"\n{'═'*60}")
        print(f"  AGENT RUN COMPLETE")
        print(f"  Status: {status}")
        print(f"  Iterations: {self.iteration}")
        print(f"  Duration: {duration:.1f}s")
        print(f"  Errors: {self.total_errors}")
        print(f"  LLM calls: {result['llm_usage']['total_calls']}")
        if self.completion_message:
            print(f"  Message: {self.completion_message[:200]}")
        print(f"{'═'*60}\n")

        self._log_trace(f"\n═══ RUN COMPLETE: {json.dumps(result, indent=2, default=str)} ═══\n")
        self._auto_checkpoint()
        return result

    def _handle_tool_calls(self, response: dict):
        """Execute tool calls from LLM response. Each tool is isolated — one failure won't crash others."""
        tool_calls = response["tool_calls"]

        self.context_manager.add_assistant_message(
            content=response.get("content"),
            tool_calls=tool_calls
        )

        for tc in tool_calls:
            tool_name = tc["name"]
            tool_args = tc["arguments"]
            tool_id = tc["id"]

            print(f"  🔧 {tool_name}({self._format_args(tool_args)})")
            self._log_trace(f"  TOOL: {tool_name}({json.dumps(tool_args, default=str)[:300]})\n")

            # Execute with per-tool error isolation
            start = time.time()
            try:
                result = self.tool_registry.execute(tool_name, tool_args)
            except Exception as e:
                result = f"TOOL ERROR: {type(e).__name__}: {e}"
                logger.error(f"Tool {tool_name} crashed: {e}")

            elapsed = time.time() - start
            preview = result[:200].replace("\n", " ")
            print(f"  📋 → {preview}{'...' if len(result) > 200 else ''} ({elapsed:.1f}s)")
            self._log_trace(f"  RESULT ({elapsed:.1f}s): {result[:500]}\n")

            # Track validator results to prevent false completion
            if tool_name == "run_fhir_validator":
                self.validator_run_count += 1
                if "VALIDATION SUCCESSFUL" in result and "0 errors" in result:
                    self.last_validator_passed = True
                    self.last_validator_result_summary = "0 errors"
                    self.consecutive_validator_passes += 1
                    print(f"  ✅ Validator PASSED (0 errors) [{self.consecutive_validator_passes} consecutive]")
                else:
                    self.last_validator_passed = False
                    self.consecutive_validator_passes = 0
                    err_match = re.search(r'(\d+)\s+errors?', result)
                    err_count = err_match.group(1) if err_match else "unknown"
                    self.last_validator_result_summary = f"{err_count} errors"
                    print(f"  ❌ Validator FAILED ({err_count} errors)")

            self.context_manager.add_tool_result(tool_id, tool_name, result)

        # Check for completion in the LLM's thinking text alongside tool calls
        content = response.get("content", "")
        if content and "task complete: bundle generated with zero validation errors" in content.lower():
            if self.last_validator_passed and self.validator_run_count > 0:
                cheat_check = self._check_bundle_not_example_copy()
                if cheat_check:
                    print(f"  ⚠️  CHEAT DETECTED: {cheat_check}")
                    self.context_manager.add_user_message(cheat_check)
                    self.last_validator_passed = False
                    return
                self.is_complete = True
                self.completion_message = content[:500]
                print(f"  ✅ TASK COMPLETE verified (from tool-call thinking text)")
                return

        # Auto-complete: if validator passed 3+ consecutive times, the LLM is stuck
        if self.consecutive_validator_passes >= 3:
            cheat_check = self._check_bundle_not_example_copy()
            if not cheat_check:
                self.is_complete = True
                self.completion_message = (
                    f"Auto-completed: validator passed {self.consecutive_validator_passes} "
                    f"consecutive times with 0 errors."
                )
                print(f"  ✅ AUTO-COMPLETE: Validator passed {self.consecutive_validator_passes}x consecutively")
                return
        elif self.consecutive_validator_passes >= 2:
            self.context_manager.add_user_message(
                "The FHIR validator has passed with 0 errors. The bundle is valid. "
                "Stop running more tools. Respond with ONLY this text: "
                "\"TASK COMPLETE: Bundle generated with zero validation errors.\""
            )

    def _handle_text_response(self, response: dict):
        """Handle text-only response. Let LLM think freely — don't force tools."""
        content = response["content"]
        print(f"  💬 {content[:300]}{'...' if len(content) > 300 else ''}")
        self._log_trace(f"  AGENT: {content[:500]}\n")

        self.context_manager.add_assistant_message(content=content)
        self.consecutive_text_only += 1

        # Check for GENUINE completion signals
        content_lower = content.lower()
        if "task complete: bundle generated with zero validation errors" in content_lower:
            # CRITICAL: Verify the last validator run actually passed
            if self.last_validator_passed and self.validator_run_count > 0:
                # Anti-cheat: verify the bundle isn't just a copy of an example
                cheat_check = self._check_bundle_not_example_copy()
                if cheat_check:
                    print(f"  ⚠️  CHEAT DETECTED: {cheat_check}")
                    self.context_manager.add_user_message(cheat_check)
                    self.last_validator_passed = False
                    return

                self.is_complete = True
                self.completion_message = content[:500]
                print(f"  ✅ TASK COMPLETE verified (validator passed with 0 errors)")
                return
            else:
                # LLM is hallucinating completion — reject and force re-validation
                if self.validator_run_count == 0:
                    rejection = (
                        "STOP. You claimed TASK COMPLETE but you have NEVER run the FHIR validator. "
                        "You MUST run run_fhir_validator on the bundle before declaring completion. "
                        "Do it now."
                    )
                else:
                    rejection = (
                        f"STOP. You claimed TASK COMPLETE but the last validator run showed "
                        f"{self.last_validator_result_summary}. You CANNOT declare completion "
                        f"with errors remaining. Run run_fhir_validator again to check the "
                        f"current state, then fix ALL errors before declaring completion."
                    )
                print(f"  ⚠️  FALSE COMPLETION rejected — {self.last_validator_result_summary}")
                self.context_manager.add_user_message(rejection)
                return

        # Nudge if the agent is stuck in a text-only loop
        if self.consecutive_text_only >= self.MAX_TEXT_ONLY_BEFORE_NUDGE:
            self.context_manager.add_user_message(
                f"You have given {self.consecutive_text_only} text responses in a row without using any tools. "
                "Stop planning and ACT. Use a tool right now to make concrete progress. "
                "For example: list_directory, read_file, write_file, run_terminal, or run_fhir_validator."
            )
            self.consecutive_text_only = 0
        elif len(content.strip()) < 50 and not any(w in content_lower for w in
                ["let me", "i will", "next", "now", "plan", "step", "thinking", "analyzing"]):
            self.context_manager.add_user_message("Continue. Use tools to make progress.")
        # Otherwise, let the LLM's next turn happen naturally — it will
        # decide on its own whether to call a tool or continue reasoning.

    def _check_bundle_not_example_copy(self) -> str:
        """Check that the generated bundle isn't just a copy of an example. Returns error message or None."""
        import hashlib
        generated_path = os.path.join(self.project_root, "workspace", "generated", "InsurancePlanBundle.json")
        examples_dir = os.path.join(self.project_root, "workspace", "examples")

        if not os.path.exists(generated_path):
            return "STOP. The generated bundle file does not exist at workspace/generated/InsurancePlanBundle.json."

        try:
            with open(generated_path, "rb") as f:
                gen_hash = hashlib.md5(f.read()).hexdigest()

            for fname in os.listdir(examples_dir):
                if fname.endswith(".json"):
                    example_path = os.path.join(examples_dir, fname)
                    with open(example_path, "rb") as f:
                        ex_hash = hashlib.md5(f.read()).hexdigest()
                    if gen_hash == ex_hash:
                        return (
                            f"STOP. The generated bundle is an EXACT COPY of the example file '{fname}'. "
                            f"You CANNOT pass off an example bundle as your generated output. "
                            f"You MUST generate the bundle from the actual PDF policy data. "
                            f"Go back to your generate_bundle.py script, fix the validation errors, "
                            f"and regenerate. Do NOT copy example files."
                        )
        except Exception as e:
            logger.warning(f"Bundle copy check failed: {e}")

        return None

    def _auto_checkpoint(self):
        """Save automatic checkpoint. Never crashes."""
        try:
            state = {
                "iteration": self.iteration,
                "is_complete": self.is_complete,
                "completion_message": self.completion_message,
                "total_errors": self.total_errors,
                "context_state": self.context_manager.get_state(),
                "llm_usage": self.llm_client.get_usage_stats(),
            }
            name = f"auto_iter_{self.iteration:03d}"
            self.checkpoint_manager.save(state, name)
            print(f"  💾 Checkpoint: {name}")
        except Exception as e:
            logger.warning(f"Checkpoint failed: {e}")

    def _tool_save_checkpoint(self, checkpoint_name: str = None) -> str:
        """Tool: agent-initiated checkpoint."""
        try:
            state = {
                "iteration": self.iteration,
                "is_complete": self.is_complete,
                "context_state": self.context_manager.get_state(),
            }
            name = checkpoint_name or f"agent_iter_{self.iteration:03d}"
            return self.checkpoint_manager.save(state, name)
        except Exception as e:
            return f"ERROR: {e}"

    def _tool_load_checkpoint(self, checkpoint_name: str = None) -> str:
        """Tool: agent-initiated checkpoint restore."""
        try:
            state = self.checkpoint_manager.load(checkpoint_name)
            self.iteration = state.get("iteration", self.iteration)
            if "context_state" in state:
                self.context_manager.load_state(state["context_state"])
            return f"Checkpoint loaded: iteration {self.iteration}"
        except Exception as e:
            return f"ERROR: {e}"

    def _format_args(self, args: dict) -> str:
        """Format tool arguments for compact display."""
        parts = []
        for k, v in args.items():
            s = str(v)
            if len(s) > 50:
                s = s[:50] + "…"
            parts.append(f"{k}={s}")
        return ", ".join(parts)

    def _log_trace(self, msg: str):
        try:
            with open(self.trace_file, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat()}] {msg}")
        except Exception:
            pass

    def _log_error(self, msg: str):
        try:
            with open(self.error_file, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat()}] {msg}\n")
        except Exception:
            pass
