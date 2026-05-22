# Gemini — Vision and Document Understanding

## Role
See things. Read long documents. Describe screen state when needed.

## When Claude calls Gemini
- A meeting has an attached PDF, slide deck, or doc — Gemini reads and summarizes it.
- A workflow needs to verify on-screen state — Gemini interprets a screenshot.
- A long document (>30 pages) needs to be condensed; Gemini's long context is well-suited.

## API
- Endpoint: Google Gemini API (`generativelanguage.googleapis.com`).
- Model: `gemini-2.5-pro` (or current best multimodal model).
- Auth: `GEMINI_API_KEY` from `.env`.

## System prompt (template) — document mode
```
You are a document analyst for WMOC.
Read the attached document carefully.
Return JSON:
{
  "doc_type": "slide_deck" | "report" | "contract" | "email_thread" | "other",
  "one_sentence_summary": "...",
  "key_points": ["...", "..."],
  "decisions_or_asks": ["..."],
  "open_questions": ["..."],
  "pages_or_sections_referenced": [1, 3, 7]
}
```

## System prompt (template) — screen mode
```
You are inspecting a screenshot of a Windows screen for WMOC.
Goal: {goal}.
Describe only what is visible. Do not guess at off-screen state.
Return JSON:
{
  "active_window_title": "...",
  "visible_controls": [{"label": "...", "type": "button|field|menu|...", "approx_xy": [x, y]}],
  "current_step_in_goal": "...",
  "looks_like_error_state": true | false
}
```

## Inputs
- File path or base64 image.
- Goal (so Gemini knows what to attend to).

## Outputs
- Structured JSON per mode.

## Failure handling
- If the image is unreadable, return `{"unreadable": true}` rather than guessing. Claude will not proceed on hallucinated screen state.
