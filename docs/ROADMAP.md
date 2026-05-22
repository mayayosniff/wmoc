# WMOC Roadmap

A linear build order. Do not jump ahead — each phase removes a class of risk before the next phase adds new complexity.

## Phase 0 — Scaffold (today)
- Project directory and planning docs.
- Agent role prompts written.
- First workflow spec written.
- `.env.example` and `requirements.txt` in place.
- Empty orchestrator + approval gate stubs.
- **Exit criteria:** repo opens, imports succeed, `python -m src.orchestrator --dry-run` prints a fake plan.

## Phase 1 — Single-AI proof
- Claude (orchestrator) calls Perplexity for one research query and writes a Markdown brief to disk.
- No approval gate exercised yet (read-only).
- **Exit criteria:** run `python -m src.orchestrator research "What is the Anthropic MCP spec?"` and get a brief file out.

## Phase 2 — Approval gate
- Build CLI approval gate.
- Add a fake "send email" step that goes through the gate (still no real email sent).
- **Exit criteria:** running the workflow stops at the gate; `y` continues, `n` aborts, `edit` lets you modify the payload.

## Phase 3 — First real workflow: Meeting Prep Brief
- Pull next-meeting metadata from calendar API (Google or Microsoft Graph).
- Perplexity researches attendees + companies.
- Gemini summarizes any linked docs/attachments.
- Grok provides a "what could go wrong / contrarian angle" pass.
- Claude composes the final brief.
- Output: a .md file saved to disk + (gated) emailed/sent to user.
- **Exit criteria:** real meeting, real brief, real approval prompt before any send.

## Phase 4 — Add Windows automation (UIA)
- Pick one Windows app where API isn't available (e.g., a desktop client without a useful API).
- Use `pywinauto` to drive it for ONE action.
- Add to the tool registry.
- **Exit criteria:** orchestrator can invoke the UIA tool the same way it invokes Graph or Perplexity.

## Phase 5 — Generalize
- Add a second workflow.
- Extract anything that became copy-paste into shared helpers.
- Add replay-from-log.

## Phase 6 — Optional: pixel automation
- Only if a real use case demands it. Add `pyautogui` + OCR / Gemini vision for grounding.

## What we intentionally are NOT doing in v1
- No autonomous agent-to-agent chat.
- No background daemon.
- No web UI.
- No vector store / long-term memory beyond logs.
- No multi-user.
