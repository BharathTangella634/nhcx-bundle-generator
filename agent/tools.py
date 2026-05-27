"""
Tool Registry - Complete tool library for the ReAct agent
==========================================================
All 15+ tools the agent can use: file I/O, terminal, search, validation, context.
"""

import os
import re
import json
import glob
import subprocess
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("nhcx_agent.tools")


class ToolRegistry:
    """Registry of all tools available to the agent."""

    def __init__(self, project_root: str, settings: dict, llm_client=None, context_manager=None):
        self.project_root = os.path.abspath(project_root)
        self.settings = settings
        self.llm_client = llm_client
        self.context_manager = context_manager
        self.tool_map = {
            "read_file": self.read_file,
            "write_file": self.write_file,
            "append_to_file": self.append_to_file,
            "patch_file": self.patch_file,
            "list_directory": self.list_directory,
            "search_in_files": self.search_in_files,
            "get_file_info": self.get_file_info,
            "extract_json_path": self.extract_json_path,
            "run_terminal": self.run_terminal,
            "python_eval": self.python_eval,
            "json_validate": self.json_validate,
            "run_fhir_validator": self.run_fhir_validator,
            "compress_text": self.compress_text,
            "get_context_status": self.get_context_status,
            "save_checkpoint": self.save_checkpoint,
            "load_checkpoint": self.load_checkpoint,
            "extract_pdf_to_markdown": self.extract_pdf_to_markdown,
            "deduplicate_bundle": self.deduplicate_bundle,
        }

    def execute(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool by name with given arguments."""
        if tool_name not in self.tool_map:
            return f"ERROR: Unknown tool '{tool_name}'. Available: {list(self.tool_map.keys())}"
        try:
            result = self.tool_map[tool_name](**arguments)
            return str(result) if result is not None else "OK"
        except Exception as e:
            logger.error(f"Tool '{tool_name}' failed: {e}")
            return f"ERROR: {type(e).__name__}: {e}"

    WRITABLE_DIRS = ["workspace/generated", "workspace/scratch", "logs"]

    def _resolve_path(self, path: str) -> str:
        """Resolve a path relative to project root, with safety checks.
        Handles common LLM mistakes like '/workspace/...' (absolute but meant relative)."""
        # Fix common LLM mistake: absolute paths starting with /workspace/, /logs/, /config/, /validator/
        if os.path.isabs(path):
            for prefix in ["/workspace/", "/logs/", "/config/", "/validator/", "/agent/"]:
                if path.startswith(prefix):
                    path = path.lstrip("/")
                    break
        if os.path.isabs(path):
            resolved = os.path.abspath(path)
        else:
            resolved = os.path.abspath(os.path.join(self.project_root, path))
        if not resolved.startswith(self.project_root):
            raise PermissionError(f"Path '{path}' is outside project root")
        return resolved

    def _resolve_write_path(self, path: str) -> str:
        """Resolve a path for writing — must be inside an allowed writable directory."""
        resolved = self._resolve_path(path)
        rel = os.path.relpath(resolved, self.project_root)
        for allowed in self.WRITABLE_DIRS:
            if rel == allowed or rel.startswith(allowed + os.sep):
                return resolved
        raise PermissionError(
            f"WRITE BLOCKED: '{rel}' is not in an allowed directory. "
            f"You can ONLY write to: {', '.join(self.WRITABLE_DIRS)}. "
            f"Use 'workspace/generated/' for final outputs (InsurancePlanBundle.json, "
            f"extraction_notes.txt, generate_bundle.py). "
            f"Use 'workspace/scratch/' for temporary/test files."
        )

    # ═══════════════════════════════════════════
    # FILE OPERATIONS
    # ═══════════════════════════════════════════

    def read_file(self, path: str, start_line: int = None, end_line: int = None,
                  search_keyword: str = None) -> str:
        """Read a file with optional partial reading and keyword search."""
        resolved = self._resolve_path(path)
        if not os.path.exists(resolved):
            return f"ERROR: File not found: {path}"

        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        if search_keyword:
            matched = []
            for i, line in enumerate(lines, 1):
                if search_keyword.lower() in line.lower():
                    ctx_start = max(0, i - 3)
                    ctx_end = min(len(lines), i + 2)
                    snippet = "".join(lines[ctx_start:ctx_end])
                    matched.append(f"--- Line {i} ---\n{snippet}")
            if matched:
                return f"Found {len(matched)} matches for '{search_keyword}':\n\n" + "\n".join(matched[:10])
            return f"No matches found for '{search_keyword}' in {path}"

        if start_line or end_line:
            start = max(1, start_line or 1) - 1
            end = min(len(lines), end_line or len(lines))
            content = "".join(lines[start:end])
            return f"[Lines {start+1}-{end} of {len(lines)}]\n{content}"

        max_chars = self.settings.get("tools", {}).get("max_file_read_chars", 10000)
        content = "".join(lines)
        if len(content) > max_chars:
            return (f"[File has {len(lines)} lines, {len(content)} chars. Showing first {max_chars} chars. "
                    f"Use start_line/end_line or search_keyword for targeted reading.]\n"
                    + content[:max_chars])
        return content

    def write_file(self, path: str, content: str, create_dirs: bool = True) -> str:
        """Write content to a file. Only allowed in: workspace/generated/, workspace/scratch/, logs/."""
        resolved = self._resolve_write_path(path)
        if create_dirs:
            os.makedirs(os.path.dirname(resolved), exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)
        size = os.path.getsize(resolved)
        return f"File written: {path} ({size} bytes, {content.count(chr(10))+1} lines)"

    def append_to_file(self, path: str, content: str) -> str:
        """Append content to an existing file. Only allowed in: workspace/generated/, workspace/scratch/, logs/."""
        resolved = self._resolve_write_path(path)
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        with open(resolved, "a", encoding="utf-8") as f:
            f.write(content)
        return f"Appended {len(content)} chars to {path}"

    def patch_file(self, path: str, search_text: str, replace_text: str) -> str:
        """Find and replace text in a file. Only allowed in: workspace/generated/, workspace/scratch/, logs/."""
        resolved = self._resolve_write_path(path)
        if not os.path.exists(resolved):
            return f"ERROR: File not found: {path}"
        with open(resolved, "r", encoding="utf-8") as f:
            content = f.read()
        count = content.count(search_text)
        if count == 0:
            return f"ERROR: Search text not found in {path}"
        new_content = content.replace(search_text, replace_text)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(new_content)
        return f"Patched {count} occurrence(s) in {path}"

    # ═══════════════════════════════════════════
    # DISCOVERY & SEARCH
    # ═══════════════════════════════════════════

    def list_directory(self, path: str = ".", pattern: str = None, recursive: bool = False) -> str:
        """List files in a directory with optional glob filtering."""
        resolved = self._resolve_path(path)
        if not os.path.isdir(resolved):
            return f"ERROR: Not a directory: {path}"

        if pattern and recursive:
            matches = glob.glob(os.path.join(resolved, "**", pattern), recursive=True)
        elif pattern:
            matches = glob.glob(os.path.join(resolved, pattern))
        else:
            matches = [os.path.join(resolved, f) for f in os.listdir(resolved)]

        entries = []
        for fp in sorted(matches):
            rel = os.path.relpath(fp, self.project_root)
            if os.path.isdir(fp):
                count = len(os.listdir(fp))
                entries.append(f"  📁 {rel}/ ({count} items)")
            else:
                size = os.path.getsize(fp)
                if size > 1024 * 1024:
                    size_str = f"{size / (1024*1024):.1f} MB"
                elif size > 1024:
                    size_str = f"{size / 1024:.1f} KB"
                else:
                    size_str = f"{size} B"
                entries.append(f"  📄 {rel} ({size_str})")

        return f"Directory: {path} ({len(entries)} items)\n" + "\n".join(entries)

    def search_in_files(self, directory: str, pattern: str, file_glob: str = "*") -> str:
        """Search for a regex/text pattern across files (like grep)."""
        resolved = self._resolve_path(directory)
        if not os.path.isdir(resolved):
            return f"ERROR: Not a directory: {directory}"

        results = []
        search_files = glob.glob(os.path.join(resolved, "**", file_glob), recursive=True)

        for fp in search_files:
            if os.path.isdir(fp):
                continue
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if re.search(pattern, line, re.IGNORECASE):
                            rel = os.path.relpath(fp, self.project_root)
                            results.append(f"{rel}:{i}: {line.rstrip()}")
                            if len(results) >= 30:
                                break
            except Exception:
                continue
            if len(results) >= 30:
                break

        if results:
            return f"Found {len(results)} matches:\n" + "\n".join(results)
        return f"No matches for '{pattern}' in {directory}"

    def get_file_info(self, path: str) -> str:
        """Get file metadata: size, line count, modification time."""
        resolved = self._resolve_path(path)
        if not os.path.exists(resolved):
            return f"ERROR: File not found: {path}"

        stat = os.stat(resolved)
        size = stat.st_size
        mtime = datetime.fromtimestamp(stat.st_mtime).isoformat()

        line_count = 0
        try:
            with open(resolved, "r", encoding="utf-8", errors="replace") as f:
                line_count = sum(1 for _ in f)
        except Exception:
            pass

        return json.dumps({
            "path": path,
            "size_bytes": size,
            "size_kb": round(size / 1024, 1),
            "lines": line_count,
            "modified": mtime,
        }, indent=2)

    def extract_json_path(self, path: str, json_path: str) -> str:
        """Extract specific values from a JSON file using dot-notation or JSONPath."""
        resolved = self._resolve_path(path)
        if not os.path.exists(resolved):
            return f"ERROR: File not found: {path}"

        with open(resolved, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Simple dot-path navigation (e.g., "entry.0.resource.resourceType")
        keys = json_path.replace("[", ".").replace("]", "").split(".")
        current = data
        for key in keys:
            if not key:
                continue
            try:
                if isinstance(current, list):
                    current = current[int(key)]
                elif isinstance(current, dict):
                    current = current[key]
                else:
                    return f"ERROR: Cannot navigate '{key}' in {type(current).__name__}"
            except (KeyError, IndexError, ValueError) as e:
                return f"ERROR: Path '{json_path}' failed at '{key}': {e}"

        if isinstance(current, (dict, list)):
            result = json.dumps(current, indent=2)
            if len(result) > 5000:
                return result[:5000] + f"\n... [truncated, {len(result)} total chars]"
            return result
        return str(current)

    # ═══════════════════════════════════════════
    # EXECUTION
    # ═══════════════════════════════════════════

    def run_terminal(self, command: str, timeout: int = None, cwd: str = None) -> str:
        """Execute a shell command and return output."""
        # Block copying example bundles to generated output
        if ("cp " in command or "copy " in command) and "examples/" in command and "generated/" in command:
            return (
                "ERROR: BLOCKED. You cannot copy example bundles to the generated output. "
                "You must generate the bundle from the PDF data using your Python script. "
                "Fix the validation errors in your generated bundle instead of copying the example."
            )

        # Block redirect/tee writes outside allowed directories
        import shlex
        for pattern in [" > ", " >> ", " | tee "]:
            if pattern in command:
                after = command.split(pattern, 1)[1].strip().split()[0] if pattern in command else ""
                after = after.strip("'\"")
                if after and not any(after.startswith(d) for d in self.WRITABLE_DIRS):
                    if not after.startswith("/dev/"):
                        return (
                            f"ERROR: BLOCKED. Cannot write to '{after}'. "
                            f"Only these directories are writable: {', '.join(self.WRITABLE_DIRS)}"
                        )

        # Safety check
        blocked = self.settings.get("tools", {}).get("blocked_commands", [])
        for b in blocked:
            if b in command:
                return f"ERROR: Command blocked for safety: contains '{b}'"

        if timeout is None:
            timeout = self.settings.get("tools", {}).get("terminal_timeout", 60)

        work_dir = self._resolve_path(cwd) if cwd else self.project_root

        logger.info(f"Running terminal: {command}")
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=work_dir,
            )
            output = ""
            if result.stdout:
                output += result.stdout
            if result.stderr:
                output += f"\n[STDERR]:\n{result.stderr}"
            if result.returncode != 0:
                output += f"\n[EXIT CODE: {result.returncode}]"

            max_chars = self.settings.get("tools", {}).get("max_terminal_output_chars", 5000)
            if len(output) > max_chars:
                head = output[:max_chars // 2]
                tail = output[-(max_chars // 2):]
                output = (f"{head}\n\n... [TRUNCATED: {len(output)} total chars] ...\n\n{tail}")

            return output.strip() if output.strip() else "[No output]"

        except subprocess.TimeoutExpired:
            return f"ERROR: Command timed out after {timeout}s"

    def python_eval(self, expression: str) -> str:
        """Evaluate a Python expression and return the result. For quick computations."""
        try:
            safe_globals = {"__builtins__": {
                "len": len, "str": str, "int": int, "float": float, "list": list,
                "dict": dict, "range": range, "enumerate": enumerate, "zip": zip,
                "sorted": sorted, "sum": sum, "min": min, "max": max, "abs": abs,
                "round": round, "isinstance": isinstance, "type": type, "print": print,
                "True": True, "False": False, "None": None,
            }}
            result = eval(expression, safe_globals)
            return str(result)
        except Exception as e:
            return f"ERROR: {e}"

    # ═══════════════════════════════════════════
    # PDF EXTRACTION
    # ═══════════════════════════════════════════

    def extract_pdf_to_markdown(self, pdf_path: str, output_path: str = None) -> str:
        """Extract text from a PDF file using Docling and save as markdown."""
        resolved_pdf = self._resolve_path(pdf_path)
        if not os.path.exists(resolved_pdf):
            return f"ERROR: PDF not found: {pdf_path}"

        if not output_path:
            base = os.path.splitext(os.path.basename(resolved_pdf))[0]
            output_dir = os.path.dirname(resolved_pdf)
            output_path = os.path.join(output_dir, f"{base}.md")
        else:
            output_path = self._resolve_path(output_path)

        try:
            from docling.document_converter import DocumentConverter
            logger.info(f"Extracting PDF with Docling: {resolved_pdf}")
            converter = DocumentConverter()
            result = converter.convert(resolved_pdf)
            markdown_text = result.document.export_to_markdown()

            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(markdown_text)

            rel_out = os.path.relpath(output_path, self.project_root)
            lines = markdown_text.count('\n') + 1
            chars = len(markdown_text)
            return (f"PDF extracted successfully!\n"
                    f"  Source: {pdf_path}\n"
                    f"  Output: {rel_out}\n"
                    f"  Size: {chars} chars, {lines} lines\n"
                    f"  Preview (first 500 chars):\n{markdown_text[:500]}")
        except ImportError:
            return "ERROR: docling is not installed. Run: pip install docling"
        except Exception as e:
            logger.error(f"PDF extraction failed: {e}")
            return f"ERROR: PDF extraction failed: {type(e).__name__}: {e}"

    # ═══════════════════════════════════════════
    # VALIDATION
    # ═══════════════════════════════════════════

    def json_validate(self, content: str = None, path: str = None) -> str:
        """Validate JSON syntax. Provide either content string or file path."""
        if path:
            resolved = self._resolve_path(path)
            if not os.path.exists(resolved):
                return f"ERROR: File not found: {path}"
            with open(resolved, "r", encoding="utf-8") as f:
                content = f.read()

        if not content:
            return "ERROR: No content or path provided"

        try:
            data = json.loads(content)
            if isinstance(data, dict):
                keys = list(data.keys())[:10]
                return f"Valid JSON (object with {len(data)} keys: {keys})"
            elif isinstance(data, list):
                return f"Valid JSON (array with {len(data)} items)"
            return f"Valid JSON ({type(data).__name__})"
        except json.JSONDecodeError as e:
            return f"INVALID JSON: {e}"

    def run_fhir_validator(self, bundle_path: str, extra_args: str = "") -> str:
        """Run the FHIR validator on a bundle file. Waits for full output and parses errors."""
        resolved_bundle = self._resolve_path(bundle_path)
        validator_jar = os.path.join(
            self.project_root,
            self.settings.get("paths", {}).get("validator_jar", "validator/validator_cli.jar")
        )

        if not os.path.exists(validator_jar):
            return f"ERROR: Validator JAR not found at {validator_jar}"
        if not os.path.exists(resolved_bundle):
            return f"ERROR: Bundle not found at {bundle_path}"

        cmd_template = self.settings.get("validator", {}).get(
            "command_template",
            "java -jar {validator_jar} {bundle_path} -ig ndhm.in"
        )
        cmd = cmd_template.format(validator_jar=validator_jar, bundle_path=resolved_bundle)
        if extra_args:
            cmd += f" {extra_args}"

        timeout = self.settings.get("validator", {}).get("timeout", 600)

        # Run validator directly (not through run_terminal) to get full untruncated output
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=self.project_root,
            )
            raw_output = (result.stdout or "") + "\n" + (result.stderr or "")
        except subprocess.TimeoutExpired:
            return f"ERROR: Validator timed out after {timeout}s"

        # Save raw output for debugging
        logs_dir = os.path.join(self.project_root, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        try:
            with open(os.path.join(logs_dir, "last_validator_output.txt"), "w") as f:
                f.write(raw_output)
        except Exception:
            pass

        # Parse the summary line first (e.g., "*FAILURE*: 110 errors, 0 warnings, 41 notes")
        summary_error_count = None
        summary_status = None
        clean_output = re.sub(r'\033\[[0-9;]*m', '', raw_output)
        summary_match = re.search(
            r'\*?(success|failure|information|warning)\*?\s*:\s*(\d+)\s+errors?,\s*(\d+)\s+warnings?',
            clean_output,
            re.IGNORECASE
        )
        if summary_match:
            summary_status = summary_match.group(1).upper()
            summary_error_count = int(summary_match.group(2))

        # Parse individual error and warning lines
        errors = []
        warnings = []
        grouped_errors = {}
        grouped_warnings = {}

        for line in raw_output.split('\n'):
            # Strip ANSI escape codes FIRST
            line = re.sub(r'\033\[[0-9;]*m', '', line)
            line = line.strip()
            if not line:
                continue

            # Pattern 1: Error @ <path> (line X, colY) : <message>
            match = re.match(
                r'(Error|Warning|Information|Fatal)\s+@\s+(.*?)\s+'
                r'\(line\s+(\d+),\s*col\s*(\d+)\)'
                r'(?:\s+in\s+\S+)?'
                r'\s*:\s*(.*)',
                line
            )
            if not match:
                # Pattern 2: Error @ <path> : <message> (no line/col)
                match = re.match(
                    r'(Error|Warning|Information|Fatal)\s+@\s+(.*?)\s*:\s*(.*)',
                    line
                )
                if match:
                    severity = match.group(1).lower()
                    path = match.group(2).strip()
                    message = match.group(3).strip()
                else:
                    continue
            else:
                severity = match.group(1).lower()
                path = match.group(2).strip()
                message = match.group(5).strip()

            if severity in ("error", "fatal"):
                errors.append({"path": path, "message": message, "raw": line})
                # Normalize for grouping
                norm_msg = re.sub(r'\(line \d+, col\s*\d+\)', '', line)
                norm_msg = re.sub(r'[0-9a-fA-F\-]{32,36}', '<UUID>', norm_msg)
                norm_msg = re.sub(r'Bundle\.entry\[\d+\]', 'Bundle.entry[X]', norm_msg)
                norm_msg = re.sub(r'\.plan\[\d+\]', '.plan[X]', norm_msg)
                norm_msg = re.sub(r'\.coverage\[\d+\]', '.coverage[X]', norm_msg)
                norm_msg = re.sub(r'\.benefit\[\d+\]', '.benefit[X]', norm_msg)
                norm_msg = re.sub(r'\.extension\[\d+\]', '.extension[X]', norm_msg)
                norm_msg = re.sub(r'/\*.*?\*/', '/*...*/', norm_msg)
                grouped_errors[norm_msg] = grouped_errors.get(norm_msg, 0) + 1
            elif severity == "warning":
                warnings.append({"path": path, "message": message})
                norm_msg = re.sub(r'\(line \d+, col\s*\d+\)', '', line)
                norm_msg = re.sub(r'[0-9a-fA-F\-]{32,36}', '<UUID>', norm_msg)
                norm_msg = re.sub(r'Bundle\.entry\[\d+\]', 'Bundle.entry[X]', norm_msg)
                norm_msg = re.sub(r'/\*.*?\*/', '/*...*/', norm_msg)
                grouped_warnings[norm_msg] = grouped_warnings.get(norm_msg, 0) + 1

        total_errors = len(errors)

        # Success case: 0 errors found
        if total_errors == 0:
            # Summary line confirms 0 errors (SUCCESS, INFORMATION, or WARNING with 0 errors)
            if summary_error_count == 0:
                return f"VALIDATION SUCCESSFUL: 0 errors, {len(warnings)} warnings. (Status: {summary_status})"
            # Summary line found but says errors > 0 — trust the summary
            if summary_error_count is not None and summary_error_count > 0:
                return (
                    f"VALIDATION FAILED: Summary reports {summary_error_count} errors but parser "
                    f"could not extract them. Raw output (last 3000 chars):\n\n"
                    f"{clean_output[-3000:]}"
                )
            # No summary line found — check if validator actually ran
            has_done_line = "Done. Times:" in clean_output
            if has_done_line:
                return f"VALIDATION SUCCESSFUL: 0 errors, {len(warnings)} warnings."
            else:
                return (
                    f"VALIDATION RESULT UNCLEAR: Validator may not have completed. "
                    f"Raw output (last 2000 chars):\n{clean_output[-2000:]}"
                )

        # Build structured error report
        output_lines = [
            f"VALIDATION FAILED: {total_errors} errors, {len(warnings)} warnings.",
            f"",
            f"ERRORS GROUPED BY TYPE ({len(grouped_errors)} unique types):",
        ]
        for err, count in sorted(grouped_errors.items(), key=lambda x: x[1], reverse=True):
            output_lines.append(f"  [{count}x] {err}")

        if grouped_warnings:
            output_lines.append(f"\nWARNINGS ({len(grouped_warnings)} unique types, showing top 5):")
            for warn, count in sorted(grouped_warnings.items(), key=lambda x: x[1], reverse=True)[:5]:
                output_lines.append(f"  [{count}x] {warn}")

        # Add first few raw errors for context (LLM needs exact paths to fix)
        output_lines.append(f"\nFIRST 10 RAW ERRORS (use these paths to locate issues):")
        for err in errors[:10]:
            output_lines.append(f"  - {err['raw']}")

        output_lines.append(f"\nFix the most common error type first, then re-validate.")
        return "\n".join(output_lines)

    # ═══════════════════════════════════════════
    # BUNDLE DEDUPLICATION
    # ═══════════════════════════════════════════

    def deduplicate_bundle(self, bundle_path: str) -> str:
        """Remove duplicate entries from all arrays in a FHIR bundle JSON file."""
        resolved = self._resolve_path(bundle_path)
        if not os.path.exists(resolved):
            return f"ERROR: File not found: {bundle_path}"

        try:
            with open(resolved, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            return f"ERROR: Invalid JSON: {e}"

        original_size = os.path.getsize(resolved)
        stats = {"arrays_cleaned": 0, "items_removed": 0, "empty_removed": 0}
        self._dedup_recursive(data, stats)

        with open(resolved, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        new_size = os.path.getsize(resolved)
        reduction = original_size - new_size
        pct = (reduction / original_size * 100) if original_size > 0 else 0

        return (
            f"Deduplication complete:\n"
            f"  Original size: {original_size:,} bytes\n"
            f"  New size: {new_size:,} bytes\n"
            f"  Reduced by: {reduction:,} bytes ({pct:.1f}%)\n"
            f"  Arrays cleaned: {stats['arrays_cleaned']}\n"
            f"  Duplicate items removed: {stats['items_removed']}\n"
            f"  Empty elements removed: {stats['empty_removed']}"
        )

    def _dedup_recursive(self, obj, stats: dict):
        """Recursively deduplicate arrays and remove empty elements in a JSON structure."""
        if isinstance(obj, dict):
            keys_to_remove = []
            for key, value in obj.items():
                if isinstance(value, list):
                    deduped = self._dedup_array(value)
                    removed = len(value) - len(deduped)
                    if removed > 0:
                        stats["arrays_cleaned"] += 1
                        stats["items_removed"] += removed
                    obj[key] = deduped
                    for item in obj[key]:
                        self._dedup_recursive(item, stats)
                elif isinstance(value, dict):
                    self._dedup_recursive(value, stats)
                if value is None or value == "" or value == [] or value == {}:
                    keys_to_remove.append(key)
            for key in keys_to_remove:
                del obj[key]
                stats["empty_removed"] += 1
        elif isinstance(obj, list):
            for item in obj:
                self._dedup_recursive(item, stats)

    def _dedup_array(self, arr: list) -> list:
        """Remove duplicate items from a JSON array while preserving order."""
        if not arr:
            return arr
        seen = []
        result = []
        for item in arr:
            serialized = json.dumps(item, sort_keys=True)
            if serialized not in seen:
                seen.append(serialized)
                result.append(item)
        return result

    # ═══════════════════════════════════════════
    # CONTEXT MANAGEMENT
    # ═══════════════════════════════════════════

    def compress_text(self, text: str, target_chars: int = 500) -> str:
        """Use LLM to compress/summarize long text."""
        if not self.llm_client:
            if len(text) <= target_chars:
                return text
            return text[:target_chars] + f"... [truncated from {len(text)} chars]"

        try:
            result = self.llm_client.simple_complete(
                prompt=(
                    f"Summarize the following text to under {target_chars} characters. "
                    f"Preserve all key details (error messages, file names, numbers, codes):\n\n{text}"
                ),
                system_prompt="You are a concise summarizer. Output ONLY the summary."
            )
            return result[:target_chars + 100]
        except Exception as e:
            return text[:target_chars] + f"... [compression failed: {e}]"

    def get_context_status(self) -> str:
        """Get the agent's current context/token usage status."""
        if self.context_manager:
            status = self.context_manager.get_context_status()
            return json.dumps(status, indent=2)
        return "Context manager not available"

    def save_checkpoint(self, checkpoint_name: str = None) -> str:
        """Save current agent state to a checkpoint file."""
        # This is a stub - actual implementation is in checkpoint.py
        # and gets wired up by the core agent
        return "Checkpoint save requested (handled by agent core)"

    def load_checkpoint(self, checkpoint_name: str = None) -> str:
        """Load agent state from a checkpoint file."""
        return "Checkpoint load requested (handled by agent core)"

    # ═══════════════════════════════════════════
    # TOOL DEFINITIONS (OpenAI function format)
    # ═══════════════════════════════════════════

    def get_tool_definitions(self) -> list:
        """Return all tool definitions in OpenAI function calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file from the workspace. Supports partial reading with line ranges and keyword search to avoid loading large files into context.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "File path (relative to project root or absolute)"},
                            "start_line": {"type": "integer", "description": "Start line number (1-indexed). Optional."},
                            "end_line": {"type": "integer", "description": "End line number (1-indexed). Optional."},
                            "search_keyword": {"type": "string", "description": "Search for this keyword and return matching lines with context. Optional."},
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write content to a file. ONLY writes to: workspace/generated/ (final outputs), workspace/scratch/ (temp files), logs/. Writing anywhere else is blocked.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "File path to write to"},
                            "content": {"type": "string", "description": "Content to write"},
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "append_to_file",
                    "description": "Append content to a file. ONLY writes to: workspace/generated/, workspace/scratch/, logs/.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "File path to append to"},
                            "content": {"type": "string", "description": "Content to append"},
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "patch_file",
                    "description": "Find and replace specific text in a file. ONLY edits files in: workspace/generated/, workspace/scratch/, logs/. Use for surgical fixes to the bundle JSON.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "File path to patch"},
                            "search_text": {"type": "string", "description": "Exact text to find"},
                            "replace_text": {"type": "string", "description": "Text to replace with"},
                        },
                        "required": ["path", "search_text", "replace_text"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_directory",
                    "description": "List files and subdirectories in a directory. Supports glob patterns and recursive search.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Directory path to list (default: project root)"},
                            "pattern": {"type": "string", "description": "Glob pattern filter (e.g., '*.json', '*.py'). Optional."},
                            "recursive": {"type": "boolean", "description": "Search recursively in subdirectories. Optional."},
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_in_files",
                    "description": "Search for a text pattern across multiple files (like grep). Returns matching lines with file and line number.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "directory": {"type": "string", "description": "Directory to search in"},
                            "pattern": {"type": "string", "description": "Text or regex pattern to search for"},
                            "file_glob": {"type": "string", "description": "File glob pattern (default: '*'). E.g., '*.json', '*.py'"},
                        },
                        "required": ["directory", "pattern"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_file_info",
                    "description": "Get file metadata: size, line count, last modified time. Use before reading large files.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "File path to inspect"},
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "extract_json_path",
                    "description": "Extract a specific value from a JSON file using dot-notation path. E.g., 'entry.0.resource.resourceType'",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Path to the JSON file"},
                            "json_path": {"type": "string", "description": "Dot-notation path (e.g., 'entry.0.resource.resourceType')"},
                        },
                        "required": ["path", "json_path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_terminal",
                    "description": "Execute a shell command (Python scripts, grep, find, validator, etc.). Returns stdout, stderr, and exit code.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string", "description": "Shell command to execute"},
                            "timeout": {"type": "integer", "description": "Timeout in seconds (default: 60)"},
                            "cwd": {"type": "string", "description": "Working directory (default: project root)"},
                        },
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "python_eval",
                    "description": "Evaluate a simple Python expression and return the result. For quick calculations.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "expression": {"type": "string", "description": "Python expression to evaluate"},
                        },
                        "required": ["expression"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "json_validate",
                    "description": "Check if content is valid JSON. Provide either raw content or a file path.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "JSON string to validate. Optional if path is provided."},
                            "path": {"type": "string", "description": "Path to JSON file to validate. Optional if content is provided."},
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_fhir_validator",
                    "description": "Run the FHIR/NHCX validator on a bundle JSON file. Returns validation errors and warnings.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "bundle_path": {"type": "string", "description": "Path to the bundle JSON file to validate"},
                            "extra_args": {"type": "string", "description": "Additional validator arguments. Optional."},
                        },
                        "required": ["bundle_path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "compress_text",
                    "description": "Summarize long text to a target character count while preserving key information.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "Text to compress/summarize"},
                            "target_chars": {"type": "integer", "description": "Target character count (default: 500)"},
                        },
                        "required": ["text"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_context_status",
                    "description": "Check your current token usage, remaining budget, and context health. Use periodically to manage context.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "save_checkpoint",
                    "description": "Save current agent state to a checkpoint for recovery.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "checkpoint_name": {"type": "string", "description": "Name for this checkpoint (default: auto-generated)"},
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "load_checkpoint",
                    "description": "Restore agent state from a previously saved checkpoint.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "checkpoint_name": {"type": "string", "description": "Name of checkpoint to load"},
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "extract_pdf_to_markdown",
                    "description": "Extract text from a PDF file using Docling and save it as a Markdown (.md) file. The markdown file is much easier to read and search than raw PDF. Use this FIRST before trying to read any PDF.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pdf_path": {"type": "string", "description": "Path to the PDF file"},
                            "output_path": {"type": "string", "description": "Path for the output .md file (default: same directory as PDF, same name with .md extension)"},
                        },
                        "required": ["pdf_path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "deduplicate_bundle",
                    "description": "Remove duplicate entries from all arrays in a FHIR bundle JSON file. Run this after generating or modifying a bundle to prevent bloat from repeated extensions, exclusions, or coverages. Also removes empty elements.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "bundle_path": {"type": "string", "description": "Path to the bundle JSON file to deduplicate"},
                        },
                        "required": ["bundle_path"],
                    },
                },
            },
        ]
