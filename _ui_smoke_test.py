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

# ---------- column order is part of the contract: files | code | chat ----------
order = [d.home_split.widget(i) for i in range(d.home_split.count())]
print("column order (files, code, chat) ->",
      order == [d.home_files, d.home_tabs, d.chat_panel])
print("files column has no show/hide toggle ->",
      not hasattr(d.chat_panel, "files_button"))

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

# left column has no dead space: the open-files section hides when empty
print("open-files section hidden when empty ->", not d.home_files.open_section.isVisible())

d.home_files._open_file(d.home_files.fs_model.index(f))
d.home_files.update_open_list(d.home_tabs.open_paths())
app.processEvents()
print("open-files section shown when open ->", d.home_files.open_section.isVisible())

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
print("tabs after double click (must be 1) ->", d.home_tabs.tabs.count())
d.home_tabs.close_all()
d.home_tabs.auto_open("worker_file.py", root_path=proj2)
app.processEvents()
d.nav_buttons["Home"].click()
app.processEvents()
print("auto-open on worker write ->", d.home_tabs.tabs.count(),
      "| column visible ->", d.home_tabs.isVisible(),
      "| hidden ->", d.home_tabs.isHidden(),
      "| stack ->", d.stack.currentIndex(),
      "| code shown -,->", "y = 2" in d.home_tabs._open[
          os.path.abspath(g)].toPlainText())
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

# ---------- chat input grows/wraps (was a fixed one-line box) ----------
from agentos_dashboard import ChatInput
assert isinstance(d.chat_panel.input, ChatInput), "chat input is not ChatInput"
p = d.chat_panel
p.set_ready(True)
p.input.setPlainText("short")
app.processEvents()
one = p.input.height()
p.input.setPlainText("a much longer message that has to wrap " * 8)
app.processEvents()
grown = p.input.height()
print("chat input grows ->", grown > one,
      f"({one}px -> {grown}px, cap {ChatInput.MAX_H})")
p.input.setPlainText("line1\nline2\nline3\nline4")
app.processEvents()
print("chat input multi-line ->", p.input.height() > one)
p.input.clear()
app.processEvents()
print("chat input shrinks back ->", p.input.height() == one)

# ---------- commands are typed straight in: $ / ! ----------
cmds = []
p.command_requested.connect(lambda c, t: cmds.append(c))
p.input.setPlainText("$ echo hi")
p._on_send(); app.processEvents()
print("$ command routed ->", cmds == ["echo hi"], cmds)
p.input.setPlainText("!echo yo")
p._on_send(); app.processEvents()
print("! command routed ->", cmds == ["echo hi", "echo yo"], cmds)
print("command toggle button removed ->", not hasattr(p, "command_button"))
print("terminal_mode flag gone ->", not hasattr(p, "terminal_mode"))

cmds.clear()
p.input.setPlainText("/help")
p._on_send(); app.processEvents()
helped = " ".join(l.text() for l in p.chat_log.findChildren(QLabel))
print("/help lists commands ->", "$ <cmd>" in helped and not cmds)

# ---------- planner model showcase under the chat ----------
print("model chip is clickable ->", hasattr(p.model_chip, "clicked"))
print("model chip shows provider/model ->", "/" in p.model_chip.text(),
      repr(p.model_chip.text()))

# ---------- the code column has no on/off switch any more ----------
print("code toggle removed ->", not hasattr(d.home_files, "code_toggle"))

# ---------- execution log pane actually receives lines ----------
d._append_log("log-line-check-123")
app.processEvents()
print("log pane receives lines ->", "log-line-check-123" in d.log_view.toPlainText())

# ---------- chat sessions: one conversation per task ----------
from agentos.sessions import SessionStore
p.session_title_label.setText("placeholder")  # ensure attribute exists
first_id = p.session["id"]
p.append_user("Build a weather dashboard")
assert p.session_store.load(first_id) is not None, "session not persisted"
stored = p.session_store.load(first_id)
assert any(m.get("text") == "Build a weather dashboard" for m in stored["messages"]), \
    "user message not saved into the session"
ctx = SessionStore.context_block(stored)
print("session context carries task ->", "Task:" in ctx and "weather dashboard" in ctx)

p.new_chat(); app.processEvents()
assert p.session["id"] != first_id, "new chat did not create a new session"
old_texts = [m.get("text", "") for m in p.session.get("messages", [])]
assert not any("weather dashboard" in t for t in old_texts), \
    "new chat inherited old messages"
print("new chat messages are fresh ->", old_texts)
ctx2 = SessionStore.context_block(p.session)
print("new chat has clean context ->", "weather dashboard" not in ctx2)
print("chats listed ->", [s["id"][:8] for s in p.session_store.list()][:3])
assert len(p.session_store.list()) >= 2, "old chats not kept"

p.switch_chat(first_id); app.processEvents()
labels = [l.text() for l in p.chat_log.findChildren(QLabel)]
print("switch back restores history ->",
      any("weather dashboard" in t for t in labels))
# title: rename the restored chat (auto-titling runs in _on_send)
p.session["title"] = "Weather dashboard"
p.session_store.save(p.session)
p._render_session()
print("switch back shows title ->", "Weather dashboard" in p.session_title_label.text())
titles = [s["title"] for s in p.session_store.list()]
print("chat list keeps task titles ->", "Weather dashboard" in titles, titles[:3])

# model link is under the chat box (plain underlined text, no pills)
print("model link style ->", p.model_chip.objectName() == "modelLink",
      repr(p.model_chip.text()))
print("role chooser hidden from header ->", not p.role_combo.isVisible())

# cleanup sessions created by this test
for s in p.session_store.list():
    p.session_store.delete(s["id"])
p.new_chat()

# let any in-flight model-catalog fetch finish before the app goes away
d.chat_panel.wait_for_model_fetch()
print("SMOKE_OK")

