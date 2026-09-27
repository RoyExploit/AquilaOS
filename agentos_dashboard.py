"""AgentOS Dashboard - Phase 3 (single file).

A PyQt6 desktop UI wired directly to the real Phase 1+2 engine
(agentos.brain.CoreBrain + agentos.agents.MultiAgentCoordinator). It does
not simulate anything: pressing Run actually calls the planner LLM,
actually dispatches atomic actions to the specialized agents, actually
verifies/tests/repairs, and streams every real event into the UI live.

Run:
    python agentos_dashboard.py
Requires the `agentos` package (Phase 1+2) importable -- keep this file
in the same project root as the agentos/ folder, or `pip install -e .`
it, or just leave it alongside as shipped in the delivered zip.

Panels (top header nav switches between four sections):
  - Home (default)  : left  = folder tree of generated files (click a file
                         to read the code the models wrote)
                      right = chat section -- talk directly to the planner
                         model (it plans everything and hands tasks to the
                         worker models); role selector picks Planner/Worker;
                         Run feeds the message to the full agent pipeline
  - Tasks            : Agent Dashboard + Task Queue + Execution Log +
                         Memory/History + Model Manager
  - Files            : full-width file explorer + code viewer
  - Settings         : provider/key dialog (same as before)
"""
import sys
import os
import re
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QFileSystemModel, QColor, QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QSplitter, QLineEdit, QPushButton, QLabel, QTabWidget, QTableWidget,
    QTableWidgetItem, QPlainTextEdit, QTreeView, QGroupBox, QHeaderView,
    QMessageBox, QDialog, QDialogButtonBox, QComboBox, QSpinBox, QCheckBox,
    QScrollArea, QFrame, QSizePolicy, QStackedWidget, QTextBrowser,
    QButtonGroup,
)

from agentos.config import AgentOSConfig
from agentos.llm.factory import build_provider, list_models, REGISTRY as PROVIDER_REGISTRY
from agentos.brain import CoreBrain
from agentos.agents import AGENT_REGISTRY

# Per-provider field requirements, used to build the Settings dialog and to
# decide which fields to enable/show for the provider currently selected in
# each role's dropdown. This mirrors what each *_provider.py __init__ accepts.
# model_selectable: only local providers (ollama / lmstudio) show a model
# picker -- for cloud providers you give provider + API key and the model is
# detected automatically from the key at run time (nothing to choose).
PROVIDER_FIELDS = {
    "anthropic":  {"api_key": True,  "base_url": False, "model_selectable": False,
                   "default_model": "claude-sonnet-4-6",
                   "api_key_hint": "sk-ant-...", "base_url_default": ""},
    "openai":     {"api_key": True,  "base_url": True, "model_selectable": False,
                   "default_model": "gpt-4.1",
                   "api_key_hint": "sk-...", "base_url_default": "https://api.openai.com/v1"},
    "gemini":     {"api_key": True,  "base_url": False, "model_selectable": False,
                   "default_model": "gemini-flash-latest",
                   "api_key_hint": "AIza...", "base_url_default": ""},
    "openrouter": {"api_key": True,  "base_url": False, "model_selectable": False,
                   "default_model": "meta-llama/llama-3.1-70b-instruct",
                   "api_key_hint": "sk-or-...", "base_url_default": ""},
    "ollama":     {"api_key": False, "base_url": True,  "model_selectable": True,
                   "default_model": "llama3.1",
                   "api_key_hint": "", "base_url_default": "http://localhost:11434"},
    "lmstudio":   {"api_key": False, "base_url": True,  "model_selectable": True,
                   "default_model": "local-model",
                   "api_key_hint": "", "base_url_default": "http://localhost:1234/v1"},
    "mock":       {"api_key": False, "base_url": False, "model_selectable": False,
                   "default_model": "mock-deterministic",
                   "api_key_hint": "", "base_url_default": ""},
}

# Fenced code blocks are scrubbed from planner chat replies: weak local
# models sometimes dump code anyway, but code belongs to the workers.
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)

STATUS_COLORS = {
    "done": "#3fb950", "written": "#3fb950", "completed": "#3fb950",
    "clean": "#3fb950", "flagged_and_fixed": "#d29922",
    "flagged_unresolved": "#f85149", "failed_unrepaired": "#f85149",
    "escalation_required": "#f85149", "started": "#58a6ff",
}

# --------------------------------------------------------------------- #
# Visual theme -- a modern dark palette (deep charcoal + violet accent)
# applied across the main window and the Settings dialog.
# --------------------------------------------------------------------- #

_FONT_STACK = '"Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif'

