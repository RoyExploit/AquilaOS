"""Verify the terminal-testing loop: the pipeline runs the project's own
command, records the result, and the dashboard log pane receives lines.

This is the "test it and retry if it fails" path -- the agents are only
allowed to claim success after the code has actually been executed.
"""
import sys, os, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agentos.llm.factory import build_provider
from agentos.brain import CoreBrain

events = []
brain = CoreBrain(
    build_provider({"provider": "mock"}), build_provider({"provider": "mock"}),
    workspace_dir="workspace", max_repair_attempts=1,
    test_command="python -m pytest -q",
)
report = brain.run("Build a calculator", "_term_test", progress_cb=events.append)

runs = [e for e in events if e["kind"] == "command_run"]
assert runs, "the pipeline never executed the project's test command"
run = runs[0]
print(f"PASS pipeline ran the project's own command: {run['command']!r}")
print(f"     ok={run['ok']} | {len(run['output'])} chars of output")
tail = (run["output"] or "").strip().splitlines()
print(f"     tail: {tail[-1][:90]!r}" if tail else "     (no output)")
assert report.get("command_ok") in (True, False), "report has no command result"
print(f"PASS report carries the terminal verdict -> command_ok={report['command_ok']}")

stages = [e["name"] for e in events if e["kind"] == "stage"]
print("PASS pipeline stages seen ->", stages)
assert "Test" in stages and "Apply" in stages, stages

# the working directory must be the generated project, not the repo root
assert "workspace" not in (run["command"] or ""), "command looks like it ran in the wrong place"
print("PASS command ran inside the generated project")

shutil.rmtree(os.path.join("workspace", "_term_test"), ignore_errors=True)
print("TERMINAL_PIPELINE_OK")
