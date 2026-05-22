"""
WMOC orchestrator — entry point.

Phase 0 stub: this file exists to anchor the wiring. It does NOT yet make real
API calls. Running it with `--dry-run` should print a fake plan so you can see
the data flow without any external dependencies.

Run:
    python -m src.orchestrator --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import broker
from .approval_gate import ApprovalDecision, ApprovalGate


# --------------------------------------------------------------------------- #
# Data shapes
# --------------------------------------------------------------------------- #


@dataclass
class Step:
    step_id: str
    type: str  # "research" | "vision" | "critique" | "tool_call" | "compose"
    agent_or_tool: str  # "perplexity" | "gemini" | "grok" | "claude" | ...
    input: dict[str, Any]
    requires_approval: bool = False
    reversible: bool = True
    result: dict[str, Any] | None = None
    status: str = "pending"  # pending | running | done | failed | denied


@dataclass
class RunLog:
    run_id: str
    started_at: str
    goal: str
    steps: list[Step] = field(default_factory=list)

    def write(self, runs_dir: Path) -> Path:
        run_dir = runs_dir / self.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        out = run_dir / "run.jsonl"
        with out.open("w", encoding="utf-8") as f:
            for step in self.steps:
                f.write(json.dumps(asdict(step), default=str) + "\n")
        return out


# --------------------------------------------------------------------------- #
# Stub plan — replaced in Phase 1+ with real Claude planning
# --------------------------------------------------------------------------- #


def stub_plan_meeting_prep() -> list[Step]:
    """Hard-coded plan that mirrors workflows/meeting_prep_brief.md."""
    from .agents import perplexity_client

    return [
        Step("s1", "tool_call", "calendar.next", {"window_hours": 24}),
        Step("s2", "compose", "claude", {"task": "extract_attendees_agenda"}),
        Step(
            "s3",
            "research",
            "perplexity",
            {
                "request": {
                    "intent": "meeting_prep_research",
                    "people": ["Sundar Pichai"],
                    "companies": ["Alphabet"],
                    "meeting_context": "Executive meeting prep",
                    "recency_days": 90,
                    "search_domain_filter": perplexity_client.RECOMMENDED_EXCLUSIONS,
                }
            },
        ),
        Step("s4", "vision", "gemini", {"task": "summarize_attached_docs"}),
        Step("s5", "compose", "claude", {"task": "draft_brief"}),
        Step(
            "s6",
            "critique",
            "grok",
            {
                "task": "adversarial_review",
                "context": "Internal pre-meeting brief for the CEO",
            },
        ),
        Step("s7", "compose", "claude", {"task": "revise_brief"}),
        Step(
            "s8",
            "tool_call",
            "fs.write",
            {"path": "brief.md"},
            requires_approval=True,
        ),
        Step(
            "s9",
            "tool_call",
            "mail.send",
            {"to": "user@example.com"},
            requires_approval=True,
            reversible=False,
        ),
    ]


# --------------------------------------------------------------------------- #
# Broker wiring smoke (Phase 0 minimal slice)
# --------------------------------------------------------------------------- #


def broker_smoke(db_path: Path) -> int:
    """Post one pc -> screen_left message via the broker and fetch it back.

    Phase 0 only. Does not touch the step plan, approval gate, or any agent
    client. Exists to confirm the orchestrator can drive broker.post / fetch.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = broker.connect(str(db_path))
    try:
        broker.init_db(conn)
        mid = broker.post(
            conn,
            from_role="pc",
            to_role="screen_left",
            type="status",
            subject="broker smoke",
            body="hello from pc",
        )
        print(f"[WMOC] broker: posted message id={mid} (db={db_path})")

        rows = broker.fetch(conn, to_role="screen_left")
        match = next((r for r in rows if r["id"] == mid), None)
        if match is None:
            print(
                f"[WMOC] broker: ERROR — posted id={mid} not visible to screen_left"
            )
            return 1
        print("[WMOC] broker: fetched message for screen_left:")
        print(json.dumps(match, indent=2, default=str))
        return 0
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Run loop
# --------------------------------------------------------------------------- #


