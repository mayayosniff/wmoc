"""Mail tool — Phase 0 stub. Phase 3: wire to Gmail API or MS Graph sendMail."""
from __future__ import annotations

from typing import Any


def send(to: str, subject: str, body: str, attachments: list[str] | None = None) -> dict[str, Any]:
    # IRREVERSIBLE — never call without an approval decision recorded.
    return {"stub": True, "to": to, "subject": subject, "sent": False}
