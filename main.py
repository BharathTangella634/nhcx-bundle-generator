#!/usr/bin/env python3
"""
NHCX Bundle Generator - Main Entry Point
==========================================
Usage:
    python3 main.py --pdf workspace/pdf/your_policy.pdf
    python3 main.py                                       # auto-finds PDF in workspace/pdf/
    python3 main.py --test                                # test LLM connection only
"""

import os
import sys
import json
import logging
import argparse
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)


def setup_logging(log_level: str = "INFO"):
    logs_dir = os.path.join(PROJECT_ROOT, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    log_file = os.path.join(logs_dir, f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_file


def load_settings() -> dict:
    settings_path = os.path.join(PROJECT_ROOT, "config", "settings.json")
    if not os.path.exists(settings_path):
        print(f"ERROR: Settings file not found: {settings_path}")
        sys.exit(1)
    with open(settings_path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_connection(settings: dict):
    from agent.llm_client import LocalLLMClient
    print("\nTesting LLM connection...")
    llm_cfg = settings.get("llm", {})
    client = LocalLLMClient(
        base_url=llm_cfg.get("base_url"),
        model=llm_cfg.get("model"),
        api_key=llm_cfg.get("api_key", "not-needed"),
    )
    if client.test_connection():
        print(f"  OK — {llm_cfg['base_url']} / {llm_cfg['model']}")
    else:
        print("  FAILED!")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="NHCX Bundle Generator")
    parser.add_argument("--test", action="store_true", help="Test LLM connection only")
    parser.add_argument("--log-level", type=str, default="INFO", help="Log level")
    parser.add_argument("--pdf", type=str, help="Path to the insurance policy PDF (or its .md extraction)")
    args = parser.parse_args()

    log_file = setup_logging(args.log_level)
    settings = load_settings()

    print(f"\n{'=' * 60}")
    print(f"  NHCX Insurance Plan Bundle Generator")
    print(f"  Log: {log_file}")
    print(f"{'=' * 60}\n")

    if args.test:
        test_connection(settings)
        return

    from agent.core import ReActAgent
    agent = ReActAgent(project_root=PROJECT_ROOT, settings=settings)
    result = agent.run(pdf_path=args.pdf)

    results_path = os.path.join(PROJECT_ROOT, "logs", "last_run_result.json")
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)

    print(f"\nResult saved to: {results_path}")
    sys.exit(0 if result.get("status") == "complete" else 1)


if __name__ == "__main__":
    main()
