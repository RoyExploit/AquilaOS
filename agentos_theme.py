"""AquilaOS visual theme -- Claude Code desktop look.

Warm charcoal surfaces, one terracotta accent, borderless assistant text,
a rounded composer, and a quiet sidebar. Every objectName used by
agentos_dashboard.py is styled here, so the whole app changes from this file.
"""

# ---- palette (dark) ----
BG        = "#262624"   # main canvas
SIDEBAR   = "#1F1E1D"   # sidebar / code well / terminal blocks
SURFACE   = "#30302E"   # composer, user bubble, inputs
SURFACE2  = "#3A3936"   # hover / selected
BORDER    = "#44433F"
BORDER_SOFT = "#363532"
TEXT      = "#FAF9F5"
TEXT2     = "#C2C0B6"
MUTED     = "#9C9A92"
DIM       = "#6F6D66"
ACCENT    = "#D97757"   # Claude terracotta
ACCENT_H  = "#E5876A"
ACCENT_P  = "#C4684B"
OK        = "#7FB069"
WARN      = "#D9A441"
ERR       = "#E5675D"

UI_FONT   = '"Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif'
SERIF     = '"Georgia", "Tiempos Headline", "Times New Roman", serif'
MONO      = '"Cascadia Code", "JetBrains Mono", "Consolas", "Menlo", monospace'

