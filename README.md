# NHCX Insurance Plan Bundle Generator

A local agentic framework that generates zero-error NHCX FHIR Insurance Plan Bundles from insurance policy PDFs using a ReAct (Reason + Act) loop with a local LLM.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Test LLM connection
python main.py --test

# 3. Run the agent
python main.py

# 4. Resume from checkpoint (if needed)
python main.py --resume auto_iter_010
```

## Architecture

```
main.py → ReActAgent (core.py)
              ├── LocalLLMClient (llm_client.py) ← localhost:8090
              ├── ContextManager (context_manager.py) ← token tracking + compression
              ├── ToolRegistry (tools.py) ← 16 tools
              └── CheckpointManager (checkpoint.py) ← save/restore state
```

## Tools Available (16)

| Category | Tools |
|----------|-------|
| File I/O | `read_file`, `write_file`, `append_to_file`, `patch_file` |
| Discovery | `list_directory`, `search_in_files`, `get_file_info`, `extract_json_path` |
| Execution | `run_terminal`, `python_eval` |
| Validation | `json_validate`, `run_fhir_validator` |
| Context | `compress_text`, `get_context_status`, `save_checkpoint`, `load_checkpoint` |

## How It Works

1. Agent discovers workspace files (PDFs, rulebooks, examples)
2. Reads and understands FHIR structure definitions
3. Writes Python code (`generate_bundle.py`) to create the bundle
4. Runs the code to produce `bundle.json`
5. Validates with FHIR validator
6. Reads errors → fixes code → re-validates
7. Loops until **zero validation errors**

## Configuration

Edit `config/settings.json` to adjust:
- LLM URL, model, temperature
- Max iterations (default: 50)
- Token budget (default: 12000)
- Validator command template
