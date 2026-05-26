"""Pin the orchestrator <-> broker wiring.

Calls src.orchestrator.broker_smoke against a fresh temp SQLite file and
asserts the printed message dict has the expected role fields. No mocks,
no external services.
"""
from __future__ import annotations

from src.orchestrator import broker_smoke


def test_broker_smoke_posts_and_fetches_for_screen_left(tmp_path, capsys):
    rc = broker_smoke(tmp_path / "broker.sqlite")
    out = capsys.readouterr().out

    assert rc == 0
    assert "from_role" in out
    assert "laptop" in out
    assert "to_role" in out
    assert "screen_left" in out
