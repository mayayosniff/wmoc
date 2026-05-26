"""Smoke tests for src/broker.py.

Covers init_db, post, get, fetch, claim, complete, fail, and block.
"""
from __future__ import annotations

import pytest

from src.broker import (
    BrokerError,
    block,
    claim,
    complete,
    connect,
    fail,
    fetch,
    get,
    init_db,
    post,
)


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "broker.sqlite"
    c = connect(str(db_path))
    init_db(c)
    yield c
    c.close()


def test_init_db_is_idempotent_on_fresh_db(tmp_path):
    db_path = tmp_path / "broker.sqlite"
    c = connect(str(db_path))
    init_db(c)
    init_db(c)
    tables = {
        r[0]
        for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"messages", "audit"} <= tables
    c.close()


def test_post_inserts_message_and_matching_audit_row(conn):
    mid = post(
        conn,
        from_role="laptop",
        to_role="screen_left",
        type="request",
        subject="hello",
        body="ping",
    )
    assert isinstance(mid, int) and mid > 0

    msg = conn.execute(
        "SELECT from_role, to_role, type, status FROM messages WHERE id = ?",
        (mid,),
    ).fetchone()
    assert tuple(msg) == ("laptop", "screen_left", "request", "new")

    audit_rows = conn.execute(
        "SELECT actor, action, before_status, after_status FROM audit "
        "WHERE message_id = ?",
        (mid,),
    ).fetchall()
    assert len(audit_rows) == 1
    assert tuple(audit_rows[0]) == ("laptop", "post", None, "new")


def test_get_returns_inserted_message_with_decoded_payload(conn):
    mid = post(
        conn,
        from_role="laptop",
        to_role="monitor",
        type="request",
        subject="s",
        body="b",
        payload={"k": "v"},
        requires_approval=True,
    )
    row = get(conn, mid)
    assert row is not None
    assert row["id"] == mid
    assert row["from_role"] == "laptop"
    assert row["to_role"] == "monitor"
    assert row["type"] == "request"
    assert row["status"] == "new"
    assert row["payload"] == {"k": "v"}
    assert row["requires_approval"] is True
    assert row["result"] is None
    assert get(conn, 9999) is None


def test_fetch_returns_direct_messages(conn):
    mid = post(conn, from_role="laptop", to_role="monitor", type="status", body="x")
    rows = fetch(conn, to_role="monitor")
    assert mid in [r["id"] for r in rows]
    other = fetch(conn, to_role="screen_left")
    assert mid not in [r["id"] for r in other]


def test_fetch_includes_wildcard_broadcasts(conn):
    direct = post(
        conn, from_role="laptop", to_role="monitor", type="status", body="d"
    )
    broadcast = post(
        conn, from_role="laptop", to_role="*", type="status", body="b"
    )
    monitor_ids = [r["id"] for r in fetch(conn, to_role="monitor")]
    assert direct in monitor_ids
    assert broadcast in monitor_ids
    sr_ids = [r["id"] for r in fetch(conn, to_role="screen_right")]
    assert broadcast in sr_ids


def test_fetch_ordering_is_id_ascending(conn):
    ids = [
        post(conn, from_role="laptop", to_role="monitor", type="status", body=str(i))
        for i in range(5)
    ]
    fetched = [r["id"] for r in fetch(conn, to_role="monitor")]
    assert fetched == ids


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(from_role="bogus", to_role="monitor", type="status"),
        dict(from_role="laptop", to_role="bogus", type="status"),
        dict(from_role="laptop", to_role="monitor", type="not_a_type"),
        dict(from_role="*", to_role="monitor", type="status"),
        dict(from_role="", to_role="monitor", type="status"),
    ],
)
def test_post_rejects_invalid_inputs(conn, kwargs):
    with pytest.raises(BrokerError):
        post(conn, body="x", **kwargs)


def test_fetch_rejects_invalid_status(conn):
    with pytest.raises(BrokerError):
        fetch(conn, to_role="monitor", status="not_a_status")


def test_claim_changes_new_to_claimed(conn):
    mid = post(
        conn, from_role="laptop", to_role="monitor", type="request", body="x"
    )
    claim(conn, mid, "monitor")

    row = get(conn, mid)
    assert row is not None
    assert row["status"] == "claimed"
    assert row["claimed_by"] == "monitor"
    assert row["ts_claimed"] is not None

    audit_rows = conn.execute(
        "SELECT actor, action, before_status, after_status FROM audit "
        "WHERE message_id = ? ORDER BY id ASC",
        (mid,),
    ).fetchall()
    assert tuple(audit_rows[-1]) == ("monitor", "claim", "new", "claimed")


