"""Perplexity client — structured meeting-prep research per the WMOC↔Perplexity contract.

TRANSPORT TIERS
- "structured" = response_format={"type": "json_schema", "json_schema": {"schema": ...}}.
  Perplexity enforces the schema server-side. This is the preferred path.
- "json_mode"  = response_format={"type": "json_object"}.
  Perplexity guarantees valid JSON SYNTAX only. Schema conformance is OUR responsibility
  via the runtime validator below. Treated as a fallback, not as schema enforcement.

If both tiers are rejected (rare; would indicate a Perplexity outage or extreme tier
restriction), we raise RuntimeError with status + body. We do NOT silently degrade
to prompt-only parsing.

CITATION SOURCE OF TRUTH
- Canonical citations come from data["search_results"] (preferred) or data["citations"]
  (fallback). The model emits only citation_ids (1-indexed integers). URLs that might
  appear in the model output are discarded.

VALIDATION (two layers)
- Structural: raises PerplexityContractError on type/enum/shape violations. Hard fail.
- Provenance: drift filter + low-tier-only filter; moves offending claims to
  open_questions and downgrades confidence by one step. Soft demotion.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Literal, TypedDict
from urllib.parse import urlparse

import httpx
import yaml

_log = logging.getLogger("wmoc.perplexity")

_SETTINGS_PATH = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
_API_URL = "https://api.perplexity.ai/chat/completions"

SourceTier = Literal["primary", "secondary", "low"]
Confidence = Literal["high", "medium", "low"]
Transport = Literal["structured", "json_mode"]

_VALID_CONFIDENCE = {"high", "medium", "low"}


class _PersonIn(TypedDict, total=False):
    name: str
    title: str
    company: str


class PerplexityRequest(TypedDict, total=False):
    intent: str
    people: list[_PersonIn]
    companies: list[str]
    meeting_context: str
    recency_days: int
    search_domain_filter: list[str]  # optional pass-through to Perplexity; "-domain.com" excludes


class _Claim(TypedDict):
    text: str
    citation_ids: list[int]


class _Citation(TypedDict):
    id: int
    url: str
    source_tier: SourceTier
    title: str | None
    published_date: str | None


class _PersonOut(TypedDict, total=False):
    name: str
    role: str | None
    recent_activity: list[_Claim]


class _CompanyOut(TypedDict, total=False):
    name: str
    one_liner: str | None
    recent_news: list[_Claim]
    risk_flags: list[_Claim]


class PerplexityResponse(TypedDict):
    people: list[_PersonOut]
    companies: list[_CompanyOut]
    open_questions: list[str]
    citations: list[_Citation]
    confidence: Confidence
    model: str
    transport: Transport


class PerplexityContractError(RuntimeError):
    """Structural violation of the WMOC↔Perplexity response contract."""


# ---- response_format JSON schema for tier 1 (structured) ----
_CLAIM_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "citation_ids": {
            "type": "array",
            "items": {"type": "integer", "minimum": 1},
            "minItems": 1,
        },
    },
    "required": ["text", "citation_ids"],
    "additionalProperties": False,
}
_RESPONSE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "people": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "role": {"type": ["string", "null"]},
                "recent_activity": {"type": "array", "items": _CLAIM_SCHEMA},
            },
            "required": ["name", "recent_activity"],
            "additionalProperties": False,
        }},
        "companies": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "one_liner": {"type": ["string", "null"]},
                "recent_news": {"type": "array", "items": _CLAIM_SCHEMA},
                "risk_flags": {"type": "array", "items": _CLAIM_SCHEMA},
            },
            "required": ["name", "recent_news", "risk_flags"],
            "additionalProperties": False,
        }},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["people", "companies", "open_questions", "confidence"],
    "additionalProperties": False,
}


# ---- source-tier tables ----
_TIER_PRIMARY_TLDS = (".gov", ".gov.uk", ".gc.ca", ".gov.au")
_TIER_PRIMARY_EXACT = {"sec.gov", "ftc.gov", "europa.eu"}
_TIER_SECONDARY = {
    # mainstream press + wire services (original set)
    "reuters.com", "ft.com", "bloomberg.com", "wsj.com", "nytimes.com",
    "economist.com", "theverge.com", "techcrunch.com", "axios.com",
    "cnbc.com", "forbes.com", "businessinsider.com",
    "en.wikipedia.org", "wikipedia.org",
    "apnews.com", "ap.org", "bbc.com", "bbc.co.uk",
    "theguardian.com", "washingtonpost.com",
    # +16 conservative expansion: additional mainstream tech/business/news/policy
    "arstechnica.com", "wired.com", "technologyreview.com",
    "npr.org", "fortune.com", "hbr.org",
    "marketwatch.com", "nasdaq.com",
    "spectrum.ieee.org", "nature.com", "science.org",
    "foreignaffairs.com", "politico.com", "semafor.com",
    "abcnews.go.com", "nbcnews.com", "cbsnews.com",
}

# Opt-in default exclusion list for retrieval-quality-sensitive calls.
# Callers wanting "serious brief" mode pass this as request["search_domain_filter"].
# NOT applied automatically — preserves caller intent for queries that legitimately
# want social/video sources (creator-economy, sentiment, etc.).
RECOMMENDED_EXCLUSIONS = [
    "-youtube.com", "-youtu.be",
    "-twitter.com", "-x.com",
    "-reddit.com", "-quora.com",
    "-facebook.com", "-instagram.com", "-tiktok.com",
]


_SYSTEM_PROMPT = """You are a research specialist for a meeting-prep workflow. Return ONLY a JSON object. Rules:

