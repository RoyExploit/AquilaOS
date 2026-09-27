"""Temporary pre-push secret scan (deleted before committing)."""
import os
import re
import sys

PATTERNS = {
    "gemini/API key (AQ.)": re.compile(r"AQ\.[A-Za-z0-9_\-]{10,}"),
    "openai-style key (sk-)": re.compile(r"\bsk-[A-Za-z0-9_\-]{10,}"),
    "google api key (AIza)": re.compile(r"AIza[A-Za-z0-9_\-]{10,}"),
    "github token": re.compile(r"\b(ghp_|gho_|ghu_|ghs_|ghr_)[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"),
    "slack token": re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "aws key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "hardcoded api_key value": re.compile(r"[\"']?api[_-]?key[\"']?\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']", re.I),
    "hardcoded token": re.compile(r"\btoken\s*[:=]\s*[\"'][^\"'\s]{16,}[\"']", re.I),
    "password literal": re.compile(r"password\s*[:=]\s*[\"'][^\"'\s]{6,}[\"']", re.I),
    "personal path (C:\\Users\\<name>)": re.compile(r"[Cc]:\\\\?Users\\\\?[A-Za-z0-9._\-]+"),
    "personal email": re.compile(r"\b[A-Za-z0-9._%+\-]+@(?:gmail|outlook|hotmail|yahoo)\.[A-Za-z]{2,}\b", re.I),
    "env var with value": re.compile(r"\b[A-Z][A-Z0-9_]*(API_KEY|TOKEN|SECRET)\s*=\s*[\"']?[A-Za-z0-9_\-]{8,}"),
}

SKIP_DIRS = {".git", "__pycache__", "workspace", "node_modules", ".venv", "venv"}
SKIP_FILES = {"agentos_memory.sqlite3", "_secret_scan.py"}
SKIP_EXT = {".pyc", ".sqlite3", ".png", ".jpg", ".zip", ".exe"}

roots = sys.argv[1:] or ["."]
findings = []
checked = 0
for root in roots:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name in SKIP_FILES or os.path.splitext(name)[1].lower() in SKIP_EXT:
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except OSError:
                continue
            checked += 1
            for lineno, line in enumerate(lines, 1):
                for label, rx in PATTERNS.items():
                    if rx.search(line):
                        findings.append((path, lineno, label, line.strip()[:160]))

print(f"scanned {checked} files")
if not findings:
    print("CLEAN: no secrets / personal data patterns found")
else:
    print(f"FOUND {len(findings)} potential issue(s):")
    for path, lineno, label, text in findings:
        print(f"  [{label}] {path}:{lineno}: {text}")