def test_complete_changes_claimed_to_done(conn):
    mid = post(
        conn, from_role="laptop", to_role="monitor", type="request", body="x"
    )
    claim(conn, mid, "monitor")
    complete(conn, mid, "monitor")

    row = get(conn, mid)
    assert row is not None
    assert row["status"] == "done"
    assert row["claimed_by"] == "monitor"
    assert row["ts_completed"] is not None

    audit_rows = conn.execute(
        "SELECT actor, action, before_status, after_status FROM audit "
        "WHERE message_id = ? ORDER BY id ASC",
        (mid,),
    ).fetchall()
    assert tuple(audit_rows[-1]) == ("monitor", "complete", "claimed", "done")


def test_fail_changes_new_to_failed(conn):
    mid = post(
        conn, from_role="laptop", to_role="monitor", type="request", body="x"
    )
    fail(conn, mid, "monitor", error="tool timeout")

    row = get(conn, mid)
    assert row is not None
    assert row["status"] == "failed"
    assert row["claimed_by"] == "monitor"
    assert row["error"] == "tool timeout"

    audit_rows = conn.execute(
        "SELECT actor, action, before_status, after_status FROM audit "
        "WHERE message_id = ? ORDER BY id ASC",
        (mid,),
    ).fetchall()
    assert tuple(audit_rows[-1]) == ("monitor", "fail", "new", "failed")


def test_fail_changes_claimed_to_failed(conn):
    mid = post(
        conn, from_role="laptop", to_role="monitor", type="request", body="x"
    )
    claim(conn, mid, "monitor")
    fail(conn, mid, "monitor", error="api failure")

    row = get(conn, mid)
    assert row is not None
    assert row["status"] == "failed"
    assert row["claimed_by"] == "monitor"
    assert row["error"] == "api failure"

    audit_rows = conn.execute(
        "SELECT actor, action, before_status, after_status FROM audit "
        "WHERE message_id = ? ORDER BY id ASC",
        (mid,),
    ).fetchall()
    assert tuple(audit_rows[-1]) == ("monitor", "fail", "claimed", "failed")


def test_block_changes_new_to_blocked(conn):
    mid = post(
        conn, from_role="laptop", to_role="monitor", type="request", body="x"
    )
    block(conn, mid, "monitor", reason="waiting on approval")

    row = get(conn, mid)
    assert row is not None
    assert row["status"] == "blocked"
    assert row["claimed_by"] == "monitor"
    assert row["error"] == "waiting on approval"

    audit_rows = conn.execute(
        "SELECT actor, action, before_status, after_status FROM audit "
        "WHERE message_id = ? ORDER BY id ASC",
        (mid,),
    ).fetchall()
    assert tuple(audit_rows[-1]) == ("monitor", "block", "new", "blocked")


def test_block_changes_claimed_to_blocked(conn):
    mid = post(
        conn, from_role="laptop", to_role="monitor", type="request", body="x"
    )
    claim(conn, mid, "monitor")
    block(conn, mid, "monitor", reason="need human input")

    row = get(conn, mid)
    assert row is not None
    assert row["status"] == "blocked"
    assert row["claimed_by"] == "monitor"
    assert row["error"] == "need human input"

    audit_rows = conn.execute(
        "SELECT actor, action, before_status, after_status FROM audit "
        "WHERE message_id = ? ORDER BY id ASC",
        (mid,),
    ).fetchall()
    assert tuple(audit_rows[-1]) == ("monitor", "block", "claimed", "blocked")


def test_claim_rejects_wrong_role(conn):
    mid = post(
        conn, from_role="laptop", to_role="monitor", type="request", body="x"
    )
    with pytest.raises(BrokerError):
        claim(conn, mid, "screen_left")


def test_complete_rejects_non_claimed_message(conn):
    mid = post(
        conn, from_role="laptop", to_role="monitor", type="request", body="x"
    )
    with pytest.raises(BrokerError):
        complete(conn, mid, "monitor")


def test_fail_rejects_done_message(conn):
    mid = post(
        conn, from_role="laptop", to_role="monitor", type="request", body="x"
    )
    claim(conn, mid, "monitor")
    complete(conn, mid, "monitor")
    with pytest.raises(BrokerError):
        fail(conn, mid, "monitor", error="too late")


def test_block_rejects_wrong_role(conn):
    mid = post(
        conn, from_role="laptop", to_role="monitor", type="request", body="x"
    )
    with pytest.raises(BrokerError):
        block(conn, mid, "screen_left", reason="not mine")