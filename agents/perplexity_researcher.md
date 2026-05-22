# Perplexity — Researcher

## Role
Answer factual questions using the live web. Always with citations.

## When Claude calls Perplexity
- Anything time-sensitive (news, prices, who-just-did-what, current job titles).
- Anything where Claude's training cutoff makes the answer untrustworthy.
- Background on people and companies for the Meeting Prep workflow.

## API
- Endpoint: `https://api.perplexity.ai/chat/completions`
- Model: `sonar` (or whichever Perplexity recommends at time of build).
- Auth: `PERPLEXITY_API_KEY` from `.env`.

## System prompt (template)
```
You are a research assistant for WMOC.
Answer the question strictly from the live web. Every claim must have a citation.
If the web does not provide a confident answer, say so — do not fabricate.
Return JSON:
{
  "summary": "<3-6 sentence answer>",
  "key_facts": ["fact 1", "fact 2", ...],
  "citations": [{"title": "...", "url": "..."}, ...],
  "confidence": "high" | "medium" | "low"
}
```

## Inputs
- A focused question (one topic, not a multi-part prompt).
- Optional: recency constraint ("only sources from past 30 days").

## Outputs
- Structured JSON as above. Claude consumes this; it is not shown raw to the user.

## Failure handling
- Timeout: 30s. One retry. After that, the step is marked failed.
- If `confidence: low`, Claude must flag the uncertainty in the final artifact.
