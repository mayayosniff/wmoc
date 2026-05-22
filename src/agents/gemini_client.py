"""Gemini client — multimodal document/screen analysis per the WMOC↔Gemini contract.

INPUT CONTRACT
- Single public entry: call(path, *, goal, mode)
- path: str | Path to a local file (image or PDF). Files API delivery is deferred;
  this implementation reads inline bytes only.
- goal: non-empty string describing what to analyze.
- mode: "document" (text-heavy file) or "screen" (UI screenshot).

OUTPUT CONTRACT (GeminiResponse)
{
  "summary": str,
  "key_observations": list[str],
  "extracted_entities": list[str],
  "risks_or_anomalies": list[str],
  "open_questions": list[str],
  "confidence": "high" | "medium" | "low",
  "model": str,
  "mode": "document" | "screen"
}

TRANSPORT
- response_mime_type="application/json" + response_schema=<dict> via
  GenerateContentConfig. Provider-enforced structured output.

FAILURE HANDLING
- Missing GEMINI_API_KEY → RuntimeError with .env guidance.
- Invalid mode / empty goal → ValueError.
- Missing file / unsupported mime type / file too large → RuntimeError.
- Model rejection (ClientError with code in {400,403,404} and "model"-related body)
  → RuntimeError pointing to config/settings.yaml.
- Safety block (finish_reason in safety set), incomplete output, or JSON parse
  failure → degraded low-confidence response with the reason in open_questions.
- Structural violation of the parsed JSON → GeminiContractError.
- Other ClientError / ServerError / network → propagate with status + body
  via a RuntimeError wrapper.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Literal, TypedDict

import yaml
from google import genai
from google.genai import errors, types

_log = logging.getLogger("wmoc.gemini")

_SETTINGS_PATH = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"

Confidence = Literal["high", "medium", "low"]
Mode = Literal["document", "screen"]

_VALID_CONFIDENCE = {"high", "medium", "low"}
_VALID_MODES = {"document", "screen"}

# Inline-bytes threshold. Per Gemini docs, requests >~20 MB should use the Files API.
# We don't implement Files API yet — surface a clear error above this.
_MAX_INLINE_BYTES = 20 * 1024 * 1024

# Common multimodal mime types Gemini accepts on multimodal inputs.
_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
}

# Finish reasons that indicate the response was withheld or incomplete for safety
# or policy reasons; treated as degraded (not contract violations).
_DEGRADING_FINISH_REASONS = {
    "SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII",
    "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT", "IMAGE_RECITATION",
    "IMAGE_OTHER", "NO_IMAGE", "OTHER", "MALFORMED_FUNCTION_CALL",
    "UNEXPECTED_TOOL_CALL",
}


class GeminiResponse(TypedDict):
    summary: str
    key_observations: list[str]
    extracted_entities: list[str]
    risks_or_anomalies: list[str]
    open_questions: list[str]
    confidence: Confidence
    model: str
    mode: Mode


class GeminiContractError(RuntimeError):
    """Structural violation of the WMOC↔Gemini response contract."""


_RESPONSE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "key_observations": {"type": "array", "items": {"type": "string"}},
        "extracted_entities": {"type": "array", "items": {"type": "string"}},
        "risks_or_anomalies": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": [
        "summary", "key_observations", "extracted_entities",
        "risks_or_anomalies", "open_questions", "confidence",
    ],
}


_SYSTEM_PROMPT = """You are a multimodal analyst for a meeting-prep workflow. Read the supplied file (an image or PDF) and return ONLY a JSON object matching the schema. Rules:

