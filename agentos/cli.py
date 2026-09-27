import argparse
import json
import sys

from .config import AgentOSConfig
from .llm.factory import build_provider
from .brain import CoreBrain

DEMO_GOAL = "Build a PyQt6 calculator application with a numeric display and basic arithmetic buttons."


def main():
    parser = argparse.ArgumentParser(prog="agentos", description="Autonomous AI Agent Platform (Phase 1)")
    parser.add_argument("goal", nargs="?", default=None,
                         help="High-level goal, e.g. 'Build a PyQt6 application'. "
                              "Omit and use --demo to run with zero setup.")
    parser.add_argument("--project-name", default="generated_project")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--demo", action="store_true",
                         help="Run a canned demo goal using the built-in mock LLM provider (no API keys needed).")
    args = parser.parse_args()

    if args.demo or not args.goal:
        goal = DEMO_GOAL
        project_name = "calculator_demo"
        cfg = AgentOSConfig()  # defaults to mock provider
        print("[agentos] Running DEMO mode with the built-in mock provider (no API keys required).")
    else:
        goal = args.goal
        project_name = args.project_name
        cfg = AgentOSConfig.load(args.config)

    try:
        planner_llm = build_provider(cfg.planner)
        worker_llm = build_provider(cfg.worker)
    except ValueError as e:
        print(f"[agentos] Provider configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    brain = CoreBrain(
        planner_llm, worker_llm,
        workspace_dir=cfg.workspace_dir,
        max_repair_attempts=cfg.max_repair_attempts,
    )
    report = brain.run(goal, project_name)

    print("\n--- FINAL REPORT ---")
    print(json.dumps({k: v for k, v in report.items() if k != "test_log"}, indent=2))
    print("\n--- TEST LOG ---")
    print(report["test_log"])


if __name__ == "__main__":
    main()
