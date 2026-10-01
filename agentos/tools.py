"""Tool Manager: file/folder creation, code execution, and test running.

Security: every path is resolved relative to the project's workspace root
and checked to still be inside it after resolution, so a generated
target_path like '../../etc/passwd' is rejected rather than executed.
"""
import subprocess
import py_compile
import json
import os
import sys
import difflib
from pathlib import Path


class SandboxViolation(Exception):
    pass


def count_line_changes(old: str, new: str) -> tuple[int, int]:
    """(lines_added, lines_removed) between two file contents.

    Used by the dashboard HUD to show '+12 / -2' per generated file."""
    added = removed = 0
    for line in difflib.ndiff(old.splitlines(), new.splitlines()):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return added, removed


class ToolManager:
    def __init__(self, project_root: Path, extra_roots=None, allow_outside: bool = False):
        self.root = Path(project_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        # Paths the user explicitly approved from the chat (e.g. by tagging
        # a file with @). Writes/reads are still sandboxed to these unless
        # allow_outside is on.
        self.extra_roots = [Path(p).resolve() for p in (extra_roots or [])]
        self.allow_outside = bool(allow_outside)
        # Last write's (+added, -removed) line stats -- read by CoreBrain to
        # emit file_diff events for the dashboard HUD. None until first write.
        self.last_write: dict | None = None

    def add_root(self, path: str):
        """Approve one more location (a folder or a single file) for this run."""
        p = Path(path).resolve()
        if p.is_file():
            p = p.parent
        if p not in self.extra_roots:
            self.extra_roots.append(p)

    def _roots(self):
        return [self.root] + self.extra_roots

    def _safe_path(self, relative_path: str) -> Path:
        raw = Path(relative_path)
        candidate = (raw if raw.is_absolute() else self.root / raw).resolve()
        if self.allow_outside:
            return candidate
        for root in self._roots():
            if candidate == root or root in candidate.parents:
                return candidate
        raise SandboxViolation(
            f"Path outside the approved locations: {relative_path} "
            "(tag the file with @ in the chat, or enable PC access in Settings)"
        )

    def create_folder(self, relative_path: str) -> Path:
        p = self._safe_path(relative_path)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def write_file(self, relative_path: str, content: str) -> Path:
        p = self._safe_path(relative_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        previous = ""
        if p.exists():
            try:
                previous = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                previous = ""
        added, removed = count_line_changes(previous, content)
        self.last_write = {"path": relative_path, "added": added, "removed": removed}
        p.write_text(content)
        return p

    def read_file(self, relative_path: str) -> str:
        return self._safe_path(relative_path).read_text()

    def check_syntax(self, relative_path: str) -> tuple[bool, str]:
        """Cheapest reliable check for this file type. .py gets compiled,
        .json/.yaml manifests get parsed -- a malformed manifest used to be
        waved through as "skipped (non-python file)", which meant a broken
        requirements/config file could only surface much later, if at all."""
        p = self._safe_path(relative_path)
        suffix = p.suffix.lower()
        if suffix == ".py":
            try:
                py_compile.compile(str(p), doraise=True)
                return True, ""
            except py_compile.PyCompileError as e:
                return False, str(e)
        if suffix == ".json":
            try:
                json.loads(p.read_text(encoding="utf-8"))
                return True, ""
            except (OSError, ValueError) as e:
                return False, f"invalid JSON: {e}"
        if suffix in (".yaml", ".yml"):
            try:
                import yaml
                yaml.safe_load(p.read_text(encoding="utf-8"))
                return True, ""
            except (OSError, ValueError) as e:
                return False, f"invalid YAML: {e}"
        return True, ""

    def run_command(self, command: str, cwd: str | None = None,
                    timeout: int = 180) -> tuple[bool, str, int]:
        """Run a shell command on the user's machine (client-agent style) so
        generated code can actually be tested. Returns (ok, output, code)."""
        workdir = str(self._safe_path(cwd)) if cwd else str(self.root)
        try:
            result = subprocess.run(
                command, shell=True, cwd=workdir, capture_output=True,
                text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return False, f"(timed out after {timeout}s): {command}", -1
        except OSError as e:
            return False, f"(could not run: {e})", -1
        output = (result.stdout or "") + (("\n" + result.stderr) if result.stderr else "")
        return result.returncode == 0, output.strip(), result.returncode

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
