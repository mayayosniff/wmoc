"""Grok client — adversarial critique per the WMOC↔Grok contract.

INPUT CONTRACT
- Single public entry: critique(artifact, *, context)
- artifact: non-empty string — the text to critique (typically a draft brief).
- context: optional string — what the brief is for, audience, constraints.

OUTPUT CONTRACT (GrokResponse)
{
  "factual_issues":     list[str],   # claims that are factually suspect
  "tone_issues":        list[str],   # tone/register problems for the audience
  "blocking_problems":  list[str],   # issues severe enough to block shipping
  "verdict":            "pass" | "revise" | "fail",
  "model":              str
}

TRANSPORT TIERS
- "structured" = response_format={"type": "json_schema", ...}. xAI enforces schema.
- "json_mode"  = response_format={"type": "json_object"}. Syntax-only enforcement;
  schema conformance falls to the runtime validator.
- Both rejected → RuntimeError with status + body.

FAILURE HANDLING
- Missing XAI_API_KEY → RuntimeError with .env guidance.
- Empty artifact → ValueError.
- Model rejection (400 with "model" in body) → RuntimeError pointing to settings.yaml.
- Other 4xx/5xx → RuntimeError with status + body snippet (no bare HTTPStatusError).
- Malformed JSON → degraded low-confidence response with reason recorded.
- Structural violation → GrokContractError.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Literal, TypedDict

import httpx
import yaml

_log = logging.getLogger("wmoc.grok")

_SETTINGS_PATH = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"
_API_URL = "https://api.x.ai/v1/chat/completions"

Verdict = Literal["pass", "revise", "fail"]
Transport = Literal["structured", "json_mode"]

_VALID_VERDICTS = {"pass", "revise", "fail"}


class GrokResponse(TypedDict):
    factual_issues: list[str]
    tone_issues: list[str]
    blocking_problems: list[str]
    verdict: Verdict
    model: str


class GrokContractError(RuntimeError):
    """Structural violation of the WMOC↔Grok response contract."""


_RESPONSE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "factual_issues":    {"type": "array", "items": {"type": "string"}},
        "tone_issues":       {"type": "array", "items": {"type": "string"}},
        "blocking_problems": {"type": "array", "items": {"type": "string"}},
        "verdict":           {"type": "string", "enum": ["pass", "revise", "fail"]},
    },
    "required": ["factual_issues", "tone_issues", "blocking_problems", "verdict"],
    "additionalProperties": False,
}


_SYSTEM_PROMPT = """You are an adversarial critic for a meeting-prep brief workflow. Your job is to find what is wrong with the supplied draft. Return ONLY a JSON object matching the schema.

Rules:
- factual_issues: claims in the draft that are factually wrong, unsupported by evidence, or internally contradictory. Be specific; quote the offending phrase where useful.
- tone_issues: places where the tone is inappropriate for a professional meeting brief — hype, condescension, undue certainty, vagueness, jargon without payoff.
- blocking_problems: issues severe enough that the brief should not ship as-is — fabricated quotes, missing critical context, defamatory framing, broken structure, key questions left unanswered.
- verdict:
  - "pass"   = no factual/blocking issues; tone issues are at most cosmetic. Brief is ready.
  - "revise" = at least one tone issue or one non-blocking factual issue. Brief needs editing but isn't broken.
  - "fail"   = at least one blocking_problems item. Brief must be substantially rewritten before shipping.