- summary: 1-3 sentences describing what the file shows.
- key_observations: factual things visible in the file, one per item. Do not invent.
- extracted_entities: names, organizations, dates, identifiers, monetary amounts, or URLs visible in the file. Empty list if none.
- risks_or_anomalies: visible issues a careful reader should know — errors, warnings, missing fields, malformed data, signs of tampering. Empty if none.
- open_questions: things the file does not answer that would be needed to act on it confidently.
- confidence: "high" if the file is legible and you are sure of every observation; "medium" if some content is ambiguous or partially obscured; "low" if the file is mostly unreadable, very low resolution, or you are guessing.
- Do not include URLs from outside the file. Do not invent content. Empty arrays are fine."""


def _gemini_settings() -> dict[str, Any]:
    with _SETTINGS_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["specialists"]["gemini"]


def _detect_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in _MIME_BY_SUFFIX:
        raise RuntimeError(
            f"Unsupported file type {suffix!r} for {path.name}. "
            f"Supported: {sorted(_MIME_BY_SUFFIX.keys())}."
        )
    return _MIME_BY_SUFFIX[suffix]


# ---- Structural validator (hard fail) ----

def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise GeminiContractError(msg)


def _check_str(val: Any, path: str, *, non_empty: bool = False) -> None:
    _require(isinstance(val, str), f"{path}: expected string, got {type(val).__name__}")
    if non_empty:
        _require(bool(val), f"{path}: expected non-empty string")


def _check_str_list(val: Any, path: str) -> None:
    _require(isinstance(val, list), f"{path}: expected list, got {type(val).__name__}")
    for i, x in enumerate(val):
        _require(isinstance(x, str), f"{path}[{i}]: expected string, got {type(x).__name__}")


def _validate_structure(parsed: Any) -> None:
    """Raise GeminiContractError on any structural violation."""
    _require(isinstance(parsed, dict), f"top-level: expected dict, got {type(parsed).__name__}")
    _check_str(parsed.get("summary"), "summary")
    for key in ("key_observations", "extracted_entities", "risks_or_anomalies", "open_questions"):
        _check_str_list(parsed.get(key), key)
    conf = parsed.get("confidence")
    _require(conf in _VALID_CONFIDENCE,
             f"confidence: expected one of {sorted(_VALID_CONFIDENCE)}, got {conf!r}")


# ---- Degraded response builder ----

def _degraded(reason: str, model: str, mode: Mode) -> GeminiResponse:
    return {
        "summary": "",
        "key_observations": [],
        "extracted_entities": [],
        "risks_or_anomalies": [],
        "open_questions": [f"[degraded] {reason}"],
        "confidence": "low",
        "model": model,
        "mode": mode,
    }


# ---- Public entry ----

def call(path: str | Path, *, goal: str, mode: Mode) -> GeminiResponse:
    """Run multimodal analysis on a local file. See module docstring for contract."""
    if mode not in _VALID_MODES:
        raise ValueError(
            f"mode must be one of {sorted(_VALID_MODES)}, got {mode!r}"
        )
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("goal must be a non-empty string")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to config/.env "
            "(see config/.env.example for the line format)."
        )

    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        raise FileNotFoundError(f"Gemini input file not found: {file_path}")

    size = file_path.stat().st_size
    if size > _MAX_INLINE_BYTES:
        raise RuntimeError(
            f"File {file_path.name} is {size} bytes; exceeds inline limit "
            f"({_MAX_INLINE_BYTES} bytes). Files API delivery is not yet implemented."
        )

    mime_type = _detect_mime_type(file_path)
    file_bytes = file_path.read_bytes()

    cfg = _gemini_settings()
    model = cfg["model"]

    user_prompt = f"Mode: {mode}\nGoal: {goal}\n\nAnalyze the supplied file and return the JSON object."

    config = types.GenerateContentConfig(
        system_instruction=_SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_schema=_RESPONSE_JSON_SCHEMA,
        temperature=cfg.get("temperature", 0.2),
        max_output_tokens=cfg.get("max_output_tokens", 2048),
    )

    _log.info(
        "call -> model=%s mode=%s file=%s bytes=%d",
        model, mode, file_path.name, size,
    )

    client = genai.Client(api_key=api_key)
    try:
        response = client.models.generate_content(
            model=model,
            contents=[
                types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
                user_prompt,
            ],
            config=config,
        )
    except errors.ClientError as e:
        code = getattr(e, "code", None)
        err_text = str(e).lower()
        if code in (400, 403, 404) and (
            "model" in err_text or "not found" in err_text or "deployment" in err_text
        ):
            raise RuntimeError(
                f"Gemini rejected model {model!r} "
                f"(status={code}). Change specialists.gemini.model in "
                f"config/settings.yaml. Original error: {e}"
            ) from e
        raise RuntimeError(
            f"Gemini request failed: status={code} body={str(e)[:200]}"
        ) from e
    except errors.ServerError as e:
        raise RuntimeError(
            f"Gemini server error: status={getattr(e, 'code', None)} body={str(e)[:200]}"
        ) from e

    _log.info("call <- model=%s status=ok", model)

    # Inspect finish_reason for safety/policy blocks.
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        finish_reason = getattr(candidates[0], "finish_reason", None)
        finish_name = (
            finish_reason.name if hasattr(finish_reason, "name") else str(finish_reason)
        )
        if finish_name in _DEGRADING_FINISH_REASONS:
            _log.warning("degraded: finish_reason=%s", finish_name)
            return _degraded(f"Gemini finish_reason={finish_name}", model, mode)
        if finish_name == "MAX_TOKENS":
            _log.warning("degraded: response truncated (MAX_TOKENS)")
            return _degraded("response truncated at MAX_TOKENS", model, mode)

    text = getattr(response, "text", None)
    if not text:
        _log.warning("degraded: response.text empty")
        return _degraded("Gemini returned empty content", model, mode)

    try:
        parsed = json.loads(text.strip())
    except json.JSONDecodeError as e:
        _log.warning("JSON parse failed; first 200 chars: %r", text.strip()[:200])
        return _degraded(f"JSON parse failed: {e}", model, mode)

    _validate_structure(parsed)  # raises GeminiContractError on structural violations

    return {
        "summary": parsed["summary"],
        "key_observations": list(parsed["key_observations"]),
        "extracted_entities": list(parsed["extracted_entities"]),
        "risks_or_anomalies": list(parsed["risks_or_anomalies"]),
        "open_questions": list(parsed["open_questions"]),
        "confidence": parsed["confidence"],
        "model": model,
        "mode": mode,
    }
