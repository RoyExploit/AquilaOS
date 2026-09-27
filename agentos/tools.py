"""Tool Manager: file/folder creation, code execution, and test running.

Security: every path is resolved relative to the project's workspace root
and checked to still be inside it after resolution, so a generated
target_path like '../../etc/passwd' is rejected rather than executed.
"""
import subprocess
import py_compile
import os
import sys
from pathlib import Path


class SandboxViolation(Exception):
    pass


class ToolManager:
    def __init__(self, project_root: Path):
        self.root = project_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        if self.root not in candidate.parents and candidate != self.root:
            raise SandboxViolation(f"Path escapes workspace sandbox: {relative_path}")
        return candidate

    def create_folder(self, relative_path: str) -> Path:
        p = self._safe_path(relative_path)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def write_file(self, relative_path: str, content: str) -> Path:
        p = self._safe_path(relative_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p

    def read_file(self, relative_path: str) -> str:
        return self._safe_path(relative_path).read_text()

    def check_syntax(self, relative_path: str) -> tuple[bool, str]:
        p = self._safe_path(relative_path)
        try:
            py_compile.compile(str(p), doraise=True)
            return True, ""
        except py_compile.PyCompileError as e:
            return False, str(e)

    def run_pytest(self, relative_dir: str = ".", timeout: int = 60) -> tuple[bool, str]:
        target = self._safe_path(relative_dir)
        # sys.executable -m pytest: bare "python3"/"pytest" can hit the
        # Windows Store alias stub ("Python was not found") or a PATH
        # mismatch, which silently fails every run.
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(target), "-v", "--tb=short"],
            cwd=str(target),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        passed = result.returncode == 0
        return passed, (result.stdout + "\n" + result.stderr)
