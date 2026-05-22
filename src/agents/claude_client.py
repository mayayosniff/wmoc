"""Claude client — orchestrator-side calls (planning, composing)."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import anthropic
import yaml
from anthropic import Anthropic

_log = logging.getLogger("wmoc.claude")
_SETTINGS_PATH = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"


def _orchestrator_model() -> str:
    with _SETTINGS_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["orchestrator"]["model"]


def plan(goal: str, *, tool_registry: list[str]) -> list[dict[str, Any]]:
    """Still a stub — real planning lands in a later phase."""
    return [{"stub": True, "goal": goal, "note": "real planning not wired yet"}]


def compose(task: str, *, context: dict[str, Any]) -> str:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to config/.env "
            "(see config/.env.example for the line format)."
        )
    model = _orchestrator_model()
    _log.info("compose -> model=%s task=%r", model, task[:40])
    client = Anthropic()
    prompt = f"Task: {task}\n\nContext:\n{context}"
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
    except (anthropic.NotFoundError, anthropic.BadRequestError, anthropic.PermissionDeniedError) as e:
        err_text = str(e).lower()
        if "model" in err_text or "deployment" in err_text:
            raise RuntimeError(
                f"Anthropic rejected model {model!r} "
                f"(error_class={type(e).__name__}). "
                f"Change orchestrator.model in config/settings.yaml. "
                f"Original error: {e}"
            ) from e
        raise
    parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
    _log.info("compose <- model=%s chars=%d", model, sum(len(p) for p in parts))
    return "\n".join(parts)
