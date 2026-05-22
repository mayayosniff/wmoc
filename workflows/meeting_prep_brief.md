# Workflow: Meeting Prep Brief

## Goal
For the user's next meeting (or a chosen meeting), produce a concise prep brief covering: who is attending, what each attendee/company is doing right now, what attached materials say, what could go wrong, and what the user should walk in knowing.

## Why this workflow first
- Uses all four AIs naturally — no contrived role assignment.
- API-first: calendar + email + research APIs. No screen automation needed.
- Recurring value: useful every week, so improvements compound.
- One clear artifact (`brief.md`). Easy to judge success/failure.
- Read-mostly: only one risky action at the end (saving + optionally emailing the brief), so the approval gate is exercised but not constantly.

## Inputs
- Calendar source (Google Calendar OR Microsoft Graph — picked at setup).
- Optional: specific meeting ID. Default: next scheduled meeting in the next 24 hours.

## Steps

| # | Step                                | Agent / Tool          | Approval? |
|---|-------------------------------------|-----------------------|-----------|
| 1 | Fetch next meeting                  | tool: `calendar.next` | no        |
| 2 | Extract attendees, agenda, attachments | Claude              | no        |
| 3 | For each external attendee, research them and their company | Perplexity | no |
| 4 | If meeting has attached docs, summarize each | Gemini       | no        |
| 5 | Draft brief                         | Claude                | no        |
| 6 | Adversarial review of the brief     | Grok                  | no        |
| 7 | Revise brief addressing Grok's flags| Claude                | no        |
| 8 | Save `brief.md` to workspace        | tool: `fs.write`      | yes       |
| 9 | (Optional) Email brief to user      | tool: `mail.send`     | yes       |

Steps 8 and 9 stop at the approval gate. The user sees the full brief and can approve, deny, or edit before any side effect.

## Output artifact (brief.md format)
```
# Meeting Brief: <Meeting Title>
When: <ISO timestamp + local time>
Where: <location / link>
Duration: <minutes>

## Attendees
- Name (Role @ Company) — one-line "what they're up to"

## Agenda (from invite)
- ...

## Materials (from attachments)
- <Doc title>: one-sentence summary
  - Key points / decisions requested

## Walk-in Brief
- 3-5 bullets the user must know before the meeting starts

## Risks & Counterpoints (Grok pass)
- ...

## Open Questions
- ...

---
Sources: <citations from Perplexity step>
```

## Success criteria for the workflow
- Brief is generated end-to-end without manual intervention until the approval gate.
- All factual claims about people/companies have a citation.
- Grok's review is recorded in the run log, and any `blocking_problems` are visibly addressed.
- The user can approve, deny, or edit at the gate, and the resulting file matches what they approved.

## Out of scope (v1)
- Auto-scheduling follow-ups.
- Sending the brief to other attendees.
- Pulling in CRM data.
- Sentiment analysis of past emails with attendees.
