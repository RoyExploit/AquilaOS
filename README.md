# AgentOS - Autonomous AI Agent Platform

Give AgentOS one high-level goal, for example "Build a PyQt6 calculator
application". It plans the work, generates the project files, checks them,
runs the tests, repairs failures, and reports an honest final status. The
same engine drives both the desktop dashboard and the command line.

## What it does

1. **Plan** - the planner model decomposes your goal into a validated plan
   tree: `phases -> milestones -> tasks -> subtasks -> atomic_actions`.
2. **Generate** - a worker agent writes each file. Every path stays inside
   the project's workspace directory; anything outside it is rejected.
3. **Verify** - every generated `.py` file is compiled with `py_compile`;
   `.json`/`.yaml` manifests are parsed. A file that fails is re-sent to
   the worker with the exact error, then re-checked - up to
   `max_repair_attempts` (default 3). The planner also reviews each result
   against its success criteria when `planner_review` is on.
4. **Test** - the generated project's real `pytest` suite runs. If it fails,
   the pipeline repairs the **implementation first** and re-runs the suite;
   only then does it touch the test file itself.
5. **Report honestly** - the final status is `completed`,
   `escalation_required`, or `completed_with_command_failure`. Anything that
   could not be fixed is logged and shown, never silently marked successful.
6. **Optional project command** - set `test_command` (for example
   `python -m pytest -q`) to run one more command inside the generated
   project and record its result.

The dashboard streams every step live: pipeline stage, per-file changes,
planner reasoning, terminal output, test results, and the final status.

## How it works

- **LLM providers** - Anthropic, OpenAI, Gemini, OpenRouter, Ollama
  (local/offline), LM Studio (local/offline), and a deterministic `mock`
  provider for zero-setup runs. Planner and worker can use different
  providers/models. Cloud models are resolved from the API key
  (`model: auto`); a configured model name is used only if the key can
  actually call it.
- **Agents** - a Coding Agent (with backend/api/database/ui/refactoring
  variants), a Testing Agent, a Documentation Agent, a DevOps Agent, and a
  Security Agent. Each role is only allowed specific tool methods; anything
  else raises `PermissionError` at runtime.
- **Coordinator** - routes each atomic action to the matching specialist
  based on its `agent` tag.
- **Verification** - compiles every generated `.py` file and parses every
  generated `.json`/`.yaml` manifest.
- **Testing** - runs the generated project's real `pytest` suite.
- **Repair** - on verification or test failure, sends the broken file and
  the exact error back to the worker, rewrites the file, and re-checks -
  up to `max_repair_attempts` times (default 3), then escalates instead of
  looping forever or silently reporting success.
- **Security scan** - every generated `.py` file is scanned with a fixed
  pattern list (`eval()`, `exec()`, `os.system()`, `shell=True`, unsafe
  unpickling, unparameterized SQL). Findings are fixed and re-scanned.
- **Run history** - every project, action, and event is written to a
  SQLite log (`agentos_memory.sqlite3`) you can inspect later.
- **Desktop dashboard** - a PyQt6 interface that runs the engine above on
  a background thread and streams every event live: goal, model in use,
  agent status, task queue, execution log, project files, and saved chat.

---

## The pipeline, step by step

The same sequence runs from the CLI and the dashboard. The dashboard displays the emitted stages: `Intent`, `Context`, `Plan`, `Modules`, `Generate`, `Static analysis`, `Review`, `Test`, `Repair`, and `Apply`.

1. **Intent** records the goal.
2. **Context** lists the existing files in the target project folder, so the planner extends or repairs them instead of recreating them.
3. **Plan** returns a validated plan tree: `phases -> milestones -> tasks -> subtasks -> atomic_actions`. A malformed plan fails before anything is executed.
4. **Modules** flattens that validated tree into an ordered atomic-action list.
5. **Generate** - the coordinator sends each atomic action to its specialist:
   `create_folder` creates the folder directly; `generate_file` writes code;
   `generate_test` writes pytest tests; `generate_doc` writes the README;
   `generate_config` writes build/config files. Every write is checked to
   stay inside the project workspace.
