import json
from pathlib import Path

d = sorted(Path("logs/runs").iterdir(), key=lambda p: p.stat().st_mtime)[-1]
for r in (json.loads(l) for l in (d / "run.jsonl").open(encoding="utf-8")):
    print(
        f"{r['step_id']:>3}  {r['agent_or_tool']:<15}  "
        f"status={r['status']:<8}  result_keys={list((r.get('result') or {}).keys())}"
    )