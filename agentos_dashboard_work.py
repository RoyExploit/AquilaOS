"""AquilaOS Dashboard (AgentOS engine) - single file.

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

Panels (top header nav):
  - Home (default)  : LEFT   = files
                      MIDDLE = coding center: tabs with numbered lines 1..N
                               and syntax colors. Tabs open when clicked,
                               and open by themselves when workers write
                               code (watch it appear live).
                      RIGHT  = chat with the models, plus a live HUD showing
                               what is happening right now and how many lines
                               were added/removed per file (green +, red -),
                               plus "Think" cards with the planner's reasoning
  - Tasks            : Agent Dashboard + Task Queue + Execution Log +
                         Memory/History + Model Manager
  - Settings         : provider/key dialog + projects folder

First run: the app asks where your projects live; chat stays disabled until
a folder is chosen (change it later with the chat's "Folder..." button).
Every message, diff line and error in the chat is drag-selectable and can be
copied (Ctrl+C, right-click menu, or the Copy button).
"""
import sys
import os
import re
import time
import json
import yaml
import threading
from html import escape as html_escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ------------------------------------------------------------------ #
# Remembered UI state: one JSON file per machine/user so the layout
# adapts to whatever screen it runs on and is restored on next start.
# ------------------------------------------------------------------ #
UI_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_state.json")


def load_ui_state() -> dict:
    try:
        with open(UI_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_ui_state(state: dict):
    try:
        with open(UI_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1)
    except OSError:
        pass  # layout simply isn't remembered; never break the app for this

from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QObject, QTimer, QFileSystemWatcher, QStringListModel,
    pyqtSlot, QMetaObject, Q_ARG,
)
from PyQt6.QtGui import (
    QFileSystemModel, QColor, QFont, QIcon, QFontMetrics,
    QSyntaxHighlighter, QTextCharFormat, QPainter, QShortcut, QKeySequence,
    QTextCursor,
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QSplitter, QLineEdit, QPushButton, QLabel, QTabWidget, QTableWidget,
    QTableWidgetItem, QPlainTextEdit, QTreeView, QGroupBox, QHeaderView,
    QMessageBox, QDialog, QDialogButtonBox, QComboBox, QSpinBox, QCheckBox,
    QScrollArea, QFrame, QSizePolicy, QStackedWidget, QTextBrowser,
    QButtonGroup, QMenu, QFileDialog, QTabBar, QCompleter, QInputDialog,
    QListWidget, QListWidgetItem,
)

from agentos.config import AgentOSConfig
from agentos.sessions import SessionStore
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
    "escalation_required": "#f85149", "started": "#D97757",
}

# Visual theme lives in agentos_theme.py (Claude Code desktop look).
from agentos_theme import MODERN_STYLESHEET, ACCENT

# --------------------------------------------------------------------- #
# Background worker: runs the real CoreBrain off the UI thread
# --------------------------------------------------------------------- #