MODERN_STYLESHEET = f"""
    * {{
        font-family: {_FONT_STACK};
    }}
    QMainWindow, QWidget, QDialog {{
        background-color: #0b0e14;
        color: #e6e8ef;
        font-size: 13px;
    }}
    /* QWidget's background rule also matches QLabels (subclasses), which
       would paint an opaque box behind every label's text -- e.g. a dark
       rectangle behind the header title. Keep labels see-through. */
    QLabel {{ background-color: transparent; }}

    /* ---- Header bar ---- */
    QWidget#headerBar {{
        background-color: #12141c;
        border-bottom: 1px solid #23263080;
    }}
    QFrame#logoMark {{
        background-color: #1b1e33;
        border: 1px solid #3a3480;
        border-radius: 10px;
    }}
    QLabel#logoLetter {{
        color: #a89bff;
        font-size: 18px;
        font-weight: 800;
        background-color: transparent;
    }}
    QLabel#headerTitle {{
        color: #f2f3f8;
        font-size: 17px;
        font-weight: 700;
        letter-spacing: 0.3px;
    }}
    QLabel#headerSubtitle {{
        color: #7d8296;
        font-size: 11px;
    }}

    /* ---- Header nav pills ---- */
    QPushButton#navButton {{
        background-color: transparent;
        border: 1px solid transparent;
        color: #8b93a7;
        padding: 7px 18px;
        border-radius: 999px;
        font-weight: 600;
        font-size: 12.5px;
    }}
    QPushButton#navButton:hover {{ background-color: #171a24; color: #d7dae4; }}
    QPushButton#navButton:checked {{
        background-color: #1b1e33;
        border: 1px solid #3a3480;
        color: #a89bff;
    }}

    /* ---- Chat section ---- */
    QLabel#panelTitle {{ color: #f2f3f8; font-size: 15px; font-weight: 700; }}
    QScrollArea#chatScroll {{
        border: none;
        background-color: transparent;
    }}
    QWidget#chatLog {{ background-color: transparent; }}
    QFrame#bubbleUser {{
        background-color: #251d55;
        border: 1px solid #7c5cff;
        border-radius: 14px;
    }}
    QFrame#bubbleBot {{
        background-color: #171a24;
        border: 1px solid #2a2f3f;
        border-radius: 14px;
    }}
    QLabel#bubbleNameMine {{ color: #b9a8ff; font-size: 10.5px; font-weight: 700; }}
    QLabel#bubbleName {{ color: #8fb8ff; font-size: 10.5px; font-weight: 700; }}
    QLabel#chatSystem {{ color: #7d8296; font-size: 11.5px; }}
    QComboBox#roleCombo {{
        background-color: #171a24;
        border: 1px solid #2a2f3f;
        border-radius: 999px;
        padding: 5px 14px;
        font-weight: 600;
        min-width: 130px;
    }}
    QComboBox#roleCombo:focus {{ border: 1px solid #7c5cff; }}

    /* ---- Code viewer ---- */
    QPlainTextEdit#codeView {{
        background-color: #0a0c12;
        border: 1px solid #232735;
        border-radius: 12px;
        font-family: "Cascadia Code", "Consolas", "Menlo", monospace;
        font-size: 12.5px;
        color: #c9d4f2;
        selection-background-color: #7c5cff;
    }}
    QLabel#pathChip {{
        background-color: #171a24;
        border: 1px solid #2a2f3f;
        border-radius: 999px;
        padding: 4px 12px;
        color: #8fb8ff;
        font-size: 11px;
        font-family: "Cascadia Code", "Consolas", monospace;
    }}

    /* ---- Cards / group boxes ---- */
    QGroupBox {{
        background-color: #12151d;
        border: 1px solid #232735;
        border-radius: 12px;
        margin-top: 14px;
        padding: 14px 12px 12px 12px;
        font-weight: 600;
        font-size: 12.5px;
        color: #c6cad6;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 12px;
        top: 2px;
        padding: 0 4px;
        color: #b7bdd0;
    }}
    QWidget#providerCard {{
        background-color: transparent;
    }}

    /* ---- Inputs ---- */
    QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {{
        background-color: #171a24;
        border: 1px solid #2a2f3f;
        border-radius: 8px;
        padding: 6px 10px;
        color: #e6e8ef;
        selection-background-color: #7c5cff;
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{
        border: 1px solid #7c5cff;
    }}
    QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
        color: #565b6b;
        background-color: #14161e;
        border-color: #202331;
    }}
    QComboBox::drop-down {{
        border: none;
        width: 22px;
    }}
    QComboBox QAbstractItemView {{
        background-color: #171a24;
        border: 1px solid #2a2f3f;
        selection-background-color: #7c5cff;
        color: #e6e8ef;
        outline: none;
    }}
    QCheckBox {{ color: #b7bdd0; spacing: 6px; }}

    /* ---- Buttons ---- */
    QPushButton {{
        background-color: #1c1f2b;
        color: #d7dae4;
        border: 1px solid #2a2f3f;
        border-radius: 8px;
        padding: 7px 16px;
        font-weight: 600;
    }}
    QPushButton:hover {{ background-color: #232739; border-color: #3a4056; }}
    QPushButton:pressed {{ background-color: #191c27; }}
    QPushButton:disabled {{ background-color: #14161e; color: #4d5164; border-color: #1c1f2b; }}

    QPushButton#primaryButton {{
        background-color: #7c5cff;
        color: #ffffff;
        border: 1px solid #7c5cff;
    }}
    QPushButton#primaryButton:hover {{ background-color: #8f72ff; }}
    QPushButton#primaryButton:pressed {{ background-color: #6a4bfa; }}
    QPushButton#primaryButton:disabled {{ background-color: #33304f; color: #7a7a92; border-color: #33304f; }}

    QPushButton#secondaryButton {{
        background-color: transparent;
        color: #b7bdd0;
        border: 1px solid #2a2f3f;
    }}
    QPushButton#secondaryButton:hover {{ background-color: #171a24; border-color: #7c5cff; color: #ffffff; }}

    QPushButton#detectButton {{
        background-color: #1a2333;
        color: #8fb8ff;
        border: 1px solid #2c3c58;
        padding: 6px 12px;
    }}
    QPushButton#detectButton:hover {{ background-color: #20304a; }}

    /* ---- Labels ---- */
    QLabel#hintLabel {{ color: #7d8296; font-size: 11px; }}
    QLabel#modelChip {{
        background-color: #171a24;
        border: 1px solid #2a2f3f;
        border-radius: 999px;
        padding: 4px 12px;
        color: #b7bdd0;
        font-size: 11.5px;
        font-weight: 600;
    }}
    QLabel#agentPill {{
        background-color: #12151d;
        border: 1px solid #202331;
        border-radius: 8px;
        padding: 6px 10px;
    }}
    QLabel#statusBar {{
        background-color: #12141c;
        border-top: 1px solid #23263080;
        padding: 7px 18px;
        font-size: 11.5px;
        font-weight: 600;
    }}

    /* ---- Settings dialog chrome ---- */
    QWidget#dialogHeader {{
        background-color: #12141c;
        border-bottom: 1px solid #23263080;
    }}
    QLabel#dialogTitle {{ font-size: 16px; font-weight: 700; color: #f2f3f8; }}
    QLabel#dialogSubtitle {{ font-size: 11.5px; color: #7d8296; }}
    QWidget#dialogFooter {{
        background-color: #0e1017;
        border-top: 1px solid #23263080;
    }}
    QScrollArea#settingsScroll {{ border: none; background: transparent; }}
    QGroupBox#sectionCard {{ background-color: #12151d; }}

    /* ---- Tabs ---- */
    QTabWidget::pane {{ border: 1px solid #232735; border-radius: 10px; top: -1px; }}
    QTabBar::tab {{
        background: transparent;
        color: #7d8296;
        padding: 8px 16px;
        margin-right: 2px;
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
        font-weight: 600;
    }}
    QTabBar::tab:selected {{ background: #12151d; color: #e6e8ef; border: 1px solid #232735; border-bottom: none; }}
    QTabBar::tab:hover:!selected {{ color: #b7bdd0; }}

    /* ---- Tables / trees ---- */
    QTableWidget, QTreeView {{
        background-color: #0f121a;
        alternate-background-color: #12151d;
        border: 1px solid #232735;
        border-radius: 10px;
        gridline-color: #1d2130;
        color: #e6e8ef;
    }}
    QHeaderView::section {{
        background-color: #171a24;
        color: #9aa0b4;
        border: none;
        border-bottom: 1px solid #232735;
        padding: 6px;
        font-weight: 700;
    }}
    QTableWidget::item:selected, QTreeView::item:selected {{
        background-color: #2a2440;
        color: #ffffff;
    }}
    QPlainTextEdit#logView {{
        background-color: #0a0c12;
        border: 1px solid #232735;
        border-radius: 10px;
        font-family: "Cascadia Code", "Consolas", "Menlo", monospace;
        font-size: 12px;
        color: #b7d9ff;
    }}

    QSplitter#mainSplitter::handle {{ background-color: #0b0e14; width: 6px; }}

    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: #2a2f3f; border-radius: 5px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: #3a4056; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: #2a2f3f; border-radius: 5px; min-width: 24px; }}

    QMessageBox {{ background-color: #12151d; }}
"""


# --------------------------------------------------------------------- #
# Background worker: runs the real CoreBrain off the UI thread
# --------------------------------------------------------------------- #

class BrainWorker(QObject):
    log_line = pyqtSignal(str)
    step_event = pyqtSignal(dict)
    finished = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, goal: str, project_name: str, config: AgentOSConfig):
        super().__init__()
        self.goal = goal
        self.project_name = project_name
        self.config = config

    def run(self):
        try:
            planner_llm = build_provider(self.config.planner)
            worker_llm = build_provider(self.config.worker)
            brain = CoreBrain(
                planner_llm, worker_llm,
                workspace_dir=self.config.workspace_dir,
                max_repair_attempts=self.config.max_repair_attempts,
                planner_review=getattr(self.config, "planner_review", True),
            )

            def progress_cb(evt: dict):
                self.step_event.emit(evt)
                self.log_line.emit(_format_event(evt))

            report = brain.run(self.goal, self.project_name, progress_cb=progress_cb)
            self.finished.emit(report)
        except Exception as e:
            self.failed.emit(str(e))


