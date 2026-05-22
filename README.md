# Windows Multi-Agent Operations Copilot (WMOC)

A reliability-first system that coordinates multiple AIs (Claude, Perplexity, Gemini, Grok) to automate repetitive workflows on a Windows PC. Claude is the orchestrator. Every risky action passes through an approval gate before executing.

## Guiding Principles

1. Reliability beats speed. A workflow that works 99% of the time is worth more than one that works 80% twice as fast.
2. Deterministic first. Use vendor APIs (Microsoft Graph, Google APIs, etc.) before falling back to Windows UI Automation, and only use mouse/keyboard screen automation as a last resort.
3. Narrow before wide. Ship one workflow end-to-end before adding a second.
4. Approval before destruction. Any action that writes, sends, deletes, or modifies external state pauses for human approval.
5. Every action is logged. Reproducible, inspectable, revertible where possible.

## Project Layout

```
wmoc/
  README.md              -> you are here
  docs/
    ARCHITECTURE.md      -> how the system is structured
    ROADMAP.md           -> phased build plan
    DECISIONS.md         -> log of architectural choices and why
  agents/
    claude_orchestrator.md
    perplexity_researcher.md
    gemini_vision.md
    grok_critic.md
  workflows/
    meeting_prep_brief.md   -> first workflow spec
  src/
    orchestrator.py        -> entry point, wires agents + tools + approval gate
    approval_gate.py       -> CLI approval queue
    agents/                -> Python clients per AI
    tools/                 -> API + Windows automation tools
  config/
    .env.example           -> required environment variables
    settings.yaml          -> non-secret runtime config
  logs/                    -> run logs and approval audit trail
  requirements.txt
```

## Status

Phase 0: scaffolding (you are here).
Next: implement Meeting Prep Brief workflow end-to-end before generalizing.

See `docs/ROADMAP.md` for the full phase plan.
