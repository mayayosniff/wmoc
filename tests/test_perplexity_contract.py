"""Offline contract tests for src/agents/perplexity_client.py.

Mocks httpx; no network, no API key. Covers:
- missing key / empty request errors,
- transport tier 1 (structured / json_schema) success,
- transport tier 2 (json_mode / json_object) fallback,
- both tiers rejected → raises with status + body,
- non-model HTTP error → RuntimeError with status + body snippet,
- bad model 400 → settings-pointer RuntimeError,
- search_results vs citations as source of truth,
- citation drift filter,
- low-tier-only filter,
- JSON parse failure → degraded response,
- structural contract violations → PerplexityContractError (the hard-fail boundary),
- source_tier classification.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from src.agents import perplexity_client as ppx


def _resp(body: dict[str, Any] | None = None, *, status_code: int = 200,
          text: str | None = None) -> httpx.Response:
    req = httpx.Request("POST", ppx._API_URL)
    if text is not None:
        return httpx.Response(status_code=status_code, text=text, request=req)
    return httpx.Response(status_code=status_code, json=body, request=req)


def _chat(content: str, *, search_results: list[dict[str, Any]] | None = None,
          citations: list[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"choices": [{"message": {"content": content}}]}
    if search_results is not None:
        out["search_results"] = search_results
    if citations is not None:
        out["citations"] = citations
    return out


_REQUEST: ppx.PerplexityRequest = {
    "intent": "meeting_prep_research",
    "people": [{"name": "Jane Doe", "company": "Acme"}],
    "companies": ["Acme"],
}


def test_missing_api_key_raises_clear_runtime_error(monkeypatch):
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="PERPLEXITY_API_KEY is not set"):
        ppx.call(_REQUEST)


def test_request_validation(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "fake")
    with pytest.raises(ValueError, match="intent is required"):
        ppx.call({"people": [{"name": "x"}]})
    with pytest.raises(ValueError, match="at least one of"):
        ppx.call({"intent": "meeting_prep_research"})


def test_structured_tier_success_uses_search_results(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "fake")
    model_output = {
        "people": [{"name": "Jane Doe", "role": "VP Eng",
                    "recent_activity": [{"text": "QCon talk", "citation_ids": [1]}]}],
        "companies": [{"name": "Acme", "one_liner": "cloud security",
                       "recent_news": [{"text": "Series C", "citation_ids": [2]}],
                       "risk_flags": []}],
        "open_questions": [],
        "confidence": "high",
    }
    search_results = [
        {"url": "https://acme.com/qcon", "title": "QCon talk", "date": "2026-04-01"},
        {"url": "https://reuters.com/x", "title": "Series C", "date": "2026-03-22"},
    ]
    with patch.object(httpx, "post",
                      return_value=_resp(_chat(json.dumps(model_output),
                                               search_results=search_results))):
        result = ppx.call(_REQUEST)
    assert result["transport"] == "structured"
    assert result["confidence"] == "high"
    assert result["citations"][0]["source_tier"] == "primary"   # acme.com matches request
    assert result["citations"][1]["source_tier"] == "secondary"
    assert len(result["people"][0]["recent_activity"]) == 1
    assert len(result["companies"][0]["recent_news"]) == 1


def test_falls_back_to_json_mode_on_schema_400(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "fake")
    out = {"people": [], "companies": [], "open_questions": [], "confidence": "low"}
    err = _resp(status_code=400, text='{"error":"json_schema not supported on this plan"}')
    ok = _resp(_chat(json.dumps(out), citations=["https://reuters.com/y"]))
    with patch.object(httpx, "post", side_effect=[err, ok]):
        result = ppx.call(_REQUEST)
    assert result["transport"] == "json_mode"
    assert result["citations"][0]["source_tier"] == "secondary"


def test_falls_back_to_json_mode_when_body_mentions_model_and_schema(monkeypatch):
    """Regression: a 400 body containing BOTH 'json_schema' and 'model' must
    take the response_format-fallback path, not the model-rejection path."""
    monkeypatch.setenv("PERPLEXITY_API_KEY", "fake")
    out = {"people": [], "companies": [], "open_questions": [], "confidence": "low"}
    err = _resp(status_code=400,
                text='{"error":"json_schema not supported on this model"}')
    ok = _resp(_chat(json.dumps(out), citations=["https://reuters.com/y"]))
    with patch.object(httpx, "post", side_effect=[err, ok]):
        result = ppx.call(_REQUEST)
    assert result["transport"] == "json_mode"


def test_both_tiers_rejected_raises(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "fake")
    err1 = _resp(status_code=400, text='{"error":"json_schema not supported"}')
    err2 = _resp(status_code=400, text='{"error":"response_format json_object not supported"}')
    with patch.object(httpx, "post", side_effect=[err1, err2]):
        with pytest.raises(RuntimeError, match="rejected both"):
            ppx.call(_REQUEST)


def test_bad_model_400_raises_with_settings_hint(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "fake")
    err = _resp(status_code=400, text='{"error":"invalid model: sonar-xyz"}')
    with patch.object(httpx, "post", return_value=err):
        with pytest.raises(RuntimeError, match=r"settings\.yaml.*status=400"):
            ppx.call(_REQUEST)


def test_non_model_http_error_includes_status_and_body(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "fake")
    bad = _resp(status_code=500, text='{"error":"internal server error"}')
    with patch.object(httpx, "post", return_value=bad):
        with pytest.raises(RuntimeError, match=r"status=500.*internal server error"):
            ppx.call(_REQUEST)


def test_citation_drift_moves_claim_and_downgrades(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "fake")
    out = {
        "people": [{"name": "Jane", "recent_activity": [
            {"text": "good", "citation_ids": [1]},
            {"text": "drifted", "citation_ids": [99]},
        ]}],
        "companies": [], "open_questions": [], "confidence": "high",
    }
    sr = [{"url": "https://acme.com/x", "title": None, "date": None}]
    with patch.object(httpx, "post",
                      return_value=_resp(_chat(json.dumps(out), search_results=sr))):
        result = ppx.call(_REQUEST)
    assert len(result["people"][0]["recent_activity"]) == 1
    assert any("[drift:" in q and "drifted" in q for q in result["open_questions"])
    assert result["confidence"] == "medium"


def test_low_tier_only_claim_moves_and_downgrades(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "fake")
    out = {
        "people": [],
        "companies": [{"name": "Acme", "recent_news": [
            {"text": "twitter rumor", "citation_ids": [1]}
        ], "risk_flags": []}],
        "open_questions": [], "confidence": "high",
    }
    sr = [{"url": "https://twitter.com/someone/status/1", "title": None, "date": None}]
    with patch.object(httpx, "post",
                      return_value=_resp(_chat(json.dumps(out), search_results=sr))):
        result = ppx.call(_REQUEST)
    assert len(result["companies"][0]["recent_news"]) == 0
    assert any("[low-tier:" in q for q in result["open_questions"])
    assert result["confidence"] == "medium"


def test_unparseable_json_returns_degraded(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "fake")
    with patch.object(httpx, "post",
                      return_value=_resp(_chat("not json", citations=["https://example.com"]))):
        result = ppx.call(_REQUEST)
    assert result["people"] == [] and result["companies"] == []
    assert result["confidence"] == "low"
    assert result["citations"][0]["source_tier"] == "low"
    assert any("[degraded]" in q for q in result["open_questions"])


@pytest.mark.parametrize("bad_output, expected_match", [
    # invalid confidence enum (user's example)
    ({"people": [], "companies": [], "open_questions": [], "confidence": "maybe"},
     r"confidence.*maybe"),
    # non-list people
    ({"people": "not a list", "companies": [], "open_questions": [], "confidence": "low"},
     r"people.*list"),
    # non-string person name
    ({"people": [{"name": 123, "recent_activity": []}],
      "companies": [], "open_questions": [], "confidence": "low"},
     r"people\[0\]\.name.*string"),
    # empty person name
    ({"people": [{"name": "", "recent_activity": []}],
      "companies": [], "open_questions": [], "confidence": "low"},
     r"people\[0\]\.name.*non-empty"),
    # recent_activity as string instead of list
    ({"people": [{"name": "Jane", "recent_activity": "not a list"}],
      "companies": [], "open_questions": [], "confidence": "low"},
     r"recent_activity.*list"),
    # recent_news as string instead of list (user's specific example)
    ({"people": [],
      "companies": [{"name": "Acme", "recent_news": "not a list", "risk_flags": []}],
      "open_questions": [], "confidence": "low"},
     r"recent_news.*list"),
    # malformed citation_ids (not a list)
    ({"people": [{"name": "Jane",
                  "recent_activity": [{"text": "x", "citation_ids": "abc"}]}],
      "companies": [], "open_questions": [], "confidence": "low"},
     r"citation_ids.*list"),
    # non-int citation id
    ({"people": [{"name": "Jane",
                  "recent_activity": [{"text": "x", "citation_ids": [1, "two"]}]}],
      "companies": [], "open_questions": [], "confidence": "low"},
     r"citation_ids\[1\].*int"),
    # non-string open_question
    ({"people": [], "companies": [], "open_questions": [123], "confidence": "low"},
     r"open_questions\[0\].*string"),
    # company missing name
    ({"people": [], "companies": [{"recent_news": [], "risk_flags": []}],
      "open_questions": [], "confidence": "low"},
     r"companies\[0\]\.name.*string"),
    # claim text wrong type
    ({"people": [], "companies": [{
        "name": "Acme",
        "recent_news": [{"text": 42, "citation_ids": [1]}],
        "risk_flags": []
     }], "open_questions": [], "confidence": "low"},
     r"recent_news\[0\]\.text.*string"),
])
def test_structural_violations_raise_contract_error(monkeypatch, bad_output, expected_match):
    """Hard-fail boundary: structurally malformed payloads do NOT silently pass."""
    monkeypatch.setenv("PERPLEXITY_API_KEY", "fake")
    sr = [{"url": "https://acme.com/x", "title": None, "date": None}]
    with patch.object(httpx, "post",
                      return_value=_resp(_chat(json.dumps(bad_output), search_results=sr))):
        with pytest.raises(ppx.PerplexityContractError, match=expected_match):
            ppx.call(_REQUEST)


def test_classify_tier_buckets():
    cd = {"acme.com", "microsoft.com"}
    # primary: exact match
    assert ppx._classify_tier("https://acme.com/x", cd) == "primary"
    # primary: subdomains of company-owned domains (Q1 fix)
    assert ppx._classify_tier("https://news.microsoft.com/abc", cd) == "primary"
    assert ppx._classify_tier("https://blogs.microsoft.com/abc", cd) == "primary"
    assert ppx._classify_tier("https://investor.acme.com/q1", cd) == "primary"
    # negative: similar-looking host must NOT match
    assert ppx._classify_tier("https://notmicrosoft.com/x", cd) == "low"
    assert ppx._classify_tier("https://acme.com.attacker.com/x", cd) == "low"
    # primary: gov + regulators
    assert ppx._classify_tier("https://www.sec.gov/filing", cd) == "primary"
    assert ppx._classify_tier("https://anything.gov.uk/x", cd) == "primary"
    # secondary (existing)
    assert ppx._classify_tier("https://reuters.com/y", cd) == "secondary"
    assert ppx._classify_tier("https://en.wikipedia.org/wiki/X", cd) == "secondary"
    # secondary (new additions)
    assert ppx._classify_tier("https://arstechnica.com/x", cd) == "secondary"
    assert ppx._classify_tier("https://www.technologyreview.com/x", cd) == "secondary"
    assert ppx._classify_tier("https://npr.org/x", cd) == "secondary"
    assert ppx._classify_tier("https://hbr.org/x", cd) == "secondary"
    assert ppx._classify_tier("https://nasdaq.com/x", cd) == "secondary"
    assert ppx._classify_tier("https://nature.com/articles/x", cd) == "secondary"
    # low
    assert ppx._classify_tier("https://twitter.com/a/status/1", cd) == "low"
    assert ppx._classify_tier("https://youtube.com/watch?v=x", cd) == "low"
    assert ppx._classify_tier("https://randomblog.example/post", cd) == "low"


def test_search_domain_filter_passes_through_to_payload(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "fake")
    captured: dict[str, Any] = {}
    out = {"people": [], "companies": [], "open_questions": [], "confidence": "low"}

    def mock_post(url, *, headers, json, timeout):
        captured["payload"] = json
        return _resp(_chat(__import__("json").dumps(out), search_results=[]))

    with patch.object(httpx, "post", side_effect=mock_post):
        req = dict(_REQUEST)
        req["search_domain_filter"] = ["-youtube.com", "reuters.com"]
        ppx.call(req)
    assert captured["payload"]["search_domain_filter"] == ["-youtube.com", "reuters.com"]


def test_omitted_search_domain_filter_is_not_in_payload(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "fake")
    captured: dict[str, Any] = {}
    out = {"people": [], "companies": [], "open_questions": [], "confidence": "low"}

    def mock_post(url, *, headers, json, timeout):
        captured["payload"] = json
        return _resp(_chat(__import__("json").dumps(out), search_results=[]))

    with patch.object(httpx, "post", side_effect=mock_post):
        ppx.call(_REQUEST)
    assert "search_domain_filter" not in captured["payload"]
