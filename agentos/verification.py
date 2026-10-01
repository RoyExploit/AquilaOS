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
        """Cheap, reliable checks for every generated file type: Python syntax
        for .py, well-formed JSON/YAML for manifests. Types with no cheap
        check return ok -- verify_file never claims more than it checked."""
        return self.tools.check_syntax(relative_path)
