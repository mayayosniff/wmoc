# WMOC — Next Steps (plain version)

You don't write any code. Claude Code does. You just run things and check
they work.

## 1. Get the project running

In a terminal, in the `wmoc` folder, type one at a time:

    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt

Copy `config\.env.example`, rename the copy to `.env`, and paste in your
Claude and Perplexity API keys. (Gemini and Grok can wait.)

Test it: `python -m src.orchestrator --dry-run` should print a list of 9
steps. If it does, you're good.

## 2. Open the project in Claude Code

The rest is asking Claude Code to do things. You check each one works
before moving on.

Ask, one at a time:

- "Make claude_client.py actually call the Claude API." Check it works.
- "Make perplexity_client.py actually call Perplexity. Ask it a simple
   question and show me the answer." Does the answer make sense?
- "Run the orchestrator with no --dry-run. When it asks for approval, try
   y, then n, then e. Make sure all three work." This is the safety brake.

## 3. Connect your calendar

Pick: Google Calendar or Outlook. Whichever you actually use.

Ask Claude Code: "Make the calendar tool fetch my next real meeting from
[Google / Outlook]. Show me what it returns."

Check that what it returns matches your actual calendar.

## 4. Connect Gemini and Grok

Get API keys for both. Add to `.env`. Then ask Claude Code:

- "Make gemini_client.py read a PDF and summarize it. Test with a real
   PDF on my computer."
- "Make grok_client.py critique a sample paragraph."

## 5. Wire it all together

Ask Claude Code: "Replace the fake plan with a real one from Claude. Run
the meeting prep workflow on my actual next meeting."

It should:
1. Find your next meeting
2. Research the attendees online
3. Read attached docs
4. Write a brief
5. Have Grok critique it
6. Pause and ask if it can save the brief
7. You say yes
8. The brief shows up as a file

If all that works — you have a working MVP.

## 6. Use it for real

Run it for a few real meetings. Note what's bad. Tell Claude Code to fix
those specific things.

## 7. Then plan what's next

Don't add anything else until step 6 is solid.

## Rules

- Do these in order.
- After each step, the system should still run.
- Never turn off the approval prompt.
- If something's weird, find out why. Don't paper over it.