- Every claim in recent_activity, recent_news, and risk_flags MUST include at least one citation_id (1-indexed integer referring to a search result you used).
- Do NOT invent citation_ids. Do NOT include URLs in the JSON. Citations are tracked by the system from your search results.
- risk_flags must be FACTUAL EVENTS: layoffs, lawsuits, executive departures, regulatory action, data breaches, missed earnings, bankruptcy. Not subjective assessments.
- If a claim is supported only by weak or low-quality sources, move it to open_questions rather than asserting it. (Exception: a person's name or a company's one_liner may use any source.)
- confidence: "high" if all claims rest on clearly reputable sources; "medium" if mixed; "low" if predominantly weak or sparse.
- Return empty arrays if you have nothing to say. Do not invent.

Schema (treat this as authoritative whether or not it is enforced by response_format):
{
  "people":    [{"name": "str", "role": "str|null",      "recent_activity": [{"text": "str", "citation_ids": [int]}]}],
  "companies": [{"name": "str", "one_liner": "str|null", "recent_news":     [{"text": "str", "citation_ids": [int]}],
                                                          "risk_flags":      [{"text": "str", "citation_ids": [int]}]}],
  "open_questions": ["str"],
  "confidence": "high|medium|low"
}"""


# ---- helpers ----

def _perplexity_settings() -> dict[str, Any]:
    with _SETTINGS_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["specialists"]["perplexity"]


def _recency_filter(recency_days: int | None) -> str | None:
    if recency_days is None:
        return None
    if recency_days <= 1:
        return "day"
    if recency_days <= 7:
        return "week"
    if recency_days <= 31:
        return "month"
    return "year"


def _build_user_prompt(request: PerplexityRequest) -> str:
    people = request.get("people") or []
    companies = request.get("companies") or []
    meeting_context = request.get("meeting_context") or ""
    recency_days = request.get("recency_days", 90)

    parts: list[str] = []
    if people:
        lines: list[str] = []
        for p in people:
            line = f"  - {p.get('name','')}"
            if p.get("title"):
                line += f" ({p['title']})"
            if p.get("company"):
                line += f" at {p['company']}"
            lines.append(line)
        parts.append("People to research:\n" + "\n".join(lines))
    if companies:
        parts.append("Companies to research:\n" + "\n".join(f"  - {c}" for c in companies))
    if meeting_context:
        parts.append(f"Meeting context: {meeting_context}")
    parts.append(f"Apply recency window: {recency_days} days for recent_news and risk_flags.")
    return "\n\n".join(parts)


def _company_domains(companies: list[str]) -> set[str]:
    out: set[str] = set()
    for c in companies:
        compact = "".join(ch for ch in c.lower() if ch.isalnum())
        if compact:
            out.add(f"{compact}.com")
    return out


def _classify_tier(url: str, company_domains: set[str]) -> SourceTier:
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
    except Exception:
        return "low"
    if not host:
        return "low"
    # Company-owned: base domain OR any subdomain → primary.
    # Pattern `host.endswith("." + d)` requires a leading dot, so
    # `notmicrosoft.com` does NOT match `microsoft.com`.
    if any(host == d or host.endswith("." + d) for d in company_domains):
        return "primary"
    if host in _TIER_PRIMARY_EXACT:
        return "primary"
    if any(host.endswith(s) for s in _TIER_PRIMARY_TLDS):
        return "primary"
    if host in _TIER_SECONDARY:
        return "secondary"
    return "low"


def _extract_api_citations(data: dict[str, Any]) -> list[dict[str, Any]]:
    sr = data.get("search_results")
    if isinstance(sr, list) and sr:
        return [
            {"url": item.get("url", ""), "title": item.get("title"),
             "published_date": item.get("date") or item.get("published_date")}
            for item in sr if isinstance(item, dict) and item.get("url")
        ]
    raw = data.get("citations") or []
    if isinstance(raw, list):
        return [{"url": u, "title": None, "published_date": None}
                for u in raw if isinstance(u, str)]
    return []


def _build_canonical_citations(api_citations: list[dict[str, Any]],
                               company_domains: set[str]) -> list[_Citation]:
    out: list[_Citation] = []
    for i, c in enumerate(api_citations, start=1):
        out.append({
            "id": i,
            "url": c["url"],
            "source_tier": _classify_tier(c["url"], company_domains),
            "title": c.get("title"),
            "published_date": c.get("published_date"),
        })
    return out


def _downgrade(c: str) -> Confidence:
    return {"high": "medium", "medium": "low", "low": "low"}.get(c, "low")  # type: ignore[return-value]


# ---- Structural validator (Layer A: hard fail) ----

def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise PerplexityContractError(msg)


def _check_str(val: Any, path: str, *, nullable: bool = False, non_empty: bool = False) -> None:
    if nullable and val is None:
        return
    _require(isinstance(val, str), f"{path}: expected string, got {type(val).__name__}")
    if non_empty:
        _require(bool(val), f"{path}: expected non-empty string")


def _check_int_list(val: Any, path: str) -> None:
    _require(isinstance(val, list), f"{path}: expected list, got {type(val).__name__}")
    for i, x in enumerate(val):
        _require(isinstance(x, int) and not isinstance(x, bool),
                 f"{path}[{i}]: expected int, got {type(x).__name__}")


def _validate_claim(claim: Any, path: str) -> None:
    _require(isinstance(claim, dict), f"{path}: expected dict, got {type(claim).__name__}")
    _check_str(claim.get("text"), f"{path}.text")
    _check_int_list(claim.get("citation_ids"), f"{path}.citation_ids")


def _validate_structure(parsed: Any) -> None:
    """Raise PerplexityContractError on any structural violation. Does not mutate."""
    _require(isinstance(parsed, dict), f"top-level: expected dict, got {type(parsed).__name__}")

    for key in ("people", "companies", "open_questions"):
        _require(isinstance(parsed.get(key, []), list),
                 f"{key}: expected list, got {type(parsed.get(key)).__name__}")

    conf = parsed.get("confidence")
    _require(conf in _VALID_CONFIDENCE,
             f"confidence: expected one of {sorted(_VALID_CONFIDENCE)}, got {conf!r}")

    for i, q in enumerate(parsed.get("open_questions", [])):
        _check_str(q, f"open_questions[{i}]")

    for i, p in enumerate(parsed.get("people", [])):
        _require(isinstance(p, dict), f"people[{i}]: expected dict, got {type(p).__name__}")
        _check_str(p.get("name"), f"people[{i}].name", non_empty=True)
        _check_str(p.get("role"), f"people[{i}].role", nullable=True)
        ra = p.get("recent_activity", [])
        _require(isinstance(ra, list),
                 f"people[{i}].recent_activity: expected list, got {type(ra).__name__}")
        for j, claim in enumerate(ra):
            _validate_claim(claim, f"people[{i}].recent_activity[{j}]")

    for i, c in enumerate(parsed.get("companies", [])):
        _require(isinstance(c, dict), f"companies[{i}]: expected dict, got {type(c).__name__}")
        _check_str(c.get("name"), f"companies[{i}].name", non_empty=True)
        _check_str(c.get("one_liner"), f"companies[{i}].one_liner", nullable=True)
        for field in ("recent_news", "risk_flags"):
            arr = c.get(field, [])
            _require(isinstance(arr, list),
                     f"companies[{i}].{field}: expected list, got {type(arr).__name__}")
            for j, claim in enumerate(arr):
                _validate_claim(claim, f"companies[{i}].{field}[{j}]")


# ---- Provenance repair (Layer B: soft demotion) ----

def _repair_provenance(parsed: dict[str, Any],
                       canonical_citations: list[_Citation],
                       model: str, transport: Transport) -> PerplexityResponse:
    parsed.setdefault("people", [])
    parsed.setdefault("companies", [])
    parsed.setdefault("open_questions", [])

    valid_ids = {c["id"] for c in canonical_citations}
    tier_by_id = {c["id"]: c["source_tier"] for c in canonical_citations}
    drift = 0
    low_only = 0

    def process(claims: list[dict[str, Any]], owner: str) -> list[dict[str, Any]]:
        nonlocal drift, low_only
        kept: list[dict[str, Any]] = []
        for claim in claims:
            cids = claim["citation_ids"]
            if not cids or any(cid not in valid_ids for cid in cids):
                drift += 1
                parsed["open_questions"].append(f"[drift:{owner}] {claim['text']}")
                continue
            tiers = {tier_by_id[cid] for cid in cids}
            if tiers == {"low"}:
                low_only += 1
                parsed["open_questions"].append(f"[low-tier:{owner}] {claim['text']}")
                continue
            kept.append(claim)
        return kept

    for person in parsed["people"]:
        person["recent_activity"] = process(
            person.get("recent_activity") or [], f"person:{person['name']}"
        )
    for company in parsed["companies"]:
        cname = company["name"]
        company["recent_news"] = process(company.get("recent_news") or [], f"company:{cname}:news")
        company["risk_flags"] = process(company.get("risk_flags") or [], f"company:{cname}:risk")

    confidence: Confidence = parsed["confidence"]
    if drift or low_only:
        _log.warning("repair: drift=%d low_tier_only=%d (confidence downgraded)", drift, low_only)
        confidence = _downgrade(confidence)

    return {
        "people": parsed["people"],
        "companies": parsed["companies"],
        "open_questions": parsed["open_questions"],
        "citations": canonical_citations,
        "confidence": confidence,
        "model": model,
        "transport": transport,
    }


# ---- Transport: tiered attempts with uniform error handling ----

def _attempt(api_key: str, cfg: dict[str, Any],
             request: PerplexityRequest, recency: str | None,
             transport: Transport) -> dict[str, Any] | None:
    """One attempt at a transport tier.
    Returns parsed response dict on success, None to signal fallback to next tier.
    Raises RuntimeError on any other failure (with status + body snippet)."""
    base_messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(request)},
    ]
    payload: dict[str, Any] = {"model": cfg["model"], "messages": base_messages}
    if recency:
        payload["search_recency_filter"] = recency
    domain_filter = request.get("search_domain_filter")
    if domain_filter:
        payload["search_domain_filter"] = list(domain_filter)
    if transport == "structured":
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"schema": _RESPONSE_JSON_SCHEMA},
        }
    elif transport == "json_mode":
        payload["response_format"] = {"type": "json_object"}

    _log.info(
        "call -> model=%s transport=%s recency=%s people=%d companies=%d domain_filter=%d",
        cfg["model"], transport, recency,
        len(request.get("people") or []), len(request.get("companies") or []),
        len(domain_filter or []),
    )
    resp = httpx.post(
        _API_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=cfg.get("timeout_seconds", 30),
    )
    _log.info("call <- status=%d transport=%s", resp.status_code, transport)

    if resp.status_code == 200:
        return resp.json()

    body = resp.text or ""
    snippet = body[:200] if body else "(empty body)"
    body_l = body.lower()

    # Order matters: detect feature-rejection (response_format / json_schema / etc.)
    # BEFORE model-rejection. A body like "json_schema not supported on this model"
    # contains the substring "model" but is a feature failure, not a model failure.
    if resp.status_code == 400 and any(s in body_l for s in
                                       ("response_format", "json_schema", "json_object", "structured")):
        _log.warning(
            "transport=%s not accepted (status=400); falling back to next tier. body=%s",
            transport, snippet,
        )
        return None

    if resp.status_code == 400 and "model" in body_l:
        raise RuntimeError(
            f"Perplexity rejected model {cfg['model']!r}. "
            f"Change specialists.perplexity.model in config/settings.yaml. "
            f"status={resp.status_code} body={snippet}"
        )

    raise RuntimeError(
        f"Perplexity request failed: status={resp.status_code} body={snippet}"
    )


def _execute(api_key: str, cfg: dict[str, Any],
             request: PerplexityRequest, recency: str | None) -> tuple[dict[str, Any], Transport]:
    """Try structured first, fall back to json_mode. Both rejected → raise."""
    data = _attempt(api_key, cfg, request, recency, "structured")
    if data is not None:
        return data, "structured"
    data = _attempt(api_key, cfg, request, recency, "json_mode")
    if data is not None:
        return data, "json_mode"
    raise RuntimeError(
        "Perplexity rejected both structured outputs and JSON mode. "
        "Verify the Sonar tier on your account supports response_format."
    )


# ---- Public entry ----

def call(request: PerplexityRequest) -> PerplexityResponse:
    """Run a structured meeting-prep research request. See module docstring."""
    cfg = _perplexity_settings()
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        raise RuntimeError(
            "PERPLEXITY_API_KEY is not set. Add it to config/.env "
            "(see config/.env.example for the line format)."
        )
    if not request.get("intent"):
        raise ValueError("PerplexityRequest.intent is required")
    if not (request.get("people") or request.get("companies")):
        raise ValueError("PerplexityRequest requires at least one of `people` or `companies`")

    recency = _recency_filter(request.get("recency_days", 90))
    data, transport = _execute(api_key, cfg, request, recency)

    company_domains = _company_domains(request.get("companies") or [])
    canonical_citations = _build_canonical_citations(
        _extract_api_citations(data), company_domains
    )

    content = data["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(content.strip())
    except json.JSONDecodeError as e:
        _log.warning("JSON parse failed on transport=%s; first 200 chars: %r",
                     transport, content.strip()[:200])
        return {
            "people": [],
            "companies": [],
            "open_questions": [f"[degraded] JSON parse failed on transport={transport}: {e}"],
            "citations": canonical_citations,
            "confidence": "low",
            "model": cfg["model"],
            "transport": transport,
        }

    _validate_structure(parsed)  # raises PerplexityContractError on structural violations
    return _repair_provenance(parsed, canonical_citations, cfg["model"], transport)
