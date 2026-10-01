import os
import yaml
from dataclasses import dataclass, field


DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.yaml")


@dataclass
class AgentOSConfig:
    planner: dict = field(default_factory=lambda: {"provider": "mock", "model": "mock-deterministic"})
    worker: dict = field(default_factory=lambda: {"provider": "mock", "model": "mock-deterministic"})
    max_repair_attempts: int = 3
    workspace_dir: str = "workspace"
    # When True, the planner LLM reviews each worker-produced file against
    # its success criteria; a rejection re-prompts the worker's repair loop.
    planner_review: bool = True
    # Let the agents touch files outside the projects folder (paths you tag
    # with @ in the chat are always allowed on top of this).
    allow_pc_access: bool = False
    # Ask before running a terminal command (recommended). Turn off only if
    # you want the agents to test things without prompts.
    confirm_commands: bool = True
    # Optional terminal command the platform runs against every generated
    # project, e.g. "python -m pytest -q" or "npm test". Empty = skip.
    test_command: str = ""

    @classmethod
    def load(cls, path: str | None = None) -> "AgentOSConfig":
        path = path or DEFAULT_CONFIG_PATH
        if not os.path.exists(path):
            return cls()  # defaults (mock provider) so the tool always runs out of the box
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        default = cls()
        return cls(
            planner=raw.get("planner") or dict(default.planner),
            worker=raw.get("worker") or dict(default.worker),
            max_repair_attempts=raw.get("max_repair_attempts", 3),
            workspace_dir=raw.get("workspace_dir", "workspace"),
            planner_review=bool(raw.get("planner_review", True)),
            allow_pc_access=bool(raw.get("allow_pc_access", False)),
            confirm_commands=bool(raw.get("confirm_commands", True)),
            test_command=str(raw.get("test_command", "") or ""),
        )

    def to_dict(self) -> dict:
        return {
            "planner": self.planner,
            "worker": self.worker,
            "max_repair_attempts": self.max_repair_attempts,
            "workspace_dir": self.workspace_dir,
            "planner_review": self.planner_review,
            "allow_pc_access": self.allow_pc_access,
            "confirm_commands": self.confirm_commands,
            "test_command": self.test_command,
        }

    def save(self, path: str | None = None) -> str:
        """Write this config out to config.yaml (creating it if absent).

        Used by the dashboard's Settings dialog so provider/model/api_key/
        base_url choices made in the UI persist across restarts, exactly as
        if the user had hand-edited config.yaml.
        """
        path = path or DEFAULT_CONFIG_PATH
        path = os.path.abspath(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            yaml.safe_dump(self.to_dict(), f, sort_keys=False, default_flow_style=False)
        return path
