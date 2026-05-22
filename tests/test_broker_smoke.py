"""Smoke tests for src/broker.py (Phase 0 minimal slice).

Covers only init_db, post, get, fetch. No claim/complete/approve — those
verbs don't exist yet and are not tested here.
"""
from __future__ import annotations

import pytest

from src.broker import (
    BrokerError,
    connect,
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
        from_role="pc",
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
    assert tuple(msg) == ("pc", "screen_left", "request", "new")

    audit_rows = conn.execute(
        "SELECT actor, action, before_status, after_status FROM audit "
        "WHERE message_id = ?",
        (mid,),
    ).fetchall()
    assert len(audit_rows) == 1
    assert tuple(audit_rows[0]) == ("pc", "post", None, "new")


def test_get_returns_inserted_message_with_decoded_payload(conn):
    mid = post(
        conn,
        from_role="pc",
        to_role="tv",
        type="request",
        subject="s",
        body="b",
        payload={"k": "v"},
        requires_approval=True,
    )
    row = get(conn, mid)
    assert row is not None
    assert row["id"] == mid
    assert row["from_role"] == "pc"
    assert row["to_role"] == "tv"
    assert row["type"] == "request"
    assert row["status"] == "new"
    assert row["payload"] == {"k": "v"}
    assert row["requires_approval"] is True
    assert row["result"] is None
    assert get(conn, 9999) is None


def test_fetch_returns_direct_messages(conn):
    mid = post(conn, from_role="pc", to_role="tv", type="status", body="x")
    rows = fetch(conn, to_role="tv")
    assert mid in [r["id"] for r in rows]
    other = fetch(conn, to_role="screen_left")
    assert mid not in [r["id"] for r in other]


def test_fetch_includes_wildcard_broadcasts(conn):
    direct = post(conn, from_role="pc", to_role="tv", type="status", body="d")
    broadcast = post(conn, from_role="pc", to_role="*", type="status", body="b")
    tv_ids = [r["id"] for r in fetch(conn, to_role="tv")]
    assert direct in tv_ids
    assert broadcast in tv_ids
    sr_ids = [r["id"] for r in fetch(conn, to_role="screen_right")]
    assert broadcast in sr_ids


def test_fetch_ordering_is_id_ascending(conn):
    ids = [
        post(conn, from_role="pc", to_role="tv", type="status", body=str(i))
        for i in range(5)
    ]
    fetched = [r["id"] for r in fetch(conn, to_role="tv")]
    assert fetched == ids


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(from_role="bogus", to_role="tv", type="status"),
        dict(from_role="pc", to_role="bogus", type="status"),
        dict(from_role="pc", to_role="tv", type="not_a_type"),
        dict(from_role="*", to_role="tv", type="status"),
        dict(from_role="", to_role="tv", type="status"),
    ],
)
def test_post_rejects_invalid_inputs(conn, kwargs):
    with pytest.raises(BrokerError):
        post(conn, body="x", **kwargs)


def test_fetch_rejects_invalid_status(conn):
    with pytest.raises(BrokerError):
        fetch(conn, to_role="tv", status="not_a_status")
