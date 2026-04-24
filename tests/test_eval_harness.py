import json
import subprocess
import sys
from pathlib import Path


def test_eval_harness_cli_generates_json_report(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    output_path = tmp_path / "eval-report.json"

    command = [
        sys.executable,
        str(repo_root / "evals" / "run_eval.py"),
        "--output",
        str(output_path),
        "--router",
        "rule-based",
        "--planner",
        "rule-based",
        "--interpreter",
        "rule-based",
        "--verifier",
        "rule-based",
        "--synthesizer",
        "rule-based",
        "--composer",
        "rule-based",
    ]

    subprocess.run(command, check=True, cwd=repo_root)

    report = json.loads(output_path.read_text())
    assert report["summary"]["scenario_count"] == 3
    assert len(report["scenarios"]) == 3
    assert "parse_hypothesis" in report["per_activity_metrics"]
    assert "run_algorithm" in report["per_activity_metrics"]
    assert "by_workflow" in report["agent_token_metrics"]