def _format_event(evt: dict) -> str:
    kind = evt.get("kind", "")
    if kind == "planning_started":
        return f"[Planner] Planning goal: {evt['goal']}"
    if kind == "plan_ready":
        return f"[Planner] Plan ready (project_id={evt['project_id']})"
    if kind == "actions_flattened":
        return f"[Coordinator] {evt['count']} atomic actions queued"
    if kind == "action_started":
        return f"[{evt['agent']}] -> {evt['path']}"
    if kind == "verify_failed":
        return f"  ! Verification FAILED: {evt['error'].strip()[:150]}"
    if kind == "repair_done":
        return f"  [Repair Engine] fixed={evt['fixed']} (attempts={evt['attempts']})"
    if kind == "security_scanned":
        return f"  [Security Agent] {evt['status']}"
    if kind == "action_done":
        return f"  done: {evt['status']}"
    if kind == "test_run_started":
        return "[Testing Agent] Running pytest..."
    if kind == "test_run_done":
        return f"[Testing Agent] tests passed={evt['passed']}"
    if kind == "run_complete":
        return f"=== RUN COMPLETE: {evt['final_status']} ({evt['project_root']}) ==="
    return f"[event] {kind}: {evt}"


# --------------------------------------------------------------------- #
# Background worker: fetches the live model list for a provider off-thread
# --------------------------------------------------------------------- #

class ModelFetchWorker(QObject):
    models_ready = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, provider_name: str, spec: dict):
        super().__init__()
        self.provider_name = provider_name
        self.spec = spec

    def run(self):
        try:
            models = list_models(self.provider_name, **self.spec)
            self.models_ready.emit(models)
        except Exception as e:
            self.failed.emit(str(e))


# --------------------------------------------------------------------- #
# Chat section: talk directly to the configured LLM (planner by default)
# --------------------------------------------------------------------- #

class ChatWorker(QObject):
    """Background chat call -- one message to the selected role's LLM."""
    reply = pyqtSignal(str, str)  # (answer text, "provider / model" used)
    failed = pyqtSignal(str)

    def __init__(self, role: str, spec: dict, prompt: str):
        super().__init__()
        self.role = role
        self.spec = dict(spec)
        self.prompt = prompt

    def run(self):
        try:
            llm = build_provider(self.spec)
            if self.role == "planner":
                # Orchestrator persona -- deliberately short: small local
                # models follow short instructions far better than long
                # ones. Casual chat in, plain-language plan out, no code;
                # actionable goals end with the [[DISPATCH]] marker.
                system = (
                    "You are the AgentOS planner. Chat naturally; never ask "
                    "for 'better prompts'. NEVER output code, XML, JSON or "
                    "file contents -- only workers create files. Keep "
                    "replies under 120 words as short bullet steps. If the "
                    "user asks to build, create, fix or change something, "
                    "end with exactly one final line:\n"
                    "[[DISPATCH]] <one-line goal>\n"
                    "Otherwise omit that line."
                )
            else:
                system = (
                    "You are a worker model inside AgentOS. You receive "
                    "tasks from the planner and execute them. In chat, be "
                    "brief and practical; do not paste large code blocks -- "
                    "code is written by the pipeline, not in conversation."
                )
            resp = llm.generate(system, self.prompt, max_tokens=2000, temperature=0.3)
            self.reply.emit(resp.text.strip(),
                            f"{self.spec.get('provider', '?')} / {resp.model}")
        except Exception as e:
            self.failed.emit(str(e))


class ChatPanel(QWidget):
    """The right-hand chat section of the Home screen.

    Lets you converse directly with the configured LLM -- the planner by
    default, since it plans everything and dispatches tasks to the worker
    models. The role pill switches between Planner/Worker (which model
    answers), and Run hands the current input to the full agent pipeline.
    """

    run_requested = pyqtSignal(str)  # goal text from the input box

    def __init__(self, config: AgentOSConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self._thread = None
        self._worker = None
        self._busy = False
        self._warned_about_model = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("\U0001F4AC  Chat")
        title.setObjectName("panelTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.role_combo = QComboBox()
        self.role_combo.setObjectName("roleCombo")
        self.role_combo.currentIndexChanged.connect(self._refresh_role_chip)
        head.addWidget(self.role_combo)
        self.model_chip = QLabel("")
        self.model_chip.setObjectName("modelChip")
        head.addWidget(self.model_chip)
        root.addLayout(head)

        self.chat_scroll = QScrollArea()
        self.chat_scroll.setObjectName("chatScroll")
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.chat_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        # Real widget bubbles (not rich text) so rounded corners, borders
        # and colors come straight from the stylesheet and actually render.
        self.chat_log = QWidget()
        self.chat_log.setObjectName("chatLog")
        self.chat_layout = QVBoxLayout(self.chat_log)
        self.chat_layout.setContentsMargins(4, 4, 4, 4)
        self.chat_layout.setSpacing(8)
        self.chat_layout.addStretch(1)
        self.chat_scroll.setWidget(self.chat_log)
        root.addWidget(self.chat_scroll, stretch=1)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText(
            "Message the planner directly, or type a goal and press Run\u2026"
        )
        self.input.returnPressed.connect(self._on_send)
        self.project_input = QLineEdit("generated_project")
        self.project_input.setPlaceholderText("project name")
        self.project_input.setMaximumWidth(160)
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("primaryButton")
        self.send_button.clicked.connect(self._on_send)
        self.run_button = QPushButton("\u25B6  Run")
        self.run_button.clicked.connect(
            lambda: self.run_requested.emit(self.input.text().strip())
        )
        input_row.addWidget(self.input, stretch=1)
        input_row.addWidget(self.project_input)
        input_row.addWidget(self.send_button)
        input_row.addWidget(self.run_button)
        root.addLayout(input_row)

        self.refresh_roles()
        self.append_system(
            "Chat is live \u2014 pick a role above and talk to the model. "
            "Planner plans everything; workers execute. Press Run to hand "
            "your message to the full agent pipeline."
        )

    # ---------------- messages ---------------- #

    def _append_bubble(self, who: str, text: str, mine: bool):
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)

        bubble = QFrame()
        bubble.setObjectName("bubbleUser" if mine else "bubbleBot")
        inner = QVBoxLayout(bubble)
        inner.setContentsMargins(12, 8, 12, 8)
        inner.setSpacing(2)

        name = QLabel(who)
        name.setObjectName("bubbleNameMine" if mine else "bubbleName")
        body = QLabel(text)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setMaximumWidth(520)
        inner.addWidget(name)
        inner.addWidget(body)

        if mine:
            row.addStretch(1)
            row.addWidget(bubble)
        else:
            row.addWidget(bubble)
            row.addStretch(1)
        # insert before the trailing stretch so messages stack top-down
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, wrap)
        self._scroll_chat_bottom()

    def append_system(self, text: str):
        lbl = QLabel(text)
        lbl.setObjectName("chatSystem")
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, lbl)
        self._scroll_chat_bottom()

    def _scroll_chat_bottom(self):
        bar = self.chat_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def append_user(self, text: str):
        self._append_bubble("You", text, mine=True)

    # ---------------- role / model selection ---------------- #

    def refresh_roles(self):
        """Re-read planner/worker specs (after Settings were saved)."""
        previous = self.role_combo.currentData() or "planner"
        self.role_combo.blockSignals(True)
        self.role_combo.clear()
        self.role_combo.addItem("\U0001F9E0 Planner", "planner")
        self.role_combo.addItem("\U0001F6E0\uFE0F Worker", "worker")
        idx = self.role_combo.findData(previous)
        self.role_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.role_combo.blockSignals(False)
        self._refresh_role_chip()

    def _refresh_role_chip(self):
        role = self.role_combo.currentData() or "planner"
        spec = getattr(self.config, role, {}) or {}
        model = spec.get("model", "auto") or "auto"
        self.model_chip.setText(f"{spec.get('provider', '?')} / {model}")

    def _planner_model_warning(self) -> str | None:
        """Coder/completion models make poor planners -- they ignore the
        orchestrator persona and just dump code. Warn once per session so
        users know it's the model, not the platform."""
        role = self.role_combo.currentData() or "planner"
        if role != "planner":
            return None
        spec = getattr(self.config, role, {}) or {}
        model = str(spec.get("model", "")).lower()
        if "cod" in model or "coder" in model or "completion" in model:
            return (
                "Heads-up: your planner is a code-completion model. For "
                "planning, an instruction/chat model (e.g. llama3.1, "
                "qwen2.5-instruct, mistral-instruct or a cloud chat model) "
                "works far better -- keep coder models for the worker role."
            )
        return None

    # ---------------- send ---------------- #

    def _on_send(self):
        text = self.input.text().strip()
        if not text or self._busy:
            return
        self.input.clear()
        role = self.role_combo.currentData() or "planner"
        spec = dict(getattr(self.config, role, {}) or {})
        self.append_user(text)
        self.append_system(f"\u2192 {role} is thinking\u2026")
        warning = self._planner_model_warning()
        if warning and not self._warned_about_model:
            self._warned_about_model = True
            self.append_system(f"\u2139 {warning}")

        self._busy = True
        self.send_button.setEnabled(False)
        self._thread = QThread()
        self._worker = ChatWorker(role, spec, text)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.reply.connect(self._on_reply)
        self._worker.failed.connect(self._on_chat_failed)
        self._worker.reply.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.start()

    def _on_reply(self, text: str, model: str):
        self._busy = False
        self.send_button.setEnabled(True)
        role = self.role_combo.currentData() or "planner"
        text = (text or "").strip()

        # The planner marks actionable goals with [[DISPATCH]] -- casual
        # chat stays chat, real requests flow straight into the pipeline
        # (plan -> workers -> planner checks) without extra typing.
        goal = ""
        if "[[DISPATCH]]" in text:
            text, _, tail = text.partition("[[DISPATCH]]")
            goal = tail.strip().splitlines()[0].strip() if tail.strip() else ""
            text = text.rstrip()

        # Guard against code dumps: strip fenced blocks so the chat stays
        # conversational even if the model ignores its persona.
        scrubbed = _CODE_FENCE_RE.sub("", text)
        had_code = scrubbed != text
        text = re.sub(r"\n{3,}", "\n\n", scrubbed).strip()

        self._append_bubble(f"{role} \u00B7 {model}", text or "(empty reply)", mine=False)
        if goal:
            self.append_system("Plan ready \u2014 dispatching to the workers\u2026")
            self.run_requested.emit(goal)
        elif had_code:
            self.append_system(
                "Code stays out of the chat \u2014 the workers write the "
                "files during a run. Press \u25B6 Run to dispatch the goal."
            )

    def _on_chat_failed(self, error: str):
        self._busy = False
        self.send_button.setEnabled(True)
        self.append_system(f"\u26A0 Chat error: {error}")


