# WMOC — Next Steps (the build sequence)

A strict ordering. After each step, the system should still run end-to-end
(with stubs filling in what isn't wired yet). Never have two broken pieces
at once.

## Step 0 — Environment (5 min, once)

1. Open a terminal in this folder.
2. `python -m venv .venv`
3. `.venv\Scripts\activate`  (Windows) or `source .venv/bin/activate` (mac/linux)
4. `pip install -r requirements.txt`
5. Copy `config\.env.example` to `.env`. Fill in only the keys you have today.
   Anthropic + Perplexity is enough to start.
6. Sanity check: `python -m src.orchestrator --dry-run` prints the 9-step plan.

## Step 1 — Wire Claude for real

7. Open `src/agents/claude_client.py`.
8. Replace the `plan()` stub with a real Anthropic API call. Use the system
   prompt from `agents/claude_orchestrator.md` and the JSON plan shape from
   `docs/ARCHITECTURE.md`. Return `list[dict]`.
9. Add `tests/test_claude_client.py` asserting the returned plan is a
   non-empty list of dicts with `step_id` and `type`.

## Step 2 — Wire Perplexity for real

10. Open `src/agents/perplexity_client.py`.
11. Replace `call()` with an httpx POST to
    `https://api.perplexity.ai/chat/completions`. Use the system prompt from
    `agents/perplexity_researcher.md`. Parse to the JSON shape in that file.
    30-second timeout, one retry.
12. Smoke test: call `perplexity_client.call("What is the Anthropic MCP spec?")`
    and print. If real citations come back, Phase 1 is done.

## Step 3 — Exercise the approval gate

13. Run `python -m src.orchestrator` (no --dry-run).
14. The orchestrator runs the stub plan and stops at `s8` and `s9`.
15. Try `y`, `n`, and `e`. Verify `logs/approvals.jsonl` records all three.
    If yes, the gate is trustworthy.

## Step 4 — Wire the calendar tool

16. Pick one: Google Calendar or Microsoft Graph. Don't do both.
17. Replace `src/tools/calendar.py:next_event()` with a real call.
18. Return: `title`, `start`, `end`, `location`, `attendees` (list of
    {name, email}), `attachments` (list of file refs).
19. Hand-verify against a real event on your calendar.

## Step 5 — Wire Gemini (document mode only)

20. Replace `src/agents/gemini_client.py:call()` using the
    `google-generativeai` SDK and the document-mode prompt from
    `agents/gemini_vision.md`.
21. Test against a real PDF or deck on disk.

## Step 6 — Wire Grok

22. Replace `src/agents/grok_client.py:critique()` with an httpx POST to
    `https://api.x.ai/v1/chat/completions` and the critic-mode prompt from
    `agents/grok_critic.md`.
23. On failure, return `{"verdict": "do_not_send", "blocking_problems":
    ["grok unreachable"]}` so the gate catches it.

## Step 7 — Replace the stub plan with real Claude planning

24. In `src/orchestrator.py`, delete `stub_plan_meeting_prep()`.
25. Call `claude_client.plan(goal, tool_registry=[...])` instead.
26. Add a dispatch function that routes a `Step` to the right client/tool by
    `type` and `agent_or_tool`.
27. Run `python -m src.orchestrator meeting_prep`. Approve the save. Open
    `brief.md`.

## Step 8 — Tighten before celebrating

28. Inspect `logs/runs/<latest>/run.jsonl`. No `{"stub": true}` results
    should remain.
29. Run against three real meetings. Note where the brief is vague, wrong,
    or missing context. Those notes feed Phase 5.
30. `git init && git add . && git commit -m "wmoc mvp"`

## Then, and only then

31. Pick the second workflow (email triage or research digest).
32. Resist adding Windows UI Automation until a real workflow needs it.
    Pixel/OCR automation is further out still.

## Rules that apply throughout

- Wire one component at a time. After each step, the system still runs.
- Never "fix" weird tool output in the orchestrator. Fix it in the client,
  or surface it as a failure. The orchestrator trusts what clients return.
- Never add a "skip gate" flag for convenience. Use `--dry-run` to skip the
  gate during testing. The moment the gate can be bypassed, it stops being
  trustworthy.
- Every prompt edit in `agents/*.md` deserves a re-run against a known
  input. Prompt edits are silent bugs in waiting.
