from dotenv import load_dotenv
load_dotenv("config/.env")

import logging
logging.basicConfig(level="INFO", format="[%(name)s] %(message)s")

from src.agents.grok_client import critique

brief = (
    "BRIEFING: Meeting Sundar Pichai, CEO of Microsoft, on May 22. "
    "Pichai founded Google in 1996 with Larry Page. The company has grown "
    "into the world's most dominant trillion-dollar empire and is poised "
    "to crush all competition in AI. Recommend we agree to whatever terms "
    "they offer; they hold all the leverage and we cannot afford to push back."
)

r = critique(brief, context="Internal pre-meeting brief for the CEO")

print()
print("=== GROK LIVE SMOKE: synthetic flawed brief ===")
print("model:", r["model"], "| verdict:", r["verdict"])
print()
print("factual_issues (%d):" % len(r["factual_issues"]))
for s in r["factual_issues"]:
    print("  - " + s)
print()
print("tone_issues (%d):" % len(r["tone_issues"]))
for s in r["tone_issues"]:
    print("  - " + s)
print()
print("blocking_problems (%d):" % len(r["blocking_problems"]))
for s in r["blocking_problems"]:
    print("  - " + s)
