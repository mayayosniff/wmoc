"""Filesystem tool — write artifacts to the workspace."""
from __future__ import annotations

from pathlib import Path


def write(path: str, content: str) -> dict[str, str]:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"path": str(p.resolve()), "bytes": str(len(content.encode('utf-8')))}
