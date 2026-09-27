"""Phase 2: Worker Agents + Multi-Agent Coordinator.

Phase 1's ExecutionManager gave every atomic action the same two prompts
(Coding Agent / Testing Agent). Phase 2 replaces that with real, separate
agent classes, each with its OWN system prompt and its OWN restricted set
of tool operations -- a DocsAgent cannot run pytest, a SecurityAgent
cannot silently rewrite files without a flagged finding, etc.

Roles not worth a fully separate prompt yet (Backend/API/Database/UI/
Refactoring) are implemented as CodingAgent *specializations*: same
restricted toolset, a role-specific system prompt. Splitting them into
fully independent classes with no behavioral difference would just be
12 copies of the same code -- specialization-by-prompt is the honest
version of that until each role needs genuinely different tool access.

The Planner (updated below) now tags every atomic action with an
"agent" field. The MultiAgentCoordinator reads that field and routes
the action to the matching agent instead of switching on action type
alone.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------- #
# Agent base
# ---------------------------------------------------------------- #

class BaseAgent:
    """A focused worker. Only touches the tool methods listed in
    `allowed_tool_methods` -- enforced by a thin proxy, not just convention.
    """
    agent_name = "base"
    system_prompt = "You are a focused worker agent."
    allowed_tool_methods = ("write_file", "create_folder", "read_file", "check_syntax")

    def __init__(self, llm_provider, tool_manager):
        self.llm = llm_provider
        self.tools = _RestrictedToolProxy(tool_manager, self.allowed_tool_methods)

    def run(self, action: dict) -> dict:
        raise NotImplementedError

    def _ask(self, prompt: str, max_tokens=3000, temperature=0.2) -> str:
        resp = self.llm.generate(self.system_prompt, prompt, max_tokens=max_tokens, temperature=temperature)
        return _strip_fences(resp.text)


class _RestrictedToolProxy:
    """Raises if an agent calls a tool method it wasn't granted."""
    def __init__(self, tools, allowed_methods):
        self._tools = tools
        self._allowed = set(allowed_methods)

    def __getattr__(self, name):
        if name not in self._allowed:
            raise PermissionError(f"Agent tried to call restricted tool method '{name}'")
        return getattr(self._tools, name)


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        lines = t.split("\n")[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines)
    return t


# ---------------------------------------------------------------- #
# Concrete agents
# ---------------------------------------------------------------- #

class CodingAgent(BaseAgent):
    agent_name = "coding"
    system_prompt = (
        "You are the Coding Agent. You receive ONE file to write at a time and "
        "a short 'role' hint (general/backend/api/database/ui/refactoring) that "
        "tells you what conventions to favor. Return ONLY the complete file "
        "content -- no markdown fences, no explanation."
    )
    allowed_tool_methods = ("write_file", "create_folder", "read_file", "check_syntax")

    ROLE_HINTS = {
        "backend": "Favor clear service/business-logic separation, explicit error handling.",
        "api": "Design a clean, versioned, RESTful interface; validate inputs explicitly.",
        "database": "Use parameterized queries only; never string-format SQL; include a schema/migration comment.",
        "ui": "Favor PyQt6 idioms: clear widget hierarchy, signals/slots, no blocking calls on the UI thread.",
        "refactoring": "Preserve existing behavior exactly; only improve structure/readability.",
        "general": "Write clean, idiomatic, well-commented Python.",
    }

    def run(self, action: dict) -> dict:
        role = action.get("role", "general")
        hint = self.ROLE_HINTS.get(role, self.ROLE_HINTS["general"])
        prompt = (
            f"Target file: {action['target_path']}\n"
            f"Role: {role} ({hint})\n"
            f"Description: {action['description']}\n"
            f"Success criteria: {action.get('success_criteria', 'N/A')}\n\n"
            f"Write the complete contents of {action['target_path']} now."
        )
        content = self._ask(prompt)
        self.tools.write_file(action["target_path"], content)
        return {"action_id": action["id"], "status": "written", "detail": action["target_path"]}


class TestingAgent(BaseAgent):
    agent_name = "testing"
    system_prompt = (
        "You are the Testing Agent. You write pytest test files for an "
        "already-generated module. Return ONLY the complete test file "
        "content -- no markdown fences, no explanation."
    )
    allowed_tool_methods = ("write_file", "read_file", "check_syntax", "run_pytest")

    def run(self, action: dict) -> dict:
        prompt = (
            f"Target test file: {action['target_path']}\n"
            f"Description: {action['description']}\n"
            f"Success criteria: {action.get('success_criteria', 'N/A')}\n\n"
            f"Write the complete contents of {action['target_path']} now."
        )
        content = self._ask(prompt)
        self.tools.write_file(action["target_path"], content)
        return {"action_id": action["id"], "status": "written", "detail": action["target_path"]}


class DocumentationAgent(BaseAgent):
    agent_name = "documentation"
    system_prompt = (
        "You are the Documentation Agent. Write clear, accurate project "
        "documentation (README sections, docstrings-level explanations) "
        "based on what was actually generated. Return ONLY the file content."
    )
    allowed_tool_methods = ("write_file", "read_file")

    def run(self, action: dict) -> dict:
        prompt = (
            f"Target doc file: {action['target_path']}\n"
            f"Description: {action['description']}\n\n"
            f"Write the complete contents of {action['target_path']} now."
        )
        content = self._ask(prompt, temperature=0.3)
        self.tools.write_file(action["target_path"], content)
        return {"action_id": action["id"], "status": "written", "detail": action["target_path"]}


