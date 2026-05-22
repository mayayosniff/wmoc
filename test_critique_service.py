from dotenv import load_dotenv
load_dotenv("config/.env")

from src.services.critique_service import review_brief

brief = (
    "BRIEFING: Meeting Sundar Pichai, CEO of Microsoft, on May 22. "
    "Pichai founded Google in 1996 with Larry Page."
)

r = review_brief(
    brief,
    context="Internal pre-meeting brief for the CEO",
    brief_id="smoke-1",
)

print(r)