6. **Static analysis** - each generated file is checked immediately
   (`.py` compiled, `.json`/`.yaml` parsed). A failure emits a `Repair`
   running stage.
7. **Review** - with `planner_review` on, the planner judges each result
   against its success criteria: `approved`, `approved_after_fix`,
   `review_warning`, or `review_unresolved`. A rejection re-prompts the
   worker through the repair loop; a failed repair is marked
   `failed_unrepaired` and sets `escalation_required`.
8. **Security scan** - every generated `.py` file is rescanned against the
   fixed pattern list and reported as `clean`, `flagged_and_fixed`, or
   `flagged_unresolved`.
9. **Test** - runs the full `pytest` suite only if the plan contains a
   `generate_test` action; otherwise it records `no tests generated` and
   continues. When tests fail, the pipeline repairs implementation `.py`
   files first and re-runs the suite; it repairs the test files only if
   the implementation still cannot pass.
10. **Repair** reports `done` when the tests pass after a fix, or `failed`
    with the remaining test log.
11. **Apply** reports the real outcome. `completed` means every stage
    passed. Anything else is shown as failed: `escalation_required` when a
    repair or the test suite could not be fixed, or
    `completed_with_command_failure` when only the optional `test_command`
    failed. Files stay on disk either way, but a non-`completed` Apply
    says not to trust them without checking the Repair/Test output.

Every project and event is also written to the SQLite log.

---

## Setup

```bash
pip install -r requirements.txt
copy config.yaml.example config.yaml
```

Edit `config.yaml`:

- **Cloud providers** need only `provider` and `api_key`. Leave
  `model: auto` unless the same key can call a different model you prefer.
- **Ollama and LM Studio** are local and need `base_url`. Their models are
  whatever is already loaded on that server.
- Without `config.yaml`, both planner and worker use the built-in `mock`
  provider.

You can also supply cloud keys through environment variables instead of
`config.yaml`:

- Anthropic: `ANTHROPIC_API_KEY`
- OpenAI: `OPENAI_API_KEY`
- Gemini: `GEMINI_API_KEY`
- OpenRouter: `OPENROUTER_API_KEY`

`config.yaml` is local-only and is not committed. Use
`config.yaml.example` as the template.

```bash
python agentos_dashboard.py
```

```bash
python -m agentos.cli --demo
python -m agentos.cli "Build a PyQt6 markdown note-taking app" --project-name notes_app
```

The dashboard saves provider, model, and project-folder changes from
Settings into your local `config.yaml`. Only `config.yaml.example` is
committed; your edited `config.yaml` is ignored and stays on your machine.

---

## Dashboard

- **Home** - chat on one side, generated code in tabs in the middle, and
  the project file tree beside them. Clicking a generated file opens it in
  a tab. Code stays editable and is saved with Ctrl+S.
- **Tasks** - live model in use, agent status, task queue, execution log,
  run history, and the final status (`completed`, `escalation_required`,
  or `completed_with_command_failure`).
- **Settings** - provider, model, project folder, `planner_review`,
  `max_repair_attempts`, and the optional `test_command`.
- **Chat input** - plain text, Enter sends and Shift+Enter adds a line.
  Type `@` to select a project file to attach to the message. Type
  `$ command` or `! command` to run a terminal command; type `/help` to
  see the supported commands. Terminal output appears in the same chat.
- **Model selector** - the model chip shows the role and the resolved
  model. Clicking it switches between Auto and any model the current key
  can actually call, and saves that choice to the configuration.

---

## Safety

- File operations stay inside the project workspace unless a rooted path
  is explicitly allowed through configuration or chat tagging.
- Each agent role can call only its listed tool methods; other tool calls
  raise `PermissionError`.
- Repairs are bounded by `max_repair_attempts`; unresolved files and tests
  are escalated instead of silently marked successful.
- There is no process sandbox or container isolation.
- Entering commands in chat or setting `test_command` executes real shell
  commands, so only run or configure commands you trust.