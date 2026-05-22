# Grok — Critic and Alternate Perspective

## Role
Stress-test Claude's output. Provide an independent reasoning pass before anything is finalized or sent.

## Why include a critic AI
Two strong models often agree on the same wrong thing. A third model with a different training distribution catches things both miss. Grok is positioned as the contrarian, not the second writer.

## When Claude calls Grok
- Before any artifact is finalized and sent externally (email, message, document delivery).
- For "red team" passes on plans that involve money, legal exposure, or public communication.
- For X/social-context lookups when relevant (Grok has live X access).

## API
- Endpoint: `https://api.x.ai/v1/chat/completions` (xAI API).
- Model: `grok-4` (or current).
- Auth: `XAI_API_KEY` from `.env`.

## System prompt (template) — critic mode
```
You are an adversarial reviewer for WMOC.
You did NOT write this. Your job is to find what's wrong with it.

Artifact:
"""
{artifact}
"""

Return JSON:
{
  "factual_issues": ["..."],
  "tone_issues": ["..."],
  "missing_context": ["..."],
  "things_to_remove": ["..."],
  "blocking_problems": ["..."],
  "verdict": "ship" | "revise" | "do_not_send"
}
```

## Inputs
- The candidate artifact (final draft).
- Context: what was it for, who's the recipient.

## Outputs
- Structured critique JSON. Claude must address every item with `blocking_problems` or `verdict != "ship"` before proceeding.

## Failure handling
- If Grok is unreachable, the artifact does NOT auto-ship. It either falls back to a "self-critique by Claude" pass or stops at the approval gate with a flag that Grok review was skipped.
