# WMOC Architecture (v1)

## Mental Model

A user task enters at the top. Claude (the orchestrator) decomposes it into steps. Each step is dispatched to either a specialist AI or a deterministic tool. Steps that mutate external state stop at the approval gate. Everything is logged.

```
+----------------------------------------------------------+
|                       USER REQUEST                       |
+----------------------------------------------------------+
                            |
                            v
+----------------------------------------------------------+
|           ORCHESTRATOR  (Claude)                         |
|  - parses goal                                           |
|  - plans steps                                           |
|  - dispatches to specialist AIs or tools                 |
|  - assembles final output                                |
+----------------------------------------------------------+
       |              |              |              |
       v              v              v              v
+-----------+   +-----------+   +-----------+   +-----------+
| Perplexity|   |  Gemini   |   |   Grok    |   |   Tools   |
| (research)|   | (vision/  |   |  (critic/ |   | (APIs &   |
|           |   |  docs)    |   |   altpov) |   | Win auto) |
+-----------+   +-----------+   +-----------+   +-----------+
                                                      |
                                                      v
                                            +-------------------+
                                            |  APPROVAL GATE    |
                                            | (blocks risky ops)|
                                            +-------------------+
                                                      |
                                                      v
                                            +-------------------+
                                            |   EXECUTION       |
                                            |   + AUDIT LOG     |
                                            +-------------------+
```

## Layers

### 1. Orchestrator (Claude)
- Owns the run loop.
- Maintains a plan: an ordered list of steps with `{step_id, type, payload, requires_approval}`.
- Calls specialists or tools by name. Never executes risky operations directly.
- Writes a structured run log.

### 2. Specialist AIs
| AI         | Primary role                                            | Why this AI                                      |
|------------|---------------------------------------------------------|--------------------------------------------------|
| Claude     | Orchestrator, planner, writer, code generator           | Strong reasoning + tool use, drives the loop     |
| Perplexity | Live web research with citations                        | Fresh-web search beats stale model knowledge     |
| Gemini     | Multimodal: screen reading, document/PDF understanding  | Strong vision, long-context document parsing     |
| Grok       | Adversarial reviewer / X-context lookups                | Independent reasoning pass + social signal       |

All four are reachable over HTTPS. None require local model hosting in v1.

### 3. Tool layer (deterministic, ranked by preference)
1. **Vendor APIs** — Microsoft Graph, Google Calendar/Gmail, Slack, etc. Best reliability.
2. **Windows UI Automation (UIA)** via `pywinauto` or `uiautomation` — driven by control trees, not pixels. Survives layout changes.
3. **Keyboard automation** via `keyboard` / `pynput` — for shortcuts inside an already-focused app.
4. **Screen automation** via `pyautogui` + OCR (Tesseract) or a vision model — last resort only.

### 4. Approval gate
- Every step has `requires_approval: bool`. Risky == anything that sends, writes externally, deletes, or modifies files outside the project workspace.
- v1 implementation: CLI prompt. The orchestrator prints a structured "approval card" (action, target, payload, reversible y/n) and waits for `y`/`n`/`edit`.
- v2: Slack/email/web approvals.
- Every approval decision is appended to `logs/approvals.jsonl`.

### 5. Audit log
- `logs/runs/<timestamp>/run.jsonl` — every step the orchestrator took.
- `logs/approvals.jsonl` — every approval prompt and decision.
- Designed so a future "replay" command can re-run from the log.

## Communication Between AIs

In v1, AIs do **not** talk to each other directly. Claude is the hub. This is deliberate: it keeps the trace linear, prevents loops, and makes failures debuggable. Multi-agent crosstalk can come in v3 once observability is solid.

## Data shapes (sketch)

```python
# A step the orchestrator plans
{
  "step_id": "s3",
  "type": "research" | "vision" | "critique" | "tool_call" | "compose",
  "agent": "perplexity" | "gemini" | "grok" | "claude" | None,
  "tool":  None | "gcal.list_events" | "graph.send_mail" | ...,
  "input": { ... },
  "requires_approval": false,
  "reversible": true
}

# An approval decision
{
  "step_id": "s7",
  "decision": "approve" | "deny" | "edit",
  "edited_input": { ... } | None,
  "decided_at": "2026-05-20T12:00:00Z"
}
```

## Failure model

- Any specialist call has a timeout and one retry. After that, the step is marked failed and surfaced to the user, not silently retried.
- Tool calls that mutate state are idempotent where possible (use client-supplied IDs).
- The orchestrator must never invent action results. If a tool returns nothing, the next step sees that, not a hallucinated success.
