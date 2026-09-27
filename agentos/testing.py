class TestingEngine:
    def __init__(self, tool_manager):
        self.tools = tool_manager

    def run_suite(self, relative_dir: str = "."):
        return self.tools.run_pytest(relative_dir)
