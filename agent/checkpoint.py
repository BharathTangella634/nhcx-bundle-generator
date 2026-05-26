"""
Checkpoint Manager - Save and restore agent state
===================================================
"""

import os
import json
import logging
from datetime import datetime

logger = logging.getLogger("nhcx_agent.checkpoint")


class CheckpointManager:
    """Save and restore agent state for recovery across iterations."""

    def __init__(self, checkpoints_dir: str):
        self.checkpoints_dir = os.path.abspath(checkpoints_dir)
        os.makedirs(self.checkpoints_dir, exist_ok=True)

    def save(self, state: dict, name: str = None) -> str:
        """Save agent state to a checkpoint file."""
        if not name:
            name = f"checkpoint_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        filepath = os.path.join(self.checkpoints_dir, f"{name}.json")
        state["_checkpoint_name"] = name
        state["_checkpoint_time"] = datetime.now().isoformat()

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, default=str)

        logger.info(f"Checkpoint saved: {filepath}")
        return f"Checkpoint saved: {name}"

    def load(self, name: str = None) -> dict:
        """Load agent state from a checkpoint. If no name given, load the latest."""
        if name:
            filepath = os.path.join(self.checkpoints_dir, f"{name}.json")
        else:
            # Find the latest checkpoint
            files = sorted(
                [f for f in os.listdir(self.checkpoints_dir) if f.endswith(".json")],
                reverse=True
            )
            if not files:
                raise FileNotFoundError("No checkpoints found")
            filepath = os.path.join(self.checkpoints_dir, files[0])

        with open(filepath, "r", encoding="utf-8") as f:
            state = json.load(f)

        logger.info(f"Checkpoint loaded: {filepath}")
        return state

    def list_checkpoints(self) -> list:
        """List all available checkpoints."""
        files = sorted(
            [f.replace(".json", "") for f in os.listdir(self.checkpoints_dir) if f.endswith(".json")],
            reverse=True
        )
        return files