- Return empty arrays where you have nothing to say. Do not invent issues.
- Be terse: one issue per array item, no preamble."""


# ---- helpers ----

def _grok_settings() -> dict[str, Any]:
    with _SETTINGS_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["specialists"]["grok"]


def _build_user_prompt(artifact: str, context: str) -> str:
    parts = []
    if context:
        parts.append(f"Brief context: {context}")
    parts.append("Draft to critique:")
    parts.append(artifact)
    return "\n\n".join(parts)


# ---- Structural validator (hard fail) ----

def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise GrokContractError(msg)


def _check_str_list(val: Any, path: str) -> None:
    _require(isinstance(val, list), f"{path}: expected list, got {type(val).__name__}")
    for i, x in enumerate(val):
        _require(isinstance(x, str), f"{path}[{i}]: expected string, got {type(x).__name__}")


def _validate_structure(parsed: Any) -> None:
    _require(isinstance(parsed, dict), f"top-level: expected dict, got {type(parsed).__name__}")
    for key in ("factual_issues", "tone_issues", "blocking_problems"):
        _check_str_list(parsed.get(key), key)
    verdict = parsed.get("verdict")
    _require(verdict in _VALID_VERDICTS,
             f"verdict: expected one of {sorted(_VALID_VERDICTS)}, got {verdict!r}")


# ---- Transport (mirrors Perplexity) ----

def _attempt(api_key: str, cfg: dict[str, Any],
             artifact: str, context: str,
             transport: Transport) -> dict[str, Any] | None:
    """One attempt at a transport tier. Returns response dict on 200,
    None to signal fallback, raises RuntimeError on other failures."""
    payload: dict[str, Any] = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": _build_user_prompt(artifact, context)},
        ],
    }
    if transport == "structured":
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "grok_critique",
                "schema": _RESPONSE_JSON_SCHEMA,
                "strict": True,
            },
        }
    elif transport == "json_mode":
        payload["response_format"] = {"type": "json_object"}

    _log.info(
        "critique -> model=%s transport=%s artifact_chars=%d context_chars=%d",
        cfg["model"], transport, len(artifact), len(context),
    )
    resp = httpx.post(
        _API_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=cfg.get("timeout_seconds", 30),
    )
    _log.info("critique <- status=%d transport=%s", resp.status_code, transport)

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
            f"xAI rejected model {cfg['model']!r}. "
            f"Change specialists.grok.model in config/settings.yaml. "
            f"status={resp.status_code} body={snippet}"
        )
    raise RuntimeError(
        f"xAI request failed: status={resp.status_code} body={snippet}"
    )


def _execute(api_key: str, cfg: dict[str, Any],
             artifact: str, context: str) -> tuple[dict[str, Any], Transport]:
    data = _attempt(api_key, cfg, artifact, context, "structured")
    if data is not None:
        return data, "structured"
    data = _attempt(api_key, cfg, artifact, context, "json_mode")
    if data is not None:
        return data, "json_mode"
    raise RuntimeError(
        "xAI rejected both structured outputs and JSON mode. "
        "Verify the Grok tier on your account supports response_format."
    )


# ---- Public entry ----

def critique(artifact: str, *, context: str = "") -> GrokResponse:
    """Run a structured adversarial critique. See module docstring for contract."""
    if not isinstance(artifact, str) or not artifact.strip():
        raise ValueError("critique(artifact): expected non-empty string")
    if not isinstance(context, str):
        raise ValueError("critique(context): expected string (use '' for none)")

    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "XAI_API_KEY is not set. Add it to config/.env "
            "(see config/.env.example for the line format)."
        )

    cfg = _grok_settings()
    data, transport = _execute(api_key, cfg, artifact, context)

    content = data["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(content.strip())
    except json.JSONDecodeError as e:
        _log.warning("JSON parse failed on transport=%s; first 200 chars: %r",
                     transport, content.strip()[:200])
        return {
            "factual_issues": [],
            "tone_issues": [],
            "blocking_problems": [f"[degraded] JSON parse failed on transport={transport}: {e}"],
            "verdict": "fail",
            "model": cfg["model"],
        }

    _validate_structure(parsed)
    return {
        "factual_issues":    list(parsed["factual_issues"]),
        "tone_issues":       list(parsed["tone_issues"]),
        "blocking_problems": list(parsed["blocking_problems"]),
        "verdict":           parsed["verdict"],
        "model":             cfg["model"],
    }
