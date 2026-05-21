from src.agents.grok_client import critique as grok_critique


def review_brief(brief_text: str, *, context: str, brief_id: str | None = None) -> dict:
    result = grok_critique(brief_text, context=context)
    return {
        "brief_id": brief_id,
        "model": result["model"],
        "verdict": result["verdict"],
        "factual_issues": result.get("factual_issues", []),
        "tone_issues": result.get("tone_issues", []),
        "blocking_problems": result.get("blocking_problems", []),
    }