def run(goal: str, dry_run: bool, runs_dir: Path) -> int:
    from .agents import claude_client, gemini_client, perplexity_client
    from .services.critique_service import review_brief

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log = RunLog(run_id=run_id, started_at=run_id, goal=goal)
    gate = ApprovalGate(audit_path=runs_dir.parent / "approvals.jsonl")

    log.steps = stub_plan_meeting_prep()

    print(f"\n[WMOC] Plan ({len(log.steps)} steps) for goal: {goal!r}")
    for s in log.steps:
        marker = "  GATE" if s.requires_approval else "      "
        print(f"  {marker}  {s.step_id}  {s.type:<10}  {s.agent_or_tool}")

    if dry_run:
        brief_text = (
            "BRIEFING: Meeting Sundar Pichai, CEO of Microsoft, on May 22. "
            "Pichai founded Google in 1996 with Larry Page."
        )

        critique = review_brief(
            brief_text,
            context="Internal pre-meeting brief for the CEO",
            brief_id="orchestrator-smoke-1",
        )

        print("\n[WMOC] --dry-run: critique smoke")
        print(json.dumps({"dry_run": True, "critique": critique}, indent=2))

        out = log.write(runs_dir)
        print(f"\n[WMOC] Run log written to {out}")
        return 0

    for step in log.steps:
        step.status = "running"

        if step.requires_approval:
            decision: ApprovalDecision = gate.request(
                step_id=step.step_id,
                action=step.agent_or_tool,
                payload=step.input,
                reversible=step.reversible,
            )
            if decision.decision == "deny":
                step.status = "denied"
                print(f"[WMOC] {step.step_id} denied. Stopping.")
                break
            if decision.decision == "edit" and decision.edited_input is not None:
                step.input = decision.edited_input

        dispatch_log = logging.getLogger("wmoc.dispatcher")

        try:
            if step.agent_or_tool == "claude" and step.type == "compose":
                text = claude_client.compose(
                    task=step.input.get("task", ""),
                    context=step.input,
                )
                step.result = {"text": text}

            elif step.agent_or_tool == "grok" and step.type == "critique":
                previous_draft = ""
                for prior_step in log.steps:
                    if (
                        prior_step.step_id == "s5"
                        and prior_step.result
                        and isinstance(prior_step.result, dict)
                    ):
                        previous_draft = prior_step.result.get("text", "")
                        break

                brief_text = step.input.get("brief_text") or step.input.get("draft") or previous_draft
                context = step.input.get("context") or "Internal pre-meeting brief for the CEO"

                critique = review_brief(
                    brief_text,
                    context=context,
                    brief_id=step.step_id,
                )
                step.result = critique

            elif step.agent_or_tool == "perplexity":
                request = step.input.get("request") or {
                    "intent": "meeting_prep_research",
                    "people": [],
                    "companies": [],
                    "meeting_context": step.input.get("topic", ""),
                    "recency_days": step.input.get("recency_days", 90),
                    "search_domain_filter": perplexity_client.RECOMMENDED_EXCLUSIONS,
                }
                step.result = perplexity_client.call(request)

            elif step.agent_or_tool == "gemini" and step.type == "analyze":
                req = step.input.get("request") or {}
                path = req.get("path")
                task_goal = req.get("goal")
                mode = req.get("mode")

                if not path or not task_goal or not mode:
                    step.result = {
                        "stub": True,
                        "note": "gemini step missing request.path/goal/mode; no real call",
                    }
                else:
                    step.result = gemini_client.call(
                        path=path,
                        goal=task_goal,
                        mode=mode,
                    )

            else:
                step.result = {"stub": True, "note": "no real call wired yet"}

            step.status = "done"
            print(f"[WMOC] {step.step_id} done.")

        except Exception as e:
            step.result = {"error": type(e).__name__, "message": str(e)}
            step.status = "failed"
            dispatch_log.error(
                "step %s failed (agent=%s, type=%s): %s: %s",
                step.step_id,
                step.agent_or_tool,
                step.type,
                type(e).__name__,
                e,
            )
            print(f"[WMOC] {step.step_id} failed: see [wmoc.dispatcher] log above.")

    out = log.write(runs_dir)
    print(f"\n[WMOC] Run log written to {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("WMOC_LOG_LEVEL", "INFO"),
        format="[%(name)s] %(message)s",
        stream=sys.stderr,
    )

    try:
        from dotenv import load_dotenv

        load_dotenv(Path("config/.env"))
    except ImportError:
        pass

    parser = argparse.ArgumentParser(prog="wmoc")
    parser.add_argument(
        "goal",
        nargs="?",
        default="meeting_prep",
        help="High-level goal name or natural language",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan without executing",
    )
    parser.add_argument(
        "--runs-dir",
        default="logs/runs",
        help="Where to write run logs",
    )
    parser.add_argument(
        "--broker-smoke",
        action="store_true",
        help="Phase 0: post one pc->screen_left message via the broker and fetch it back",
    )
    parser.add_argument(
        "--broker-db",
        default="logs/broker.sqlite",
        help="SQLite path used by --broker-smoke",
    )
    args = parser.parse_args(argv)

    if args.broker_smoke:
        return broker_smoke(Path(args.broker_db))

    return run(goal=args.goal, dry_run=args.dry_run, runs_dir=Path(args.runs_dir))


if __name__ == "__main__":
    sys.exit(main())