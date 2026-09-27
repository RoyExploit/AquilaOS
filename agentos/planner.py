"""Planner + Task Decomposer.

Turns a high-level goal into a structured plan tree:
    Project -> Phase -> Milestone -> Task -> Subtask -> Atomic Action

Atomic actions are the only things the Execution Manager ever runs, and
each one is small and typed (create_folder / generate_file / generate_test)
so no worker ever receives a large vague task.
"""

SYSTEM_PROMPT = """You are the Planning Engine of an autonomous software agent.
Decompose the user's goal into a plan tree: phases -> milestones -> tasks ->
subtasks -> atomic_actions. Each atomic_action must have:
  - id (short string)
  - type: one of "create_folder", "generate_file", "generate_test",
    "generate_doc", "generate_config"
  - agent: which specialized worker handles it -- one of "coding", "backend",
    "api", "database", "ui", "refactoring", "testing", "documentation",
    "devops" (omit for create_folder)
  - target_path (relative path inside the project)
  - description (precise enough that the assigned agent can do the work with
    no further context)
  - success_criteria (a short, checkable condition)
Keep the plan minimal and concrete -- target a small, runnable project, not
an exhaustive one. Always include at least one "testing" action and prefer
including one "documentation" (e.g. a README) and one "devops" action
(e.g. requirements.txt) so the coordinator can demonstrate every agent role."""


class Planner:
    def __init__(self, llm_provider):
        self.llm = llm_provider

    def plan(self, goal: str) -> dict:
        prompt = f"Goal: {goal}\n\nDecompose this into the plan tree JSON described in the system prompt."
        plan = self.llm.generate_json(SYSTEM_PROMPT, prompt, max_tokens=3000)
        self._validate(plan)
        return plan

    @staticmethod
    def _validate(plan: dict):
        if "phases" not in plan or not isinstance(plan["phases"], list) or not plan["phases"]:
            raise ValueError("Plan missing non-empty 'phases' list")
        for phase in plan["phases"]:
            for milestone in phase.get("milestones", []):
                for task in milestone.get("tasks", []):
                    for subtask in task.get("subtasks", []):
                        actions = subtask.get("atomic_actions", [])
                        if not actions:
                            raise ValueError(f"Subtask '{subtask.get('name')}' has no atomic_actions")
                        for a in actions:
                            for key in ("id", "type", "target_path", "description"):
                                if key not in a:
                                    raise ValueError(f"Atomic action missing '{key}': {a}")


def flatten_atomic_actions(plan: dict) -> list[dict]:
    """Walk the plan tree and return atomic actions in execution order."""
    out = []
    for phase in plan.get("phases", []):
        for milestone in phase.get("milestones", []):
            for task in milestone.get("tasks", []):
                for subtask in task.get("subtasks", []):
                    out.extend(subtask.get("atomic_actions", []))
    return out
