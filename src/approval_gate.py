"""
Approval gate — CLI implementation for v1.

Every step with requires_approval=True passes through here. The gate prints
a structured "approval card", waits for y/n/e, records the decision to an
append-only audit log, and returns the decision to the orchestrator.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal


Decision = Literal["approve", "deny", "edit"]


@dataclass
class ApprovalDecision:
    decision: Decision
    edited_input: dict[str, Any] | None = None
    decided_at: str = ""


class ApprovalGate:
    def __init__(self, audit_path: Path) -> None:
        self.audit_path = audit_path
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)

    def request(
        self,
        *,
        step_id: str,
        action: str,
        payload: dict[str, Any],
        reversible: bool,
    ) -> ApprovalDecision:
        print("\n" + "=" * 60)
        print(f"APPROVAL REQUIRED  ({step_id})")
        print("=" * 60)
        print(f"Action      : {action}")
        print(f"Reversible  : {'yes' if reversible else 'NO  <-- irreversible'}")
        print("Payload     :")
        print(json.dumps(payload, indent=2, default=str))
        print("=" * 60)
        print("[y] approve   [n] deny   [e] edit payload as JSON")
        choice = input("> ").strip().lower()

        if choice == "y":
            decision = ApprovalDecision("approve")
        elif choice == "e":
            print("Paste new JSON payload, end with a blank line:")
            lines: list[str] = []
            while True:
                line = input()
                if line == "":
                    break
                lines.append(line)
            try:
                edited = json.loads("\n".join(lines))
            except json.JSONDecodeError as e:
                print(f"Could not parse JSON ({e}). Treating as deny.")
                decision = ApprovalDecision("deny")
            else:
                decision = ApprovalDecision("approve", edited_input=edited)
        else:
            decision = ApprovalDecision("deny")

        decision.decided_at = datetime.now(timezone.utc).isoformat()
        self._audit(step_id, action, payload, decision)
        return decision

    def _audit(
        self,
        step_id: str,
        action: str,
        payload: dict[str, Any],
        decision: ApprovalDecision,
    ) -> None:
        record = {
            "step_id": step_id,
            "action": action,
            "payload": payload,
            "decision": decision.decision,
            "edited_input": decision.edited_input,
            "decided_at": decision.decided_at,
        }
        with self.audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
