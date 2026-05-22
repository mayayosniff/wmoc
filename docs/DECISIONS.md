# Architectural Decisions Log

Append-only. Each entry: date, decision, alternatives considered, why.

## 2026-05-20 — Claude is the only orchestrator
- Considered: round-robin orchestration, peer-to-peer agents.
- Chose Claude-as-hub because linear traces are debuggable; multi-agent crosstalk hides failures.

## 2026-05-20 — Deterministic > screen automation
- API > UIA > keyboard > pixel/OCR. Each fallback level is an order of magnitude more fragile.

## 2026-05-20 — CLI approval gate for v1
- Considered: Slack, email, web dashboard. CLI is simplest and forces us to design the data shape first; later UIs read the same queue.

## 2026-05-20 — Python as primary language
- Best library ecosystem for both Windows automation (`pywinauto`, `uiautomation`, `pyautogui`) and AI SDKs.
- Alternative (Node/TypeScript) was rejected because Windows UI Automation libraries are weaker there.

## 2026-05-20 — First workflow: Meeting Prep Brief
- Considered: email triage, file organizer, daily news briefing.
- Chose Meeting Prep because it (a) genuinely needs all four AIs, (b) is API-first so we don't need UI automation yet, (c) has a clear, visible artifact, (d) recurs naturally — useful every week.