class BrainWorker(QObject):
    log_line = pyqtSignal(str)
    step_event = pyqtSignal(dict)
    finished = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, goal: str, project_name: str, config: AgentOSConfig,
                 extra_roots=None, command_approver=None, test_command: str = ""):
        super().__init__()
        self.goal = goal
        self.project_name = project_name
        self.config = config
        self.extra_roots = list(extra_roots or [])
        self.command_approver = command_approver
        self.test_command = test_command

    def run(self):
        try:
            planner_llm = build_provider(self.config.planner)
            worker_llm = build_provider(self.config.worker)
            brain = CoreBrain(
                planner_llm, worker_llm,
                workspace_dir=self.config.workspace_dir,
                max_repair_attempts=self.config.max_repair_attempts,
                planner_review=getattr(self.config, "planner_review", True),
                extra_roots=self.extra_roots,
                allow_outside=getattr(self.config, "allow_pc_access", False),
                command_approver=self.command_approver,
                test_command=self.test_command,
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


class ClickableLabel(QLabel):
    """A QLabel that behaves like a button (used for the model showcase)."""

    clicked = pyqtSignal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ChatInput(QPlainTextEdit):
    """The chat box: wraps and grows with what you type (1-6 lines).

    A single-line QLineEdit cannot wrap or grow, which is why long messages
    used to run off the edge of the box instead of pushing it taller -- the
    "box never resizes" problem. Enter sends, Shift+Enter adds a line.

    It keeps the small QLineEdit-style surface (``text``/``setText``/
    ``clear``/``setCompleter``/``textEdited``) so the rest of the panel --
    including @file completion -- works unchanged.
    """

    submitted = pyqtSignal()
    textEdited = pyqtSignal(str)

    MIN_H = 34
    MAX_H = 168

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chatInput")
        self.setTabChangesFocus(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.MIN_H)
        self._completer = None
        self.textChanged.connect(self._autogrow)

    # ---------------- QLineEdit-compatible surface ---------------- #

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, value: str):
        self.setPlainText(value)

    def setCompleter(self, completer):
        """Reuse the existing QCompleter, popped up manually below."""
        self._completer = completer
        completer.setWidget(self)
        completer.activated.connect(self._apply_completion)

    def _apply_completion(self, text: str):
        """Replace the @word being typed with the picked file name."""
        body = self.toPlainText()
        at = body.rfind("@")
        cursor = self.textCursor()
        if at >= 0:
            cursor.setPosition(at)
            cursor.movePosition(QTextCursor.MoveOperation.End,
                                QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(text + " ")
        self.setTextCursor(cursor)

    # ---------------- self-sizing ---------------- #

    def _autogrow(self):
        """Height follows the text, counting wrapped lines, between MIN_H and
        MAX_H. Computed from the font metrics rather than the document layout
        so it is correct even before the widget has been laid out."""
        metrics = self.fontMetrics()
        width = max(120, self.viewport().width() or self.width() or 240)
        doc = self.document()
        lines = doc.blockCount()
        for i in range(doc.blockCount()):
            text = doc.findBlockByNumber(i).text()
            if text:
                # how many extra rows this paragraph wraps onto
                lines += int(metrics.horizontalAdvance(text) // max(1, width))
        height = max(self.MIN_H, min(self.MAX_H, lines * metrics.lineSpacing() + 14))
        if height != self.height():
            self.setFixedHeight(height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._autogrow()

    # ---------------- keys ---------------- #

    def keyPressEvent(self, event):
        popup = self._completer.popup() if self._completer else None
        if popup is not None and popup.isVisible() and event.key() in (
            Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Tab,
            Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Escape,
        ):
            event.ignore()      # the completion popup owns these keys
            return
        # Enter sends, Shift+Enter makes a new line (ChatGPT behaviour).
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)
        self.textEdited.emit(self.toPlainText())
        self._maybe_complete()

    def _maybe_complete(self):
        """Pop the @file list up while an @word is being typed."""
        if self._completer is None:
            return
        word = self.toPlainText().rsplit("\n", 1)[-1].rsplit(" ", 1)[-1]
        if not word.startswith("@") or len(word) < 2:
            self._completer.popup().hide()
            return
        self._completer.setCompletionPrefix(word)
        if self._completer.completionCount() == 0:
            self._completer.popup().hide()
            return
        rect = self.cursorRect()
        rect.setWidth(self._completer.popup().sizeHintForColumn(0)
                      + self._completer.popup().verticalScrollBar().sizeHint().width()
                      + 24)
        self._completer.complete(rect)


class ChatPanel(QWidget):
    """Chat section of the Home screen -- the human side of the platform.

    The UI spells the rules out in plain language so nothing is a mystery:

      * Send   -> talk to the selected model (answer in chat, nothing built)
      * Run    -> build it: the planner plans, worker agents create the files
      * Think  -> show/hide the planner's + workers' reasoning cards
      * Folder -> pick where projects are created/opened (also the first-run
                  gate: chat stays disabled until a folder is chosen)
      * Copy   -> copy the whole conversation; every message, diff line and
                  error is drag-selectable too.
    """

    run_requested = pyqtSignal(str)          # goal text -> full pipeline
    choose_folder_requested = pyqtSignal()   # "Folder..." clicked
    command_requested = pyqtSignal(str, list)  # command, approved roots
    sessions_changed = pyqtSignal()          # sidebar "Recents" must refresh
    files_toggled = pyqtSignal(bool)         # show/hide the files side panel

    def __init__(self, config: AgentOSConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self._thread = None
        self._worker = None
        self._busy = False
        self._warned_about_model = False
        self._ready = False
        self._replaying = False
        self._transcript: list[str] = []
        self._thinking_cards: list[QWidget] = []
        self._added = 0
        self._removed = 0
        self._hud_base = ""
        self._hud_dots = 0
        self._zoom = 1.0
        self.workspace_tabs = None
        self.project_root = ""
        self.last_tagged: list[str] = []
        # One conversation = one task/project. The store keeps every chat on
        # disk; messages are persisted as they are shown, and only THIS
        # session's history is ever sent to the model.
        self.session_store = SessionStore()
        self.session: dict = self.session_store.list() and (
            self.session_store.load(self.session_store.list()[0]["id"])
            or self.session_store.create()
        ) or self.session_store.create()
        # Live catalog for the role's provider: used to render the model
        # showcase and to fill the "switch model" menu.
        self._catalog: list[str] = []
        self._resolved = ""
        self._model_thread = None
        self._model_worker = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # ---------------- header row ---------------- #
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addStretch(1)

        self.think_button = QPushButton("Think")
        self.think_button.setObjectName("thinkToggle")
        self.think_button.setCheckable(True)
        self.think_button.setChecked(True)
        self.think_button.setToolTip(
            "Show/hide what the planner and the workers are actually thinking:\n"
            "the plan tree, each worker's task, planner review verdicts and\n"
            "re-prompts. Click a card to expand it."
        )
        self.think_button.toggled.connect(self._on_think_toggled)
        head.addWidget(self.think_button)

        # Planner/worker chooser + model info are NOT shown up here -- they
        # live as one underlined link under the chat box (see below).
        self.role_combo = QComboBox()
        self.role_combo.setObjectName("roleCombo")
        self.role_combo.setToolTip(
            "Which configured model answers you here: the Planner (it plans\n"
            "and dispatches the work) or a Worker model."
        )
        self.role_combo.currentIndexChanged.connect(self._refresh_role_chip)

        self.new_chat_button = QPushButton("＋  New chat")
        self.new_chat_button.setObjectName("thinkToggle")
        self.new_chat_button.setToolTip(
            "Start a fresh task in its own conversation. Each chat keeps its\n"
            "own history, project and context -- tasks never mix."
        )
        self.new_chat_button.clicked.connect(self.new_chat)
        head.addWidget(self.new_chat_button)
        self.new_chat_button.setVisible(False)   # lives in the sidebar now

        self.chats_button = QPushButton("🕘  Chats")
        self.chats_button.setObjectName("thinkToggle")
        self.chats_button.setToolTip(
            "Open a previous conversation (one per task/project)."
        )
        self.chats_button.clicked.connect(self._open_chats_menu)
        head.addWidget(self.chats_button)
        self.chats_button.setVisible(False)      # sidebar "Recents" replaces it

        # No "Files" show/hide toggle. The files column is always on the left
        # (see _home_page) -- a toggle that hides it only ever produced the
        # "I clicked a file and nothing happened" confusion.
        root.addLayout(head)

        # ---------------- live session strip ---------------- #
        self.session_title_label = ClickableLabel("")
        self.session_title_label.setObjectName("sessionTitle")
        self.session_title_label.setToolTip(
            "Current task/chat. Click to rename it."
        )
        self.session_title_label.clicked.connect(self._rename_current_chat)
        head.insertWidget(0, self.session_title_label)

        # ---------------- live HUD ---------------- #
        self.hud = QFrame()
        self.hud.setObjectName("chatHud")
        hud_row = QHBoxLayout(self.hud)
        hud_row.setContentsMargins(12, 7, 12, 7)
        hud_row.setSpacing(10)
        self.hud_dot = QLabel("\u25CF")
        self.hud_dot.setObjectName("hudDot")
        hud_row.addWidget(self.hud_dot)
        self.hud_activity = QLabel("Idle")
        self.hud_activity.setObjectName("hudText")
        self.hud_activity.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        hud_row.addWidget(self.hud_activity, stretch=1)
        self.hud_diff = QLabel("")
        self.hud_diff.setObjectName("hudDiff")
        self.hud_diff.setTextFormat(Qt.TextFormat.RichText)
        self.hud_diff.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.hud_diff.setToolTip("Lines added / removed by the workers in this run.")
        hud_row.addWidget(self.hud_diff)
        root.addWidget(self.hud)

        self._hud_timer = QTimer(self)
        self._hud_timer.setInterval(420)
        self._hud_timer.timeout.connect(self._tick_hud)

        # ---------------- conversation ---------------- #
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

        # ---------------- composer (Claude Code style) ---------------- #
        self.composer = QFrame()
        self.composer.setObjectName("composer")
        comp = QVBoxLayout(self.composer)
        comp.setContentsMargins(14, 10, 10, 10)
        comp.setSpacing(6)

        self.input = ChatInput()
        self.input.setPlaceholderText("Message the planner\u2026   $ command   /help")
        self.input.setToolTip(
            "Type and press Enter \u2014 the planner answers, and if you asked for a "
            "build it dispatches the workers.\n"
            "\u2022  @file   attach a file\n"
            "\u2022  $ cmd   run a terminal command\n"
            "\u2022  /help   list every command\n"
            "Shift+Enter makes a new line."
        )
        self.input.submitted.connect(self._on_send)
        self._setup_tag_completer()
        comp.addWidget(self.input)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.project_input = QLineEdit("generated_project")
        self.project_input.setObjectName("projectInput")
        self.project_input.setPlaceholderText("project folder")
        self.project_input.setMinimumWidth(110)
        self.project_input.setMaximumWidth(150)
        self.project_input.setToolTip(
            "Folder name the project is created in, inside your projects folder."
        )
        bar.addWidget(self.project_input)
        self.folder_button = QPushButton("Folder\u2026")
        self.folder_button.setObjectName("thinkToggle")
        self.folder_button.setToolTip(
            "Choose the folder where projects are created or opened.\n"
            "Chat and runs only work once a folder is selected."
        )
        self.folder_button.clicked.connect(self.choose_folder_requested.emit)
        bar.addWidget(self.folder_button)
        bar.addStretch(1)

        self.send_button = QPushButton("\u2191")
        self.send_button.setObjectName("sendButton")
        self.send_button.setFixedSize(32, 32)
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.setToolTip(
            "Send. A build/fix request dispatches the workers automatically."
        )
        self.send_button.clicked.connect(self._on_send)
        bar.addWidget(self.send_button)
        comp.addLayout(bar)
        root.addWidget(self.composer)

        # The planner model is just one underlined line under the chat box --
        # no pill, no box, no URL. Clicking the text is the only way in.
        model_row = QHBoxLayout()
        model_row.setContentsMargins(8, 0, 8, 0)
        self.model_chip = ClickableLabel("")
        self.model_chip.setObjectName("modelLink")
        self.model_chip.setToolTip(
            "The model answering here (auto-detected from your API key).\n"
            "Click to switch role or model; saved to Settings too."
        )
        self.model_chip.clicked.connect(self._open_model_menu)
        model_row.addWidget(self.model_chip)
        model_row.addStretch(1)
        root.addLayout(model_row)

        helper = QLabel(
            "Enter = send \u00B7 Shift+Enter = new line \u00B7 @ = attach a file "
            "\u00B7 $ = run a terminal command \u00B7 /help = command list"
        )
        helper.setObjectName("hintLabel")
        helper.setWordWrap(True)
        helper.setAlignment(Qt.AlignmentFlag.AlignCenter)
        helper.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(helper)

        self.refresh_roles()
        self._render_session()
        # Welcome only for a brand-new chat -- never injected into a restored
        # conversation's history.
        if not self.session.get("messages"):
            self.append_system(
                "Welcome to AquilaOS. Chat normally \u2014 you don't need perfect "
                "prompts. Ask the planner anything; if you want something built or "
                "fixed it plans it and the worker agents write the files, which then "
                "open in the code panel. Use \u201CNew chat\u201D for a new "
                "task \u2014 every task keeps its own conversation."
            )
        self.set_ready(False)

    # ---------------- file tagging (@file) ---------------- #

    def _setup_tag_completer(self):
        """Type @ to pick a file; the model then receives its path+contents."""
        self._tag_model = QStringListModel(self)
        completer = QCompleter(self._tag_model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.input.setCompleter(completer)
        self._completer = completer
        self.input.textEdited.connect(self._refresh_tag_model)

    def refresh_tag_model(self):
        """File suggestions = everything in the projects folder + any file
        that is already open in the coding center."""
        names = []
        root = getattr(self, "project_root", "") or ""
        if root and os.path.isdir(root):
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames
                               if d not in ("__pycache__", ".git", "node_modules")]
                for name in filenames:
                    full = os.path.join(dirpath, name)
                    names.append(os.path.relpath(full, root).replace("\\", "/"))
                if len(names) > 4000:
                    break
        if self.workspace_tabs is not None:
            for rel in self.workspace_tabs.open_paths():
                if rel not in names:
                    names.append(rel)
        self._tag_model.setStringList(sorted(names)[:4000])

    def _refresh_tag_model(self, text: str):
        if "@" in text:
            self.refresh_tag_model()

    def tagged_paths(self) -> list[str]:
        """Absolute paths referenced with @token in the current input."""
        out = []
        tokens = re.findall(r"@([^\s]+)", self.input.text())
        root = getattr(self, "project_root", "") or os.path.abspath(".")
        for tok in tokens:
            tok = tok.strip().rstrip(",.;:)")
            candidate = tok if os.path.isabs(tok) else os.path.join(root, tok)
            candidate = os.path.abspath(candidate)
            if os.path.exists(candidate) and candidate not in out:
                out.append(candidate)
        return out

    def _attachment_block(self, paths: list[str], limit_lines: int = 400) -> str:
        """Build the prompt section that carries tagged files to the model."""
        if not paths:
            return ""
        chunks = ["Attached files (use these as context and for edits):"]
        for path in paths:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    body = "".join(f.readlines()[:limit_lines])
            except OSError as e:
                body = f"(could not read: {e})"
            chunks.append(f"\n--- {path} ---\n{body}")
        return "\n".join(chunks)

    # ---------------- terminal commands ---------------- #

    def _show_command_help(self):
        """Typing /help lists every command -- no buttons or switches needed."""
        self.append_system(
            "\u2318 Commands \u2014 type these straight into the box:\n"
            "$ <cmd>   run a terminal command here (e.g. $ pytest -q)\n"
            "          also: $ python app.py   \u00B7   $ pip install -r requirements.txt\n"
            "! <cmd>   same as $ (quick form)\n"
            "@file     attach a file so the model can read it\n"
            "/help     this list\n"
            "Runs happen inside your projects folder; anything you @-tagged is "
            "allowed too. Output appears right here, and you can just run it "
            "again after asking for a fix."
        )

    # ---------------- zoom ---------------- #

    def apply_zoom(self, factor: float):
        """Ctrl +/- zooms the conversation and bubbles in this panel."""
        self._zoom = max(0.6, min(2.2, factor))
        base = 13.0 * self._zoom
        self.setStyleSheet(
            f"QFrame#bubbleUser, QFrame#bubbleBot {{ font-size: {base:.1f}px; }}"
            f"QLabel#chatSystem, QLabel#diffLine, QLabel#hintLabel {{ "
            f"font-size: {max(8.0, base - 1.5):.1f}px; }}"
            f"QFrame#chatHud QLabel {{ font-size: {max(8.0, base - 1.0):.1f}px; }}"
        )

    def resizeEvent(self, event):
        """Keep the conversation in a centred reading column, like Claude."""
        super().resizeEvent(event)
        side = max(20, (self.width() - 780) // 2)
        lay = self.layout()
        if lay is not None:
            lay.setContentsMargins(side, 0, side, 0)

    @staticmethod
    def _selectable(widget: QWidget):
        """Let the user drag-select and Ctrl+C from this widget."""
        widget.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        return widget

    def _attach_copy_menu(self, widget: QWidget, text_getter, label: str = "message"):
        """Right-click -> Copy (plus copy-all) on any chat element."""
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        def _menu(pos):
            menu = QMenu(widget)
            menu.addAction(f"Copy {label}", lambda: self._to_clipboard(text_getter()))
            menu.addAction("Copy whole chat", self.copy_chat)
            menu.exec(widget.mapToGlobal(pos))

        widget.customContextMenuRequested.connect(_menu)

    @staticmethod
    def _to_clipboard(text: str):
        QApplication.clipboard().setText(text or "")

    def copy_chat(self):
        self._to_clipboard("\n".join(self._transcript))
        self.append_system("\u29C9 Conversation copied to the clipboard.")

    # ---------------- chat sessions (one per task/project) ---------------- #

    def _record(self, role: str, text: str, who: str = ""):
        """Persist one message into the CURRENT session only."""
        if self._replaying or not text:
            return
        self.session.setdefault("messages", []).append(
            {"role": role, "text": text, "who": who}
        )
        self.session_store.save(self.session)
        self.sessions_changed.emit()

    def _clear_chat_view(self):
        while self.chat_layout.count() > 1:      # keep the trailing stretch
            item = self.chat_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._thinking_cards.clear()
        self._transcript.clear()
        self._added = self._removed = 0
        self._refresh_diff()

    def _render_session(self):
        """Show the current session's history (and nothing from others)."""
        self.session_title_label.setText(
            self.session.get("title", "New task")
        )
        project = self.session.get("project")
        if project:
            self.project_input.setText(project)
        self._clear_chat_view()
        self._replaying = True
        try:
            for msg in self.session.get("messages", []):
                role = msg.get("role")
                if role == "user":
                    self._append_bubble("You", msg.get("text", ""), mine=True)
                elif role == "assistant":
                    who = msg.get("who") or "assistant"
                    self._append_bubble(who, msg.get("text", ""), mine=False)
                elif role == "system":
                    self.append_system(msg.get("text", ""))
        finally:
            self._replaying = False
        self.set_ready(self._ready)
        self.sessions_changed.emit()

    def new_chat(self):
        """Fresh task -> fresh conversation; the old one stays in Chats."""
        self.session = self.session_store.create()
        if self.project_root:
            self.session["project"] = os.path.basename(self.project_root)
            self.session_store.save(self.session)
        self._render_session()
        self.append_system(
            "\u2795 New chat started \u2014 this conversation is its own task. "
            "History, project and context stay separate from every other chat."
        )

    def switch_chat(self, session_id: str):
        if session_id == self.session.get("id"):
            return
        session = self.session_store.load(session_id)
        if not session:
            self.append_system("\u26A0 That chat could not be opened.")
            return
        self.session = session
        self._render_session()
        self.append_system(
            f"\U0001F4C4 Opened \u2018{session.get('title', 'New task')}\u2019 "
            "\u2014 only this task's history is in context."
        )

    def _open_chats_menu(self):
        menu = QMenu(self)
        menu.addAction("＋ New chat", self.new_chat)
        sessions = self.session_store.list()
        if sessions:
            menu.addSeparator()
            for info in sessions[:40]:
                title = (info.get("title") or "New task")[:48]
                stamp = time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(info.get("updated") or 0)
                )
                marker = "  \u2713" if info["id"] == self.session.get("id") else ""
                act = menu.addAction(f"{title}   \u00b7 {stamp}{marker}")
                act.triggered.connect(
                    lambda _=False, sid=info["id"]: self.switch_chat(sid)
                )
            menu.addSeparator()
            menu.addAction("Rename current chat\u2026", self._rename_current_chat)
            menu.addAction("Delete current chat",
                           lambda: self._delete_current_chat())
        menu.exec(self.chats_button.mapToGlobal(
            self.chats_button.rect().bottomLeft()))

    def _rename_current_chat(self):
        title, ok = QInputDialog.getText(
            self, "Rename chat", "Task name:", QLineEdit.EchoMode.Normal,
            self.session.get("title", "New task"),
        )
        if not ok or not title.strip():
            return
        self.session["title"] = title.strip()
        self.session_store.save(self.session)
        self._render_session()

    def _delete_current_chat(self):
        current_id = self.session.get("id")
        self.session_store.delete(current_id)
        remaining = [s for s in self.session_store.list()
                     if s["id"] != current_id]
        if remaining:
            self.switch_chat(remaining[0]["id"])
        else:
            self.new_chat()

    def _insert(self, widget: QWidget):
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, widget)
        self._scroll_chat_bottom()

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
        body.setMaximumWidth(680)
        if mine:
            # your bubble hugs its text (measured generously: the stylesheet
            # font is applied after this point, so allow some slack)
            _fm = QFontMetrics(body.font())
            body.setMinimumWidth(min(680, int(_fm.boundingRect(
                0, 0, 680, 100000, Qt.TextFlag.TextWordWrap, text).width() * 1.18) + 8))
        self._selectable(name)
        self._selectable(body)
        if mine:
            name.hide()
        inner.addWidget(name)
        inner.addWidget(body)

        if mine:
            row.addStretch(1)
            row.addWidget(bubble)
        else:
            row.addWidget(bubble, 1)     # assistant text uses the full column
        # right-click copies just this message
        self._attach_copy_menu(bubble, lambda: f"{who}: {text}")
        self._transcript.append(f"{who}: {text}")
        self._insert(wrap)
        self._record("assistant" if not mine else "user", text, who=who)

    def append_system(self, text: str):
        lbl = QLabel(text)
        lbl.setObjectName("chatSystem")
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._selectable(lbl)
        self._attach_copy_menu(lbl, lambda: text, "line")
        self._transcript.append(f"-- {text}")
        self._insert(lbl)
        self._record("system", text)

    def append_diff_line(self, path: str, added: int, removed: int):
        """Per-file '+12 / -2' line, green added / red removed."""
        lbl = QLabel(
            f'<span style="color:#8b93a7">{html_escape(os.path.basename(path))}'
            f'</span>&nbsp;&nbsp;<span style="color:#3fb950; font-weight:700">'
            f'+{added}</span>&nbsp;<span style="color:#f85149; font-weight:700">'
            f'\u2212{removed}</span>'
        )
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setObjectName("diffLine")
        self._selectable(lbl)
        self._transcript.append(f"{path}: +{added} -{removed}")
        self._insert(lbl)

    def append_thinking(self, stage: str, title: str, detail: str):
        """One compact line per thinking step (planner plan, worker task,
        review verdict, re-prompt). Click the line to read the full content;
        the whole stream is hidden when the Think toggle is off."""
        row = QWidget()
        box = QVBoxLayout(row)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)

        header = QPushButton(f"\U0001F9E0 {stage} \u00B7 {title}   \u25B8")
        header.setObjectName("thinkLine")
        header.setCheckable(True)
        body = QLabel(detail or "(no detail)")
        body.setObjectName("thinkBody")
        body.setWordWrap(True)
        body.setVisible(False)
        self._selectable(body)
        header.toggled.connect(
            lambda on, b=body, h=header, t=title: (
                b.setVisible(on),
                h.setText(f"\U0001F9E0 {stage} \u00B7 {t}   "
                          + ("\u25BE" if on else "\u25B8")),
            )
        )
        box.addWidget(header)
        box.addWidget(body)

        self._attach_copy_menu(row, lambda: f"{stage}: {title}\n{detail}")
        self._transcript.append(f"[think] {stage}: {title}")
        self._thinking_cards.append(row)
        row.setVisible(self.think_button.isChecked())
        self._insert(row)

    def append_terminal(self, command: str, output: str, ok: bool):
        """Show a terminal command + its output (copyable, monospace)."""
        color = "#3fb950" if ok else "#f85149"
        mark = "\u2713" if ok else "\u2717"
        body = f"{mark} $ {command}\n{output or '(no output)'}"
        lbl = QLabel(body)
        lbl.setObjectName("terminalLine")
        lbl.setWordWrap(True)
        self._selectable(lbl)
        self._attach_copy_menu(lbl, lambda: body, "terminal output")
        self._transcript.append(body)
        self._insert(lbl)
        self.hud_dot.setStyleSheet(f"color: {color};")

    def _on_think_toggled(self, on: bool):
        for card in self._thinking_cards:
            card.setVisible(on)

    def _scroll_chat_bottom(self):
        bar = self.chat_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def append_user(self, text: str):
        self._append_bubble("You", text, mine=True)

    # ---------------- ready state / HUD ---------------- #

    def set_ready(self, ready: bool):
        """Chat only works once a projects folder has been chosen."""
        self._ready = ready
        self.input.setEnabled(ready)
        self.send_button.setEnabled(ready)
        if ready:
            self.input.setPlaceholderText(
                "Message the planner\u2026   $ command   /help"
            )
            self.set_idle("Ready")
        else:
            self.input.setPlaceholderText(
                "Choose a projects folder first (\U0001F4C2 Folder\u2026)"
            )
            self.set_idle(
                "No projects folder selected \u2014 click \U0001F4C2 Folder\u2026 "
                "to pick where projects are created or opened."
            )

    def set_activity(self, text: str):
        """HUD: what is happening right now (animated dots while active)."""
        self._hud_base = text
        self._hud_dots = 0
        self.hud_activity.setText(text)
        self.hud_dot.setStyleSheet("color: #D97757;")
        if text:
            self._hud_timer.start()
        else:
            self._hud_timer.stop()

    def set_idle(self, text: str = "Idle"):
        self._hud_timer.stop()
        self._hud_base = text
        self.hud_activity.setText(text)
        self.hud_dot.setStyleSheet("color: #3fb950;")

    def _tick_hud(self):
        self._hud_dots = (self._hud_dots + 1) % 4
        self.hud_activity.setText(self._hud_base + "." * self._hud_dots)

    def reset_run_hud(self):
        self._added = 0
        self._removed = 0
        self._refresh_diff()

    def add_diff(self, path: str, added: int, removed: int):
        self._added += added
        self._removed += removed
        self._refresh_diff()
        self.append_diff_line(path, added, removed)

    def _refresh_diff(self):
        self.hud_diff.setText(
            f'<span style="color:#3fb950">+{self._added}</span>'
            f'&nbsp;&nbsp;<span style="color:#f85149">\u2212{self._removed}</span>'
        )

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
        """Show which model answers here, then refresh the live catalog."""
        self._render_chip()
        self._start_model_fetch()

    def _spec_for(self, role: str) -> dict:
        return dict(getattr(self.config, role, {}) or {})

    def _render_chip(self):
        role = self.role_combo.currentData() or "planner"
        spec = self._spec_for(role)
        provider = spec.get("provider", "?")
        pinned = (spec.get("model") or "auto").strip() or "auto"
        shown = self._resolved or pinned
        self.model_chip.setText(f"{role.capitalize()} \u00b7 {provider} / {shown} \u25BE")

    def _start_model_fetch(self):
        """Ask the provider which models this key can reach (off the UI thread)
        so the chip can show the real model and the menu can list choices."""
        role = self.role_combo.currentData() or "planner"
        spec = self._spec_for(role)
        provider = spec.pop("provider", "")
        if provider not in PROVIDER_REGISTRY or provider == "mock":
            self._catalog, self._resolved = [], ""
            return
        if self._model_thread is not None:
            return                      # one catalog fetch at a time
        spec.pop("model", None)
        self._model_thread = QThread()
        self._model_worker = ModelFetchWorker(provider, spec)
        self._model_worker.moveToThread(self._model_thread)
        self._model_thread.started.connect(self._model_worker.run)
        self._model_worker.models_ready.connect(self._on_catalog)
        self._model_worker.models_ready.connect(self._model_thread.quit)
        self._model_worker.failed.connect(self._model_thread.quit)
        # Drop our references only once the thread has REALLY stopped --
        # clearing them from the result handler would hand a still-running
        # QThread to the garbage collector ("Destroyed while running").
        self._model_thread.finished.connect(self._finish_model_fetch)
        self._model_thread.start()

    def _finish_model_fetch(self):
        self._model_thread = None
        self._model_worker = None

    def wait_for_model_fetch(self, timeout_ms: int = 6000):
        """Let an in-flight catalog fetch finish (used on app close)."""
        thread = self._model_thread
        if thread is None:
            return
        thread.quit()
        thread.wait(timeout_ms)

    def _on_catalog(self, models):
        role = self.role_combo.currentData() or "planner"
        spec = self._spec_for(role)
        cls = PROVIDER_REGISTRY.get(spec.get("provider", ""))
        self._catalog = [m for m in (models or []) if m]
        pinned = (spec.get("model") or "auto").strip()
        preferred = pinned if pinned and pinned != "auto" else None
        try:
            self._resolved = cls.preferred_chat_model(self._catalog, preferred) if cls else ""
        except Exception:
            self._resolved = ""
        self._render_chip()

    def _open_model_menu(self):
        """Click the underlined model link -> switch role (the combo left the
        header) or pick Auto/any model this key can actually call."""
        role = self.role_combo.currentData() or "planner"
        spec = self._spec_for(role)
        provider = spec.get("provider", "?")
        current = (spec.get("model") or "auto").strip() or "auto"
        menu = QMenu(self)
        header = menu.addAction(f"{role.capitalize()} \u00b7 {provider}")
        header.setEnabled(False)
        if self._resolved:
            live = menu.addAction(f"answering now: {self._resolved}")
            live.setEnabled(False)
        # role switch -- the Planner/Worker combo is not in the header any more
        menu.addSeparator()
        for label, value in (("\U0001F9E0 Answer as Planner", "planner"),
                             ("\U0001F6E0\uFE0F Answer as Worker", "worker")):
            mark = "   \u2713" if role == value else ""
            act = menu.addAction(label + mark)
            act.triggered.connect(lambda _=False, v=value: self._set_role(v))
        menu.addSeparator()
        auto = menu.addAction("Auto \u2014 let the key/provider decide"
                              + ("   \u2713" if current == "auto" else ""))
        auto.triggered.connect(lambda: self._set_role_model("auto"))
        if self._catalog:
            menu.addSeparator()
            for name in self._catalog[:30]:
                act = menu.addAction(name + ("   \u2713" if name == current else ""))
                act.triggered.connect(lambda _=False, n=name: self._set_role_model(n))
        else:
            wait = menu.addAction("Detecting models\u2026")
            wait.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Refresh list", self._start_model_fetch)
        menu.exec(self.model_chip.mapToGlobal(self.model_chip.rect().bottomLeft()))

    def role_combo_items(self) -> list:
        return [self.role_combo.itemData(i) for i in range(self.role_combo.count())]

    def _set_role(self, role: str):
        idx = self.role_combo.findData(role)
        if idx >= 0 and idx != self.role_combo.currentIndex():
            self.role_combo.setCurrentIndex(idx)
        else:
            self._refresh_role_chip()
        self._render_chip()

    def _set_role_model(self, model: str):
        """Change the role's model from the chat; persisted, so Settings and
        the next run both use it."""
        role = self.role_combo.currentData() or "planner"
        spec = self._spec_for(role)
        spec["model"] = model
        setattr(self.config, role, spec)
        try:
            self.config.save()
        except Exception as e:
            self.append_system(f"\u26A0 Could not save the model choice: {e}")
        self._resolved = ""
        note = (" (auto: picked from your key when a request is made)"
                if model == "auto" else "")
        self.append_system(f"\u2699 {role.capitalize()} model \u2192 {model}{note}")
        self._render_chip()
        self._start_model_fetch()

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
        if not self._ready:
            self.append_system(
                "\U0001F4C2 Choose a projects folder first (Folder\u2026) \u2014 "
                "then chat and commands work inside it."
            )
            return
        text = self.input.text().strip()
        if not text or self._busy:
            return

        # ---- commands are typed straight in: $cmd / !cmd run in a terminal ----
        if text.startswith(("$", "!")):
            command = text[1:].strip()
            if not command:
                self.append_system(
                    "Type a command after $ \u2014 for example:  $ pytest -q"
                )
                return
            tags = self.tagged_paths()
            self.input.clear()
            self.append_user(text)
            self.set_activity("Running command")
            self.command_requested.emit(command, tags)
            return
        if text.split()[0].lower() in ("/help", "/commands", "help"):
            self.input.clear()
            self._show_command_help()
            return

        # ---- normal chat: attach any @tagged files to the prompt ----
        tags = self.tagged_paths()
        self.last_tagged = list(tags)
        self.input.clear()
        role = self.role_combo.currentData() or "planner"
        spec = dict(getattr(self.config, role, {}) or {})
        self.append_user(text)
        # Title the chat from the first message (ChatGPT-style), and keep it
        # tied to this task's project folder.
        if self.session.get("title") in ("", "New task"):
            self.session["title"] = text[:60]
            self.session_store.save(self.session)
            self.session_title_label.setText(
                self.session["title"]
            )
            self.sessions_changed.emit()
        if not self.session.get("project") and self.project_input.text().strip():
            self.session["project"] = self.project_input.text().strip()
            self.session_store.save(self.session)
        if tags:
            shown = ", ".join(os.path.basename(p) for p in tags[:6])
            self.append_system(f"\U0001F4CE Attached to the message: {shown}")
        # Task-scoped context: this chat's title/project/own history only, so
        # the model can identify and manage THIS task and never mixes chats.
        context = self.session_store.context_block(self.session)
        prompt = context + "\n\nUser message: " + text
        if tags:
            prompt += "\n\n" + self._attachment_block(tags)
        self.append_system(f"\u2192 {role} is thinking\u2026")
        self.set_activity(f"{role.capitalize()} is thinking")
        warning = self._planner_model_warning()
        if warning and not self._warned_about_model:
            self._warned_about_model = True
            self.append_system(f"\u2139 {warning}")

        self._busy = True
        self.send_button.setEnabled(False)
        self._thread = QThread()
        self._worker = ChatWorker(role, spec, prompt)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.reply.connect(self._on_reply)
        self._worker.failed.connect(self._on_chat_failed)
        self._worker.reply.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.start()

    def _on_reply(self, text: str, model: str):
        self._busy = False
        self.send_button.setEnabled(self._ready)
        role = self.role_combo.currentData() or "planner"
        self.set_idle("Ready")
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
            # The dispatched goal IS this chat's task -- title it accordingly
            # (only if the user hasn't renamed the chat by hand).
            if self.session.get("title") in ("", "New task"):
                self.session["title"] = goal[:60]
                self.session_store.save(self.session)
                self.session_title_label.setText(
                    self.session["title"]
                )
            self.run_requested.emit(goal)
        elif had_code:
            self.append_system(
            "Code stays out of the chat \u2014 the workers write the files "
            "during a build. Ask the planner to build it and it dispatches "
            "the workers itself."
        )

    def _on_chat_failed(self, error: str):
        self._busy = False
        self.send_button.setEnabled(self._ready)
        self.set_idle("Chat error")
        self.append_system(f"\u26A0 Chat error (right-click to copy): {error}")


# --------------------------------------------------------------------- #
# Code center: VS Code-like editor -- tabs, line numbers, colors.
# --------------------------------------------------------------------- #

class LineNumberArea(QWidget):
    """Paints the gutter with line numbers 1..N, synced to the editor."""

    def __init__(self, editor):
        super().__init__(editor)
        self._editor = editor

    def paintEvent(self, event):
        self._editor.line_number_paint_event(event)


class PythonHighlighter(QSyntaxHighlighter):
    """Lightweight Python syntax coloring for the code center."""

    _KEYWORDS = {
        "def", "class", "return", "import", "from", "if", "elif", "else",
        "for", "while", "try", "except", "finally", "with", "as", "raise",
        "pass", "break", "continue", "lambda", "yield", "assert", "del",
        "global", "nonlocal", "in", "is", "not", "and", "or", "True",
        "False", "None", "async", "await",
    }
    _BUILTINS = {
        "print", "len", "range", "open", "str", "int", "float", "list",
        "dict", "set", "tuple", "super", "self", "isinstance", "type",
        "repr", "enumerate", "zip", "map", "filter", "sorted", "sum",
        "min", "max", "abs", "Exception",
    }

    def _fmt(self, color: str, bold: bool = False) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        if bold:
            fmt.setFontWeight(QFont.Weight.Bold)
        return fmt

    def highlightBlock(self, text: str):
        keyword = self._fmt("#D97757")
        builtin = self._fmt("#8AB4D8")
        string = self._fmt("#A8C686")
        comment = self._fmt("#7A786F")
        number = self._fmt("#E0B070")
        decorator = self._fmt("#D97757")

        in_string = self.previousBlockState() == 1
        span_start = 0 if in_string else None

        tokens = re.findall(r'("[^"]*"|\'[^\']*\'|#[^\n]*|\w+|\d[\w.]*|\S)', text)
        pos = 0
        for tok in tokens:
            start = text.find(tok, pos)
            if start < 0:
                continue
            end = start + len(tok)
            pos = end
            if tok.startswith("#"):
                self.setFormat(start, end - start, comment)
                break
            if (tok.startswith('"') or tok.startswith("'")) and len(tok) >= 2:
                self.setFormat(start, end - start, string)
                continue
            if tok.startswith("@"):
                self.setFormat(start, end - start, decorator)
                continue
            if tok in self._KEYWORDS:
                self.setFormat(start, end - start, keyword)
            elif tok in self._BUILTINS:
                self.setFormat(start, end - start, builtin)
            elif tok[:1].isdigit():
                self.setFormat(start, end - start, number)

        if in_string and span_start is not None:
            self.setCurrentBlockState(0)
        else:
            self.setCurrentBlockState(0)


class CodeEditor(QPlainTextEdit):
    """Read-only code page with a live 1..N line-number gutter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("codeView")
        self.setReadOnly(True)
        self.setMinimumWidth(0)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self._line_area = LineNumberArea(self)
        self.blockCountChanged.connect(self.update_line_area_width)
        self.updateRequest.connect(self.update_line_area)
        self.update_line_area_width(0)
        # keep a reference so the highlighter is not garbage-collected
        self._highlighter = PythonHighlighter(self.document())

    def line_number_area_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 14 + QFontMetrics(self.font()).horizontalAdvance("9") * digits

    def update_line_area_width(self, _):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def update_line_area(self, rect, dy):
        if dy:
            self._line_area.scroll(0, dy)
        else:
            self._line_area.update(
                0, rect.y(), self._line_area.width(), rect.height()
            )
        if rect.contains(self.viewport().rect()):
            self.update_line_area_width(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        rect = self.contentsRect()
        self._line_area.setGeometry(rect.left(), rect.top(),
                                    self.line_number_area_width(),
                                    rect.height())

    def line_number_paint_event(self, event):
        painter = QPainter(self._line_area)
        painter.fillRect(event.rect(), QColor("#1F1E1D"))
        painter.setPen(QColor("#6F6D66"))
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block)
                    .translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(0, top, self._line_area.width() - 8,
                                 painter.fontMetrics().height(),
                                 Qt.AlignmentFlag.AlignRight,
                                 str(block_number + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_number += 1


# --------------------------------------------------------------------- #
# Coding center: VS Code-like editor column -- tabs in the middle.
#
# Lives in the MIDDLE column of Home. Tabs open when you click a file on
# the left, and they also open by themselves whenever a worker model
# writes a file -- that is how you watch the code appear live.
# --------------------------------------------------------------------- #

class WorkspaceTabs(QWidget):
    """The coding center: one tab per open file, each with line numbers
    1..N in the gutter and Python colors. Tab titles shrink to fit so the
    chat on the right is never pushed around. When the last tab closes the
    whole column hides and the chat takes the space back."""

    tabs_changed = pyqtSignal(int)   # number of open tabs (0 -> hide column)
    file_saved = pyqtSignal(str)     # path written by Ctrl+S

    def __init__(self, project_root: str, parent=None):
        super().__init__(parent)
        self.project_root = os.path.abspath(project_root)
        self.project_root_cfg = project_root
        self._open: dict[str, CodeEditor] = {}
        self._zoom = 1.0
        self._dirty: set[str] = set()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.setMinimumWidth(340)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("\U0001F4BB  Code")
        title.setObjectName("panelTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.project_label = QLabel("")
        self.project_label.setObjectName("hintLabel")
        self.project_label.setMaximumWidth(260)
        head.addWidget(self.project_label)
        layout.addLayout(head)

        # kept for status text; the open-file count lives in the left
        # column's "Open files" card so nothing crowds the chat header
        self.status_label = QLabel("no file open")
        self.status_label.setVisible(False)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("codeTabs")
        self.tabs.setTabsClosable(False)
        self.tabs.tabBar().setObjectName("codeTabBar")
        self.tabs.tabBar().setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs.tabBar().setUsesScrollButtons(True)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.setDocumentMode(False)
        self.tabs.setMovable(False)
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)
        layout.addWidget(self.tabs, stretch=1)

        # shown only when no tab is open -- the column never looks broken
        self.empty_label = QLabel("No file open yet \u2014 click a file on the left,\n"
                                  "or press \u25B6 Run \u00B7 build and watch the\n"
                                  "workers' code open here by itself.")
        self.empty_label.setObjectName("hintLabel")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        layout.addWidget(self.empty_label, stretch=1)
        self._sync_empty()

    # ---------------- project root ---------------- #

    def set_project_root(self, path: str):
        self.project_root = os.path.abspath(path)
        self.project_label.setText(f"project: {os.path.basename(self.project_root)}")
        self.project_label.setToolTip(self.project_root)
        self.close_all()

    def close_all(self):
        self.tabs.clear()
        self._open = {}
        self.status_label.setText("no file open")
        self._sync_empty()

    def _sync_empty(self):
        empty = self.tabs.count() == 0
        self.empty_label.setVisible(empty)
        self.tabs.setVisible(not empty)
        self.tabs_changed.emit(self.tabs.count())

    def open_paths(self) -> list[str]:
        """Project-relative paths of the open tabs, in tab order."""
        out = []
        for i in range(self.tabs.count()):
            full = self.tabs.tabBar().tabData(i)
            if not full:
                continue
            try:
                out.append(os.path.relpath(full, self.project_root))
            except ValueError:
                out.append(os.path.basename(full))
        return out

    # ---------------- opening files ---------------- #

    def _short_title(self, rel: str) -> str:
        shown = rel.replace("\\", "/")
        if len(shown) > 34:
            return "\u2026" + shown[-33:]
        return shown

    def _load_text(self, full_path: str) -> str:
        if os.path.getsize(full_path) > 1_000_000:
            return "(file larger than 1 MB -- not shown)"
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError as e:
            return f"(could not read file: {e})"

    def open_file(self, full_path: str):
        """Open a file tab (or jump to it / refresh it if already open)."""
        full_path = os.path.abspath(full_path)
        if full_path in self._open:
            self.refresh_file(full_path)
            self.tabs.setCurrentWidget(self._open[full_path])
            return

        editor = CodeEditor(self)
        editor.setPlainText(self._load_text(full_path))
        editor.setReadOnly(False)          # editable: fix things by hand
        editor.document().modificationChanged.connect(
            lambda changed, p=full_path: self._on_modified(p, changed)
        )
        try:
            rel = os.path.relpath(full_path, self.project_root)
        except ValueError:
            rel = os.path.basename(full_path)

        tab_index = self.tabs.addTab(editor, self._short_title(rel))
        self.tabs.tabBar().setTabData(tab_index, full_path)
        # little x close button inside the tab title
        close = QPushButton("\u00D7")
        close.setObjectName("tabClose")
        close.setFixedSize(20, 20)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip(f"Close {rel}")
        close.clicked.connect(lambda _=False, p=full_path: self.close_file(p))
        self.tabs.tabBar().setTabButton(tab_index, QTabBar.ButtonPosition.RightSide, close)
        self.tabs.setTabToolTip(tab_index, rel)

        self._open[full_path] = editor
        self.tabs.setCurrentWidget(editor)
        self.status_label.setText(f"{len(self._open)} file(s) open")
        self._sync_empty()

    def refresh_file(self, full_path: str):
        """Reload the text of an already-open tab (live run watching).
        Never clobbers edits the user has not saved yet."""
        full_path = os.path.abspath(full_path)
        editor = self._open.get(full_path)
        if editor is None or full_path in self._dirty:
            return
        text = editor.toPlainText()
        fresh = self._load_text(full_path)
        if fresh != text:
            editor.setPlainText(fresh)

    # ---------------- editing / saving ---------------- #

    def _on_modified(self, full_path: str, changed: bool):
        if changed:
            self._dirty.add(full_path)
        else:
            self._dirty.discard(full_path)
        self._mark_dirty_tab(full_path)

    def _mark_dirty_tab(self, full_path: str):
        editor = self._open.get(full_path)
        index = self.tabs.indexOf(editor) if editor else -1
        if index < 0:
            return
        base = self.tabs.tabText(index).lstrip("\u25CF ").strip()
        dirty = full_path in self._dirty
        try:
            rel = os.path.relpath(full_path, self.project_root)
        except ValueError:
            rel = base
        self.tabs.setTabText(index, ("\u25CF " if dirty else "") + self._short_title(rel))

    def save_current(self) -> bool:
        """Ctrl+S -- write the current tab back to disk."""
        editor = self.tabs.currentWidget()
        if not isinstance(editor, CodeEditor):
            return False
        return self.save_file(self._path_of(editor))

    def _path_of(self, editor) -> str:
        for path, ed in self._open.items():
            if ed is editor:
                return path
        return ""

    def save_file(self, full_path: str) -> bool:
        editor = self._open.get(os.path.abspath(full_path))
        if editor is None:
            return False
        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(editor.toPlainText())
        except OSError as e:
            self.status_label.setText(f"save failed: {e}")
            return False
        self._dirty.discard(os.path.abspath(full_path))
        editor.document().setModified(False)
        self._mark_dirty_tab(os.path.abspath(full_path))
        self.status_label.setText(f"saved {os.path.basename(full_path)}")
        self.file_saved.emit(full_path)
        return True

    def save_all(self) -> int:
        return sum(1 for p in list(self._dirty) if self.save_file(p))

    # ---------------- zoom ---------------- #

    def apply_zoom(self, factor: float):
        """Ctrl +/- zooms the code text in every open tab."""
        self._zoom = max(0.6, min(2.2, factor))
        size = 12.5 * self._zoom
        for editor in self._open.values():
            font = editor.font()
            font.setPointSizeF(max(7.5, size - 1.5))
            editor.setFont(font)

    def close_file(self, full_path: str):
        full_path = os.path.abspath(full_path)
        editor = self._open.pop(full_path, None)
        if editor is None:
            return
        index = self.tabs.indexOf(editor)
        if index >= 0:
            self.tabs.removeTab(index)
        editor.deleteLater()
        self.status_label.setText(
            f"{len(self._open)} file(s) open" if self._open else "no file open")
        self._sync_empty()
        # tell the left column to refresh its "Open files" list
        left = self.parent()
        updater = getattr(self, "opened", None)
        if callable(updater):
            updater(self.open_paths())
        elif left is not None and hasattr(left, "update_open_list"):
            left.update_open_list(self.open_paths())

    def auto_open(self, relative_path: str, root_path: str | None = None):
        """A worker wrote this file -- open it so the user sees it. Only
        files inside the current project root are opened, so chat never
        moves for something out of scope."""
        anchor = root_path or self.project_root
        anchor = os.path.abspath(anchor)
        if os.path.normcase(anchor) != os.path.normcase(self.project_root):
            return  # out-of-scope write: never touches the coding center
        full = os.path.normpath(os.path.join(root_path or self.project_root, relative_path))
        if os.path.isdir(full) or not os.path.exists(full):
            return
        self.open_file(full)


# --------------------------------------------------------------------- #
# Folder section: left column (files top, open-files list bottom)
# --------------------------------------------------------------------- #

class FilesPanel(QWidget):
    """Left column: files on top, the open coding-center tabs in the middle.

    Clicking a file opens it in the coding center (tabs live there, so the
    chat on the right is never squeezed). The panel also auto-opens files
    the worker models are writing -- that is how the user sees "it open"
    without clicking.
    """

    def __init__(self, root_path: str, parent=None):
        super().__init__(parent)
        self.root_path = root_path
        self.workspace_tabs = None  # wired by the Dashboard

        self.fs_model = QFileSystemModel(self)
        self.fs_model.setRootPath(root_path)

        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._track_change)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.setMinimumWidth(250)

        # ---- upper card: the folder section ----
        files_card = QFrame()
        files_card.setObjectName("panelCard")
        files_layout = QVBoxLayout(files_card)
        files_layout.setContentsMargins(12, 10, 12, 12)
        files_layout.setSpacing(8)

        files_head = QHBoxLayout()
        files_title = QLabel("\U0001F4C1  Files")
        files_title.setObjectName("panelTitle")
        files_head.addWidget(files_title)
        files_head.addStretch(1)
        self.changed_label = QLabel("")
        self.changed_label.setObjectName("hintLabel")
        self.changed_label.setTextFormat(Qt.TextFormat.RichText)
        files_head.addWidget(self.changed_label)
        files_layout.addLayout(files_head)

        self.tree = QTreeView()
        self.tree.setModel(self.fs_model)
        self.tree.setRootIndex(self.fs_model.index(root_path))
        self.tree.setAnimated(True)
        self.tree.setIndentation(16)
        self.tree.setHeaderHidden(True)
        for col in range(1, 4):
            self.tree.setColumnHidden(col, True)
        self.tree.setMinimumWidth(200)
        self.tree.clicked.connect(self._open_file)
        files_layout.addWidget(self.tree, stretch=1)

        # ---- open files live INSIDE the same card ----
        # They used to be a second card of their own, which left a big empty
        # box under the tree most of the time. Now the section only appears
        # when something is actually open, so the column never has dead space.
        self.open_section = QWidget()
        open_layout = QVBoxLayout(self.open_section)
        open_layout.setContentsMargins(0, 8, 0, 0)
        open_layout.setSpacing(6)

        sep = QFrame()
        sep.setObjectName("cardSeparator")
        sep.setFrameShape(QFrame.Shape.HLine)
        open_layout.addWidget(sep)

        open_title = QLabel("\U0001F4C4  Open files")
        open_title.setObjectName("panelTitle")
        self.open_title = open_title
        open_layout.addWidget(open_title)

        self.open_files_label = QLabel("")
        self.open_files_label.setObjectName("hintLabel")
        self.open_files_label.setWordWrap(True)
        open_layout.addWidget(self.open_files_label)

        self.open_section.setVisible(False)
        files_layout.addWidget(self.open_section)
        layout.addWidget(files_card, stretch=1)

    # ---------------- helpers ---------------- #

    def _elide(self, text: str, width: int = 230) -> str:
        return QFontMetrics(self.path_chip.font()).elidedText(
            text, Qt.TextElideMode.ElideMiddle, width
        )

    def set_root(self, path: str):
        """Point the tree and the coding center at a new project root."""
        self.root_path = path
        self.fs_model.setRootPath(path)
        self.tree.setRootIndex(self.fs_model.index(path))
        if self.workspace_tabs is not None:
            self.workspace_tabs.set_project_root(path)

    def mark_changed(self, added: int, removed: int):
        """Show '+12 / -2' for the most recent write, next to the title."""
        self.changed_label.setText(
            f'<span style="color:#3fb950">+{added}</span> '
            f'<span style="color:#f85149">\u2212{removed}</span>'
        )

    def update_open_list(self, paths: list[str]):
        """List which files are currently open in the coding center.

        The section is hidden when nothing is open, so an empty left column
        is just the folder tree -- no dead card eating half the screen.
        """
        self.open_section.setVisible(bool(paths))
        self.open_title.setText(f"\U0001F4C4  Open files ({len(paths)})")
        self.open_files_label.setText("\n".join(f"\u2022 {p}" for p in paths[:8]))

    def _open_file(self, index):
        if not index.isValid() or self.fs_model.isDir(index):
            return
        path = self.fs_model.filePath(index)
        if self.workspace_tabs is not None:
            self.workspace_tabs.open_file(path)
            self.update_open_list(self.workspace_tabs.open_paths())
        self._watch_if_new(path)

    def _watch_if_new(self, path: str):
        """Follow a path while the run is writing it so open tabs refresh."""
        if os.path.exists(path) and path not in self._watcher.files():
            self._watcher.addPath(path)

    def _track_change(self, path: str):
        """A watched file changed on disk -> refresh its tab if open, so the
        user watches the code change live while workers are writing."""
        if self.workspace_tabs is not None:
            self.workspace_tabs.refresh_file(path)


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
        # Terminal access is always on: these two used to be checkboxes, but
        # the agents are meant to run commands and test their own work, so
        # there is nothing to switch on or off.
        self.config.allow_pc_access = True
        self.config.confirm_commands = False

        self.test_command_edit = QLineEdit(getattr(config, "test_command", "") or "")
        self.test_command_edit.setPlaceholderText(
            'e.g. python -m pytest -q  (empty = skip)'
        )
        self.test_command_edit.setToolTip(
            "After every run the platform executes this command in a terminal "
            "inside the generated project, so you see whether it actually works."
        )
        general_form.addRow("Test command", self.test_command_edit)
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
            "pc_access": True,
            "confirm_commands": False,
            "test_command": self.test_command_edit.text().strip(),
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
        # always on: agents may test their own work anywhere on this PC
        self.config.allow_pc_access = True
        self.config.confirm_commands = False
        self.config.test_command = self.test_command_edit.text().strip()
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

class WelcomeDialog(QDialog):
    """First-run gate: choose the folder where projects live.

    Nothing can be built or shown before a folder exists, so the chat stays
    disabled until this is answered (it can be changed later with the
    \U0001F4C2 Folder\u2026 button in the chat or in Settings).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to AquilaOS")
        self.setMinimumWidth(580)
        self.chosen_path = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(26, 24, 26, 20)
        outer.setSpacing(14)

        title = QLabel("Welcome to AquilaOS")
        title.setObjectName("headerTitle")
        outer.addWidget(title)

        intro = QLabel(
            "First, choose the folder where your projects live.\n\n"
            "The agents create and edit files inside it, the Files panel "
            "shows them, and the chat works on them. You can change it any "
            "time with \U0001F4C2 Folder\u2026 in the chat or in Settings."
        )
        intro.setWordWrap(True)
        intro.setObjectName("hintLabel")
        intro.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(intro)

        row = QHBoxLayout()
        self.path_label = QLabel("No folder chosen yet")
        self.path_label.setObjectName("pathChip")
        row.addWidget(self.path_label, stretch=1)
        pick = QPushButton("\U0001F4C2  Choose projects folder\u2026")
        pick.setObjectName("primaryButton")
        pick.clicked.connect(self._pick)
        row.addWidget(pick)
        outer.addLayout(row)

        row2 = QHBoxLayout()
        use_default = QPushButton("Use default (workspace/)")
        use_default.clicked.connect(self._use_default)
        row2.addWidget(use_default)
        row2.addStretch(1)
        self.continue_button = QPushButton("Continue")
        self.continue_button.setObjectName("primaryButton")
        self.continue_button.setEnabled(False)
        self.continue_button.clicked.connect(self.accept)
        row2.addWidget(self.continue_button)
        outer.addLayout(row2)

    def _pick(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose the folder where projects live",
            os.path.expanduser("~"),
        )
        if chosen:
            self.chosen_path = chosen
            self.path_label.setText(chosen)
            self.path_label.setToolTip(chosen)
            self.continue_button.setEnabled(True)

    def _use_default(self):
        self.chosen_path = os.path.abspath("workspace")
        self.path_label.setText(self.chosen_path + "   (default)")
        self.continue_button.setEnabled(True)


class Dashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AquilaOS - Autonomous Agent Platform")
        self.ui_state = load_ui_state()
        self.ui_zoom = float(self.ui_state.get("zoom", 1.0) or 1.0)
        self.config = AgentOSConfig.load()
        self.thread = None
        self.worker = None
        self.action_rows = {}
        self._terminal_tools = None

        self._build_ui()
        self._apply_theme()
        self._restore_window()
        self._install_shortcuts()
        self.chat_panel.apply_zoom(self.ui_zoom)
        self.home_tabs.apply_zoom(self.ui_zoom)
        # First run: ask for the projects folder before chat becomes usable.
        QTimer.singleShot(0, self._first_run_check)

    # ---------------- layout that adapts to every screen ---------------- #

    def _restore_window(self):
        """Restore the window to last session's size, clamped to the screen
        that is actually in use (monitors differ; splitting a screen must
        never push the chat out of view)."""
        screen = self.screen() or QApplication.primaryScreen()
        area = screen.availableGeometry() if screen else None
        geo = self.ui_state.get("geometry") or []
        if isinstance(geo, list) and len(geo) == 4:
            x, y, w, h = (int(v) for v in geo)
            if area:
                w = min(w, area.width())
                h = min(h, area.height())
                x = min(max(x, area.left()), area.right() - w)
                y = min(max(y, area.top()), area.bottom() - h)
            self.setGeometry(x, y, max(w, 900), max(h, 600))
        else:
            if area:
                self.resize(min(1400, area.width() - 40),
                            min(900, area.height() - 60))
            else:
                self.resize(1300, 850)

    def _save_ui_state(self):
        geo = self.geometry()
        state = {
            "geometry": [geo.x(), geo.y(), geo.width(), geo.height()],
            "splitter_v3": self.home_split.sizes() if hasattr(self, "home_split") else [],
            "zoom": getattr(self, "ui_zoom", 1.0),
        }
        save_ui_state(state)

    def closeEvent(self, event):
        self._save_ui_state()
        try:
            self.chat_panel.wait_for_model_fetch()
        except Exception:
            pass
        super().closeEvent(event)

    def _install_shortcuts(self):
        """Ctrl +/- zoom the chat and code, Ctrl+0 resets, Ctrl+S saves the
        current code tab. Works the same on every screen size."""
        QShortcut(QKeySequence("Ctrl+="), self, lambda: self._set_zoom(0.1))
        QShortcut(QKeySequence("Ctrl++"), self, lambda: self._set_zoom(0.1))
        QShortcut(QKeySequence("Ctrl+-"), self, lambda: self._set_zoom(-0.1))
        QShortcut(QKeySequence("Ctrl+0"), self, self._reset_zoom)
        QShortcut(QKeySequence("Ctrl+S"), self, self._save_current_file)

    def _save_current_file(self):
        if self.home_tabs.save_current():
            self.status_label.setText("\u25CF saved")
        else:
            self.status_label.setText("\u25CF nothing to save")

    # ---------------- UI construction ---------------- #

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._header_bar())      # the sidebar

        main = QWidget()
        col = QVBoxLayout(main)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        # Sections; Home is the default one. (Files + code live inside Home.)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._home_page())    # 0 -- Home (default)
        self.stack.addWidget(self._tasks_page())   # 1 -- Tasks
        self.stack.currentChanged.connect(self._sync_nav)
        col.addWidget(self.stack, stretch=1)

        self.status_label = QLabel("\u25CF Idle")
        self.status_label.setObjectName("statusBar")
        self.status_label.setStyleSheet("color: #9C9A92;")
        col.addWidget(self.status_label)
        root.addWidget(main, stretch=1)

        self.chat_panel.sessions_changed.connect(self._refresh_recents)
        self._refresh_recents()

    def _header_bar(self):
        """Left sidebar (Claude Code desktop style): brand, New chat, section
        nav, Recents (all saved chats), Settings pinned to the bottom."""
        bar = QWidget()
        bar.setObjectName("sidebar")
        bar.setFixedWidth(264)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setSpacing(4)

        logo = QFrame()
        logo.setObjectName("logoMark")
        logo.setFixedSize(30, 30)
        logo_layout = QVBoxLayout(logo)
        logo_layout.setContentsMargins(0, 0, 0, 0)
        logo_letter = QLabel("A")
        logo_letter.setObjectName("logoLetter")
        logo_letter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_layout.addWidget(logo_letter)
        title = QLabel("AgentOS")
        title.setObjectName("headerTitle")
        subtitle = QLabel("Autonomous AI Agent Platform")
        subtitle.setObjectName("headerSubtitle")
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        brand.setContentsMargins(4, 0, 0, 0)
        brand.addWidget(logo)
        brand.addLayout(title_box)
        brand.addStretch(1)
        layout.addLayout(brand)
        layout.addSpacing(12)

        new_btn = QPushButton("\uFF0B  New chat")
        new_btn.setObjectName("newChatButton")
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.clicked.connect(self._sidebar_new_chat)
        layout.addWidget(new_btn)
        layout.addSpacing(6)

        self.nav_group = QButtonGroup(self)
        self.nav_buttons = {}
        for idx, label in enumerate(("Home", "Tasks")):
            btn = QPushButton(label)
            btn.setObjectName("navButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.nav_group.addButton(btn, idx)
            self.nav_buttons[label] = btn
            layout.addWidget(btn)
        self.nav_group.buttonClicked.connect(self._on_nav)
        self.nav_buttons["Home"].setChecked(True)

        layout.addSpacing(10)
        recents_label = QLabel("Recents")
        recents_label.setObjectName("sidebarSection")
        layout.addWidget(recents_label)

        self.recents = QListWidget()
        self.recents.setObjectName("recentsList")
        self.recents.setFrameShape(QFrame.Shape.NoFrame)
        self.recents.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recents.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.recents.itemClicked.connect(self._sidebar_open_chat)
        self.recents.customContextMenuRequested.connect(self._recents_menu)
        layout.addWidget(self.recents, stretch=1)

        settings_btn = QPushButton("\u2699  Settings")
        settings_btn.setObjectName("navButton")
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.clicked.connect(self._on_settings_clicked)
        layout.addWidget(settings_btn)
        return bar

    # ---------------- sidebar: recents ---------------- #

    def _refresh_recents(self):
        if not hasattr(self, "recents") or not hasattr(self, "chat_panel"):
            return
        current = self.chat_panel.session.get("id")
        self.recents.blockSignals(True)
        self.recents.clear()
        for info in self.chat_panel.session_store.list()[:60]:
            item = QListWidgetItem((info.get("title") or "New task")[:44])
            item.setData(Qt.ItemDataRole.UserRole, info["id"])
            item.setToolTip(time.strftime("%Y-%m-%d %H:%M",
                                          time.localtime(info.get("updated") or 0)))
            self.recents.addItem(item)
            if info["id"] == current:
                self.recents.setCurrentItem(item)
        self.recents.blockSignals(False)

    def _sidebar_new_chat(self):
        self.stack.setCurrentIndex(0)
        self.chat_panel.new_chat()

    def _sidebar_open_chat(self, item):
        self.stack.setCurrentIndex(0)
        self.chat_panel.switch_chat(item.data(Qt.ItemDataRole.UserRole))

    def _recents_menu(self, pos):
        item = self.recents.itemAt(pos)
        if item is None:
            return
        self.chat_panel.switch_chat(item.data(Qt.ItemDataRole.UserRole))
        menu = QMenu(self)
        menu.addAction("Rename\u2026", self.chat_panel._rename_current_chat)
        menu.addAction("Delete", self.chat_panel._delete_current_chat)
        menu.exec(self.recents.viewport().mapToGlobal(pos))

    def _on_nav(self, btn):
        """Header nav: switch the main stack to the chosen section."""
        self.stack.setCurrentIndex(max(self.nav_group.id(btn), 0))

    def _sync_nav(self, idx: int):
        """Keep the nav pill in sync whenever the section changes."""
        labels = ("Home", "Tasks")
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
        """Three columns, VS Code-style: files left, code center, chat right.

        The columns have bounded widths so a long tab or path in the middle
        can never shove the chat aside.
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 14, 14, 14)
        layout.setSpacing(0)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setObjectName("mainSplitter")
        split.setChildrenCollapsible(True)
        self.home_split = split
        self.home_files = FilesPanel(self._workspace_path())
        self.home_tabs = WorkspaceTabs(self._workspace_path())
        self.chat_panel = ChatPanel(self.config)
        self.home_files.workspace_tabs = self.home_tabs
        self.home_tabs.opened = self.home_files.update_open_list
        self.chat_panel.workspace_tabs = self.home_tabs
        self.chat_panel.project_root = self._workspace_path()
        self.home_tabs.tabs_changed.connect(lambda _n: self._sync_code_pane())
        self.home_tabs.file_saved.connect(
            lambda p: self.chat_panel.append_system(f"\U0001F4BE Saved {os.path.basename(p)}")
        )
        self.chat_panel.run_requested.connect(self._on_run_requested)
        self.chat_panel.choose_folder_requested.connect(self._choose_projects_folder)
        self.chat_panel.command_requested.connect(self._on_command_requested)
        # Order matters and is part of the contract: files on the LEFT, the
        # coding center in the MIDDLE, chat on the RIGHT.
        split.addWidget(self.home_files)
        split.addWidget(self.home_tabs)
        split.addWidget(self.chat_panel)
        self.chat_panel.files_toggled.connect(self.home_files.setVisible)
        # The files column keeps its width (stretch 0) and the code column is
        # the one that grows/shrinks -- it disappears entirely when no file is
        # open, handing its space back to the chat.
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 1)
        sizes = self.ui_state.get("splitter_v3") or [280, 520, 760]
        split.setSizes(sizes)
        layout.addWidget(split, stretch=1)
        self._sync_code_pane(restoring=True)
        return page

    def _sync_code_pane(self, restoring: bool = False):
        """The code column is visible exactly when a file is open -- there is
        no switch to forget about. Closing the last tab hands its space back
        to the chat."""
        has_tabs = self.home_tabs.tabs.count() > 0
        self.home_tabs.setVisible(has_tabs)
        if has_tabs and not restoring:
            self.chat_panel.set_idle(self.chat_panel._hud_base)

    def _toggle_code_pane_forced(self, _on: bool):
        self._sync_code_pane()

    def _set_zoom(self, delta: float):
        self.ui_zoom = max(0.6, min(2.2, self.ui_zoom + delta))
        self.chat_panel.apply_zoom(self.ui_zoom)
        self.home_tabs.apply_zoom(self.ui_zoom)

    def _reset_zoom(self):
        self.ui_zoom = 1.0
        self.chat_panel.apply_zoom(self.ui_zoom)
        self.home_tabs.apply_zoom(self.ui_zoom)

    # ---------------- terminal commands (client-agent access) ---------------- #

    def _terminal_tool_manager(self):
        from agentos.tools import ToolManager
        tools = ToolManager(self._workspace_path(),
                            extra_roots=getattr(self.chat_panel, "last_tagged", []),
                            allow_outside=getattr(self.config, "allow_pc_access", False))
        return tools

    def _approve_command_in_run(self, command: str) -> bool:
        """Called from the run thread when the pipeline wants to execute a
        command: always allowed when confirm_commands is off, otherwise the
        question is marshalled to the UI thread and answered there."""
        if not getattr(self.config, "confirm_commands", True):
            return True
        self._approval_result = False
        try:
            QMetaObject.invokeMethod(
                self, "_show_command_approval",
                Qt.ConnectionType.BlockingQueuedConnection,
                Q_ARG(str, command),
            )
        except Exception:
            return False
        return self._approval_result

    @pyqtSlot(str)
    def _show_command_approval(self, command: str):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Agent wants to run a command")
        box.setText("The agent wants to run this command on your PC:")
        box.setInformativeText(command)
        box.setStandardButtons(QMessageBox.StandardButton.Yes
                               | QMessageBox.StandardButton.No)
        self._approval_result = box.exec() == QMessageBox.StandardButton.Yes

    def _on_command_requested(self, command: str, tags: list):
        """Run a terminal command the user typed (after confirming it)."""
        if not command.strip():
            return
        if getattr(self.config, "confirm_commands", True):
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle("Run terminal command?")
            box.setText(f"Run this command in:\n{self._workspace_path()}")
            box.setInformativeText(command)
            box.setStandardButtons(QMessageBox.StandardButton.Yes
                                   | QMessageBox.StandardButton.No)
            if box.exec() != QMessageBox.StandardButton.Yes:
                self.chat_panel.append_system("\u2318 Command cancelled")
                self.chat_panel.set_idle("Ready")
                return
        tools = self._terminal_tool_manager()
        for tag in tags or []:
            tools.add_root(tag)
        base = os.path.basename(command.strip().split()[0]).lower()
        if base in ("cd",):
            self.chat_panel.append_terminal(
                command, "(cd only changes a directory; use the Projects folder "
                "picker or tag the target file with @)", False)
            self.chat_panel.set_idle("Ready")
            return
        ok, output, _code = tools.run_command(command, timeout=300)
        self.chat_panel.append_terminal(command, output, ok)
        self.chat_panel.set_idle("Command finished" if ok else "Command failed")
        self.status_label.setText(
            f"\u25CF command {'ok' if ok else 'failed'}: {command[:60]}"
        )

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

    def _workspace_is_set(self) -> bool:
        """True only if the user actually chose a projects folder (i.e. it is
        saved in config.yaml). The built-in default does not count, so a
        first-run user is always asked."""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
        try:
            with open(path) as f:
                raw = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            return False
        return bool(str(raw.get("workspace_dir") or "").strip())

    def _first_run_check(self):
        """First launch -> ask for the projects folder; chat stays disabled
        until one is chosen (there is nowhere to work otherwise)."""
        if self._workspace_is_set():
            self.chat_panel.set_ready(True)
            return
        dialog = WelcomeDialog(self)
        if dialog.exec() and dialog.chosen_path:
            self._set_workspace(dialog.chosen_path)
        else:
            self.chat_panel.set_ready(False)

    def _choose_projects_folder(self):
        """Pick where projects are created/opened (chat's Folder button and
        the welcome dialog both land here)."""
        start = self.config.workspace_dir or os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose the folder where projects live", start
        )
        if chosen:
            self._set_workspace(chosen)

    def _set_workspace(self, path: str):
        """Point the app at a projects folder, persist it, enable chat."""
        self.config.workspace_dir = path
        try:
            self.config.save()
        except Exception as e:
            QMessageBox.warning(
                self, "Could not save settings",
                f"The folder is active for this session only:\n{e}",
            )
        self.home_files.set_root(path)
        self.home_tabs.set_project_root(path)
        self.home_files.update_open_list([])
        self.chat_panel.project_root = os.path.abspath(path)
        self.chat_panel.refresh_tag_model()
        self.chat_panel.set_ready(True)
        self.chat_panel.append_system(f"\U0001F4C2 Projects folder: {path}")
        self.chat_panel.append_system(
            "Type @ in the chat to attach a file (from this folder or anywhere "
            "on your PC) and tell the planner what to change in it."
        )

    def _model_manager_box(self):
        # The model picker lives under the chat box now; here we only need a
        # one-line readout, not a whole card of pills.
        box = QGroupBox("\U0001F9E9  Models")
        box.setObjectName("sectionCard")
        layout = QVBoxLayout(box)
        layout.setSpacing(4)
        row = QHBoxLayout()
        self.planner_model_label = QLabel()
        self.planner_model_label.setObjectName("hintLabel")
        self.worker_model_label = QLabel()
        self.worker_model_label.setObjectName("hintLabel")
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
        self.planner_model_label.setText(
            f"Planner: {p.get('provider', '?')} / {p.get('model', '?')}"
        )
        self.worker_model_label.setText(
            f"Worker: {w.get('provider', '?')} / {w.get('model', '?')}"
        )
        self.model_manager_note.setText(
            "\u25B8 change either under the chat box, or in Settings."
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
            # A workspace change in Settings re-points the file panels and
            # unlocks chat if a folder was finally chosen.
            self.home_files.set_root(self._workspace_path())
            self.chat_panel.set_ready(self._workspace_is_set())

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
        if not self._workspace_is_set():
            self.chat_panel.append_system(
                "\U0001F4C2 Choose a projects folder first (Folder\u2026) \u2014 "
                "projects are created inside it."
            )
            return
        self.chat_panel.reset_run_hud()
        self.chat_panel.set_activity("Planner is planning the goal")
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

        self.chat_panel.set_activity("Planner is planning the goal")
        self.status_label.setText("\u25CF Running...")
        self.status_label.setStyleSheet("color: #D97757;")
        self.log_view.clear()
        self.task_table.setRowCount(0)
        self.memory_table.setRowCount(0)
        self.action_rows = {}
        for lbl in self.agent_status_labels.values():
            lbl.setText(lbl.text().split(":")[0] + ": idle")
            lbl.setStyleSheet("color: #8b949e;")

        self.thread = QThread()
        self.worker = BrainWorker(
            goal, project_name, self.config,
            extra_roots=getattr(self.chat_panel, "last_tagged", []),
            command_approver=self._approve_command_in_run,
            test_command=getattr(self.config, "test_command", ""),
        )
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

        # ---- live HUD: what is running right now, and line deltas ----
        hud_updates = {
            "planning_started": "Planner is planning the goal",
            "plan_ready": "Planner: plan ready \u2014 dispatching tasks",
            "test_run_started": "Testing agent: running pytest",
        }
        if kind in hud_updates:
            self.chat_panel.set_activity(hud_updates[kind])
        elif kind == "action_started":
            self.chat_panel.set_activity(
                f"{evt.get('agent', 'agent')} \u00B7 writing {evt.get('path', '')}"
            )
        elif kind == "planner_review":
            self.chat_panel.set_activity(
                f"Planner is checking {os.path.basename(evt.get('path', ''))}"
            )
        elif kind == "file_diff":
            added = int(evt.get("added", 0))
            removed = int(evt.get("removed", 0))
            self.chat_panel.add_diff(evt.get("path", ""), added, removed)
            self.home_files.mark_changed(added, removed)
        elif kind == "thinking":
            self.chat_panel.append_thinking(
                evt.get("stage", "Think"), evt.get("title", ""), evt.get("detail", "")
            )
        elif kind == "stage":
            name = evt.get("name", "Stage")
            status = evt.get("status", "")
            mark = {"done": "\u2713", "failed": "\u2717", "running": "\u25CF"}.get(status, "\u2022")
            self.chat_panel.append_thinking(
                "Pipeline", f"{mark} {name} ({status})", evt.get("detail", "")
            )
            self.chat_panel.set_activity(f"Pipeline \u00B7 {name}")
        elif kind == "command_run":
            self.chat_panel.append_terminal(
                evt.get("command", ""), evt.get("output", ""), bool(evt.get("ok"))
            )
        elif kind == "run_complete":
            self.chat_panel.set_idle(f"Run complete: {evt.get('final_status', '')}")

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
            self._set_agent_status(agent, "working", "#D97757")
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
            self._set_agent_status("security", evt["status"], STATUS_COLORS.get(evt["status"], "#9C9A92"))

        elif kind == "action_started" or kind == "action_done":
            pass  # handled above

        for role in self.agent_status_labels:
            if kind == "action_started" and evt.get("agent") == role:
                self._set_agent_status(role, "working", "#D97757")
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
        color = STATUS_COLORS.get(status, "#9C9A92")
        item.setForeground(QColor(color))
        return item

    def _on_finished(self, report: dict):
        self.chat_panel.set_idle(f"Finished: {report.get('final_status', 'unknown')}")
        status = report.get("final_status", "unknown")
        color = STATUS_COLORS.get(status, "#9C9A92")
        self.status_label.setStyleSheet(f"color: {color};")
        self.status_label.setText(
            f"\u25CF Finished: {status} | tests_passed={report.get('tests_passed')} | "
            f"project: {report.get('project_root')}"
        )
        root = report.get("project_root")
        project_dir = os.path.dirname(root) if root else None
        if root and os.path.isdir(root):
            # Point the files panel at the generated project and auto-open
            # the first code file so the user sees "it open" right away.
            self.home_files.set_root(root)
            self.home_tabs.set_project_root(root)
            # auto-open the project's code files (tabs open by themselves)
            opened = []
            for dirpath, _dirnames, filenames in os.walk(root):
                for name in sorted(filenames):
                    if name.endswith((".py", ".md", ".txt", ".json", ".yaml", ".yml",
                                       ".toml", ".cfg", ".ini")):
                        self.home_tabs.auto_open(
                            os.path.relpath(os.path.join(dirpath, name), root),
                            root_path=root)
                        opened.append(name)
                        if len(opened) >= 6:
                            break
                if len(opened) >= 6:
                    break
            self.home_files.update_open_list(self.home_tabs.open_paths())
            self.fs_model.setRootPath(root)
            self.file_tree.setRootIndex(self.fs_model.index(root))
            # bring the user to the code they just generated
            home_btn = self.nav_buttons.get("Home")
            if home_btn is not None and not home_btn.isChecked():
                home_btn.click()

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
        self.chat_panel.set_idle("Run failed")
        self.chat_panel.append_system(f"\u26A0 Run failed: {error}")
        self.status_label.setStyleSheet("color: #f85149;")
        self.status_label.setText(f"\u25CF FAILED: {error}")
        # Errors must be copyable: a message box whose details are
        # selectable + copyable (and the same text is in the chat above).
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("Run failed")
        box.setText("The run failed. Full error (selectable, copyable) below:")
        box.setDetailedText(error or "(no error text)")
        box.exec()


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(MODERN_STYLESHEET)  # applies to the main window AND any dialogs it opens
    window = Dashboard()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()