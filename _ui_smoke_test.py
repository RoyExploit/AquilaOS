"""Offscreen smoke test for the redesigned dashboard (not shipped code)."""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtWidgets import QApplication

app = QApplication([])
from agentos_dashboard import Dashboard

d = Dashboard()
d.resize(1300, 850)
d.show()
app.processEvents()

os.makedirs("workspace", exist_ok=True)
with open("workspace/demo.py", "w") as f:
    f.write('print("hello from the models")\n')

# seed the chat so the screenshot shows real conversation bubbles
p = d.chat_panel
p.append_user("Build me a PyQt6 calculator app")
p.append_system("→ planner is thinking…")
p._append_bubble("planner · gemini / gemini-3.8-flash",
                 "Plan ready. I will create 4 files: calculator UI, logic, "
                 "tests and README. Dispatching to the workers now.", mine=False)
p.append_system("✅ Run complete: success")
app.processEvents()

for name, page in (("home", "Home"), ("tasks", "Tasks"), ("files", "Files")):
    d.nav_buttons[page].click()
    app.processEvents()
    d.grab().save(f"_shot_{name}.png")
print("SHOTS_OK")

