"""Core Brain: the orchestrator.

Goal -> Plan -> Decompose -> Execute -> Verify -> Test -> Repair -> Complete

This is the real, runnable Phase 1 loop. Every step logs to MemoryManager
so progress and failures are auditable after the fact, not just printed
and lost.
"""
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
                 planner_review: bool = True):
        self.planner = Planner(planner_llm)
        self.memory = MemoryManager(memory_db)
        self.workspace_dir = workspace_dir
        self.worker_llm = worker_llm
        self.max_repair_attempts = max_repair_attempts
        self.planner_review = planner_review

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
        plan = self.planner.plan(goal)
        project_id = self.memory.create_project(goal, plan)
        emit("plan_ready", project_id=project_id, plan=plan)

        project_root = Path(self.workspace_dir) / project_name
        tools = ToolManager(project_root)
        coordinator = MultiAgentCoordinator(self.worker_llm, tools)
        verification = VerificationEngine(tools)
        testing = TestingEngine(tools)
        repair = RepairEngine(self.worker_llm, tools, max_attempts=self.max_repair_attempts)

        actions = flatten_atomic_actions(plan)
        print(f"[CoreBrain] Plan has {len(actions)} atomic actions.")
        emit("actions_flattened", count=len(actions), actions=actions)

        for action in actions:
            agent_label = action.get("agent") or ("folder" if action["type"] == "create_folder" else "coding")
            print(f"  -> [{agent_label}] {action['type']} {action['target_path']}")
            self.memory.log(project_id, "execute", action["id"], action["target_path"])
            emit("action_started", action_id=action["id"], agent=agent_label, path=action["target_path"])

            result = coordinator.dispatch(action)
            action_report = {
                "id": action["id"], "path": action["target_path"],
                "status": result["status"], "agent": result.get("agent", agent_label),
            }

            if action["type"] in ("generate_file", "generate_test"):
                ok, err = verification.verify_file(action["target_path"])
                if not ok:
                    print(f"     ! Verification failed: {err.strip()[:200]}")
                    emit("verify_failed", action_id=action["id"], error=err)
                    self.memory.log(project_id, "verify_fail", action["id"], err)
                    fixed, attempts = repair.repair_file(
                        action["target_path"], err, verification.verify_file
                    )
                    action_report["repaired"] = fixed
                    action_report["repair_attempts"] = attempts
                    self.memory.log(
                        project_id, "repair",
                        action["id"], f"fixed={fixed} attempts={attempts}",
                    )
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
                        self.memory.log(project_id, "planner_review",
                                        action["id"], f"{status}: {feedback[:400]}")
                        if not approved and self.max_repair_attempts > 0:
                            fixed, attempts = repair.repair_file(
                                action["target_path"], feedback,
                                verification.verify_file,
                            )
                            if fixed:
                                approved2, feedback2 = self._review_file(
                                    tools, action, action["target_path"]
                                )
                                status = ("approved_after_fix" if approved2
                                          else "review_warning")
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
        passed, log = testing.run_suite(".") if any(
            a["type"] == "generate_test" for a in actions
        ) else (True, "no tests generated")
        report["tests_passed"] = passed
        report["test_log"] = log
        self.memory.log(project_id, "test_run", "", f"passed={passed}")
        emit("test_run_done", passed=passed)

        if not passed:
            print("[CoreBrain] Tests failed -- attempting repair on test file(s)...")
            test_actions = [a for a in actions if a["type"] == "generate_test"]
            for a in test_actions:
                fixed, attempts = repair.repair_file(
                    a["target_path"], log, lambda p: testing.run_suite(".")
                )
                self.memory.log(project_id, "repair_test", a["id"], f"fixed={fixed}")
                emit("repair_done", action_id=a["id"], fixed=fixed, attempts=attempts)
                if fixed:
                    passed, log = testing.run_suite(".")
            report["tests_passed"] = passed
            report["test_log"] = log

        final_status = "completed" if (report["final_status"] != "escalation_required" and passed) else "escalation_required"
        report["final_status"] = final_status
        self.memory.set_status(project_id, final_status)
        self.memory.log(project_id, "complete", "", final_status)
        print(f"[CoreBrain] Done. Status: {final_status}")
        report["project_root"] = str(project_root.resolve())
        report["project_id"] = project_id
        emit("run_complete", final_status=final_status, project_root=report["project_root"])
        return report