MODERN_STYLESHEET = f"""
* {{ font-family: {UI_FONT}; }}

QMainWindow, QWidget, QDialog {{
    background-color: {BG};
    color: {TEXT};
    font-size: 13px;
}}
QLabel {{ background-color: transparent; }}
QToolTip {{
    background-color: {SIDEBAR}; color: {TEXT2};
    border: 1px solid {BORDER}; padding: 5px 8px; border-radius: 6px;
}}

/* ================= Sidebar ================= */
QWidget#sidebar {{
    background-color: {SIDEBAR};
    border-right: 1px solid {BORDER_SOFT};
}}
QWidget#sidebar QLabel, QWidget#sidebar QWidget#sidebarInner {{ background: transparent; }}
QWidget#sidebarInner {{ background-color: {SIDEBAR}; }}

QFrame#logoMark {{ background-color: {ACCENT}; border: none; border-radius: 8px; }}
QLabel#logoLetter {{ color: #ffffff; font-size: 15px; font-weight: 800; font-family: {SERIF}; }}
QLabel#headerTitle {{ color: {TEXT}; font-size: 16px; font-weight: 600; font-family: {SERIF}; }}
QLabel#headerSubtitle {{ color: {DIM}; font-size: 10.5px; }}

QPushButton#newChatButton {{
    background-color: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER_SOFT};
    border-radius: 10px;
    padding: 9px 12px;
    text-align: left;
    font-weight: 600;
}}
QPushButton#newChatButton:hover {{ background-color: {SURFACE2}; border-color: {BORDER}; }}

QPushButton#navButton {{
    background-color: transparent;
    border: none;
    color: {TEXT2};
    padding: 8px 12px;
    border-radius: 8px;
    font-weight: 500;
    text-align: left;
}}
QPushButton#navButton:hover {{ background-color: {SURFACE}; color: {TEXT}; }}
QPushButton#navButton:checked {{ background-color: {SURFACE2}; color: {TEXT}; }}

QLabel#sidebarSection {{
    color: {DIM}; font-size: 11px; font-weight: 600; padding: 0px 12px;
}}
QListWidget#recentsList {{
    background: transparent; border: none; outline: none; color: {TEXT2};
}}
QListWidget#recentsList::item {{
    padding: 7px 10px; border-radius: 8px; margin: 1px 0px;
}}
QListWidget#recentsList::item:hover {{ background-color: {SURFACE}; color: {TEXT}; }}
QListWidget#recentsList::item:selected {{ background-color: {SURFACE2}; color: {TEXT}; }}

/* ================= Chat ================= */
QLabel#panelTitle {{ color: {TEXT}; font-size: 14px; font-weight: 600; }}
QLabel#sessionTitle {{
    color: {TEXT2}; font-size: 13.5px; font-weight: 600;
    text-decoration: none; padding: 0px; background: transparent; border: none;
}}
QLabel#sessionTitle:hover {{ color: {TEXT}; }}

QScrollArea#chatScroll {{ border: none; background-color: transparent; }}
QWidget#chatLog {{ background-color: transparent; }}

QFrame#bubbleUser {{
    background-color: {SURFACE}; border: none; border-radius: 14px;
}}
QFrame#bubbleBot {{ background-color: transparent; border: none; }}
QLabel#bubbleNameMine {{ color: {MUTED}; font-size: 10.5px; font-weight: 600; }}
QLabel#bubbleName {{ color: {ACCENT}; font-size: 11px; font-weight: 600; }}
QLabel#chatSystem {{ color: {DIM}; font-size: 11.5px; }}

/* tool-call rows: quiet, monospace, one line each */
QLabel#diffLine {{ color: {MUTED}; font-size: 11.5px; font-family: {MONO}; padding: 2px 0px; }}
QPushButton#thinkLine {{
    background-color: transparent; border: none; color: {MUTED};
    font-size: 11.5px; text-align: left; padding: 2px 0px;
}}
QPushButton#thinkLine:hover {{ color: {TEXT2}; }}
QLabel#thinkBody {{
    color: {MUTED}; font-size: 11.5px; font-family: {MONO}; padding-left: 14px;
}}
QLabel#terminalLine {{
    color: {TEXT2}; background-color: {SIDEBAR};
    border: 1px solid {BORDER_SOFT}; border-radius: 8px;
    padding: 8px 10px; font-family: {MONO}; font-size: 11.5px;
}}

/* live status strip */
QFrame#chatHud {{ background-color: transparent; border: none; }}
QLabel#hudDot {{ color: {ACCENT}; font-size: 11px; }}
QLabel#hudText {{ color: {MUTED}; font-size: 12px; }}
QLabel#hudDiff {{ font-size: 12px; font-weight: 600; }}

/* ================= Composer ================= */
QFrame#composer {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 18px;
}}
QFrame#composer QPlainTextEdit#chatInput {{
    background: transparent; border: none; padding: 4px 4px; color: {TEXT};
    selection-background-color: {ACCENT_P};
}}
QPushButton#sendButton {{
    background-color: {ACCENT}; color: #ffffff; border: none;
    border-radius: 16px; padding: 0px; font-size: 16px; font-weight: 700;
}}
QPushButton#sendButton:hover {{ background-color: {ACCENT_H}; }}
QPushButton#sendButton:pressed {{ background-color: {ACCENT_P}; }}
QPushButton#sendButton:disabled {{ background-color: {SURFACE2}; color: {DIM}; }}

QLineEdit#projectInput {{
    background-color: transparent; border: 1px solid {BORDER_SOFT};
    border-radius: 999px; padding: 3px 10px; color: {MUTED}; font-size: 11.5px;
}}
QLineEdit#projectInput:focus {{ border-color: {ACCENT}; color: {TEXT}; }}

QLabel#modelLink {{
    color: {MUTED}; font-size: 11.5px; text-decoration: underline;
    padding: 0px 6px; background: transparent; border: none;
}}
QLabel#modelLink:hover {{ color: {ACCENT}; }}
QLabel#hintLabel {{ color: {DIM}; font-size: 11px; }}

QPushButton#thinkToggle {{
    background-color: transparent; border: 1px solid {BORDER_SOFT};
    border-radius: 999px; color: {MUTED}; padding: 4px 12px;
    font-size: 11.5px; font-weight: 500;
}}
QPushButton#thinkToggle:hover {{ color: {TEXT}; border-color: {BORDER}; }}
QPushButton#thinkToggle:checked {{
    background-color: {SURFACE}; border: 1px solid {BORDER}; color: {TEXT};
}}

QComboBox#roleCombo {{
    background-color: {SURFACE}; border: 1px solid {BORDER_SOFT};
    border-radius: 999px; padding: 5px 14px; min-width: 130px;
}}
QLabel#modelChip {{
    background-color: {SURFACE}; border: 1px solid {BORDER_SOFT};
    border-radius: 999px; padding: 4px 12px; color: {TEXT2}; font-size: 11.5px;
}}

/* ================= Code / files side panels ================= */
QFrame#panelCard {{
    background-color: {SIDEBAR}; border: 1px solid {BORDER_SOFT}; border-radius: 12px;
}}
QFrame#cardSeparator {{
    border: none; border-top: 1px solid {BORDER_SOFT}; background: transparent;
    max-height: 1px; min-height: 1px;
}}
QPlainTextEdit#codeView {{
    background-color: {SIDEBAR}; border: 1px solid {BORDER_SOFT}; border-radius: 10px;
    font-family: {MONO}; font-size: 12.5px; color: #E8E6DC;
    selection-background-color: {ACCENT_P};
}}
QLabel#pathChip {{
    background-color: {SURFACE}; border: 1px solid {BORDER_SOFT}; border-radius: 999px;
    padding: 4px 12px; color: {TEXT2}; font-size: 11px; font-family: {MONO};
}}
QTabWidget#codeTabs::pane {{
    border: 1px solid {BORDER_SOFT}; border-radius: 10px;
    background-color: {SIDEBAR}; top: -1px;
}}
QTabBar#codeTabBar::tab {{
    background-color: transparent; color: {MUTED};
    border: none; border-bottom: 2px solid transparent;
    padding: 7px 10px 7px 14px; margin-right: 2px;
}}
QTabBar#codeTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar#codeTabBar::tab:hover:!selected {{ color: {TEXT2}; }}
QPushButton#tabClose {{
    background: transparent; border: none; color: {DIM};
    font-size: 13px; font-weight: 700; padding: 0px;
}}
QPushButton#tabClose:hover {{ color: {ERR}; }}

/* ================= Generic widgets / Settings ================= */
QGroupBox {{
    background-color: {SURFACE}; border: 1px solid {BORDER_SOFT}; border-radius: 12px;
    margin-top: 14px; padding: 14px 12px 12px 12px; font-weight: 600; color: {TEXT2};
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 12px; top: 2px; padding: 0 4px; color: {TEXT2};
}}
QWidget#providerCard {{ background-color: transparent; }}
QGroupBox#sectionCard {{ background-color: {SURFACE}; }}

QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {{
    background-color: {SURFACE}; border: 1px solid {BORDER_SOFT}; border-radius: 8px;
    padding: 6px 10px; color: {TEXT}; selection-background-color: {ACCENT_P};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{
    border: 1px solid {ACCENT};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    color: {DIM}; background-color: {SIDEBAR}; border-color: {BORDER_SOFT};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background-color: {SURFACE}; border: 1px solid {BORDER};
    selection-background-color: {SURFACE2}; color: {TEXT}; outline: none;
}}
QCheckBox {{ color: {TEXT2}; spacing: 6px; }}
QMenu {{
    background-color: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px;
}}
QMenu::item {{ padding: 7px 22px; border-radius: 6px; color: {TEXT2}; }}
QMenu::item:selected {{ background-color: {SURFACE2}; color: {TEXT}; }}
QMenu::separator {{ height: 1px; background: {BORDER_SOFT}; margin: 4px 8px; }}

QPushButton {{
    background-color: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER_SOFT};
    border-radius: 8px; padding: 7px 16px; font-weight: 500;
}}
QPushButton:hover {{ background-color: {SURFACE2}; border-color: {BORDER}; }}
QPushButton:pressed {{ background-color: {SIDEBAR}; }}
QPushButton:disabled {{ background-color: {SIDEBAR}; color: {DIM}; border-color: {BORDER_SOFT}; }}
QPushButton#primaryButton {{
    background-color: {ACCENT}; color: #ffffff; border: 1px solid {ACCENT}; font-weight: 600;
}}
QPushButton#primaryButton:hover {{ background-color: {ACCENT_H}; }}
QPushButton#primaryButton:pressed {{ background-color: {ACCENT_P}; }}
QPushButton#primaryButton:disabled {{ background-color: {SURFACE2}; color: {DIM}; border-color: {SURFACE2}; }}
QPushButton#secondaryButton {{ background-color: transparent; color: {TEXT2}; border: 1px solid {BORDER}; }}
QPushButton#secondaryButton:hover {{ background-color: {SURFACE}; color: {TEXT}; }}
QPushButton#detectButton {{ background-color: {SURFACE}; color: {ACCENT}; border: 1px solid {BORDER}; padding: 6px 12px; }}
QPushButton#detectButton:hover {{ background-color: {SURFACE2}; }}

QLabel#agentPill {{
    background-color: {SURFACE}; border: 1px solid {BORDER_SOFT};
    border-radius: 8px; padding: 6px 10px;
}}
QLabel#statusBar {{
    background-color: {SIDEBAR}; border-top: 1px solid {BORDER_SOFT};
    padding: 6px 18px; font-size: 11.5px; font-weight: 500;
}}

QWidget#dialogHeader {{ background-color: {SIDEBAR}; border-bottom: 1px solid {BORDER_SOFT}; }}
QLabel#dialogTitle {{ font-size: 16px; font-weight: 600; color: {TEXT}; font-family: {SERIF}; }}
QLabel#dialogSubtitle {{ font-size: 11.5px; color: {MUTED}; }}
QWidget#dialogFooter {{ background-color: {SIDEBAR}; border-top: 1px solid {BORDER_SOFT}; }}
QScrollArea#settingsScroll {{ border: none; background: transparent; }}
QDialog#settingsDialog {{ background-color: {BG}; }}
QTabWidget#mainTabs::pane {{ border: none; background: transparent; }}
QTabWidget#mainTabs > QWidget {{ background: transparent; }}

QTabWidget::pane {{ border: 1px solid {BORDER_SOFT}; border-radius: 10px; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {MUTED}; padding: 8px 16px; margin-right: 2px;
    border-bottom: 2px solid transparent; font-weight: 500;
}}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover:!selected {{ color: {TEXT2}; }}

QTableWidget, QTreeView {{
    background-color: transparent; alternate-background-color: {SURFACE};
    border: none; gridline-color: {BORDER_SOFT}; color: {TEXT};
}}
QTreeView {{ outline: none; }}
QTreeView::item {{ padding: 3px 2px; border-radius: 6px; }}
QTreeView::item:hover {{ background-color: {SURFACE}; }}
QHeaderView::section {{
    background-color: {SURFACE}; color: {MUTED}; border: none;
    border-bottom: 1px solid {BORDER_SOFT}; padding: 6px; font-weight: 600;
}}
QTableWidget::item:selected, QTreeView::item:selected {{ background-color: {SURFACE2}; color: {TEXT}; }}

QPlainTextEdit#logView {{
    background-color: {SIDEBAR}; border: 1px solid {BORDER_SOFT}; border-radius: 10px;
    font-family: {MONO}; font-size: 12px; color: {TEXT2};
}}

QSplitter#mainSplitter::handle {{ background-color: {BG}; width: 8px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {DIM}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 4px; min-width: 24px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
QMessageBox {{ background-color: {SURFACE}; }}
"""
