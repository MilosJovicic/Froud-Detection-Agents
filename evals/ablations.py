"""
Ablation testing harness for the Fraud Detection Agent.

Supports testing the impact of individual components on system performance:
- --no-gds-verifier: Disable structural (GDS) verification
- --no-cypher-verifier: Disable semantic (Cypher) verification
- --max-branches=N: Limit parallel branch exploration
- --router=rule-based|llm: Use deterministic vs LLM routing
- --planner=rule-based|llm: Use deterministic vs LLM planning
- --algo-set=minimal|full: Limit to WCC only or use full library
- --allow-composer-always: Use ProjectionComposerAgent for all hypotheses
- --temp=0.0-0.7: Model temperature sweep

Results are saved to evals/ablation_results/ with comparisons and per-component metrics.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

load_dotenv(ROOT / ".env")

from evals.run_eval import run_eval


class AblationConfig:
    """Configuration for a single ablation test."""

    def __init__(
        self,
        name: str,
        description: str,
        env_vars: dict[str, str] | None = None,
        **kwargs: Any,
    ):
        self.name = name
        self.description = description
        self.env_vars = env_vars or {}
        self.extra = kwargs

    def apply(self):
        """Apply environment variables for this ablation."""
        for key, value in self.env_vars.items():
            os.environ[key] = value

    def __repr__(self) -> str:
        return f"AblationConfig(name={self.name!r})"


# Predefined ablation configurations
ABLATIONS = {
    "baseline": AblationConfig(
        name="baseline",
        description="Full system with all components enabled (control)",
    ),
    "no_gds_verifier": AblationConfig(
        name="no_gds_verifier",
        description="Disable structural (GDS) verification; rely on LLM-only",
        env_vars={"ABLATE_GDS_VERIFIER": "true"},
    ),
    "no_cypher_verifier": AblationConfig(
        name="no_cypher_verifier",
        description="Disable semantic (Cypher) verification; rely on algorithm only",
        env_vars={"ABLATE_CYPHER_VERIFIER": "true"},
    ),
    "single_branch": AblationConfig(
        name="single_branch",
        description="Graph-of-Thought fan-out disabled; single branch only",
        env_vars={"MAX_BRANCHES": "1"},
    ),
    "router_rule_based": AblationConfig(
        name="router_rule_based",
        description="Deterministic router (no LLM); use rule-based claim classification",
        env_vars={"ROUTER_MODE": "rule-based"},
    ),
    "planner_rule_based": AblationConfig(
        name="planner_rule_based",
        description="Deterministic planner (no LLM); use fixed projection/algorithm pairs",
        env_vars={"PLANNER_MODE": "rule-based"},
    ),
    "minimal_algo_set": AblationConfig(
        name="minimal_algo_set",
        description="Restrict to WCC algorithm only; disable Louvain/PageRank/etc",
        env_vars={"ALGO_SET": "minimal"},
    ),
    "composer_always": AblationConfig(
        name="composer_always",
        description="ProjectionComposerAgent for all hypotheses (no pre-registered library)",
        env_vars={"ALLOW_COMPOSER_ALWAYS": "true"},
    ),
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run ablation studies on the Fraud Detection Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python evals/ablations.py baseline                 # Run control
  python evals/ablations.py baseline no_gds_verifier # Compare two configs
  python evals/ablations.py --all                    # Run all predefined ablations
  python evals/ablations.py --custom ABLATE_X=true   # Custom environment variable
        """,
    )
    parser.add_argument(
        "ablations",
        nargs="*",
        default=["baseline"],
        help="Ablation config names or 'all' for all predefined ablations",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all predefined ablations (ignores positional args)",
    )
    parser.add_argument(
        "--temp",
        type=float,
        nargs="+",
        help="Temperature sweep (e.g., --temp 0.0 0.3 0.5 0.7)",
    )
    parser.add_argument(
        "--custom",
        nargs="+",
        help="Custom environment variables (e.g., --custom VAR1=value1 VAR2=value2)",
    )
    parser.add_argument(
        "--scenario",
        default=str(ROOT / "evals" / "scenarios.yaml"),
        help="Path to scenarios YAML file",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "evals" / "ablation_results"),
        help="Directory to save results",
    )
    parser.add_argument(
        "--skip-comparison",
        action="store_true",
        help="Skip generating comparison report",
    )

    return parser.parse_args()


