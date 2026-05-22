"""WMOC local broker — SQLite-backed durable inbox/outbox for named roles.

Phase 0 minimal slice: init_db, post, get, fetch only. No claim / complete /
block / fail / approve yet. No MCP. The broker is not the orchestrator and
does not execute business actions — it persists and serves messages.

Usage:
    from src.broker import connect, init_db, post, get, fetch
    conn = connect("broker.sqlite")
    init_db(conn)
    mid = post(conn, from_role="pc", to_role="screen_left",
               type="request", subject="hello", body="test")
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

# --------------------------------------------------------------------------- #
# Allowlists
# --------------------------------------------------------------------------- #

CANONICAL_ROLES: frozenset[str] = frozenset(
    {
        "pc",
        "screen_left",
        "screen_right",
        "tv",
        "generic",
        "human",
        "system",
    }
)

MESSAGE_TYPES: frozenset[str] = frozenset(
    {
        "request",
        "response",
        "critique",
        "approval_request",
        "approval_decision",
        "status",
        "error",
    }
)

STATUSES: frozenset[str] = frozenset(
    {
        "new",
        "claimed",
        "done",
        "blocked",
        "failed",
        "expired",
    }
)

WILDCARD = "*"

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class BrokerError(ValueError):
    """Validation or schema error raised by the broker."""


# --------------------------------------------------------------------------- #
# Connection
# --------------------------------------------------------------------------- #


def connect(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection with the broker's required PRAGMAs applied.

    Uses autocommit mode (isolation_level=None) so transactions are explicit
    via BEGIN/COMMIT in the write path; reads do not need a transaction.
    """
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# --------------------------------------------------------------------------- #
# Normalization & validation
# --------------------------------------------------------------------------- #


def normalize_role(role: str) -> str:
    """Lowercase and convert spaces to underscores. Preserves '*'."""
    if not isinstance(role, str) or not role.strip():
        raise BrokerError(f"role must be a non-empty string, got: {role!r}")
    return role.strip().lower().replace(" ", "_")


def _validate_role(role: str, *, allow_wildcard: bool) -> str:
    norm = normalize_role(role)
    if norm == WILDCARD:
        if not allow_wildcard:
            raise BrokerError("wildcard '*' is only allowed for to_role")
        return norm
    if norm not in CANONICAL_ROLES:
        raise BrokerError(
            f"unknown role {norm!r}; allowed: {sorted(CANONICAL_ROLES)}"
        )
    return norm


def _validate_type(msg_type: str) -> str:
    if msg_type not in MESSAGE_TYPES:
        raise BrokerError(
            f"unknown message type {msg_type!r}; allowed: {sorted(MESSAGE_TYPES)}"
        )
    return msg_type


def _validate_status(status: str) -> str:
    if status not in STATUSES:
        raise BrokerError(
            f"unknown status {status!r}; allowed: {sorted(STATUSES)}"
        )
    return status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #


_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_created          TEXT    NOT NULL,
    from_role           TEXT    NOT NULL,
    to_role             TEXT    NOT NULL,
    type                TEXT    NOT NULL,
    status              TEXT    NOT NULL DEFAULT 'new',
    thread_id           TEXT,
    parent_id           INTEGER,
    subject             TEXT,
    body                TEXT,
    payload_json        TEXT,
    requires_approval   INTEGER NOT NULL DEFAULT 0,
    approval_status     TEXT,
    claimed_by          TEXT,
    ts_claimed          TEXT,
    ts_completed        TEXT,
    result_json         TEXT,
    error               TEXT,
    FOREIGN KEY (parent_id) REFERENCES messages(id)
);

CREATE INDEX IF NOT EXISTS idx_messages_inbox
    ON messages(to_role, status, id);

CREATE INDEX IF NOT EXISTS idx_messages_thread
    ON messages(thread_id);

CREATE TABLE IF NOT EXISTS audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,
    message_id      INTEGER NOT NULL,
    actor           TEXT    NOT NULL,
    action          TEXT    NOT NULL,
    before_status   TEXT,
    after_status    TEXT,
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

CREATE INDEX IF NOT EXISTS idx_audit_message
    ON audit(message_id);
"""


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables and indexes if missing. Idempotent."""
    conn.executescript(_SCHEMA)


# --------------------------------------------------------------------------- #
# Write path
# --------------------------------------------------------------------------- #