# --------------------------------------------------------------------- #
# Folder section: workspace tree + code viewer (click a file -> read it)
# --------------------------------------------------------------------- #

class FilesPanel(QWidget):
    """Folder tree of the generated workspace with a code viewer.

    Used on the left of Home (compact) and as the full Files section --
    click any file the models created to see exactly what code was written
    inside it.
    """

    def __init__(self, root_path: str, compact: bool = True, parent=None):
        super().__init__(parent)
        self.fs_model = QFileSystemModel(self)
        self.fs_model.setRootPath(root_path)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("\U0001F4C1  Files")
        title.setObjectName("panelTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.path_chip = QLabel("\u2014")
        self.path_chip.setObjectName("pathChip")
        head.addWidget(self.path_chip)
        layout.addLayout(head)

        split = QSplitter(Qt.Orientation.Vertical)
        self.tree = QTreeView()
        self.tree.setModel(self.fs_model)
        self.tree.setRootIndex(self.fs_model.index(root_path))
        self.tree.setAnimated(True)
        self.tree.setIndentation(16)
        if compact:
            self.tree.setHeaderHidden(True)
            for col in range(1, 4):
                self.tree.setColumnHidden(col, True)
        self.tree.clicked.connect(self._open_file)
        split.addWidget(self.tree)

        self.code_view = QPlainTextEdit()
        self.code_view.setObjectName("codeView")
        self.code_view.setReadOnly(True)
        self.code_view.setPlaceholderText(
            "Click a file on the left to see the code the models wrote\u2026"
        )
        if compact:
            split.addWidget(self.code_view)
            split.setSizes([240, 360])
        else:
            split.addWidget(self.code_view)
            split.setSizes([320, 480])
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        layout.addWidget(split, stretch=1)

        self.root_path = root_path

    def set_root(self, path: str):
        """Point both tree and viewers at a new project root."""
        self.root_path = path
        self.fs_model.setRootPath(path)
        self.tree.setRootIndex(self.fs_model.index(path))

    def _open_file(self, index):
        if not index.isValid() or self.fs_model.isDir(index):
            return
        path = self.fs_model.filePath(index)
        try:
            if os.path.getsize(path) > 1_000_000:
                self.code_view.setPlainText("(file larger than 1 MB -- not shown)")
                self.path_chip.setText(os.path.basename(path))
                return
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as e:
            content = f"(could not read file: {e})"
        self.code_view.setPlainText(content)
        try:
            self.path_chip.setText(os.path.relpath(path, self.root_path))
        except ValueError:
            self.path_chip.setText(os.path.basename(path))


# --------------------------------------------------------------------- #
# Provider settings (one instance per role: planner / worker)
# --------------------------------------------------------------------- #

class ProviderSettingsWidget(QWidget):
    """One role's (planner or worker) provider configuration block: provider
    dropdown, API key field (with show/hide) and base URL field. Local
    providers (Ollama / LM Studio) additionally get a model picker with a
    'Detect models' button listing what the local server actually has
    loaded. Cloud providers have no model field at all -- provider + API key
    is the entire configuration; the model is detected automatically from
    the key when a run starts.
    """

    def __init__(self, role_label: str, role_dict: dict, parent=None):
        super().__init__(parent)
        self.role_label = role_label
        self.setObjectName("providerCard")
        self._fetch_thread = None
        self._fetch_worker = None

        form = QFormLayout(self)
        form.setSpacing(10)
        form.setContentsMargins(4, 4, 4, 4)

        self.provider_combo = QComboBox()
        self.provider_combo.addItems(list(PROVIDER_REGISTRY.keys()))
        form.addRow("Provider", self.provider_combo)

        key_row = QHBoxLayout()
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_key_check = QCheckBox("Show")
        self.show_key_check.toggled.connect(
            lambda checked: self.api_key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        key_row.addWidget(self.api_key_edit, stretch=1)
        key_row.addWidget(self.show_key_check)
        self.api_key_row_label = QLabel("API key")
        form.addRow(self.api_key_row_label, key_row)

        self.base_url_edit = QLineEdit()
        self.base_url_row_label = QLabel("Endpoint / base URL")
        form.addRow(self.base_url_row_label, self.base_url_edit)

        model_row = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_combo.setMinimumWidth(220)
        self.detect_button = QPushButton("\u21bb Detect models")
        self.detect_button.setObjectName("detectButton")
        self.detect_button.clicked.connect(self._on_detect_clicked)
        model_row.addWidget(self.model_combo, stretch=1)
        model_row.addWidget(self.detect_button)
        # Wrapped in a widget (instead of a bare layout) so the whole model
        # row can be hidden for cloud providers -- for them the model is
        # chosen automatically from the API key, so there is nothing to pick.
        self.model_row_label = QLabel("Model")
        self.model_row_widget = QWidget()
        self.model_row_widget.setLayout(model_row)
        form.addRow(self.model_row_label, self.model_row_widget)

        # Cloud providers: one optional line to pin a specific model. Left
        # blank (the default), the model is detected from the API key; a
        # typed name/ID is honored and self-heals if it's ever retired.
        self.model_pin_label = QLabel("Model (optional)")
        self.model_pin = QLineEdit()
        self.model_pin.setPlaceholderText("auto -- detected from your API key")
        self.model_pin_row = QWidget()
        pin_layout = QHBoxLayout(self.model_pin_row)
        pin_layout.setContentsMargins(0, 0, 0, 0)
        pin_layout.addWidget(self.model_pin, stretch=1)
        self.model_pin_row.setVisible(False)
        self.model_pin_label.setVisible(False)
        form.addRow(self.model_pin_label, self.model_pin_row)

        self.model_status_label = QLabel("")
        self.model_status_label.setObjectName("hintLabel")
        self.model_status_label.setWordWrap(True)
        form.addRow("", self.model_status_label)

        self.env_hint_label = QLabel("")
        self.env_hint_label.setObjectName("hintLabel")
        self.env_hint_label.setWordWrap(True)
        form.addRow("", self.env_hint_label)

        self._loading = False
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)
        self._load(role_dict)

    def _load(self, role_dict: dict):
        # Suppress the "user switched providers" auto-defaulting behavior
        # while populating from a saved config -- saved values should stick
        # exactly as they were, even if they differ from that provider's
        # out-of-the-box default.
        self._loading = True
        try:
            provider = role_dict.get("provider", "mock")
            idx = self.provider_combo.findText(provider)
            self.provider_combo.setCurrentIndex(idx if idx >= 0 else 0)
            saved_model = role_dict.get("model", "")
            self.model_combo.clear()
            if saved_model:
                self.model_combo.addItem(saved_model)
            self.model_combo.setCurrentText(saved_model)
            saved_pin = role_dict.get("model", "") or ""
            self.model_pin.setText("" if saved_pin == "auto" else saved_pin)
            self.api_key_edit.setText(role_dict.get("api_key", "") or "")
            self.base_url_edit.setText(role_dict.get("base_url", "") or "")
            self._on_provider_changed(self.provider_combo.currentText())
        finally:
            self._loading = False

    def _on_provider_changed(self, provider: str):
        fields = PROVIDER_FIELDS.get(provider, {})
        # When the user actively switches providers, refresh the model
        # combo to that provider's default (a stale model name left over
        # from a different provider is far more likely to be wrong than
        # right). When loading a saved config, keep whatever was saved.
        if not self._loading or not self.model_combo.currentText().strip():
            default_model = fields.get("default_model", "")
            self.model_combo.clear()
            if default_model:
                self.model_combo.addItem(default_model)
            self.model_combo.setCurrentText(default_model)
            self.model_status_label.setText("")

        needs_key = fields.get("api_key", False)
        self.api_key_edit.setEnabled(needs_key)
        self.api_key_row_label.setEnabled(needs_key)
        self.show_key_check.setEnabled(needs_key)
        self.api_key_edit.setPlaceholderText(fields.get("api_key_hint", ""))

        needs_url = fields.get("base_url", False)
        self.base_url_edit.setEnabled(needs_url)
        self.base_url_row_label.setEnabled(needs_url)
        if needs_url and (not self._loading or not self.base_url_edit.text().strip()):
            self.base_url_edit.setText(fields.get("base_url_default", ""))

        # Local providers get the manual model picker + live detection; for
        # cloud providers the whole model row disappears -- the model is
        # resolved automatically from the API key at run time.
        selectable = fields.get("model_selectable", False)
        self.model_row_label.setVisible(selectable)
        self.model_row_widget.setVisible(selectable)
        self.model_status_label.setVisible(selectable)
        is_cloud = bool(fields.get("api_key")) and not selectable and provider != "mock"
        self.model_pin_label.setVisible(is_cloud)
        self.model_pin_row.setVisible(is_cloud)

        env_var = f"{provider.upper()}_API_KEY"
        if needs_key:
            self.env_hint_label.setText(
                f"Paste your key here, or leave blank to fall back to the {env_var} "
                "environment variable. The model is detected automatically from the "
                "key -- nothing else to choose."
            )
        elif provider in ("ollama", "lmstudio"):
            self.env_hint_label.setText("Local/offline provider -- no API key needed, just a running server.")
        elif provider == "mock":
            self.env_hint_label.setText("Deterministic built-in provider -- no setup needed.")
        else:
            self.env_hint_label.setText("")

    def _on_detect_clicked(self):
        """Calls the provider's own /models endpoint with whatever API key
        / base URL is currently typed in, and repopulates the model combo
        with exactly what came back -- so the person picks from models this
        key can actually call, instead of typing a guessed name."""
        provider = self.provider_combo.currentText()
        spec = {}
        fields = PROVIDER_FIELDS.get(provider, {})
        if fields.get("api_key") and self.api_key_edit.text().strip():
            spec["api_key"] = self.api_key_edit.text().strip()
        if fields.get("base_url") and self.base_url_edit.text().strip():
            spec["base_url"] = self.base_url_edit.text().strip()

        self.detect_button.setEnabled(False)
        self.detect_button.setText("Detecting...")
        self.model_status_label.setStyleSheet("color: #8b949e;")
        self.model_status_label.setText("Contacting provider...")

        self._fetch_thread = QThread()
        self._fetch_worker = ModelFetchWorker(provider, spec)
        self._fetch_worker.moveToThread(self._fetch_thread)
        self._fetch_thread.started.connect(self._fetch_worker.run)
        self._fetch_worker.models_ready.connect(self._on_models_ready)
        self._fetch_worker.failed.connect(self._on_models_failed)
        self._fetch_worker.models_ready.connect(self._fetch_thread.quit)
        self._fetch_worker.failed.connect(self._fetch_thread.quit)
        self._fetch_thread.start()

    def _on_models_ready(self, models: list):
        self.detect_button.setEnabled(True)
        self.detect_button.setText("\u21bb Detect models")
        current = self.model_combo.currentText().strip()
        self.model_combo.clear()
        self.model_combo.addItems(models)
        if current and current in models:
            self.model_combo.setCurrentText(current)
        elif models:
            self.model_combo.setCurrentIndex(0)
        self.model_status_label.setStyleSheet("color: #3fb950;")
        self.model_status_label.setText(f"\u2713 Found {len(models)} model(s) available to this key.")

    def _on_models_failed(self, error: str):
        self.detect_button.setEnabled(True)
        self.detect_button.setText("\u21bb Detect models")
        self.model_status_label.setStyleSheet("color: #f85149;")
        self.model_status_label.setText(f"Couldn't detect models: {error}")

    def to_dict(self) -> dict:
        provider = self.provider_combo.currentText()
        fields = PROVIDER_FIELDS.get(provider, {})
        if fields.get("model_selectable", False):
            model = self.model_combo.currentText().strip() or fields.get("default_model", "")
        elif provider == "mock":
            model = fields.get("default_model", "mock-deterministic")
        else:
            # Cloud providers: blank pin = model resolved from the API key
            # at run time; a pinned name/ID is honored (and self-heals if
            # the provider later retires it).
            model = self.model_pin.text().strip() or "auto"
        out = {
            "provider": provider,
            "model": model,
        }
        if fields.get("api_key") and self.api_key_edit.text().strip():
            out["api_key"] = self.api_key_edit.text().strip()
        if fields.get("base_url") and self.base_url_edit.text().strip():
            out["base_url"] = self.base_url_edit.text().strip()
        return out


class SettingsDialog(QDialog):
    """LLM Provider settings -- lets the user pick, per role (Planner /
    Worker), which provider to use and supply its API key / endpoint
    directly from the UI, instead of hand-editing config.yaml.
    """

    def __init__(self, config: AgentOSConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setObjectName("settingsDialog")
        self.resize(600, 680)
        self.config = config

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        header.setObjectName("dialogHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(24, 20, 24, 18)
        title = QLabel("\u2699\ufe0f  Provider Settings")
        title.setObjectName("dialogTitle")
        subtitle = QLabel(
            "Choose an LLM provider per role and paste its API key -- that's all "
            "for cloud providers; the model is detected automatically from your "
            "key. Local servers also let you pick or detect the loaded model."
        )
        subtitle.setObjectName("dialogSubtitle")
        subtitle.setWordWrap(True)
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        outer.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("settingsScroll")
        scroll_body = QWidget()
        body = QVBoxLayout(scroll_body)
        body.setContentsMargins(24, 18, 24, 18)
        body.setSpacing(16)

        planner_box = QGroupBox("\U0001F9E0  Planner")
        planner_box.setObjectName("sectionCard")
        planner_layout = QVBoxLayout(planner_box)
        self.planner_widget = ProviderSettingsWidget("Planner", config.planner)
        planner_layout.addWidget(self.planner_widget)
        body.addWidget(planner_box)

        worker_box = QGroupBox("\U0001F6E0\ufe0f  Worker")
        worker_box.setObjectName("sectionCard")
        worker_layout = QVBoxLayout(worker_box)
        self.worker_widget = ProviderSettingsWidget("Worker", config.worker)
        worker_layout.addWidget(self.worker_widget)
        body.addWidget(worker_box)

        general_box = QGroupBox("\u2699\ufe0f  General")
        general_box.setObjectName("sectionCard")
        general_form = QFormLayout(general_box)
        general_form.setSpacing(10)
        self.max_repair_spin = QSpinBox()
        self.max_repair_spin.setRange(0, 20)
        self.max_repair_spin.setValue(config.max_repair_attempts)
        general_form.addRow("Max repair attempts", self.max_repair_spin)
        self.workspace_edit = QLineEdit(config.workspace_dir)
        general_form.addRow("Workspace directory", self.workspace_edit)
        self.review_check = QCheckBox(
            "Planner reviews worker output (re-prompts workers on rejection)"
        )
        self.review_check.setChecked(getattr(config, "planner_review", True))
        general_form.addRow("", self.review_check)
        body.addWidget(general_box)

        test_card = QGroupBox("")
        test_card.setObjectName("sectionCard")
        test_layout = QVBoxLayout(test_card)
        test_row = QHBoxLayout()
        self.test_button = QPushButton("\u25B6  Test connection")
        self.test_button.setObjectName("secondaryButton")
        self.test_button.clicked.connect(self._on_test_clicked)
        test_row.addWidget(self.test_button)
        test_row.addStretch(1)
        test_layout.addLayout(test_row)
        self.test_result_label = QLabel("")
        self.test_result_label.setObjectName("hintLabel")
        self.test_result_label.setWordWrap(True)
        test_layout.addWidget(self.test_result_label)
        test_hint = QLabel("Testing uses the fields as-is -- press Save to keep your changes.")
        test_hint.setObjectName("hintLabel")
        test_layout.addWidget(test_hint)
        body.addWidget(test_card)

        body.addStretch(1)
        scroll.setWidget(scroll_body)
        outer.addWidget(scroll, stretch=1)

        footer = QWidget()
        footer.setObjectName("dialogFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(24, 14, 24, 14)
        footer_layout.addStretch(1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primaryButton")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setObjectName("secondaryButton")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        footer_layout.addWidget(buttons)
        outer.addWidget(footer)

        # Snapshot of everything as loaded -- compared when the dialog is
        # closed so a "Test connection, then X/Cancel" sequence can never
        # silently throw the user's selections away again.
        self._initial_state = self._snapshot()

    def _snapshot(self) -> dict:
        return {
            "planner": self.planner_widget.to_dict(),
            "worker": self.worker_widget.to_dict(),
            "max_repair": self.max_repair_spin.value(),
            "workspace": self.workspace_edit.text().strip(),
            "review": self.review_check.isChecked(),
        }

    def reject(self):
        if self._snapshot() != getattr(self, "_initial_state", None):
            choice = QMessageBox.question(
                self,
                "Unsaved changes",
                "Your settings changes were not saved yet.\n\n"
                "Save them before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if choice == QMessageBox.StandardButton.Save:
                self._on_save()
                return
            if choice == QMessageBox.StandardButton.Cancel:
                return
        super().reject()

    def _on_test_clicked(self):
        """Best-effort connectivity check: builds each configured provider
        and fires a tiny real generate() call. Never blocks the whole app --
        this dialog is modal and the calls are short, but errors are always
        caught and shown rather than raised.
        """
        self.test_result_label.setStyleSheet("color: #8b949e;")
        self.test_result_label.setText("Testing...")
        QApplication.processEvents()
        results = []
        for role_name, widget in (("Planner", self.planner_widget), ("Worker", self.worker_widget)):
            spec = widget.to_dict()
            try:
                provider = build_provider(dict(spec))
                resp = provider.generate(
                    system="Reply with exactly one word: OK",
                    prompt="Say OK.",
                    max_tokens=10,
                    temperature=0.0,
                )
                snippet = (resp.text or "").strip().replace("\n", " ")[:40]
                results.append(f"{role_name} ({spec['provider']}): OK -- \"{snippet}\"")
            except Exception as e:
                results.append(f"{role_name} ({spec.get('provider', '?')}): FAILED -- {e}")
        ok = all("FAILED" not in r for r in results)
        self.test_result_label.setStyleSheet(f"color: {'#3fb950' if ok else '#f85149'};")
        self.test_result_label.setText(" | ".join(results))

    def _on_save(self):
        self.config.planner = self.planner_widget.to_dict()
        self.config.worker = self.worker_widget.to_dict()
        self.config.max_repair_attempts = self.max_repair_spin.value()
        self.config.workspace_dir = self.workspace_edit.text().strip() or "workspace"
        self.config.planner_review = self.review_check.isChecked()
        try:
            path = self.config.save()
        except Exception as e:
            QMessageBox.critical(self, "Save failed", f"Could not write config.yaml:\n{e}")
            return
        QMessageBox.information(self, "Settings saved", f"Provider settings saved to:\n{path}")
        self.accept()


# --------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------- #

class Dashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AgentOS - Autonomous Agent Platform (Phase 3 Dashboard)")
        self.resize(1300, 850)
        self.config = AgentOSConfig.load()
        self.thread = None
        self.worker = None
        self.action_rows = {}

        self._build_ui()
        self._apply_theme()

    # ---------------- UI construction ---------------- #

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._header_bar())

        # Four header sections; Home is the default one.
        self.stack = QStackedWidget()
        self.stack.addWidget(self._home_page())    # 0 -- Home (default)
        self.stack.addWidget(self._tasks_page())   # 1 -- Tasks
        self.stack.addWidget(self._files_page())   # 2 -- Files
        self.stack.currentChanged.connect(self._sync_nav)
        root.addWidget(self.stack, stretch=1)

        self.status_label = QLabel("\u25CF Idle")
        self.status_label.setObjectName("statusBar")
        self.status_label.setStyleSheet("color: #8b949e;")
        root.addWidget(self.status_label)

    def _header_bar(self):
        """Top bar: brand block optically centered (no glitch between the
        mark, the name and the subtitle), section nav pills in the middle,
        Settings on the right."""
        bar = QWidget()
        bar.setObjectName("headerBar")
        bar.setFixedHeight(66)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(22, 0, 22, 0)
        layout.setSpacing(8)

        # Letter mark instead of a special glyph -- renders identically on
        # every system (missing-symbol "tofu" boxes caused header glitches).
        logo = QFrame()
        logo.setObjectName("logoMark")
        logo.setFixedSize(36, 36)
        logo_layout = QVBoxLayout(logo)
        logo_layout.setContentsMargins(0, 0, 0, 0)
        logo_letter = QLabel("A")
        logo_letter.setObjectName("logoLetter")
        logo_letter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_layout.addWidget(logo_letter)

        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title_box.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        title = QLabel("AgentOS")
        title.setObjectName("headerTitle")
        subtitle = QLabel("Autonomous AI Agent Platform")
        subtitle.setObjectName("headerSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        brand.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        brand.addWidget(logo)
        brand.addLayout(title_box)
        layout.addLayout(brand)
        layout.addStretch(1)

        self.nav_group = QButtonGroup(self)
        self.nav_buttons = {}
        for idx, label in enumerate(("Home", "Tasks", "Files")):
            btn = QPushButton(label)
            btn.setObjectName("navButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.nav_group.addButton(btn, idx)
            self.nav_buttons[label] = btn
            layout.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.nav_group.buttonClicked.connect(self._on_nav)
        self.nav_buttons["Home"].setChecked(True)

        settings_btn = QPushButton("\u2699  Settings")
        settings_btn.setObjectName("navButton")
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.clicked.connect(self._on_settings_clicked)
        layout.addWidget(settings_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        return bar

    def _on_nav(self, btn):
        """Header nav: switch the main stack to the chosen section."""
        self.stack.setCurrentIndex(max(self.nav_group.id(btn), 0))

    def _sync_nav(self, idx: int):
        """Keep the nav pill in sync whenever the section changes."""
        labels = ("Home", "Tasks", "Files")
        if 0 <= idx < len(labels):
            self.nav_buttons[labels[idx]].setChecked(True)

    def _workspace_path(self) -> str:
        path = self.config.workspace_dir or "workspace"
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass
        return os.path.abspath(path)

    def _home_page(self):
        """Default section: folder tree (left) + chat with the models (right)."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 16, 18, 12)
        layout.setSpacing(0)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setObjectName("mainSplitter")
        self.home_files = FilesPanel(self._workspace_path(), compact=True)
        self.chat_panel = ChatPanel(self.config)
        self.chat_panel.run_requested.connect(self._on_run_requested)
        split.addWidget(self.home_files)
        split.addWidget(self.chat_panel)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        layout.addWidget(split, stretch=1)
        return page

    def _tasks_page(self):
        """Agents, task queue, execution log, memory and the model manager."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 16, 18, 12)
        layout.setSpacing(14)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(14)
        left_layout.addWidget(self._agent_dashboard_box())
        left_layout.addWidget(self._model_manager_box())
        left_layout.addStretch(1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("mainSplitter")
        splitter.addWidget(left)
        splitter.addWidget(self._tabs())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        layout.addWidget(splitter, stretch=1)
        return page

    def _files_page(self):
        """Full-width explorer for everything the models created."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 16, 18, 12)
        self.files_full = FilesPanel(self._workspace_path(), compact=False)
        layout.addWidget(self.files_full, stretch=1)
        return page

    def _model_manager_box(self):
        box = QGroupBox("\U0001F9E9  Model Manager")
        box.setObjectName("sectionCard")
        layout = QVBoxLayout(box)
        layout.setSpacing(4)
        row = QHBoxLayout()
        self.planner_model_label = QLabel()
        self.planner_model_label.setObjectName("modelChip")
        self.worker_model_label = QLabel()
        self.worker_model_label.setObjectName("modelChip")
        row.addWidget(self.planner_model_label)
        row.addWidget(self.worker_model_label)
        row.addStretch(1)
        layout.addLayout(row)
        self.model_manager_note = QLabel()
        self.model_manager_note.setObjectName("hintLabel")
        layout.addWidget(self.model_manager_note)
        self._refresh_model_manager_box()
        return box

    def _refresh_model_manager_box(self):
        p, w = self.config.planner, self.config.worker
        self.planner_model_label.setText(f"Planner: {p.get('provider', '?')} / {p.get('model', '?')}")
        self.worker_model_label.setText(f"Worker: {w.get('provider', '?')} / {w.get('model', '?')}")
        self.model_manager_note.setText(
            "(no config.yaml found -- using built-in mock provider)"
            if not os.path.exists("config.yaml") else ""
        )

    def _on_settings_clicked(self):
        dialog = SettingsDialog(self.config, parent=self)
        if dialog.exec():
            # Settings were saved to config.yaml -- reload to pick up exactly
            # what's on disk (rather than trusting in-memory state), then
            # refresh the Model Manager panel and the chat's role/model chip
            # to reflect it immediately, with no app restart required.
            self.config = AgentOSConfig.load()
            self._refresh_model_manager_box()
            self.chat_panel.config = self.config
            self.chat_panel.refresh_roles()

    def _agent_dashboard_box(self):
        box = QGroupBox("\U0001F916  Agent Dashboard")
        box.setObjectName("sectionCard")
        layout = QVBoxLayout(box)
        layout.setSpacing(8)
        self.agent_status_labels = {}
        seen = set()
        for role, cls in AGENT_REGISTRY.items():
            display = f"{cls.__name__} ({role})"
            if cls.__name__ in seen and role not in ("coding",):
                pass  # still show role-specialized entries individually
            seen.add(cls.__name__)
            lbl = QLabel(f"\u25CF {display}: idle")
            lbl.setObjectName("agentPill")
            lbl.setStyleSheet("color: #8b949e;")
            layout.addWidget(lbl)
            self.agent_status_labels[role] = lbl
        layout.addStretch(1)
        return box

    def _tabs(self):
        tabs = QTabWidget()
        tabs.setObjectName("mainTabs")

        # Task Queue
        self.task_table = QTableWidget(0, 5)
        self.task_table.setHorizontalHeaderLabels(["ID", "Agent", "Path", "Status", "Notes"])
        self.task_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.task_table.setAlternatingRowColors(True)
        self.task_table.verticalHeader().setVisible(False)
        tabs.addTab(self.task_table, "\U0001F4CB  Task Queue")

        # Execution Log
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("logView")
        tabs.addTab(self.log_view, "\U0001F5A5\ufe0f  Execution Log")

        # Project Tree (real file explorer)
        self.fs_model = QFileSystemModel()
        self.file_tree = QTreeView()
        self.file_tree.setModel(self.fs_model)
        self.file_tree.setAlternatingRowColors(True)
        tabs.addTab(self.file_tree, "\U0001F4C1  Project Tree")

        # Memory / History viewer
        self.memory_table = QTableWidget(0, 4)
        self.memory_table.setHorizontalHeaderLabels(["Time", "Kind", "Action", "Detail"])
        self.memory_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.memory_table.setAlternatingRowColors(True)
        self.memory_table.verticalHeader().setVisible(False)
        tabs.addTab(self.memory_table, "\U0001F4DC  Memory / History")

        return tabs

    def _apply_theme(self):
        self.setStyleSheet(MODERN_STYLESHEET)

    # ---------------- Run handling ---------------- #

    def _on_run_requested(self, goal: str):
        project_name = self.chat_panel.project_input.text().strip() or "generated_project"
        if not goal:
            goal = ("Build a PyQt6 calculator application with a numeric display "
                    "and basic arithmetic buttons.")
        # Record the dispatched goal in the conversation so it's clear what
        # the pipeline is working on (manual Run and auto-dispatch both).
        self.chat_panel.append_system(f"Goal \u2192 {goal}")
        self.chat_panel.input.clear()
        self.chat_panel.append_system(
            f"Running the full pipeline for \u2018{project_name}\u2019 \u2014 "
            "live progress shows up here and on the Tasks tab."
        )

        self.chat_panel.run_button.setEnabled(False)
        self.status_label.setText("\u25CF Running...")
        self.status_label.setStyleSheet("color: #58a6ff;")
        self.log_view.clear()
        self.task_table.setRowCount(0)
        self.memory_table.setRowCount(0)
        self.action_rows = {}
        for lbl in self.agent_status_labels.values():
            lbl.setText(lbl.text().split(":")[0] + ": idle")
            lbl.setStyleSheet("color: #8b949e;")

        self.thread = QThread()
        self.worker = BrainWorker(goal, project_name, self.config)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log_line.connect(self._append_log)
        self.worker.step_event.connect(self._handle_event)
        self.worker.finished.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.start()

    def _append_log(self, line: str):
        self.log_view.appendPlainText(line)

    def _handle_event(self, evt: dict):
        kind = evt.get("kind")

        # Mirror the interesting pipeline events into the chat so the
        # conversation shows what the planner/workers are actually doing.
        chat_notes = {
            "planning_started": "\U0001F9E0 Planner: planning the goal\u2026",
            "plan_ready": "\U0001F9ED Plan ready \u2014 dispatching tasks to the workers.",
            "actions_flattened": f"\U0001F4CB {evt.get('count', 0)} atomic tasks queued.",
            "verify_failed": "\u26A0 Verification failed \u2192 repair loop.",
            "planner_review": (
                f"\U0001F50D Planner check \u00B7 "
                f"{os.path.basename(evt.get('path', ''))}: "
                + ("approved" if evt.get("approved")
                   else "needs fixes \u2192 re-prompting worker")
            ),
            "planner_review_fixed": (
                f"\U0001F50D Planner re-check: {evt.get('status')}"
            ),
            "test_run_done": f"\U0001F9EA Tests passed={evt.get('passed')}",
            "run_complete": f"\u2705 Run complete: {evt.get('final_status')}",
        }
        if kind in chat_notes:
            self.chat_panel.append_system(chat_notes[kind])

        if kind == "action_started":
            agent = evt["agent"]
            self._set_agent_status(agent, "working", "#58a6ff")
            row = self.task_table.rowCount()
            self.task_table.insertRow(row)
            self.task_table.setItem(row, 0, QTableWidgetItem(evt["action_id"]))
            self.task_table.setItem(row, 1, QTableWidgetItem(agent))
            self.task_table.setItem(row, 2, QTableWidgetItem(evt["path"]))
            self.task_table.setItem(row, 3, self._colored_item("running"))
            self.task_table.setItem(row, 4, QTableWidgetItem(""))
            self.action_rows[evt["action_id"]] = row

        elif kind == "action_done":
            row = self.action_rows.get(evt["action_id"])
            if row is not None:
                self.task_table.setItem(row, 3, self._colored_item(evt["status"]))

        elif kind == "verify_failed":
            row = self.action_rows.get(evt["action_id"])
            if row is not None:
                self.task_table.setItem(row, 4, QTableWidgetItem("verify failed -> repairing"))

        elif kind == "repair_done":
            row = self.action_rows.get(evt["action_id"])
            if row is not None:
                note = f"repaired in {evt['attempts']} attempt(s)" if evt["fixed"] else "repair FAILED"
                self.task_table.setItem(row, 4, QTableWidgetItem(note))

        elif kind == "security_scanned":
            row = self.action_rows.get(evt["action_id"])
            if row is not None:
                existing = self.task_table.item(row, 4).text() if self.task_table.item(row, 4) else ""
                self.task_table.setItem(row, 4, QTableWidgetItem(f"{existing} | security: {evt['status']}".strip(" |")))
            self._set_agent_status("security", evt["status"], STATUS_COLORS.get(evt["status"], "#8b949e"))

        elif kind == "action_started" or kind == "action_done":
            pass  # handled above

        for role in self.agent_status_labels:
            if kind == "action_started" and evt.get("agent") == role:
                self._set_agent_status(role, "working", "#58a6ff")
            if kind == "action_done":
                aid = evt.get("action_id")
                row = self.action_rows.get(aid)
                if row is not None and self.task_table.item(row, 1) and self.task_table.item(row, 1).text() == role:
                    self._set_agent_status(role, "idle", "#3fb950")

    def _set_agent_status(self, role: str, status: str, color: str):
        lbl = self.agent_status_labels.get(role)
        if lbl:
            base = lbl.text().split(":")[0]
            lbl.setText(f"{base}: {status}")
            lbl.setStyleSheet(f"color: {color};")

    def _colored_item(self, status: str) -> QTableWidgetItem:
        item = QTableWidgetItem(status)
        color = STATUS_COLORS.get(status, "#8b949e")
        item.setForeground(QColor(color))
        return item

    def _on_finished(self, report: dict):
        self.chat_panel.run_button.setEnabled(True)
        status = report.get("final_status", "unknown")
        color = STATUS_COLORS.get(status, "#8b949e")
        self.status_label.setStyleSheet(f"color: {color};")
        self.status_label.setText(
            f"\u25CF Finished: {status} | tests_passed={report.get('tests_passed')} | "
            f"project: {report.get('project_root')}"
        )
        root = report.get("project_root")
        if root and os.path.isdir(root):
            # Point every folder section at the generated project so the
            # files the models created can be opened and read right away.
            self.home_files.set_root(root)
            self.files_full.set_root(root)
            self.fs_model.setRootPath(root)
            self.file_tree.setRootIndex(self.fs_model.index(root))

        project_id = report.get("project_id")
        if project_id:
            self._load_memory(project_id)

    def _load_memory(self, project_id: int):
        try:
            from agentos.memory import MemoryManager
            mem = MemoryManager()  # same default db path CoreBrain used
            for evt in mem.history(project_id):
                row = self.memory_table.rowCount()
                self.memory_table.insertRow(row)
                self.memory_table.setItem(row, 0, QTableWidgetItem(f"{evt['ts']:.1f}"))
                self.memory_table.setItem(row, 1, QTableWidgetItem(evt["kind"]))
                self.memory_table.setItem(row, 2, QTableWidgetItem(evt["action_id"]))
                self.memory_table.setItem(row, 3, QTableWidgetItem(str(evt["detail"])[:200]))
        except Exception as e:
            self._append_log(f"[Memory Viewer] could not load history: {e}")

    def _on_failed(self, error: str):
        self.chat_panel.run_button.setEnabled(True)
        self.chat_panel.append_system(f"\u26A0 Run failed: {error}")
        self.status_label.setStyleSheet("color: #f85149;")
        self.status_label.setText(f"\u25CF FAILED: {error}")
        QMessageBox.critical(self, "Run failed", error)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(MODERN_STYLESHEET)  # applies to the main window AND any dialogs it opens
    window = Dashboard()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
