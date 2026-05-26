"""Thin broker-facing API for WMOC.

Purpose:
- Provide a stable adapter layer above src.broker.
- Keep orchestrator/window code from depending directly on raw broker details.
- Preserve current SQLite schema and lifecycle rules.

This is intentionally thin in Phase 0:
- no network transport
- no MCP server yet
- no auth layer yet
- just a stable Python API surface
"""
from __future__ import annotations

from typing import Any

from . import broker
from .broker import BrokerError


def send_message(
    conn,
    *,
    from_role: str,
    to_role: str,
    type: str,
    subject: str | None = None,
    body: str | None = None,
    payload: dict[str, Any] | None = None,
    thread_id: str | None = None,
    parent_id: int | None = None,
    requires_approval: bool = False,
) -> int:
    return broker.post(
        conn,
        from_role=from_role,
        to_role=to_role,
        type=type,
        subject=subject,
        body=body,
        payload=payload,
        thread_id=thread_id,
        parent_id=parent_id,
        requires_approval=requires_approval,
    )


def list_inbox(
    conn,
    *,
    role: str,
    status: str = "new",
    types: list[str] | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    return broker.fetch(
        conn,
        to_role=role,
        status=status,
        types=types,
        limit=limit,
    )


def read_message(conn, message_id: int) -> dict[str, Any] | None:
    return broker.get(conn, message_id)


def claim_message(conn, message_id: int, role: str) -> None:
    broker.claim(conn, message_id, role)


def complete_message(conn, message_id: int, role: str) -> None:
    broker.complete(conn, message_id, role)


def fail_message(conn, message_id: int, role: str, *, error: str) -> None:
    broker.fail(conn, message_id, role, error=error)


def block_message(conn, message_id: int, role: str, *, reason: str) -> None:
    broker.block(conn, message_id, role, reason=reason)


__all__ = [
    "BrokerError",
    "send_message",
    "list_inbox",
    "read_message",
    "claim_message",
    "complete_message",
    "fail_message",
    "block_message",
]