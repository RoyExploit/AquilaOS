"""A deterministic fake provider. Not for real project generation --
it exists so the orchestration loop (plan -> decompose -> execute ->
verify -> test -> repair) can be proven to actually work end-to-end
without requiring any API key or local model server. Swap it for a
real provider in config.yaml the moment you have credentials.
"""
import json
from .base import LLMProvider, LLMResponse


PLAN_JSON = {
    "phases": [
        {
            "name": "Build calculator app",
            "milestones": [
                {
                    "name": "Core app scaffold",
                    "tasks": [
                        {
                            "name": "Create calculator module",
                            "subtasks": [
                                {
                                    "name": "Implement calculator logic + PyQt6 UI",
                                    "atomic_actions": [
                                        {
                                            "id": "a1",
                                            "type": "create_folder",
                                            "target_path": "calculator_app",
                                            "description": "project root",
                                        },
                                        {
                                            "id": "a2",
                                            "type": "generate_file",
                                            "agent": "ui",
                                            "target_path": "calculator_app/calculator.py",
                                            "description": (
                                                "A PyQt6 calculator: QMainWindow with a QLineEdit display "
                                                "and a QGridLayout of buttons 0-9, +, -, *, /, =, C. "
                                                "Wire button clicks to build an expression string and "
                                                "evaluate it safely on '='. Must be importable without "
                                                "opening a window (guard app.exec() behind "
                                                "`if __name__ == '__main__':`)."
                                            ),
                                            "success_criteria": "valid python, imports PyQt6, no syntax errors",
                                        },
                                        {
                                            "id": "a3",
                                            "type": "generate_test",
                                            "agent": "testing",
                                            "target_path": "calculator_app/test_calculator.py",
                                            "description": (
                                                "pytest test that imports calculator.py and checks the "
                                                "expression-evaluation function directly (e.g. '2+2' -> 4) "
                                                "WITHOUT instantiating QApplication."
                                            ),
                                            "success_criteria": "pytest passes",
                                        },
                                        {
                                            "id": "a4",
                                            "type": "generate_doc",
                                            "agent": "documentation",
                                            "target_path": "calculator_app/README.md",
                                            "description": "Short README explaining what the calculator app is and how to run it.",
                                            "success_criteria": "non-empty markdown file",
                                        },
                                        {
                                            "id": "a5",
                                            "type": "generate_config",
                                            "agent": "devops",
                                            "target_path": "calculator_app/requirements.txt",
                                            "description": "requirements.txt listing PyQt6 and pytest.",
                                            "success_criteria": "non-empty file listing PyQt6",
                                        },
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    ]
}


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, model: str = "mock-deterministic", **kwargs):
        super().__init__(model, **kwargs)

    @staticmethod
    def list_models(**kwargs) -> list[str]:
        return ["mock-deterministic"]

    def _generate(self, system, prompt, max_tokens=2000, temperature=0.2) -> LLMResponse:
        p = prompt.lower()

        if "only valid json" in system.lower() and "decompose" in p:
            text = json.dumps(PLAN_JSON)

        elif "readme.md" in p:
            text = README_CONTENT

        elif "requirements.txt" in p:
            text = REQUIREMENTS_CONTENT

        elif "generate_file" in p or ("write the complete contents" in p and "calculator.py" in p):
            text = CALCULATOR_CODE

        elif "generate_test" in p or ("write the complete contents" in p and "test_calculator" in p):
            text = TEST_CODE

        elif "findings" in p:
            # Security Agent path: nothing dangerous in the demo files, so this
            # branch only fires if a scan somehow flags something; return as-is.
            if "calculator.py" in p:
                text = CALCULATOR_CODE
            else:
                text = TEST_CODE

        elif "fix" in p and "error" in p:
            # Repair path: if asked to fix broken code, just return the known-good version.
            if "test_calculator" in p:
                text = TEST_CODE
            else:
                text = CALCULATOR_CODE
        else:
            text = "OK"

        return LLMResponse(text=text, provider=self.name, model=self.model, raw=None)


CALCULATOR_CODE = '''"""Simple PyQt6 calculator."""
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QGridLayout, QLineEdit, QPushButton
import sys


def evaluate_expression(expr: str):
    """Safely evaluate a simple arithmetic expression string."""
    allowed = set("0123456789+-*/(). ")
    if not expr or not all(c in allowed for c in expr):
        raise ValueError(f"Invalid expression: {expr!r}")
    return eval(expr, {"__builtins__": {}}, {})


class CalculatorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Calculator")
        self.expression = ""

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.display = QLineEdit()
        self.display.setReadOnly(True)
        layout.addWidget(self.display)

        grid = QGridLayout()
        layout.addLayout(grid)

        buttons = [
            "7", "8", "9", "/",
            "4", "5", "6", "*",
            "1", "2", "3", "-",
            "0", "C", "=", "+",
        ]
        positions = [(i // 4, i % 4) for i in range(len(buttons))]
        for pos, text in zip(positions, buttons):
            btn = QPushButton(text)
            btn.clicked.connect(lambda checked, t=text: self.on_button(t))
            grid.addWidget(btn, *pos)

    def on_button(self, text):
        if text == "C":
            self.expression = ""
        elif text == "=":
            try:
                self.expression = str(evaluate_expression(self.expression))
            except Exception:
                self.expression = "Error"
        else:
            self.expression += text
        self.display.setText(self.expression)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = CalculatorWindow()
    window.show()
    sys.exit(app.exec())
'''

README_CONTENT = """# Calculator App

A minimal PyQt6 calculator with a numeric display and buttons for
0-9, +, -, *, /, =, and C (clear).

## Run

```
pip install -r requirements.txt
python calculator.py
```

## Test

```
pytest test_calculator.py
```
"""

REQUIREMENTS_CONTENT = "PyQt6>=6.6\npytest>=7.4\n"

TEST_CODE = '''"""Tests for calculator logic (no QApplication instantiation needed)."""
from calculator import evaluate_expression
import pytest


def test_addition():
    assert evaluate_expression("2+2") == 4


def test_multiplication():
    assert evaluate_expression("3*4") == 12


def test_invalid_expression_raises():
    with pytest.raises(ValueError):
        evaluate_expression("import os")
'''
