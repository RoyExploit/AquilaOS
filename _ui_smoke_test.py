"""Offscreen smoke test for the redesigned dashboard (not shipped code)."""
import sys, os, time, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtWidgets import QApplication, QLabel, QMessageBox

QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
QMessageBox.warning = staticmethod(lambda *a, **k: None)

app = QApplication([])
from agentos_dashboard import Dashboard

d = Dashboard()
d.resize(1400, 880)
d.show()
for _ in range(6):
    app.processEvents()
    time.sleep(0.02)

print("sections ->", d.stack.count(), "| nav ->", list(d.nav_buttons.keys()))
d.nav_buttons["Tasks"].click(); app.processEvents()
d.nav_buttons["Home"].click(); app.processEvents()
print("tasks/home switch ok ->", d.stack.currentIndex() == 0)

# ---------- code column: opens on click, hides when all tabs close ----------
root = d._workspace_path()
proj = os.path.join(root, "_smoke")
os.makedirs(proj, exist_ok=True)
f = os.path.join(proj, "calc.py")
with open(f, "w") as fh:
    fh.write("import time\n\n\ndef add(a, b):\n    return a + b\n\n\nprint(add(1, 2))\n")
d.home_tabs.set_project_root(proj)
d.home_files.set_root(proj)
d.home_files.update_open_list([])
app.processEvents()
print("code column hidden with no tabs ->", not d.home_tabs.isVisible())

d.home_files._open_file(d.home_files.fs_model.index(f))
app.processEvents()
print("opened tabs ->", d.home_tabs.tabs.count(),
      "| column visible ->", d.home_tabs.isVisible())
ed = d.home_tabs._open.get(os.path.abspath(f))
print("gutter width ->", ed.line_number_area_width(), "| editable ->", not ed.isReadOnly())

# edit + Ctrl+S save path (simulate real typing so the dirty flag trips)
ed.setPlainText("print('edited by user')\n")
cursor = ed.textCursor()
cursor.insertText("x = 1\n")
print("dirty tabs ->", len(d.home_tabs._dirty), "| tab title ->", d.home_tabs.tabs.tabText(0))
saved = d.home_tabs.save_current()
print("save ok ->", saved, "| file on disk ->",
      repr(open(f).read().splitlines()[0]))

# closing the last tab hides the column again
d.home_tabs.close_file(f)
app.processEvents()
print("after closing all tabs -> hidden:", not d.home_tabs.isVisible(),
      "| tabs:", d.home_tabs.tabs.count())

# ---------- zoom (Ctrl +/-) ----------
d._set_zoom(0.2)
print("zoom in ->", round(d.ui_zoom, 2), "| chat zoom applied ->", round(d.chat_panel._zoom, 2))
d._reset_zoom()
print("zoom reset ->", d.ui_zoom)

# ---------- @file tagging ----------
d.chat_panel.project_root = proj
d.chat_panel.refresh_tag_model()
d.chat_panel.input.setText("please fix @calc.py")
tags = d.chat_panel.tagged_paths()
print("tagged ->", [os.path.basename(t) for t in tags])
block = d.chat_panel._attachment_block(tags)
print("attachment carries content ->", "return a + b" in block or "edited" in block)
print("tag suggestions include calc.py ->", "calc.py" in d.chat_panel._tag_model.stringList())

# ---------- terminal command from chat (auto-approved in this test) ----------
d.config.confirm_commands = False
d.chat_panel.last_tagged = []
seen = {}
before = len(d.chat_panel.chat_log.findChildren(QLabel))
d._on_command_requested("echo hello-from-terminal", [])
labels = [l.text() for l in d.chat_panel.chat_log.findChildren(QLabel)][before:]
print("terminal output shown ->", any("hello-from-terminal" in t for t in labels))

# ---------- chat: HUD, thinking stream, smart dispatch ----------
p = d.chat_panel
p.reset_run_hud()
p.add_diff("calc.py", 12, 2)
print("hud diff ->", p.hud_diff.text())
p.append_thinking("Review", "Planner verdict: approved", "criteria met")
print("think lines ->", len(p._thinking_cards))
p.think_button.setChecked(False); app.processEvents()
print("think hidden ->", all(not c.isVisibleTo(p) for c in p._thinking_cards))
p.think_button.setChecked(True); app.processEvents()

captured = []
p.run_requested.connect(lambda g: captured.append(g))
p._on_reply("Sure \u2014 here's the plan: 1) module 2) tests\n[[DISPATCH]] Build a calculator",
            "mock / mock")
print("auto-dispatch goal ->", captured)
p._on_reply("Hello! Just chatting.", "mock / mock")
print("casual chat dispatch count (still 1) ->", len(captured))
print("copy-button removed ->", not hasattr(p, "copy_button"))
p.copy_chat()
print("copy via method still works ->", len(QApplication.clipboard().text()) > 50)

# ---------- selectable text everywhere ----------
from PyQt6.QtCore import Qt
flags = [l.textInteractionFlags() for l in p.chat_log.findChildren(QLabel)]
print("selectable labels ->",
      sum(1 for fl in flags if fl & Qt.TextInteractionFlag.TextSelectableByMouse),
      "of", len(flags))

for name, page in (("home", "Home"), ("tasks", "Tasks")):
    d.nav_buttons[page].click(); app.processEvents()
    d.grab().save(f"_shot_{name}.png")
print("SHOTS_OK")

shutil.rmtree(proj, ignore_errors=True)
d.home_tabs.set_project_root(root)
d.home_files.set_root(root)

# ---------- no duplicate tabs; auto-open when a worker writes ----------
proj2 = os.path.join(root, "_smoke2")
os.makedirs(proj2, exist_ok=True)
g = os.path.join(proj2, "worker_file.py")
with open(g, "w") as fh:
    fh.write("y = 2\nprint(y)\n")
d.home_tabs.set_project_root(proj2)
d.home_files.set_root(proj2)
d.home_files._open_file(d.home_files.fs_model.index(g))
d.home_files._open_file(d.home_files.fs_model.index(g))
print("tabs after double click (must be 1) ->", d.home_tabs.tabs.count())
d.home_tabs.close_all()
d.home_tabs.auto_open("worker_file.py", root_path=proj2)
app.processEvents()
d.nav_buttons["Home"].click()
app.processEvents()
print("auto-open on worker write ->", d.home_tabs.tabs.count(),
      "| column visible ->", d.home_tabs.isVisible(),
      "| toggle ->", d.home_files.code_toggle.isChecked(),
      "| hidden ->", d.home_tabs.isHidden(),
      "| stack ->", d.stack.currentIndex())
d.home_tabs.auto_open("..\\..\\outside.py", root_path=proj2)
print("out-of-scope auto-open ignored ->", d.home_tabs.tabs.count())

# ---------- screenshots ----------
for name, page in (("home", "Home"), ("tasks", "Tasks")):
    d.nav_buttons[page].click()
    app.processEvents()
    d.grab().save(f"_shot_{name}.png")
print("SHOTS_OK")

shutil.rmtree(proj2, ignore_errors=True)
d.home_tabs.set_project_root(root)
d.home_files.set_root(root)
print("SMOKE_OK")

