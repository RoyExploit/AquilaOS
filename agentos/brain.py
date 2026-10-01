"""Core Brain: the orchestrator.

Goal -> Plan -> Decompose -> Execute -> Verify -> Test -> Repair -> Complete

This is the real, runnable Phase 1 loop. Every step logs to MemoryManager
so progress and failures are auditable after the fact, not just printed
and lost.
"""
import json
import os
from pathlib import Path

from .planner import Planner, flatten_atomic_actions
from .agents import MultiAgentCoordinator
from .tools import ToolManager
from .verification import VerificationEngine
from .testing import TestingEngine
from .repair import RepairEngine
from .memory import MemoryManager


class CoreBrain:
    # The planner's "checking" role: judge a worker's file against the
    # action's success criteria. A rejection is what triggers the second /
    # third prompts (via the repair loop) instead of accepting the first
    # thing a worker produces.
    REVIEW_SYSTEM_PROMPT = (
        "You are the Planner of an autonomous software agent, reviewing work "
        "produced by a worker model. Judge ONLY against the action's success "
        "criteria. Reply with exactly 'APPROVE' when the file satisfies them, "
        "or 'FIX: <specific reasons and what to change>' otherwise. Never "
        "rewrite the file yourself and never output full code."
    )

    def __init__(self, planner_llm, worker_llm, workspace_dir: str = "workspace",
                 max_repair_attempts: int = 3, memory_db: str = "agentos_memory.sqlite3",
                 planner_review: bool = True, extra_roots=None,
                 allow_outside: bool = False, command_approver=None,
                 test_command: str = ""):
        self.planner = Planner(planner_llm)
        self.memory = MemoryManager(memory_db)
        self.workspace_dir = workspace_dir
        self.worker_llm = worker_llm
        self.max_repair_attempts = max_repair_attempts
        self.planner_review = planner_review
        # Client-agent access: extra approved locations, optional full PC
        # access, and a callback that approves terminal commands.
        self.extra_roots = list(extra_roots or [])
        self.allow_outside = bool(allow_outside)
        self.command_approver = command_approver
        # Optional terminal command the platform itself runs against the
        # generated project (e.g. "python app.py" or "npm test").
        self.test_command = (test_command or "").strip()

    def _emit_write_diff(self, emit, tools, action_id: str):
        """Report the (+added, -removed) line stats of the last file write so
        the dashboard HUD can show e.g. '+12 / -2' for that file."""
        stats = getattr(tools, "last_write", None)
        if not stats:
            return
        tools.last_write = None
        emit("file_diff", action_id=action_id, path=stats["path"],
             added=stats["added"], removed=stats["removed"])

    def _review_file(self, tools, action: dict, path: str) -> tuple[bool, str]:
        """Ask the planner LLM to check one worker output. Returns
        (approved, feedback)."""
        try:
            content = tools.read_file(path)
        except Exception as e:
            return True, f"could not read file for review: {e}"
        prompt = (
            f"File: {path}\n"
            f"Worker action: {action.get('description', '')}\n"
            f"Success criteria: {action.get('success_criteria', 'N/A')}\n\n"
            f"--- FILE CONTENT ---\n{content}\n\n"
            "Review the file. Reply with APPROVE or FIX: <reasons>."
        )
        resp = self.planner.llm.generate(
            self.REVIEW_SYSTEM_PROMPT, prompt, max_tokens=600, temperature=0.0
        )
        text = (resp.text or "").strip()
        if text.upper().startswith("APPROVE"):
            return True, "approved"
        return False, text or "no feedback returned"

    def _project_context(self, project_root: Path, limit: int = 60) -> str:
        """Project-context step: a short inventory of what already lives in
        the target folder, handed to the planner so it plans a change
        instead of regenerating everything."""
        if not project_root.exists():
            return "(new project -- nothing exists yet)"
        found = []
        for dirpath, dirnames, filenames in os.walk(project_root):
            dirnames[:] = [d for d in dirnames
                           if d not in ("__pycache__", ".git", "node_modules", ".venv")]
            for name in sorted(filenames):
                rel = os.path.relpath(os.path.join(dirpath, name), project_root)
                try:
                    size = os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    size = 0
                found.append(f"{rel} ({size} bytes)")
                if len(found) >= limit:
                    break
            if len(found) >= limit:
                break
        return "\n".join(found) if found else "(folder exists but is empty)"

    def run(self, goal: str, project_name: str, progress_cb=None) -> dict:
        """progress_cb(event: dict) is called after every meaningful step if
        provided -- this is what Phase 3's PyQt6 dashboard hooks into for
        live updates, without CoreBrain needing to know a UI exists."""
        def emit(kind, **kw):
            evt = {"kind": kind, **kw}
            if progress_cb:
                progress_cb(evt)
            return evt

        report = {"goal": goal, "actions": [], "final_status": "unknown"}

        print(f"[CoreBrain] Planning: {goal}")
        emit("planning_started", goal=goal)
        emit("stage", name="Intent", status="running",
             detail=f"Goal understood: {goal[:200]}")
        project_root = Path(self.workspace_dir) / project_name
        context = self._project_context(project_root)
        emit("stage", name="Context", status="done", detail=context[:1200])
        plan = self.planner.plan(goal, context=context)
        project_id = self.memory.create_project(goal, plan)
        emit("plan_ready", project_id=project_id, plan=plan)
        emit("stage", name="Plan", status="done",
             detail=json.dumps(plan, indent=1)[:1500])
        # Show the planner's actual reasoning artifact: the plan tree it
        # produced (the "Think" view in the dashboard).
        emit("thinking", stage="Plan",
             title="Planner decomposed the goal into a plan tree",
             detail=json.dumps(plan, indent=1)[:1500])

        tools = ToolManager(project_root, extra_roots=self.extra_roots,
                            allow_outside=self.allow_outside)
        # expose the tools on the brain so the UI can run its own commands
        self.tools = tools
        coordinator = MultiAgentCoordinator(self.worker_llm, tools)
        verification = VerificationEngine(tools)
        testing = TestingEngine(tools)
        repair = RepairEngine(self.worker_llm, tools, max_attempts=self.max_repair_attempts)

        actions = flatten_atomic_actions(plan)
        print(f"[CoreBrain] Plan has {len(actions)} atomic actions.")
        emit("actions_flattened", count=len(actions), actions=actions)
        emit("stage", name="Modules", status="done",
             detail="\n".join(f"{a['type']}: {a['target_path']}" for a in actions)[:1200])
        emit("stage", name="Generate", status="running",
             detail=f"{len(actions)} file/module tasks dispatched to the workers")

        for action in actions:
            agent_label = action.get("agent") or ("folder" if action["type"] == "create_folder" else "coding")
            print(f"  -> [{agent_label}] {action['type']} {action['target_path']}")
            self.memory.log(project_id, "execute", action["id"], action["target_path"])
            emit("thinking", stage="Dispatch",
                 title=f"{agent_label} worker received an atomic task",
                 detail=(f"file: {action['target_path']}\n"
                         f"type: {action['type']}\n"
                         f"instruction: {action.get('description', '')}\n"
                         f"success criteria: {action.get('success_criteria', 'N/A')}"))
            emit("action_started", action_id=action["id"], agent=agent_label, path=action["target_path"])

            result = coordinator.dispatch(action)
            self._emit_write_diff(emit, tools, action["id"])
            action_report = {
                "id": action["id"], "path": action["target_path"],
                "status": result["status"], "agent": result.get("agent", agent_label),
            }

            if action["type"] in ("generate_file", "generate_test"):
                ok, err = verification.verify_file(action["target_path"])
                emit("stage", name="Static analysis", status="done" if ok else "failed",
                     detail=(action["target_path"] + ("" if ok else f" -- {err.strip()[:300]}")))
                if not ok:
                    print(f"     ! Verification failed: {err.strip()[:200]}")
                    emit("verify_failed", action_id=action["id"], error=err)
                    emit("thinking", stage="Verify",
                         title=f"Verification failed on {action['target_path']}",
                         detail=err.strip()[:800])
                    self.memory.log(project_id, "verify_fail", action["id"], err)
                    fixed, attempts = repair.repair_file(
                        action["target_path"], err, verification.verify_file
                    )
                    self._emit_write_diff(emit, tools, action["id"])
                    action_report["repaired"] = fixed
                    action_report["repair_attempts"] = attempts
                    self.memory.log(
                        project_id, "repair",
                        action["id"], f"fixed={fixed} attempts={attempts}",
                    )
                    emit("thinking", stage="Repair",
                         title=(f"Worker re-prompted on {action['target_path']} "
                                f"-- fixed={fixed} after {attempts} attempt(s)"),
                         detail=f"error fed to the worker:\n{err.strip()[:600]}")
                    emit("repair_done", action_id=action["id"], fixed=fixed, attempts=attempts)
                    if not fixed:
                        action_report["status"] = "failed_unrepaired"
                        report["final_status"] = "escalation_required"
                else:
                    action_report["verified"] = True
                    # Planner review: the planner model checks the worker's
                    # output against the success criteria. A rejection
                    # becomes the "second/third prompt" that re-drives the
                    # worker's repair loop (bounded by max_repair_attempts).
                    if self.planner_review:
                        approved, feedback = self._review_file(
                            tools, action, action["target_path"]
                        )
                        status = "approved" if approved else "rejected"
                        emit("planner_review", action_id=action["id"],
                             path=action["target_path"], approved=approved,
                             status=status)
                        emit("thinking", stage="Review",
                             title=(f"Planner verdict on {action['target_path']}: "
                                    f"{status}"),
                             detail=f"success criteria: "
                                    f"{action.get('success_criteria', 'N/A')}\n\n"
                                    f"{feedback[:700]}")
                        self.memory.log(project_id, "planner_review",
                                        action["id"], f"{status}: {feedback[:400]}")
                        if not approved and self.max_repair_attempts > 0:
                            fixed, attempts = repair.repair_file(
                                action["target_path"], feedback,
                                verification.verify_file,
                            )
                            self._emit_write_diff(emit, tools, action["id"])
                            if fixed:
                                approved2, feedback2 = self._review_file(
                                    tools, action, action["target_path"]
                                )
                                status = ("approved_after_fix" if approved2
                                          else "review_warning")
                                emit("thinking", stage="Review",
                                     title=(f"Planner re-check on "
                                            f"{action['target_path']}: {status}"),
                                     detail=feedback2[:700])
                                if not approved2:
                                    self.memory.log(
                                        project_id, "planner_review",
                                        action["id"], f"warning: {feedback2[:400]}",
                                    )
                            else:
                                status = "review_unresolved"
                            emit("planner_review_fixed", action_id=action["id"],
                                 path=action["target_path"], fixed=fixed,
                                 attempts=attempts, status=status)
                        action_report["planner_review"] = status

                # Security Agent pass: every generated python file gets scanned.
                if action["target_path"].endswith(".py"):
                    sec_result = coordinator.security_scan(action["target_path"])
                    action_report["security"] = sec_result["status"]
                    self.memory.log(project_id, "security_scan", action["id"], sec_result["detail"])
                    emit("security_scanned", action_id=action["id"], status=sec_result["status"])

            report["actions"].append(action_report)
            emit("action_done", action_id=action["id"], status=action_report["status"])

        print("[CoreBrain] Running test suite...")
        emit("test_run_started")
        emit("stage", name="Test", status="running", detail="pytest suite")
        passed, log = testing.run_suite(".") if any(
            a["type"] == "generate_test" for a in actions
        ) else (True, "no tests generated")
        report["tests_passed"] = passed
        report["test_log"] = log
        self.memory.log(project_id, "test_run", "", f"passed={passed}")
        emit("test_run_done", passed=passed)
        emit("stage", name="Test", status="done" if passed else "failed",
             detail=(log or "")[-800:])

        if not passed:
            print("[CoreBrain] Tests failed -- attempting repair on test file(s)...")
            emit("thinking", stage="Tests",
                 title="pytest failed -- routing the failure back to the worker",
                 detail=(log or "")[-1200:])
            test_actions = [a for a in actions if a["type"] == "generate_test"]
            for a in test_actions:
                fixed, attempts = repair.repair_file(
                    a["target_path"], log, lambda p: testing.run_suite(".")
                )
                self._emit_write_diff(emit, tools, a["id"])
                self.memory.log(project_id, "repair_test", a["id"], f"fixed={fixed}")
                emit("thinking", stage="Repair",
                     title=(f"Test file re-prompted: {a['target_path']} "
                            f"-- fixed={fixed} after {attempts} attempt(s)"),
                     detail="pytest output was fed back to the worker.")
                emit("repair_done", action_id=a["id"], fixed=fixed, attempts=attempts)
                if fixed:
                    passed, log = testing.run_suite(".")
            report["tests_passed"] = passed
            report["test_log"] = log

        final_status = "completed" if (report["final_status"] != "escalation_required" and passed) else "escalation_required"

        # Client-agent behaviour: actually run the project's own command in a
        # terminal so "does it work?" is answered by execution, not opinion.
        if self.test_command:
            approved = True
            if self.command_approver is not None:
                try:
                    approved = bool(self.command_approver(self.test_command))
                except Exception:
                    approved = False
            if approved:
                emit("thinking", stage="Terminal",
                     title=f"Running: {self.test_command}", detail="")
                ok, output, _rc = tools.run_command(self.test_command, timeout=600)
                emit("command_run", command=self.test_command, ok=ok,
                     output=(output or "")[-6000:])
                self.memory.log(project_id, "command", "",
                                f"{self.test_command} -> ok={ok}")
                report["command_ok"] = ok
                report["command_output"] = (output or "")[-6000:]
                if not ok and final_status == "completed":
                    final_status = "completed_with_command_failure"

        report["final_status"] = final_status
        self.memory.set_status(project_id, final_status)
        self.memory.log(project_id, "complete", "", final_status)
        print(f"[CoreBrain] Done. Status: {final_status}")
        report["project_root"] = str(project_root.resolve())
        report["project_id"] = project_id
        emit("run_complete", final_status=final_status, project_root=report["project_root"])
        emit("stage", name="Apply", status="done",
             detail=f"Files written under {report['project_root']}")
        return report
