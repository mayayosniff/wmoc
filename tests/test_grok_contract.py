"""Offline contract tests for src/agents/grok_client.py.

Mocks httpx; no network, no API key. Covers:
- missing XAI_API_KEY → RuntimeError,
- empty/non-string artifact → ValueError,
- structured tier success,
- json_mode fallback on schema-400,
- both tiers rejected → RuntimeError,
- bad model 400 → settings-pointer RuntimeError,
- non-model HTTP error → status+body RuntimeError,
- malformed JSON → degraded fail-verdict,
- structural contract violations → GrokContractError (parametrized).
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from src.agents import grok_client as gk


def _resp(body: dict[str, Any] | None = None, *, status_code: int = 200,
          text: str | None = None) -> httpx.Response:
    req = httpx.Request("POST", gk._API_URL)
    if text is not None:
        return httpx.Response(status_code=status_code, text=text, request=req)
    return httpx.Response(status_code=status_code, json=body, request=req)


def _chat(content: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}}]}


_ARTIFACT = "Apple was founded in 1975 by Steve Jobs alone. It's the greatest company ever."


def test_missing_api_key_raises_clear_runtime_error(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="XAI_API_KEY is not set"):
        gk.critique(_ARTIFACT)


def test_empty_artifact_raises_valueerror(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "fake")
    with pytest.raises(ValueError, match="non-empty string"):
        gk.critique("   ")
    with pytest.raises(ValueError, match="non-empty string"):
        gk.critique("")


def test_non_string_context_raises_valueerror(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "fake")
    with pytest.raises(ValueError, match="expected string"):
        gk.critique(_ARTIFACT, context=123)  # type: ignore[arg-type]


def test_structured_tier_success(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "fake")
    out = {
        "factual_issues": ["Apple was founded in 1976, not 1975. Steve Wozniak co-founded it."],
        "tone_issues": ["'greatest company ever' is hype, not analysis."],
        "blocking_problems": [],
        "verdict": "revise",
    }
    with patch.object(httpx, "post", return_value=_resp(_chat(json.dumps(out)))):
        result = gk.critique(_ARTIFACT, context="board meeting briefing")
    assert result["verdict"] == "revise"
    assert len(result["factual_issues"]) == 1
    assert len(result["tone_issues"]) == 1
    assert result["blocking_problems"] == []
    assert result["model"]  # populated from settings


def test_falls_back_to_json_mode_on_schema_400(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "fake")
    out = {"factual_issues": [], "tone_issues": [], "blocking_problems": [], "verdict": "pass"}
    err = _resp(status_code=400, text='{"error":"json_schema not supported on this model"}')
    ok = _resp(_chat(json.dumps(out)))
    with patch.object(httpx, "post", side_effect=[err, ok]):
        result = gk.critique(_ARTIFACT)
    assert result["verdict"] == "pass"


def test_both_tiers_rejected_raises(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "fake")
    err1 = _resp(status_code=400, text='{"error":"json_schema not supported"}')
    err2 = _resp(status_code=400, text='{"error":"json_object not supported"}')
    with patch.object(httpx, "post", side_effect=[err1, err2]):
        with pytest.raises(RuntimeError, match="rejected both"):
            gk.critique(_ARTIFACT)


def test_bad_model_400_raises_with_settings_hint(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "fake")
    err = _resp(status_code=400, text='{"error":"invalid model: grok-xyz"}')
    with patch.object(httpx, "post", return_value=err):
        with pytest.raises(RuntimeError, match=r"settings\.yaml.*status=400"):
            gk.critique(_ARTIFACT)


def test_non_model_http_error_includes_status_and_body(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "fake")
    bad = _resp(status_code=500, text='{"error":"upstream timeout"}')
    with patch.object(httpx, "post", return_value=bad):
        with pytest.raises(RuntimeError, match=r"status=500.*upstream timeout"):
            gk.critique(_ARTIFACT)


def test_unparseable_json_returns_degraded_fail(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "fake")
    with patch.object(httpx, "post", return_value=_resp(_chat("not json"))):
        result = gk.critique(_ARTIFACT)
    assert result["verdict"] == "fail"
    assert any("[degraded]" in p and "JSON parse" in p for p in result["blocking_problems"])
    assert result["factual_issues"] == []
    assert result["tone_issues"] == []


@pytest.mark.parametrize("bad_output, expected_match", [
    # invalid verdict enum
    ({"factual_issues": [], "tone_issues": [], "blocking_problems": [], "verdict": "ship"},
     r"verdict.*ship"),
    # non-list factual_issues
    ({"factual_issues": "not a list", "tone_issues": [], "blocking_problems": [],
      "verdict": "pass"},
     r"factual_issues.*list"),
    # non-string element
    ({"factual_issues": [123], "tone_issues": [], "blocking_problems": [],
      "verdict": "pass"},
     r"factual_issues\[0\].*string"),
    # missing tone_issues
    ({"factual_issues": [], "blocking_problems": [], "verdict": "pass"},
     r"tone_issues.*list"),
    # missing verdict
    ({"factual_issues": [], "tone_issues": [], "blocking_problems": []},
     r"verdict.*None"),
])
def test_structural_violations_raise_contract_error(monkeypatch, bad_output, expected_match):
    monkeypatch.setenv("XAI_API_KEY", "fake")
    with patch.object(httpx, "post",
                      return_value=_resp(_chat(json.dumps(bad_output)))):
        with pytest.raises(gk.GrokContractError, match=expected_match):
            gk.critique(_ARTIFACT)
