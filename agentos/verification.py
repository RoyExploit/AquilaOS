"""Verification Engine.

Phase 1 verifies what can be checked cheaply and reliably: syntax
correctness of every generated Python file. Logic/security/performance
verification are richer LLM-driven Self-Critic passes slated for a later
phase; bolting them on now, without a working syntax gate first, would
just mean unverified verification.
"""


class VerificationEngine:
    def __init__(self, tool_manager):
        self.tools = tool_manager

    def verify_file(self, relative_path: str) -> tuple[bool, str]:
        if not relative_path.endswith(".py"):
            return True, "skipped (non-python file)"
        return self.tools.check_syntax(relative_path)