async def run_ablation(
    config: AblationConfig,
    scenario_file: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Run evaluation with an ablation configuration."""
    print(f"\n{'=' * 80}")
    print(f"Running: {config.name}")
    print(f"Description: {config.description}")
    print("=" * 80)

    # Save current environment
    saved_env = os.environ.copy()

    try:
        # Apply ablation configuration
        config.apply()

        # Determine output path
        output_path = output_dir / f"{config.name}.json"

        # Run evaluation
        report = await run_eval(
            scenario_file=scenario_file,
            output_path=output_path,
        )

        print(f"\n✓ {config.name}: {report['summary']['scenario_accuracy']:.1%} accuracy")
        print(f"  Total time: {report['summary']['total_wall_time_ms']:.0f}ms")
        print(f"  Tokens: {report['summary']['total_estimated_tokens']}")

        return {
            "ablation": config.name,
            "description": config.description,
            "output_path": str(output_path),
            "summary": report["summary"],
        }

    finally:
        # Restore environment
        os.environ.clear()
        os.environ.update(saved_env)


def _build_comparison(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a comparison report across ablations."""
    if len(results) < 2:
        return {}

    baseline = next(
        (r for r in results if r["ablation"] == "baseline"), results[0]
    )
    baseline_accuracy = baseline["summary"]["scenario_accuracy"]
    baseline_time = baseline["summary"]["total_wall_time_ms"]
    baseline_tokens = baseline["summary"]["total_estimated_tokens"]

    comparison = {
        "baseline": baseline["ablation"],
        "ablations": [],
    }

    for result in results:
        if result["ablation"] == "baseline":
            continue

        accuracy_delta = result["summary"]["scenario_accuracy"] - baseline_accuracy
        time_delta = (
            result["summary"]["total_wall_time_ms"] - baseline_time
        ) / baseline_time
        token_delta = (
            result["summary"]["total_estimated_tokens"] - baseline_tokens
        ) / baseline_tokens

        comparison["ablations"].append(
            {
                "name": result["ablation"],
                "description": result["description"],
                "accuracy": result["summary"]["scenario_accuracy"],
                "accuracy_delta": accuracy_delta,
                "total_time_ms": result["summary"]["total_wall_time_ms"],
                "time_delta_pct": round(time_delta * 100, 1),
                "total_tokens": result["summary"]["total_estimated_tokens"],
                "token_delta_pct": round(token_delta * 100, 1),
            }
        )

    # Sort by accuracy delta (descending impact)
    comparison["ablations"].sort(
        key=lambda x: x["accuracy_delta"], reverse=True
    )

    return comparison


async def main():
    args = _parse_args()

    scenario_file = Path(args.scenario)
    if not scenario_file.exists():
        print(f"Error: Scenario file not found: {scenario_file}")
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Determine which ablations to run
    ablation_names = []
    if args.all:
        ablation_names = list(ABLATIONS.keys())
    else:
        ablation_names = args.ablations
        if not ablation_names:
            ablation_names = ["baseline"]

    # Validate ablation names
    for name in ablation_names:
        if name not in ABLATIONS and name != "custom":
            print(f"Error: Unknown ablation '{name}'")
            print(f"Available: {', '.join(ABLATIONS.keys())}")
            sys.exit(1)

    # Handle temperature sweep
    if args.temp:
        for temp in args.temp:
            name = f"temp_{temp:.1f}".replace(".", "_")
            ABLATIONS[name] = AblationConfig(
                name=name,
                description=f"Model temperature = {temp}",
                env_vars={"OLLAMA_TEMPERATURE": str(temp)},
            )
            ablation_names.append(name)

    # Handle custom environment variables
    if args.custom:
        for custom_var in args.custom:
            if "=" not in custom_var:
                print(f"Error: Invalid custom variable format (expected VAR=value): {custom_var}")
                sys.exit(1)
            key, value = custom_var.split("=", 1)
            ABLATIONS[key] = AblationConfig(
                name=key,
                description=f"Custom: {custom_var}",
                env_vars={key: value},
            )
            if key not in ablation_names:
                ablation_names.append(key)

    # Run ablations
    print(f"\nRunning {len(ablation_names)} ablation(s) with scenarios from {scenario_file}")

    results = []
    for name in ablation_names:
        result = await run_ablation(
            ABLATIONS[name],
            scenario_file,
            output_dir,
        )
        results.append(result)

    # Build and save comparison
    if len(results) > 1 and not args.skip_comparison:
        comparison = _build_comparison(results)
        comparison_path = output_dir / "comparison.json"
        comparison_path.write_text(json.dumps(comparison, indent=2))
        print(f"\n✓ Comparison saved to {comparison_path}")

    # Summary
    print(f"\n{'=' * 80}")
    print("ABLATION RESULTS SUMMARY")
    print("=" * 80)
    for result in results:
        print(
            f"\n{result['ablation']:30} {result['summary']['scenario_accuracy']:6.1%} "
            f"({result['summary']['total_wall_time_ms']:8.0f}ms, "
            f"{result['summary']['total_estimated_tokens']:6.0f}tk)"
        )

    # Save master index
    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scenario_file": str(scenario_file),
        "ablation_results": results,
        "comparison": _build_comparison(results) if len(results) > 1 else {},
    }
    index_path = output_dir / "index.json"
    index_path.write_text(json.dumps(index, indent=2))
    print(f"\n✓ Full index saved to {index_path}")
    print(f"✓ Results directory: {output_dir}")


if __name__ == "__main__":
    asyncio.run(main())
