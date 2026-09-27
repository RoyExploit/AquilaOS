"""Repair Engine.

On a verification or test failure: feed the broken code + the exact
error back to the worker LLM, get a corrected full file, rewrite it,
re-verify. Repeats up to max_attempts before escalating (giving up and
reporting to the user) rather than looping forever.
"""

REPAIR_SYSTEM_PROMPT = """You are the Repair Engine of an autonomous coding agent.
You will be given a file's current content and the exact error it produced.
Return ONLY the complete corrected file content -- no markdown fences, no
explanation, just the fixed source code that should replace the file."""


class RepairEngine:
    def __init__(self, llm_provider, tool_manager, max_attempts: int = 3):
        self.llm = llm_provider
        self.tools = tool_manager
        self.max_attempts = max_attempts

    def repair_file(self, relative_path: str, error: str, verify_fn) -> tuple[bool, int]:
        """Returns (fixed: bool, attempts_used: int)."""
        for attempt in range(1, self.max_attempts + 1):
            current_content = self.tools.read_file(relative_path)
            prompt = (
                f"File: {relative_path}\n\n"
                f"--- CURRENT CONTENT ---\n{current_content}\n\n"
                f"--- ERROR ---\n{error}\n\n"
                f"Fix the error. Return the complete corrected file content."
            )
            resp = self.llm.generate(REPAIR_SYSTEM_PROMPT, prompt, max_tokens=3000, temperature=0.1)
            fixed_content = self._strip_fences(resp.text)
            self.tools.write_file(relative_path, fixed_content)

            ok, new_error = verify_fn(relative_path)
            if ok:
                return True, attempt
            error = new_error
        return False, self.max_attempts

    @staticmethod
    def _strip_fences(text: str) -> str:
        t = text.strip()
        if t.startswith("```"):
            lines = t.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            t = "\n".join(lines)
        return t
