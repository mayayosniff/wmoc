"""Smoke tests for src/broker_api.py."""
from __future__ import annotations

import pytest

from src.broker import BrokerError, connect, init_db
from src.broker_api import (
    block_message,
    claim_message,
    complete_message,
    fail_message,
    list_inbox,
    read_message,
    send_message,
)


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "broker.sqlite"
    c = connect(str(db_path))
    init_db(c)
    yield c
    c.close()


def test_send_and_read_message(conn):
    mid = send_message(
        conn,
        from_role="laptop",
        to_role="monitor",
        type="request",
        subject="hello",
        body="ping",
        payload={"x": 1},
    )
    row = read_message(conn, mid)
    assert row is not None
    assert row["id"] == mid
    assert row["from_role"] == "laptop"
    assert row["to_role"] == "monitor"
    assert row["payload"] == {"x": 1}


def test_list_inbox_returns_targeted_messages(conn):
    mid = send_message(
        conn,
        from_role="laptop",
        to_role="screen_left",
        type="status",
        body="ready",
    )
    rows = list_inbox(conn, role="screen_left")
    ids = [r["id"] for r in rows]
    assert mid in ids


def test_claim_and_complete_message(conn):
    mid = send_message(
        conn,
        from_role="laptop",
        to_role="monitor",
        type="request",
        body="do work",
    )
    claim_message(conn, mid, "monitor")
    complete_message(conn, mid, "monitor")

    row = read_message(conn, mid)
    assert row is not None
    assert row["status"] == "done"
    assert row["claimed_by"] == "monitor"


def test_fail_message(conn):
    mid = send_message(
        conn,
        from_role="laptop",
        to_role="monitor",
        type="request",
        body="do work",
    )
    fail_message(conn, mid, "monitor", error="tool crashed")

    row = read_message(conn, mid)
    assert row is not None
    assert row["status"] == "failed"
    assert row["error"] == "tool crashed"


def test_block_message(conn):
    mid = send_message(
        conn,
        from_role="laptop",
        to_role="monitor",
        type="request",
        body="do work",
    )
    block_message(conn, mid, "monitor", reason="waiting on approval")

    row = read_message(conn, mid)
    assert row is not None
    assert row["status"] == "blocked"
    assert row["error"] == "waiting on approval"


def test_api_propagates_broker_errors(conn):
    mid = send_message(
        conn,
        from_role="laptop",
        to_role="monitor",
        type="request",
        body="do work",
    )
    with pytest.raises(BrokerError):
        claim_message(conn, mid, "screen_left")