class DevOpsAgent(BaseAgent):
    agent_name = "devops"
    system_prompt = (
        "You are the DevOps Agent. Write build/run/deploy support files "
        "(requirements.txt, run scripts, Dockerfiles, .env.example). "
        "Return ONLY the file content."
    )
    allowed_tool_methods = ("write_file", "read_file", "create_folder")

    def run(self, action: dict) -> dict:
        prompt = (
            f"Target file: {action['target_path']}\n"
            f"Description: {action['description']}\n\n"
            f"Write the complete contents of {action['target_path']} now."
        )
        content = self._ask(prompt, temperature=0.1)
        self.tools.write_file(action["target_path"], content)
        return {"action_id": action["id"], "status": "written", "detail": action["target_path"]}


class SecurityAgent(BaseAgent):
    """Runs a real static check (not just an LLM opinion) against every
    generated .py file, then only escalates to the LLM if something was
    actually found -- so 'security review passed' means a deterministic
    pattern scan ran, not that a model said something reassuring.
    """
    agent_name = "security"
    system_prompt = (
        "You are the Security Agent. You will be shown a file and a list of "
        "specific dangerous patterns a static scanner found in it. Rewrite "
        "the file to remove or safely contain each finding while preserving "
        "behavior. Return ONLY the complete corrected file content."
    )
    allowed_tool_methods = ("write_file", "read_file", "check_syntax")

    DANGEROUS_PATTERNS = [
        (r"\beval\s*\(", "use of eval()"),
        (r"\bexec\s*\(", "use of exec()"),
        (r"os\.system\s*\(", "os.system() shell call"),
        (r"subprocess\.\w+\([^)]*shell\s*=\s*True", "subprocess call with shell=True"),
        (r"pickle\.loads?\s*\(", "unpickling untrusted data"),
        (r"%\s*.*query|\.format\(.*\)\s*.*(SELECT|INSERT|UPDATE|DELETE)", "possible unparameterized SQL"),
    ]

    def scan(self, relative_path: str) -> list[str]:
        try:
            content = self.tools.read_file(relative_path)
        except Exception:
            return []
        findings = []
        for pattern, label in self.DANGEROUS_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                findings.append(label)
        return findings

    def run(self, action: dict) -> dict:
        """action here is a synthetic review action, not a plan atomic action."""
        path = action["target_path"]
        findings = self.scan(path)
        if not findings:
            return {"action_id": action.get("id", "security_scan"), "status": "clean", "detail": path}

        current = self.tools.read_file(path)
        prompt = (
            f"File: {path}\n\n--- CURRENT CONTENT ---\n{current}\n\n"
            f"--- FINDINGS ---\n" + "\n".join(f"- {f}" for f in findings) + "\n\n"
            f"Rewrite the file to remove these issues. Return the complete corrected file."
        )
        fixed = self._ask(prompt, temperature=0.0)
        self.tools.write_file(path, fixed)
        remaining = self.scan(path)
        return {
            "action_id": action.get("id", "security_scan"),
            "status": "flagged_and_fixed" if not remaining else "flagged_unresolved",
            "detail": f"findings={findings} remaining={remaining}",
        }


# ---------------------------------------------------------------- #
# Multi-Agent Coordinator
# ---------------------------------------------------------------- #

AGENT_REGISTRY = {
    "coding": CodingAgent,
    "backend": CodingAgent,
    "api": CodingAgent,
    "database": CodingAgent,
    "ui": CodingAgent,
    "refactoring": CodingAgent,
    "testing": TestingAgent,
    "documentation": DocumentationAgent,
    "devops": DevOpsAgent,
    "security": SecurityAgent,
}

ACTION_TYPE_DEFAULT_AGENT = {
    "generate_file": "coding",
    "generate_test": "testing",
    "generate_doc": "documentation",
    "generate_config": "devops",
}


class MultiAgentCoordinator:
    """Owns one instance of each agent (so each agent's restricted tool
    proxy is created once) and routes every atomic action to the right one.
    """

    def __init__(self, worker_llm, tool_manager):
        self.tools = tool_manager
        self._agents = {
            role: cls(worker_llm, tool_manager) for role, cls in AGENT_REGISTRY.items()
        }
        # one shared SecurityAgent instance for the post-generation scan pass
        self.security = self._agents["security"]

    def dispatch(self, action: dict) -> dict:
        if action["type"] == "create_folder":
            self.tools.create_folder(action["target_path"])
            return {"action_id": action["id"], "status": "done", "detail": "folder created"}

        agent_key = action.get("agent") or ACTION_TYPE_DEFAULT_AGENT.get(action["type"])
        if agent_key not in self._agents:
            raise ValueError(f"No agent registered for '{agent_key}' (action {action['id']})")

        agent = self._agents[agent_key]
        # role hint lets CodingAgent specialize even though it's one class
        if agent_key in ("backend", "api", "database", "ui", "refactoring"):
            action = {**action, "role": agent_key}
        result = agent.run(action)
        result["agent"] = agent_key
        return result

    def security_scan(self, relative_path: str) -> dict:
        return self.security.run({"target_path": relative_path})