def post(
    conn: sqlite3.Connection,
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
    """Insert a message + matching audit row atomically. Returns the new id."""
    from_norm = _validate_role(from_role, allow_wildcard=False)
    to_norm = _validate_role(to_role, allow_wildcard=True)
    msg_type = _validate_type(type)

    payload_json = (
        json.dumps(payload, ensure_ascii=False) if payload is not None else None
    )
    ts = _now()

    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            """
            INSERT INTO messages (
                ts_created, from_role, to_role, type, status,
                thread_id, parent_id, subject, body, payload_json,
                requires_approval
            ) VALUES (?, ?, ?, ?, 'new', ?, ?, ?, ?, ?, ?)
            """,
            (
                ts,
                from_norm,
                to_norm,
                msg_type,
                thread_id,
                parent_id,
                subject,
                body,
                payload_json,
                1 if requires_approval else 0,
            ),
        )
        message_id = cur.lastrowid
        conn.execute(
            """
            INSERT INTO audit (
                ts, message_id, actor, action, before_status, after_status
            ) VALUES (?, ?, ?, 'post', NULL, 'new')
            """,
            (ts, message_id, from_norm),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return int(message_id)


def claim(conn: sqlite3.Connection, message_id: int, claimer_role: str) -> None:
    """Claim a new message for a specific role."""
    claimer = _validate_role(claimer_role, allow_wildcard=False)
    row = conn.execute(
        "SELECT id, to_role, status FROM messages WHERE id = ?",
        (int(message_id),),
    ).fetchone()
    if row is None:
        raise BrokerError(f"message not found: {message_id}")

    if row["status"] != "new":
        raise BrokerError(
            f"message {message_id} is {row['status']!r}, expected 'new'"
        )

    if row["to_role"] not in {claimer, WILDCARD}:
        raise BrokerError(
            f"message {message_id} addressed to {row['to_role']!r}, not {claimer!r}"
        )

    ts = _now()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE messages
            SET status = 'claimed',
                claimed_by = ?,
                ts_claimed = ?
            WHERE id = ?
            """,
            (claimer, ts, int(message_id)),
        )
        conn.execute(
            """
            INSERT INTO audit (
                ts, message_id, actor, action, before_status, after_status
            ) VALUES (?, ?, ?, 'claim', 'new', 'claimed')
            """,
            (ts, int(message_id), claimer),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def complete(conn: sqlite3.Connection, message_id: int, claimer_role: str) -> None:
    """Complete a claimed message for a specific role."""
    claimer = _validate_role(claimer_role, allow_wildcard=False)
    row = conn.execute(
        "SELECT id, to_role, status, claimed_by FROM messages WHERE id = ?",
        (int(message_id),),
    ).fetchone()
    if row is None:
        raise BrokerError(f"message not found: {message_id}")

    if row["status"] != "claimed":
        raise BrokerError(
            f"message {message_id} is {row['status']!r}, expected 'claimed'"
        )

    if row["to_role"] not in {claimer, WILDCARD}:
        raise BrokerError(
            f"message {message_id} addressed to {row['to_role']!r}, not {claimer!r}"
        )

    if row["claimed_by"] not in (None, claimer):
        raise BrokerError(
            f"message {message_id} claimed by {row['claimed_by']!r}, not {claimer!r}"
        )

    ts = _now()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE messages
            SET status = 'done',
                claimed_by = COALESCE(claimed_by, ?),
                ts_completed = ?
            WHERE id = ?
            """,
            (claimer, ts, int(message_id)),
        )
        conn.execute(
            """
            INSERT INTO audit (
                ts, message_id, actor, action, before_status, after_status
            ) VALUES (?, ?, ?, 'complete', 'claimed', 'done')
            """,
            (ts, int(message_id), claimer),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


# --------------------------------------------------------------------------- #
# Read path
# --------------------------------------------------------------------------- #


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    def _decode(raw: Any) -> Any:
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    d = dict(row)
    d["payload"] = _decode(d.get("payload_json"))
    d["result"] = _decode(d.get("result_json"))
    d["requires_approval"] = bool(d.get("requires_approval"))
    return d


def get(conn: sqlite3.Connection, message_id: int) -> dict[str, Any] | None:
    """Return one message row as a dict, or None if not found."""
    row = conn.execute(
        "SELECT * FROM messages WHERE id = ?",
        (int(message_id),),
    ).fetchone()
    return _row_to_dict(row) if row is not None else None


def fetch(
    conn: sqlite3.Connection,
    *,
    to_role: str,
    status: str = "new",
    types: list[str] | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return messages addressed to `to_role` (or to wildcard '*') with the
    given status, ordered deterministically by id ASC. Read-only — does not
    claim or mutate.
    """
    role = _validate_role(to_role, allow_wildcard=False)
    status_norm = _validate_status(status)

    if not isinstance(limit, int) or limit <= 0:
        raise BrokerError(f"limit must be a positive integer, got: {limit!r}")

    sql = (
        "SELECT * FROM messages "
        "WHERE (to_role = ? OR to_role = ?) AND status = ?"
    )
    params: list[Any] = [role, WILDCARD, status_norm]

    if types is not None:
        if not types:
            return []
        for t in types:
            _validate_type(t)
        placeholders = ",".join("?" for _ in types)
        sql += f" AND type IN ({placeholders})"
        params.extend(types)

    sql += " ORDER BY id ASC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]