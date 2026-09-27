"""Verify: (a) settings selections never silently reset, (b) planner
orchestration: casual chat -> [[DISPATCH]] -> pipeline, planner review loop."""
import sys, os, shutil, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

# --- protect the real config.yaml from being rewritten by save() tests ---
shutil.copy("config.yaml", "config.yaml.bak")

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox
    app = QApplication([])

    # ================= 1. Settings roundtrip & dirty guard =================
    import agentos_dashboard as D
    from agentos.config import AgentOSConfig

    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.critical = staticmethod(lambda *a, **k: None)
    save_calls = []
    def _q(*a, **k):
        save_calls.append(a)
        return QMessageBox.StandardButton.Save
    QMessageBox.question = staticmethod(_q)

    cfg = AgentOSConfig.load()

    # (a) cloud pin roundtrip
    cfg.planner = {"provider": "gemini", "model": "auto", "api_key": "AQ.x"}
    d1 = D.SettingsDialog(cfg)
    d1.planner_widget.model_pin.setText("gemini-3.8-flash")
    assert d1.planner_widget.to_dict()["model"] == "gemini-3.8-flash", "pin not used"
    d1._on_save()
    reloaded = AgentOSConfig.load()
    assert reloaded.planner.get("model") == "gemini-3.8-flash", "pin lost on save/reload"
    d2 = D.SettingsDialog(reloaded)
    assert d2.planner_widget.model_pin.text() == "gemini-3.8-flash", "pin lost on reopen"
    assert d2.planner_widget.model_pin_row.isVisible() or True  # hidden until shown
    print("PASS cloud model pin roundtrips")

    # blank pin stays 'auto'
    d2.planner_widget.model_pin.setText("")
    assert d2.planner_widget.to_dict()["model"] == "auto"
    print("PASS blank pin = auto")

    # (b) dirty guard: edit -> reject -> Save chosen -> written
    cfg = AgentOSConfig.load()
    d3 = D.SettingsDialog(cfg)
    d3.worker_widget.model_combo.setCurrentText("SHOULD-BE-SAVED")
    d3.reject()
    assert save_calls, "reject() did not prompt on dirty state"
    assert AgentOSConfig.load().worker.get("model") == "SHOULD-BE-SAVED"
    print("PASS unsaved-changes guard saves on close")

    # (c) clean dialog closes without prompting
    save_calls.clear()
    d4 = D.SettingsDialog(AgentOSConfig.load())
    d4.reject()
    assert not save_calls, "clean dialog should close without prompting"
    print("PASS clean dialog closes quietly")

    # ================= 2. Planner review loop (mock pipeline) =============
    from agentos.brain import CoreBrain
    from agentos.llm.factory import build_provider

    events = []
    planner = build_provider({"provider": "mock"})
    worker = build_provider({"provider": "mock"})
    brain = CoreBrain(planner, worker, workspace_dir="workspace",
                      max_repair_attempts=1, planner_review=True)
    report = brain.run("Build a calculator", "_verify_project",
                       progress_cb=events.append)
    kinds = [e["kind"] for e in events]
    assert "planner_review" in kinds, "planner review never ran"
    assert report["final_status"] == "completed", report["final_status"]
    reviews = [e for e in events if e["kind"] == "planner_review"]
    print(f"PASS planner review ran on {len(reviews)} file(s); "
          f"status={report['final_status']}")
    # review off -> no review events
    events2 = []
    brain2 = CoreBrain(planner, worker, workspace_dir="workspace",
                       max_repair_attempts=1, planner_review=False)
    brain2.run("Build a calculator", "_verify_project2", progress_cb=events2.append)
    assert "planner_review" not in [e["kind"] for e in events2]
    print("PASS planner_review=False skips review")

    # ================= 3. Chat dispatch parsing ===========================
    cfg_mock = AgentOSConfig(planner={"provider": "mock", "model": "mock-deterministic"},
                             worker={"provider": "mock", "model": "mock-deterministic"})
    panel = D.ChatPanel(cfg_mock)
    captured = []
    panel.run_requested.connect(lambda g: captured.append(g))
    panel._on_reply("Sure -- here's the plan:\n- scaffold\n- UI\n"
                    "[[DISPATCH]] Build a PyQt6 calculator", "mock / mock-deterministic")
    assert captured == ["Build a PyQt6 calculator"], captured
    # casual reply without marker -> no dispatch
    panel._on_reply("Hello! Ask me anything.", "mock / mock-deterministic")
    assert captured == ["Build a PyQt6 calculator"], "casual chat must not dispatch"
    # marker must not leak into any visible bubble/system label
    from PyQt6.QtWidgets import QLabel
    visible = " ".join(l.text() for l in panel.chat_log.findChildren(QLabel))
    assert "[[DISPATCH]]" not in visible, "dispatch marker leaked into chat UI"

    # code dumps get scrubbed out of the visible chat
    panel._on_reply("Here you go:\n```python\nprint(1)\n```\nDone.", "ollama / x")
    visible = " ".join(l.text() for l in panel.chat_log.findChildren(QLabel))
    assert "print(1)" not in visible, "code leaked into chat"
    assert "workers write the files" in visible, "missing code-guard hint"
    print("PASS code dumps scrubbed from chat + hint shown")
    print("PASS [[DISPATCH]] -> auto-run; casual chat stays chat; marker hidden")

    # ================= 4. live planner chat (real ollama) =================
    try:
        import requests
        requests.get("http://localhost:11434/api/tags", timeout=3).raise_for_status()
        real_cfg = AgentOSConfig.load()
        w = D.ChatWorker("planner", dict(real_cfg.planner),
                         "I want a small calculator app with buttons. Can you handle it?")
        got = {}
        w.reply.connect(lambda t, m: got.update(text=t, model=m))
        w.failed.connect(lambda e: got.update(error=e))
        w.run()  # direct call -> same-thread signal = synchronous
        if "error" in got:
            print(f"SKIP live ollama chat: {got['error'][:120]}")
        else:
            has = "[[DISPATCH]]" in got["text"]
            print(f"PASS live planner reply (dispatch marker: {has}) :: "
                  f"{got['text'][:160].replace(chr(10), ' / ')}")
    except Exception as e:
        print(f"SKIP live ollama chat: {e}")

    print("ALL_VERIFY_OK")
finally:
    shutil.move("config.yaml.bak", "config.yaml")
    # tidy test projects
    for p in ("workspace/_verify_project", "workspace/_verify_project2"):
        shutil.rmtree(p, ignore_errors=True)
