"""Offline contract tests for src/agents/gemini_client.py.

Mocks google.genai.Client so tests require no network, no API key, no real file
contents beyond a tiny synthetic PNG fixture written to a tmp path.

Covers:
- input validation (missing key, invalid mode, empty goal, missing file, unsupported type, oversize file)
- structured success path (full JSON parse + structural validation)
- malformed JSON → degraded fallback
- empty response.text → degraded fallback
- safety-block finish_reason → degraded fallback
- MAX_TOKENS finish_reason → degraded fallback
- model-rejection ClientError → RuntimeError with settings hint
- non-model ClientError → RuntimeError with status + body
- ServerError → RuntimeError with status + body
- structural contract violations → GeminiContractError (parametrized hard-fail boundary)
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.agents import gemini_client as gem


# Minimal 1x1 PNG fixture (8-byte signature + IHDR + IDAT + IEND) for tests that need a real file on disk.
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a"                          # signature
    "0000000d49484452"                          # IHDR chunk length + type
    "0000000100000001080600000077dec4"          # IHDR body + CRC
    "0000000d49444154"                          # IDAT chunk length + type
    "789c6300010000000500015c9bd1c4"            # zlib stream + CRC
    "0000000049454e44ae426082"                  # IEND
)


@pytest.fixture
def tiny_png(tmp_path: Path) -> Path:
    p = tmp_path / "tiny.png"
    p.write_bytes(_TINY_PNG)
    return p


def _mock_client_with_response(
    *,
    text: str | None = "{}",
    finish_reason: str | None = "STOP",
    raise_exc: Exception | None = None,
) -> MagicMock:
    """Build a MagicMock that quacks like a genai.Client with a controlled response."""
    candidate = SimpleNamespace(finish_reason=SimpleNamespace(name=finish_reason) if finish_reason else None)
    response = SimpleNamespace(text=text, candidates=[candidate])

    client = MagicMock()
    if raise_exc is not None:
        client.models.generate_content.side_effect = raise_exc
    else:
        client.models.generate_content.return_value = response
    return client


def _patch_client(client: MagicMock):
    """Return a context manager that patches genai.Client to return our mock."""
    return patch.object(gem.genai, "Client", return_value=client)


_VALID_OUTPUT = {
    "summary": "A small placeholder image.",
    "key_observations": ["1x1 pixel"],
    "extracted_entities": [],
    "risks_or_anomalies": [],
    "open_questions": [],
    "confidence": "high",
}


def test_missing_api_key_raises_clear_runtime_error(monkeypatch, tiny_png):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is not set"):
        gem.call(tiny_png, goal="describe", mode="document")


def test_invalid_mode_raises_valueerror(monkeypatch, tiny_png):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    with pytest.raises(ValueError, match="mode must be one of"):
        gem.call(tiny_png, goal="describe", mode="invalid")  # type: ignore[arg-type]


def test_empty_goal_raises_valueerror(monkeypatch, tiny_png):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    with pytest.raises(ValueError, match="goal must be a non-empty string"):
        gem.call(tiny_png, goal="   ", mode="document")


def test_missing_file_raises_filenotfound(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    bogus = tmp_path / "does_not_exist.png"
    with pytest.raises(FileNotFoundError):
        gem.call(bogus, goal="describe", mode="document")


def test_unsupported_extension_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    p = tmp_path / "thing.exe"
    p.write_bytes(b"\x00\x00")
    with pytest.raises(RuntimeError, match="Unsupported file type"):
        gem.call(p, goal="describe", mode="document")


def test_oversize_file_raises_with_files_api_hint(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    monkeypatch.setattr(gem, "_MAX_INLINE_BYTES", 16)  # shrink threshold for the test
    p = tmp_path / "big.png"
    p.write_bytes(_TINY_PNG)  # ~67 bytes > 16
    with pytest.raises(RuntimeError, match="Files API delivery is not yet implemented"):
        gem.call(p, goal="describe", mode="document")


def test_structured_success(monkeypatch, tiny_png):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    client = _mock_client_with_response(text=json.dumps(_VALID_OUTPUT), finish_reason="STOP")
    with _patch_client(client):
        result = gem.call(tiny_png, goal="describe", mode="document")
    assert result["summary"] == "A small placeholder image."
    assert result["key_observations"] == ["1x1 pixel"]
    assert result["confidence"] == "high"
    assert result["mode"] == "document"
    assert result["model"]  # populated from settings


def test_malformed_json_returns_degraded(monkeypatch, tiny_png):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    client = _mock_client_with_response(text="not json at all", finish_reason="STOP")
    with _patch_client(client):
        result = gem.call(tiny_png, goal="describe", mode="document")
    assert result["confidence"] == "low"
    assert result["summary"] == ""
    assert any("[degraded]" in q and "JSON parse" in q for q in result["open_questions"])


def test_empty_text_returns_degraded(monkeypatch, tiny_png):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    client = _mock_client_with_response(text=None, finish_reason="STOP")
    with _patch_client(client):
        result = gem.call(tiny_png, goal="describe", mode="document")
    assert result["confidence"] == "low"
    assert any("empty content" in q for q in result["open_questions"])


@pytest.mark.parametrize("finish_reason", [
    "SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII",
    "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT", "NO_IMAGE", "OTHER",
])
def test_safety_block_returns_degraded(monkeypatch, tiny_png, finish_reason):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    client = _mock_client_with_response(text="{}", finish_reason=finish_reason)
    with _patch_client(client):
        result = gem.call(tiny_png, goal="describe", mode="document")
    assert result["confidence"] == "low"
    assert any(finish_reason in q for q in result["open_questions"])


def test_max_tokens_returns_degraded(monkeypatch, tiny_png):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    client = _mock_client_with_response(text='{"truncated', finish_reason="MAX_TOKENS")
    with _patch_client(client):
        result = gem.call(tiny_png, goal="describe", mode="document")
    assert result["confidence"] == "low"
    assert any("MAX_TOKENS" in q for q in result["open_questions"])


def _make_client_error(code: int, body: str) -> gem.errors.ClientError:
    # ClientError(code, response_json, response=None)
    return gem.errors.ClientError(code, {"error": {"message": body}}, None)


def test_model_rejection_404_raises_settings_hint(monkeypatch, tiny_png):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    err = _make_client_error(404, "model not found: gemini-x")
    client = _mock_client_with_response(raise_exc=err)
    with _patch_client(client):
        with pytest.raises(RuntimeError, match=r"settings\.yaml"):
            gem.call(tiny_png, goal="describe", mode="document")


def test_model_rejection_400_raises_settings_hint(monkeypatch, tiny_png):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    err = _make_client_error(400, "invalid model name 'gemini-typo'")
    client = _mock_client_with_response(raise_exc=err)
    with _patch_client(client):
        with pytest.raises(RuntimeError, match=r"settings\.yaml"):
            gem.call(tiny_png, goal="describe", mode="document")


def test_non_model_client_error_includes_status_and_body(monkeypatch, tiny_png):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    err = _make_client_error(429, "rate limited")
    client = _mock_client_with_response(raise_exc=err)
    with _patch_client(client):
        with pytest.raises(RuntimeError, match=r"status=429"):
            gem.call(tiny_png, goal="describe", mode="document")


def test_server_error_includes_status(monkeypatch, tiny_png):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    err = gem.errors.ServerError(500, {"error": {"message": "internal"}}, None)
    client = _mock_client_with_response(raise_exc=err)
    with _patch_client(client):
        with pytest.raises(RuntimeError, match=r"status=500"):
            gem.call(tiny_png, goal="describe", mode="document")


@pytest.mark.parametrize("bad_output, expected_match", [
    # missing required field
    ({"key_observations": [], "extracted_entities": [], "risks_or_anomalies": [],
      "open_questions": [], "confidence": "high"},
     r"summary.*string"),
    # invalid confidence enum
    ({"summary": "x", "key_observations": [], "extracted_entities": [],
      "risks_or_anomalies": [], "open_questions": [], "confidence": "maybe"},
     r"confidence.*maybe"),
    # wrong type for list
    ({"summary": "x", "key_observations": "not a list", "extracted_entities": [],
      "risks_or_anomalies": [], "open_questions": [], "confidence": "high"},
     r"key_observations.*list"),
    # non-string list element
    ({"summary": "x", "key_observations": [123], "extracted_entities": [],
      "risks_or_anomalies": [], "open_questions": [], "confidence": "high"},
     r"key_observations\[0\].*string"),
    # summary wrong type
    ({"summary": 42, "key_observations": [], "extracted_entities": [],
      "risks_or_anomalies": [], "open_questions": [], "confidence": "high"},
     r"summary.*string"),
])
def test_structural_violations_raise_contract_error(monkeypatch, tiny_png, bad_output, expected_match):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    client = _mock_client_with_response(text=json.dumps(bad_output), finish_reason="STOP")
    with _patch_client(client):
        with pytest.raises(gem.GeminiContractError, match=expected_match):
            gem.call(tiny_png, goal="describe", mode="document")
